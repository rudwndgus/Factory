import json
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from . import database as db
from .config import STAGES, ROLES, MEDIA
from .providers import (
    OpenAI,
    OfficialFeeds,
    Pexels,
    similarity,
    ConfigurationRequired,
    BudgetBlocked,
)
from . import media, assets

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


def enqueue(office_id, test_mode=False, topic_id=None, ai_visuals=False):
    office = db.office(office_id)
    if not office:
        raise ValueError("Office not found")
    if office["mode"] == "EMERGENCY_STOP" or (
        (not test_mode or ai_visuals) and office["mode"] != "RUNNING"
    ):
        raise ValueError("Start the Office before queuing production")
    id = db.uid()
    payload = dict(test_mode=test_mode, topic_id=topic_id, video_id=id, ai_visuals=ai_visuals)
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
            "status": "QUEUED",
            "test_mode": test_mode,
            "ai_visuals": ai_visuals,
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
    found = OfficialFeeds().discover()
    prior = db.rows("topics", office_id)
    count = 0
    for topic in found:
        if any(similarity(topic["title"], p["data"]["title"]) > 0.85 for p in prior):
            continue
        db.put("topics", office_id, topic)
        count += 1
    db.event(office_id, f"Scout collected {count} new topics from RSS sources")
    return count


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
        if test:
            topic = {
                "title": TEST_SCRIPT["title"],
                "summary": "Explicit offline fixture to test rendering; never published.",
                "source_url": "https://science.nasa.gov/",
            }
        else:
            topics = db.rows("topics", office_id)
            if not topics:
                discover(office_id)
                topics = db.rows("topics", office_id)
            candidates = [
                t
                for t in topics
                if t["data"].get("status") not in ("rejected", "used")
                and (not payload.get("topic_id") or t["id"] == payload["topic_id"])
            ]
            if not candidates:
                raise ConfigurationRequired("No unused topic candidates available")
            candidates.sort(key=lambda t: t["data"].get("pinned", False), reverse=True)
            chosen = candidates[0]
            topic = chosen["data"]
            video["topic_id"] = chosen["id"]
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
        video["sources"] = [video["topic"]]
        video["facts_verified"] = test
        # RSS excerpt is evidence, not independent fact verification. Publication remains blocked until owner checks.
        video["research_note"] = (
            "Offline test fixture"
            if test
            else "Source excerpt collected; independent fact review required"
        )
    elif stage == 2:
        script = (
            dict(TEST_SCRIPT)
            if test
            else OpenAI(office_id, id).script(video["topic"], settings)
        )
        video.update(
            script=script,
            title=script["title"],
            description=script.get("description", ""),
        )
        db.put("video_scripts", office_id, script, id)
        for claim in script.get("claims", []):
            db.put("research_claims", office_id, claim, id)
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
                    OpenAI(office_id, id).speak(scene["narration"], path)
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
        video["status"] = "TEST_COMPLETE" if test else "WAITING_FOR_REVIEW"
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
            blocked = isinstance(exc, (ConfigurationRequired, BudgetBlocked))
            cancelled = isinstance(exc, InterruptedError)
            attempts = job["attempts"] + 1
            status = "WAITING" if isinstance(exc, StagePaused) else (
                "CANCELLED"
                if cancelled
                else "BLOCKED" if blocked else "RETRYING" if attempts < 3 else "FAILED"
            )
            if isinstance(exc, StagePaused):
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
                        time.time() + 2**attempts * 5,
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
        settings = office["settings"]
        now = datetime.now(ZoneInfo(settings["timezone"]))
        date = now.strftime("%Y-%m-%d")
        with db.connection() as c:
            today = [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM jobs WHERE office_id=? AND created>=?",
                    (
                        office["id"],
                        now.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ).timestamp(),
                    ),
                )
                if not json.loads(r[0]).get("test_mode")
            ]
        if len(today) >= settings["videos_per_day"]:
            continue
        if now.strftime("%H:%M") not in settings["upload_times"]:
            continue
        key = date + " " + now.strftime("%H:%M")
        if any(
            r["data"].get("slot") == key for r in db.rows("system_events", office["id"])
        ):
            continue
        enqueue(office["id"])
        db.put(
            "system_events",
            office["id"],
            dict(message="Scheduled production", slot=key, severity="info"),
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
            if v.get("test_mode") or not v.get("qc") or not all(v["qc"].values()):
                continue
            if v["status"] not in ("APPROVED", "WAITING_FOR_REVIEW"):
                continue
            if office["settings"]["review_mode"] and v["status"] != "APPROVED":
                continue
            try:
                result = OfficialYouTube(office["id"]).upload(
                    v, MEDIA / v["file"], office["settings"]["privacy"]
                )
                v["status"] = "PUBLISHED"
                v["youtube_id"] = result["youtube_id"]
                db.put("videos", office["id"], v, v["id"], v["id"])
                from .reports import mark_uploaded
                mark_uploaded(office["id"], v["id"], result.get("uploaded_at"))
                db.event(office["id"], "Approved video uploaded", video_id=v["id"])
            except Exception:
                v["status"] = "UPLOAD_BLOCKED"
                db.put("videos", office["id"], v, v["id"], v["id"])
                db.event(
                    office["id"],
                    "Upload blocked; reconnect or reconcile upload before retry",
                    "error",
                    v["id"],
                )


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
