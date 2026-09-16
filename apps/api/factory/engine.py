import json
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from . import database as db
from .config import STAGES, ROLES, MEDIA
from .providers import (
    llm_provider,
    tts_provider,
    OfficialFeeds,
    Pexels,
    similarity,
    ConfigurationRequired,
    BudgetBlocked,
    FreeQuotaWait,
)
from . import media, assets, topics, verification, planning

LOCK = threading.Lock()
ROLE_INDEX = [1, 2, 3, 4, 5, 6, 6, 7, 8]
TEST_SCRIPT = {
    "title": "Why space is silent — TEST RUN",
    "description": "Educational pipeline test. Visual provenance is recorded per scene. Synthesized narration. Not publishable.",
    "category": "Science / Space",
    "format": "Explanation",
    "hook_style": "Question",
    "sentences": [
        "Why can you not hear an explosion in empty space? Sound is a vibration that travels through matter.",
        "On Earth, air molecules pass that vibration from one molecule to the next, until it reaches your ears.",
        "In the near vacuum between planets, there are far too few particles to carry ordinary sound to you.",
        "Light is different. It is an electromagnetic wave, so it can cross a vacuum and reach our telescopes.",
        "Inside a spacecraft, astronauts can talk because there is air. Outside, their radios carry signals as electromagnetic waves.",
    ],
    "claims": [
        {
            "claim": "Ordinary sound needs a medium",
            "source_url": "https://science.nasa.gov/",
            "confidence": 1,
            "type": "fact",
        }
    ],
}


def visual_scene_groups(sentences, max_images=5):
    """Keep narration intact while grouping it into a bounded number of visuals."""
    if not sentences:
        return []
    count = min(max(1, int(max_images)), 5, len(sentences))
    groups = []
    for index in range(count):
        start = index * len(sentences) // count
        end = (index + 1) * len(sentences) // count
        groups.append((start, end, " ".join(sentences[start:end])))
    return groups


def enqueue(
    office_id,
    test_mode=False,
    topic_id=None,
    ai_visuals=False,
    scheduled_publish_at=None,
    plan_id=None,
    slot_index=None,
    replacement=False,
):
    office = db.office(office_id)
    if not office:
        raise ValueError("Office not found")
    if office["mode"] == "EMERGENCY_STOP" or (
        (not test_mode or ai_visuals) and office["mode"] != "RUNNING"
    ):
        raise ValueError("Start the Office before queuing production")
    id = db.uid()
    payload = dict(
        test_mode=test_mode,
        topic_id=topic_id,
        video_id=id,
        ai_visuals=ai_visuals,
        scheduled_publish_at=scheduled_publish_at,
        daily_plan_id=plan_id,
        slot_index=slot_index,
        replacement=replacement,
    )
    with db.connection() as c:
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                id,
                office_id,
                "production",
                "QUEUED",
                0,
                0,
                0,
                json.dumps(payload),
                time.time(),
                time.time(),
                None,
            ),
        )
    db.put(
        "videos",
        office_id,
        {
            "id": id,
            "title": "TEST RUN" if test_mode else "Queued production",
            "status": "PLANNED" if plan_id else "QUEUED",
            "test_mode": test_mode,
            "ai_visuals": ai_visuals,
            "scheduled_publish_at": scheduled_publish_at,
            "daily_plan_id": plan_id,
            "plan_slot_index": slot_index,
            "replacement": replacement,
        },
        id,
        id,
    )
    db.event(
        office_id,
        "TEST RUN queued (no publishing)" if test_mode else "Production queued",
        video_id=id,
    )
    from .reports import employee_report
    employee_report(office_id, id, "EDITOR", "제작 접수", "테스트: Why space is silent" if test_mode else "대기 중인 소재",
                    (f"{office['settings']['visual_source_mode']} · {office['settings']['visual_style_preset']} 스타일로 세로 쇼츠를 제작합니다. 테스트 영상은 업로드하지 않습니다." if ai_visuals else "오프라인 기술 검사: 비용 없이 도형과 로컬 음성을 사용하며 게시하지 않습니다.") if test_mode else "소재 선정 → 출처 조사 → 대본 → 장면 → 이미지 → 음성/자막 → 편집 → 검수 순서로 진행합니다.")
    return id


def set_mode(office_id, mode):
    if mode not in ("RUNNING", "PAUSED", "STOPPED", "MAINTENANCE", "EMERGENCY_STOP"):
        raise ValueError("Invalid mode")
    with db.connection() as c:
        c.execute("UPDATE offices SET mode=? WHERE id=?", (mode, office_id))
        if mode in ("STOPPED", "EMERGENCY_STOP"):
            c.execute(
                "UPDATE jobs SET status='CANCELLED',updated=? WHERE office_id=? AND status IN ('QUEUED','RETRYING','WAITING','RUNNING')",
                (time.time(), office_id),
            )
        if mode != "RUNNING":
            c.execute("UPDATE employee_states SET status=?,updated=? WHERE office_id=?", (mode, time.time(), office_id))
    db.event(
        office_id,
        "Factory mode → " + mode,
        "warning" if mode == "EMERGENCY_STOP" else "info",
    )


def retry(office_id, id):
    with db.connection() as c:
        row = c.execute(
            "SELECT status FROM jobs WHERE id=? AND office_id=?", (id, office_id)
        ).fetchone()
        if not row or row[0] not in ("FAILED", "BLOCKED", "CANCELLED", "WAITING"):
            raise ValueError("Job cannot be retried in its current state")
        if db.office(office_id)["mode"] in ("EMERGENCY_STOP", "MAINTENANCE"):
            raise ValueError("Factory mode blocks retry")
        c.execute(
            "UPDATE jobs SET status='QUEUED',attempts=0,next_run=0,error=NULL WHERE id=?",
            (id,),
        )
    db.event(office_id, "Retry queued at saved stage", video_id=id)


def discover(office_id):
    return topics.discover(office_id)


def run_stage(job):
    office_id = job["office_id"]
    payload = json.loads(job["payload"])
    id = job["id"]
    test = payload["test_mode"]
    stage = job["stage"]
    office = db.office(office_id)
    settings = office["settings"]
    directory = MEDIA / office_id / id
    directory.mkdir(parents=True, exist_ok=True)
    video = next(r["data"] for r in db.rows("videos", office_id) if r["id"] == id)

    def save():
        db.put("videos", office_id, video, id, id)

    def cancelled():
        with db.connection() as c:
            status = c.execute("SELECT status FROM jobs WHERE id=?", (id,)).fetchone()[
                0
            ]
        return db.office(office_id)["mode"] == "EMERGENCY_STOP" or status == "CANCELLED"

    def checkpoint():
        if cancelled():
            raise InterruptedError("Production stopped")
        if db.office(office_id)["mode"] in ("PAUSED", "MAINTENANCE"):
            raise StagePaused("Paused at scene checkpoint")

    if stage == 0:
        video["status"] = "PRODUCING"
        if test:
            topic = {
                "title": TEST_SCRIPT["title"],
                "summary": "Explicit offline fixture to test rendering; never published.",
                "source_url": "https://science.nasa.gov/",
            }
        else:
            topic_rows = db.rows("topics", office_id)
            if not topic_rows:
                discover(office_id)
                topic_rows = db.rows("topics", office_id)
            candidates = [
                t
                for t in topic_rows
                if t["data"].get("status") not in ("rejected", "used")
                and (not payload.get("topic_id") or t["id"] == payload["topic_id"])
            ]
            if not candidates:
                raise ConfigurationRequired("No unused topic candidates available")
            candidates.sort(key=lambda t: t["data"].get("pinned", False), reverse=True)
            chosen = candidates[0]
            topic = chosen["data"]
            video["topic_id"] = chosen["id"]
            if payload.get("daily_plan_id"):
                plan_row = next(
                    (
                        row
                        for row in db.rows("daily_production_plans", office_id)
                        if row["id"] == payload["daily_plan_id"]
                    ),
                    None,
                )
                if plan_row:
                    slot = next(
                        (
                            item
                            for item in plan_row["data"]["publish_slots"]
                            if item["index"] == payload.get("slot_index")
                        ),
                        {},
                    )
                    video["topic_score"] = slot.get("topic_score")
                    video["topic_score_breakdown"] = slot.get("score_breakdown", {})
                    video["topic_selection_reason"] = slot.get("selection_reason", "")
            existing = [
                v
                for v in db.rows("videos", office_id)
                if v["id"] != id and not v["data"].get("test_mode")
            ]
            if any(
                similarity(topic["title"], v["data"]["title"])
                >= settings["duplicate_threshold"]
                for v in existing
            ):
                raise ConfigurationRequired("Duplicate topic blocked")
            db.put("topics", office_id, topic | {"status": "used"}, id=chosen["id"])
        video.update(topic=topic, title=topic["title"])
        db.put("topic_sources", office_id, topic, id)
    elif stage == 1:
        if test:
            sources = [video["topic"]]
        else:
            sources = [video["topic"]]
            for row in db.rows("topics", office_id):
                candidate = row["data"]
                if candidate.get("source_url") == video["topic"].get("source_url"):
                    continue
                if similarity(candidate.get("title", ""), video["topic"]["title"]) >= 0.55:
                    sources.append(candidate)
            unique, seen = [], set()
            for source in sources:
                key = source.get("source_url")
                if key and key not in seen:
                    seen.add(key)
                    unique.append(source)
            sources = [topics.retrieve_evidence(source) for source in unique[:6]]
        video["sources"] = sources
        video["facts_verified"] = test
        if not test:
            video["research_synthesis"] = llm_provider(
                office_id, id, settings
            ).research(video["topic"], sources)
        video["research_note"] = (
            "Explicit test fixture"
            if test
            else f"Collected {len(sources)} source records; claim verification follows after script generation"
        )
    elif stage == 2:
        script = (
            dict(TEST_SCRIPT)
            if test
            else llm_provider(office_id, id, settings).script(
                video["topic"] | {"sources": video.get("sources", [])}, settings
            )
        )
        video.update(
            script=script,
            title=script["title"],
            description=script.get("description", ""),
            category=script.get(
                "category", video.get("topic", {}).get("category", "Evergreen")
            ),
            format=script.get("format", "Explanation"),
            hook_style=script.get("hook_style", "Unknown"),
        )
        db.put("video_scripts", office_id, script, id)
        fact_check = (
            {
                "claims": [
                    dict(
                        claim,
                        classification="VERIFIED",
                        confidence=1.0,
                        supporting_sources=video["sources"],
                    )
                    for claim in script.get("claims", [])
                ],
                "video_fact_confidence": 1.0,
                "facts_verified": True,
                "verification_status": "VERIFIED",
                "requires_review": False,
                "method": "explicit test fixture",
            }
            if test
            else verification.verify_claims(
                script.get("claims", []), video.get("sources", [])
            )
        )
        video["fact_check"] = fact_check
        video["facts_verified"] = fact_check["facts_verified"]
        for index, claim in enumerate(fact_check["claims"]):
            db.put(
                "research_claims",
                office_id,
                claim,
                id,
                id=f"{id}-claim-{index}",
            )
    elif stage == 3:
        sentence_groups = visual_scene_groups(
            video["script"]["sentences"], settings.get("max_images_per_short", 5)
        )
        scenes = [
            dict(
                scene_number=i + 1,
                narration=narration,
                subtitle=narration,
                visual_type="SCENE_STILL",
                visual_query=video["title"],
                motion_type="zoom",
                transition="cut",
                duration_target=settings["duration"]
                / len(sentence_groups),
                status="planned",
                narration_sentence_range=[start + 1, end],
            )
            for i, (start, end, narration) in enumerate(sentence_groups)
        ]
        visuals = video["script"].get("visuals", [])
        for i, (scene, (start, end, _)) in enumerate(zip(scenes, sentence_groups)):
            visual_index = i if len(visuals) == len(sentence_groups) else start
            proposed = visuals[visual_index] if visual_index < len(visuals) else None
            if (
                isinstance(proposed, dict)
                and len(visuals) != len(sentence_groups)
                and end - start > 1
            ):
                proposed = proposed | {
                    "prompt": ". ".join(
                        str(v.get("prompt", ""))
                        for v in visuals[start:end]
                        if isinstance(v, dict) and v.get("prompt")
                    )
                    or proposed.get("prompt", "")
                }
            scene.update(assets.visual_plan(scene["narration"], video["title"], settings, i,
                         proposed, space_test=test))
        video["scenes"] = scenes
        for i, s in enumerate(scenes):
            db.put("video_scenes", office_id, s, id, id=f"{id}-{i}")
    elif stage == 4:
        for i, scene in enumerate(video["scenes"]):
            checkpoint()
            path = directory / f"image-{i}.png"
            if path.exists():
                continue
            if "image_prompt" not in scene:
                scene.update(assets.visual_plan(scene["narration"], video["title"], settings, i, space_test=test))
            asset = assets.acquire_scene(scene, path, i, settings, office_id, id,
                                         offline=test and not payload.get("ai_visuals"))
            asset["path"] = str(path.relative_to(MEDIA))
            asset["scene_number"] = i + 1
            scene.update(asset_source=asset["asset_source"], asset_provider=asset["provider"], image_path=asset["path"])
            db.put("video_assets", office_id, asset, id, id=f"{id}-image-{i}")
            db.put("rights_records", office_id, asset, id, id=f"{id}-image-{i}")
            db.put("video_scenes", office_id, scene, id, id=f"{id}-{i}")
            save()
    elif stage == 5:
        for i, scene in enumerate(video["scenes"]):
            checkpoint()
            path = directory / f"voice-{i}.wav"
            if not path.exists():
                if test:
                    media.local_voice(scene["narration"], path)
                else:
                    tts_provider(office_id, id, settings).speak(scene["narration"], path)
            scene["duration"] = media.duration(path)
            db.put("video_scenes", office_id, scene, id, id=f"{id}-{i}")
        video["actual_duration"] = sum(s["duration"] for s in video["scenes"])
    elif stage == 6:
        media.subtitles(directory, video["scenes"])
    elif stage == 7:
        media.render(directory, video["scenes"], cancelled)
        video["file"] = str((directory / "final.mp4").relative_to(MEDIA))
        video["preview"] = str((directory / "preview.jpg").relative_to(MEDIA))
    elif stage == 8:
        video["status"] = "QC"
        checks = media.technical_qc(directory / "final.mp4")
        checks.update(
            subtitles=(directory / "captions.ass").exists(),
            duration=25 <= video["actual_duration"] <= 60,
            rights=all(
                r["data"]["rights"] == "CLEARED"
                for r in db.rows("rights_records", office_id, id)
            ),
            facts=video.get("facts_verified", False),
            sources=bool(video.get("sources")),
            not_test=not test,
        )
        video["qc"] = checks
        if test:
            video["status"] = "TEST_COMPLETE"
        else:
            hard_safety = all(
                value for name, value in checks.items() if name != "facts"
            )
            exception = (
                not video.get("facts_verified")
                or not hard_safety
                or bool(video.get("fact_check", {}).get("requires_review"))
            )
            policy = settings.get("review_policy", "Review Exceptions Only")
            video["review_policy"] = policy
            video["review_reasons"] = []
            if not video.get("facts_verified"):
                video["review_reasons"].append(
                    "Factual evidence is uncertain or unsupported"
                )
            if not hard_safety:
                video["review_reasons"].append("Hard QC or rights gate failed")
            video["status"] = (
                "REVIEW_REQUIRED"
                if policy == "Review Everything" or exception
                else "READY"
            )
        db.put(
            "qc_checks",
            office_id,
            {"checks": checks, "result": "PASS" if all(checks.values()) else "BLOCK"},
            id,
        )
        db.event(
            office_id,
            "Test MP4 ready" if test else "Video ready for CEO review",
            video_id=id,
        )
    save()
    if not test:
        planning.update_video(office_id, video)
    from .reports import stage_report
    stage_report(office_id, id, stage, video)


class StagePaused(Exception):
    pass


def tick():
    if not LOCK.acquire(blocking=False):
        return
    try:
        with db.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            jobs = c.execute(
                "SELECT j.*,o.mode FROM jobs j JOIN offices o ON j.office_id=o.id WHERE j.status IN ('QUEUED','RETRYING','WAITING') AND j.next_run<=? ORDER BY j.created",
                (time.time(),),
            ).fetchall()
            job = next(
                (
                    dict(j)
                    for j in jobs
                    if j["mode"] == "RUNNING"
                    or (
                        json.loads(j["payload"]).get("test_mode")
                        and j["mode"] not in ("EMERGENCY_STOP", "PAUSED", "MAINTENANCE")
                    )
                ),
                None,
            )
            if not job:
                return
            c.execute(
                "UPDATE jobs SET status='RUNNING',updated=? WHERE id=?",
                (time.time(), job["id"]),
            )
        role = ROLES[ROLE_INDEX[min(job["stage"], 8)]]
        with db.connection() as c:
            c.execute(
                "UPDATE employee_states SET status=?,job_id=?,stage=?,updated=? WHERE office_id=? AND role=?",
                (
                    "WORKING",
                    job["id"],
                    STAGES[job["stage"]],
                    time.time(),
                    job["office_id"],
                    role,
                ),
            )
        try:
            run_stage(job)
            with db.connection() as c:
                c.execute(
                    "UPDATE jobs SET stage=stage+1,status=?,attempts=0,updated=? WHERE id=? AND status='RUNNING'",
                    (
                        "COMPLETE" if job["stage"] == 8 else "QUEUED",
                        time.time(),
                        job["id"],
                    ),
                )
            db.put(
                "job_attempts",
                job["office_id"],
                {"stage": STAGES[job["stage"]], "result": "COMPLETE"},
                job["id"],
            )
            db.event(
                job["office_id"],
                role + " completed " + STAGES[job["stage"]],
                video_id=job["id"],
            )
        except Exception as exc:
            quota_wait = isinstance(exc, FreeQuotaWait)
            blocked = isinstance(exc, (ConfigurationRequired, BudgetBlocked)) and not quota_wait
            cancelled = isinstance(exc, InterruptedError)
            attempts = job["attempts"] + 1
            status = "WAITING" if isinstance(exc, (StagePaused, FreeQuotaWait)) else (
                "CANCELLED"
                if cancelled
                else "BLOCKED" if blocked else "RETRYING" if attempts < 3 else "FAILED"
            )
            if isinstance(exc, StagePaused):
                attempts = 0
            if quota_wait:
                attempts = 0
            # Do not persist external exception text; it may contain credentials, URLs, or response bodies.
            message = (
                str(exc)
                if isinstance(
                    exc, (ConfigurationRequired, BudgetBlocked, InterruptedError, StagePaused)
                )
                else type(exc).__name__
                + ": stage failed; inspect configuration or retry"
            )
            with db.connection() as c:
                c.execute(
                    "UPDATE jobs SET status=?,attempts=?,next_run=?,error=?,updated=? WHERE id=?",
                    (
                        status,
                        attempts,
                        time.time() + (3600 if quota_wait else 2**attempts * 5),
                        message,
                        time.time(),
                        job["id"],
                    ),
                )
            db.put(
                "errors",
                job["office_id"],
                {"stage": STAGES[job["stage"]], "message": message, "status": status},
                job["id"],
            )
            db.put(
                "job_attempts",
                job["office_id"],
                {"stage": STAGES[job["stage"]], "result": status, "attempt": attempts},
                job["id"],
            )
            db.event(job["office_id"], message, "error", job["id"])
            if status in ("FAILED", "BLOCKED", "CANCELLED"):
                video_row = next(
                    (
                        item
                        for item in db.rows("videos", job["office_id"])
                        if item["id"] == job["id"]
                    ),
                    None,
                )
                if video_row:
                    failed_video = video_row["data"]
                    failed_video["status"] = status
                    db.put(
                        "videos",
                        job["office_id"],
                        failed_video,
                        job["id"],
                        job["id"],
                    )
                    planning.update_video(job["office_id"], failed_video)
            elif quota_wait:
                video_row = next((item for item in db.rows("videos", job["office_id"])
                                  if item["id"] == job["id"]), None)
                if video_row:
                    waiting_video = video_row["data"]
                    waiting_video["status"] = "WAITING_FOR_FREE_QUOTA"
                    waiting_video["retry_after"] = time.time() + 3600
                    db.put("videos", job["office_id"], waiting_video, job["id"], job["id"])
            from .reports import add_exception
            add_exception(job["office_id"], job["id"], role, message)
        finally:
            with db.connection() as c:
                c.execute(
                    "UPDATE employee_states SET status='IDLE',job_id=NULL,stage=NULL,updated=? WHERE office_id=? AND role=?",
                    (time.time(), job["office_id"], role),
                )
    finally:
        LOCK.release()


def schedule():
    for office in db.offices():
        if office["mode"] != "RUNNING":
            continue
        try:
            planning.ensure_plan(office["id"])
        except Exception as exc:
            db.event(
                office["id"],
                f"Daily planning deferred: {type(exc).__name__}",
                "warning",
            )


def recover():
    with db.connection() as c:
        c.execute("UPDATE jobs SET status='QUEUED' WHERE status='RUNNING'")
        c.execute("UPDATE employee_states SET status='IDLE',job_id=NULL,stage=NULL")


def publish_approved():
    from .youtube import OfficialYouTube

    for office in db.offices():
        if office["mode"] != "RUNNING" or not office["settings"].get("auto_upload"):
            continue
        for entry in db.rows("videos", office["id"]):
            v = entry["data"]
            if v.get("test_mode") or not v.get("qc"):
                continue
            hard_qc = all(
                value for name, value in v["qc"].items() if name != "facts"
            )
            if not hard_qc or not (v.get("facts_verified") or v["qc"].get("facts")):
                continue
            if v["status"] != "READY":
                continue
            try:
                publish_at = (
                    v.get("scheduled_publish_at")
                    if office["settings"]["privacy"] == "public"
                    else None
                )
                result = OfficialYouTube(office["id"]).upload(
                    v,
                    MEDIA / v["file"],
                    office["settings"]["privacy"],
                    publish_at,
                )
                v["status"] = (
                    "SCHEDULED" if publish_at else "PUBLISHED"
                )
                v["youtube_id"] = result["youtube_id"]
                v["youtube_video_id"] = result["youtube_id"]
                v["youtube_upload_at"] = result.get("uploaded_at")
                v["published_at"] = result.get("published_at")
                db.put("videos", office["id"], v, v["id"], v["id"])
                from .reports import mark_uploaded
                mark_uploaded(office["id"], v["id"], result.get("uploaded_at"))
                planning.update_video(office["id"], v)
                db.event(
                    office["id"],
                    "Ready video uploaded to its YouTube schedule",
                    video_id=v["id"],
                )
            except Exception:
                v["status"] = "UPLOAD_BLOCKED"
                db.put("videos", office["id"], v, v["id"], v["id"])
                db.event(
                    office["id"],
                    "Upload blocked; reconnect or reconcile upload before retry",
                    "error",
                    v["id"],
                )
                planning.update_video(office["id"], v)


def collect_analytics():
    from .youtube import OfficialYouTube

    for office in db.offices():
        if not db.rows("uploads", office["id"]):
            continue
        try:
            OfficialYouTube(office["id"]).analytics()
        except Exception:
            db.event(
                office["id"],
                "Scheduled analytics unavailable; reconnect channel or refresh manually",
                "warning",
            )
