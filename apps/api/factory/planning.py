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
    delay = max(5, int(settings.get("missed_slot_delay_minutes", 30)))
    catchup = 0
    slots = []
    for index, value in enumerate(settings["upload_times"][: settings["videos_per_day"]]):
        hour, minute = map(int, value.split(":"))
        intended = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
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
    return local_now.strftime("%Y-%m-%d"), slots


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
        slot["job_id"] = None
        slot["video_id"] = None
        slot["youtube_video_id"] = None
        slot["status"] = "PLANNED"
        slot["replacement_count"] = slot.get("replacement_count", 0) + 1
        plan["replacement_jobs"] = plan.get("replacement_jobs", 0) + 1
        candidates_for_replacement.append(slot)
    need.extend(candidates_for_replacement)

    if create_jobs and need:
        ranked = topics.ranked_candidates(office_id, assigned)
        if len(ranked) < len(need):
            try:
                topics.discover(office_id)
            except Exception as exc:
                warning = f"Discovery unavailable during reconciliation: {type(exc).__name__}"
                if warning not in plan["warnings"]:
                    plan["warnings"].append(warning)
            ranked = topics.ranked_candidates(office_id, assigned)
        for slot, topic in zip(need, ranked):
            topic_id = topic["id"]
            job_id = engine.enqueue(
                office_id,
                topic_id=topic_id,
                scheduled_publish_at=slot["scheduled_publish_at"],
                plan_id=_id(office_id, plan["date"]),
                slot_index=slot["index"],
                replacement=slot.get("replacement_count", 0) > 0,
            )
            slot.update(
                topic_id=topic_id,
                topic_title=topic["title"],
                topic_score=topic["topic_score"],
                score_breakdown=topic["score_breakdown"],
                selection_reason="Ranked by freshness, curiosity, authority, category weight, trend, channel history and exploration",
                video_id=job_id,
                job_id=job_id,
                status="PRODUCING",
            )
            assigned.add(topic_id)

    statuses = [slot["status"] for slot in plan["publish_slots"]]
    if statuses and all(s == "PUBLISHED" for s in statuses):
        plan["production_status"] = "PUBLISHED"
    elif any(s == "REVIEW_REQUIRED" for s in statuses):
        plan["production_status"] = "REVIEW_REQUIRED"
    elif any(s in TERMINAL_FAILURES for s in statuses):
        plan["production_status"] = "DEGRADED"
    elif statuses and all(s in ("READY", "SCHEDULED", "PUBLISHED") for s in statuses):
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
