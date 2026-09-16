"""Persistent, idempotent daily production planning."""

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import database as db, topics


TERMINAL_FAILURES = {"FAILED", "REJECTED", "CANCELLED", "UPLOAD_BLOCKED"}


def _id(office_id, date):
    return f"daily-{office_id}-{date}"


def _utc_iso(value):
    return value.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def _timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def get(office_id, date):
    return next(
        (r["data"] for r in db.rows("daily_production_plans", office_id) if r["id"] == _id(office_id, date)),
        None,
    )


def _save(office_id, plan):
    plan["updated_at"] = time.time()
    db.put("daily_production_plans", office_id, plan, id=_id(office_id, plan["date"]))
    return plan


def _slot_times(settings, now):
    zone = ZoneInfo(settings["timezone"])
    local_now = datetime.fromtimestamp(now, zone)
    target_day = local_now
    if settings.get("production_window_enabled", False):
        start_hour, start_minute = map(int, settings.get("production_window_start", "18:00").split(":"))
        end_hour, end_minute = map(int, settings.get("production_window_end", "09:00").split(":"))
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute
        current_minutes = local_now.hour * 60 + local_now.minute
        if start_minutes > end_minutes and current_minutes >= end_minutes:
            # A cross-midnight shift beginning tonight produces tomorrow's upload batch.
            target_day = local_now + timedelta(days=1)
    delay = max(5, int(settings.get("missed_slot_delay_minutes", 30)))
    catchup = 0
    slots = []
    for index, value in enumerate(settings["upload_times"][: settings["videos_per_day"]]):
        hour, minute = map(int, value.split(":"))
        intended = target_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        effective = intended
        note = None
        if intended.timestamp() <= now + 600:
            catchup += 1
            effective = local_now + timedelta(minutes=delay * catchup)
            note = f"Missed or too-close {value} local slot reconciled after restart"
        slots.append(
            {
                "index": index,
                "local_publish_time": value,
                "intended_publish_at": _utc_iso(intended),
                "scheduled_publish_at": _utc_iso(effective),
                "schedule_note": note,
                "topic_id": None,
                "video_id": None,
                "job_id": None,
                "status": "PLANNED",
                "replacement_count": 0,
                "youtube_video_id": None,
                "youtube_upload_at": None,
                "published_at": None,
            }
        )
    return target_day.strftime("%Y-%m-%d"), slots


def ensure_plan(office_id, now=None, create_jobs=True):
    now = now or time.time()
    office = db.office(office_id)
    settings = office["settings"]
    date, new_slots = _slot_times(settings, now)
    plan = get(office_id, date)
    if not plan:
        plan = {
            "date": date,
            "timezone": settings["timezone"],
            "target_video_count": settings["videos_per_day"],
            "publish_slots": new_slots,
            "production_status": "PLANNED",
            "created_at": now,
            "warnings": [],
            "replacement_jobs": 0,
        }
        _save(office_id, plan)
        db.event(office_id, f"Daily plan created: {date}, target {settings['videos_per_day']} videos")
    else:
        existing_indices = {slot.get("index") for slot in plan.get("publish_slots", [])}
        added_slots = [slot for slot in new_slots if slot["index"] not in existing_indices]
        if added_slots or plan.get("target_video_count") != settings["videos_per_day"]:
            plan["publish_slots"].extend(added_slots)
            plan["target_video_count"] = settings["videos_per_day"]
            plan["timezone"] = settings["timezone"]
            _save(office_id, plan)
            if added_slots:
                db.event(
                    office_id,
                    f"Daily plan expanded to {settings['videos_per_day']} configured video slots",
                )
    reconcile(office_id, plan, now, create_jobs)
    return get(office_id, date)


def _job(job_id):
    if not job_id:
        return None
    with db.connection() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def _video(office_id, video_id):
    return next((r["data"] for r in db.rows("videos", office_id) if r["id"] == video_id), None)


def _budget_allows_replacement(office, now, estimated=0.25):
    if os.getenv("ZERO_COST_MODE", "true").lower() == "true":
        return True
    settings = office["settings"]
    zone = ZoneInfo(settings["timezone"])
    local = datetime.fromtimestamp(now, zone)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    events = db.rows("cost_events", office["id"])
    daily = sum(float(r["data"].get("estimated_usd", 0)) for r in events if r["created"] >= day_start)
    monthly = sum(float(r["data"].get("estimated_usd", 0)) for r in events if r["created"] >= month_start)
    return daily + estimated <= settings["daily_budget"] and monthly + estimated <= settings["monthly_budget"]


def _refresh_slot(office_id, slot):
    video = _video(office_id, slot.get("video_id"))
    job = _job(slot.get("job_id"))
    if video:
        slot["status"] = video.get("status", slot["status"])
        for key in ("youtube_video_id", "youtube_upload_at", "published_at"):
            if video.get(key) is not None:
                slot[key] = video[key]
    if job and job["status"] in ("FAILED", "CANCELLED", "BLOCKED") and slot["status"] not in ("PUBLISHED", "SCHEDULED"):
        slot["status"] = job["status"]
        slot["failure_reason"] = job.get("error")


def release_job(office_id, job_id):
    """Release a pending plan slot without deleting production history."""
    for row in db.rows("daily_production_plans", office_id):
        plan = row["data"]
        changed = False
        for slot in plan.get("publish_slots", []):
            if slot.get("job_id") == job_id and slot.get("status") not in ("SCHEDULED", "PUBLISHED"):
                slot.update(
                    topic_id=None, topic_title=None, category=None, category_family=None,
                    video_id=None, job_id=None, status="PLANNED",
                )
                changed = True
        if changed:
            _save(office_id, plan)


def _manual_jobs(office_id):
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT j.* FROM jobs j JOIN active_topic_jobs a ON a.job_id=j.id "
            "WHERE j.office_id=? AND j.status IN ('QUEUED','PLANNED','RETRYING','WAITING') ORDER BY j.created,j.id",
            (office_id,),
        ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("manual_queue") and not payload.get("daily_plan_id"):
            result.append((dict(row), payload))
    return result


def _assign_existing_job(office_id, plan, slot, job, payload, topic):
    with db.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO publish_slot_claims VALUES(?,?,?,?,?)",
            (office_id, slot["scheduled_publish_at"], job["id"], "PLANNED", time.time()),
        )
        payload.update(
            scheduled_publish_at=slot["scheduled_publish_at"],
            daily_plan_id=_id(office_id, plan["date"]),
            slot_index=slot["index"],
        )
        connection.execute(
            "UPDATE jobs SET payload=?,updated=? WHERE id=?",
            (json.dumps(payload), time.time(), job["id"]),
        )
    video = _video(office_id, job["id"])
    if video:
        video.update(
            scheduled_publish_at=slot["scheduled_publish_at"],
            daily_plan_id=_id(office_id, plan["date"]),
            plan_slot_index=slot["index"],
        )
        db.put("videos", office_id, video, job["id"], job["id"])
    family = topics.normalize_category(topic.get("category", ""), topic.get("title", ""))
    slot.update(
        topic_id=payload.get("topic_id"), topic_title=topic.get("title"),
        category=topic.get("category"), category_family=family,
        video_id=job["id"], job_id=job["id"], status="QUEUED",
        category_selection_reason="Owner-selected manual queue topic has priority",
        topic_selection_reason="Selected manually by owner",
        score_breakdown={},
    )


def _replaceable(slot):
    if slot.get("status") in TERMINAL_FAILURES:
        return True
    if slot.get("status") != "BLOCKED":
        return False
    # Credential, quota and global configuration failures would affect the
    # replacement too. Content-specific evidence/media blocks can be replaced.
    reason = (slot.get("failure_reason") or "").lower()
    return any(
        marker in reason
        for marker in (
            "scene requires authentic external imagery",
            "duplicate topic blocked",
            "no unused topic candidates",
        )
    )


def reconcile(office_id, plan=None, now=None, create_jobs=True):
    from . import engine

    now = now or time.time()
    office = db.office(office_id)
    if plan is None:
        zone = ZoneInfo(office["settings"]["timezone"])
        plan = get(office_id, datetime.fromtimestamp(now, zone).strftime("%Y-%m-%d"))
    if not plan:
        return None
    for slot in plan["publish_slots"]:
        _refresh_slot(office_id, slot)

    assigned = {s.get("topic_id") for s in plan["publish_slots"] if s.get("topic_id")}
    need = [s for s in plan["publish_slots"] if not s.get("job_id")]
    failed = [s for s in plan["publish_slots"] if _replaceable(s)]
    candidates_for_replacement = []
    for slot in failed:
        cutoff = _timestamp(slot["scheduled_publish_at"]) + int(office["settings"].get("replacement_cutoff_minutes", 180)) * 60
        if slot.get("replacement_count", 0) >= int(office["settings"].get("max_replacements_per_slot", 2)):
            continue
        if now > cutoff:
            slot["replacement_blocked"] = "Configured replacement cutoff passed"
            continue
        if not _budget_allows_replacement(office, now):
            slot["replacement_blocked"] = "Daily or monthly budget cannot fund a replacement"
            continue
        with db.connection() as connection:
            connection.execute("DELETE FROM publish_slot_claims WHERE video_id=?", (slot.get("job_id"),))
        slot["job_id"] = None
        slot["video_id"] = None
        slot["youtube_video_id"] = None
        slot["status"] = "PLANNED"
        slot["replacement_count"] = slot.get("replacement_count", 0) + 1
        plan["replacement_jobs"] = plan.get("replacement_jobs", 0) + 1
        candidates_for_replacement.append(slot)
    need.extend(candidates_for_replacement)

    if create_jobs and need:
        topics_by_id = {row["id"]: row["data"] for row in db.rows("topics", office_id)}
        manual = _manual_jobs(office_id)
        for slot, (job, payload) in zip(list(need), manual):
            topic = topics_by_id.get(payload.get("topic_id"), {})
            _assign_existing_job(office_id, plan, slot, job, payload, topic)
            assigned.add(payload.get("topic_id"))
            need.remove(slot)

        with db.connection() as connection:
            active_topic_ids = {
                row["topic_id"] for row in connection.execute(
                    "SELECT topic_id FROM active_topic_jobs WHERE office_id=?", (office_id,)
                ).fetchall()
            }
        existing_topics = [
            topics_by_id[slot["topic_id"]] for slot in plan["publish_slots"]
            if slot.get("topic_id") in topics_by_id
        ]
        ranked = topics.diverse_candidates(
            office_id, len(need), assigned | active_topic_ids, existing_topics
        )
        if len(ranked) < len(need):
            try:
                topics.discover(office_id)
            except Exception as exc:
                warning = f"Discovery unavailable during reconciliation: {type(exc).__name__}"
                if warning not in plan["warnings"]:
                    plan["warnings"].append(warning)
            ranked = topics.diverse_candidates(
                office_id, len(need), assigned | active_topic_ids, existing_topics
            )
        for slot, topic in zip(need, ranked):
            topic_id = topic["id"]
            job_id = engine.enqueue(
                office_id,
                topic_id=topic_id,
                scheduled_publish_at=slot["scheduled_publish_at"],
                plan_id=_id(office_id, plan["date"]),
                slot_index=slot["index"],
                replacement=slot.get("replacement_count", 0) > 0,
                override_duplicate=True,
            )
            slot.update(
                topic_id=topic_id,
                topic_title=topic["title"],
                topic_score=topic["topic_score"],
                score_breakdown=topic["score_breakdown"],
                selection_reason="Ranked by freshness, curiosity, authority, category weight, trend, channel history and exploration",
                video_id=job_id,
                job_id=job_id,
                status="QUEUED",
                category=topic.get("category"),
                category_family=topic.get("category_family"),
                category_selection_reason=topic.get("category_selection_reason"),
                topic_selection_reason=topic.get("topic_selection_reason"),
            )
            assigned.add(topic_id)
            existing_topics.append(topic)

        families = {slot.get("category_family") for slot in plan["publish_slots"] if slot.get("category_family")}
        assigned_count = sum(bool(slot.get("topic_id")) for slot in plan["publish_slots"])
        if assigned_count >= 2 and len(families) < min(3, assigned_count):
            warning = "Diversity fallback: not enough distinct qualified category families were available"
            if warning not in plan["warnings"]:
                plan["warnings"].append(warning)

    statuses = [slot["status"] for slot in plan["publish_slots"]]
    if statuses and all(s == "PUBLISHED" for s in statuses):
        plan["production_status"] = "PUBLISHED"
    elif any(s == "REVIEW_REQUIRED" for s in statuses):
        plan["production_status"] = "REVIEW_REQUIRED"
    elif any(s == "READY_FOR_APPROVAL" for s in statuses):
        plan["production_status"] = "READY_FOR_APPROVAL"
    elif any(s in TERMINAL_FAILURES for s in statuses):
        plan["production_status"] = "DEGRADED"
    elif statuses and all(s in ("READY", "READY_FOR_APPROVAL", "APPROVED", "SCHEDULED", "PUBLISHED") for s in statuses):
        plan["production_status"] = "READY"
    else:
        plan["production_status"] = "PRODUCING"
    _save(office_id, plan)
    return plan


def update_video(office_id, video):
    plan_id = video.get("daily_plan_id")
    if not plan_id:
        return
    row = next((r for r in db.rows("daily_production_plans", office_id) if r["id"] == plan_id), None)
    if not row:
        return
    plan = row["data"]
    for slot in plan["publish_slots"]:
        if slot.get("video_id") == video.get("id"):
            slot["status"] = video.get("status", slot["status"])
            for key in ("youtube_video_id", "youtube_upload_at", "published_at"):
                if video.get(key) is not None:
                    slot[key] = video[key]
            break
    reconcile(office_id, plan, create_jobs=False)


def _future_configured_slots(office_id, now):
    office = db.office(office_id)
    settings = office["settings"]
    zone = ZoneInfo(settings["timezone"])
    local_now = datetime.fromtimestamp(now, zone)
    for day_offset in range(22):
        day = (local_now + timedelta(days=day_offset)).date()
        for value in settings["upload_times"]:
            hour, minute = map(int, value.split(":"))
            local_slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            if local_slot.timestamp() > now + 15 * 60:
                yield _utc_iso(local_slot)


def schedule_preview(office_id, video_id, now=None):
    now = now or time.time()
    video = _video(office_id, video_id)
    if not video:
        raise ValueError("Video not found")
    original = video.get("scheduled_publish_at")
    with db.connection() as connection:
        claims = {
            row["publish_at"]: row["video_id"]
            for row in connection.execute(
                "SELECT publish_at,video_id FROM publish_slot_claims WHERE office_id=?",
                (office_id,),
            ).fetchall()
        }
    if original and _timestamp(original) > now + 15 * 60 and claims.get(original) in (None, video_id):
        final = original
        reason = None
    else:
        final = next(
            candidate for candidate in _future_configured_slots(office_id, now)
            if claims.get(candidate) in (None, video_id)
        )
        reason = (
            "Assigned publish slot passed or is too close; moved to the next available configured slot"
            if original else "No slot was assigned; selected the next available configured slot"
        )
    office = db.office(office_id)
    return {
        "original_slot": original,
        "final_publish_at": final,
        "reschedule_reason": reason,
        "timezone": office["settings"]["timezone"],
        "privacy": "private until YouTube publishAt",
    }


def claim_schedule(office_id, video_id, now=None):
    """Atomically reserve exactly one final YouTube publishing slot per video."""
    now = now or time.time()
    with db.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT publish_at,status FROM publish_slot_claims WHERE office_id=? AND video_id=?",
            (office_id, video_id),
        ).fetchone()
        video = _video(office_id, video_id)
        original = video.get("scheduled_publish_at") if video else None
        current_claims = {
            row["publish_at"]: row["video_id"]
            for row in connection.execute(
                "SELECT publish_at,video_id FROM publish_slot_claims WHERE office_id=?",
                (office_id,),
            ).fetchall()
        }
        if original and _timestamp(original) > now + 15 * 60 and current_claims.get(original) in (None, video_id):
            final, reason = original, None
        else:
            final = next(
                candidate for candidate in _future_configured_slots(office_id, now)
                if current_claims.get(candidate) in (None, video_id)
            )
            reason = (
                "Assigned publish slot passed or is too close; moved to the next available configured slot"
                if original else "No slot was assigned; selected the next available configured slot"
            )
        connection.execute(
            "DELETE FROM publish_slot_claims WHERE office_id=? AND video_id=?",
            (office_id, video_id),
        )
        connection.execute(
            "INSERT INTO publish_slot_claims VALUES(?,?,?,?,?)",
            (office_id, final, video_id, "APPROVED", time.time()),
        )
    return {
        "original_slot": original,
        "final_publish_at": final,
        "reschedule_reason": reason,
        "timezone": db.office(office_id)["settings"]["timezone"],
        "privacy": "private until YouTube publishAt",
    }
