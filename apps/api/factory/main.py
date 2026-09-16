import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from pydantic import BaseModel, Field, model_validator
from . import database as db, engine, reports, youtube, planning
from .config import MEDIA, DEFAULTS
from .security import require_owner, login, save_secret, secret, digest
from .providers import (
    ConfigurationRequired, llm_provider, tts_provider, zero_cost_mode,
    PaidProviderBlocked,
)
from fastapi import UploadFile, File

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app):
    db.migrate()
    engine.recover()
    scheduler.add_job(
        engine.tick, "interval", seconds=1, id="worker", max_instances=1, coalesce=True
    )
    scheduler.add_job(
        engine.schedule, "interval", seconds=20, id="production", max_instances=1
    )
    scheduler.add_job(
        reports.scheduled_reports, "interval", minutes=30, id="reports", max_instances=1
    )
    scheduler.add_job(
        engine.publish_approved, "interval", minutes=1, id="uploads", max_instances=1
    )
    scheduler.add_job(
        engine.collect_analytics, "interval", hours=6, id="analytics", max_instances=1
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=True)


app = FastAPI(title="Pixel Shorts Factory", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,https://rudwndgus.github.io"
    ).split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConfigurationRequired)
async def provider_configuration_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


class Login(BaseModel):
    password: str = Field(max_length=1024)


class OfficeInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    settings: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_settings(self):
        unknown = set(self.settings) - set(DEFAULTS)
        if unknown:
            raise ValueError("Unknown settings: " + ",".join(unknown))
        s = DEFAULTS | self.settings
        if not 25 <= s["duration"] <= 60 or not 1 <= s["videos_per_day"] <= 24:
            raise ValueError("Duration must be 25–60 seconds; daily videos 1–24")
        if s["daily_budget"] < 0 or s["monthly_budget"] < 0:
            raise ValueError("Budgets cannot be negative")
        if abs(sum(s["category_weights"].values()) - 100) > 0.01 or any(
            v < 0 for v in s["category_weights"].values()
        ):
            raise ValueError("Category weights must total 100")
        try:
            ZoneInfo(s["timezone"])
        except Exception:
            raise ValueError("Invalid timezone")
        for slot in s["upload_times"]:
            try:
                datetime.strptime(slot, "%H:%M")
            except ValueError:
                raise ValueError("Upload times must use HH:MM")
        if len(set(s["upload_times"])) < s["videos_per_day"]:
            raise ValueError("Provide a different publish time for every daily video")
        if not 0 < s["duplicate_threshold"] <= 1:
            raise ValueError("Invalid duplicate threshold")
        if s["privacy"] not in ("private", "unlisted", "public"):
            raise ValueError("Invalid privacy")
        if s["review_policy"] not in (
            "Review Everything",
            "Review Exceptions Only",
            "Fully Automatic",
        ):
            raise ValueError("Invalid review policy")
        if not 0 <= float(s["exploration_percentage"]) <= 100:
            raise ValueError("Exploration percentage must be between 0 and 100")
        if not 5 <= int(s["missed_slot_delay_minutes"]) <= 1440:
            raise ValueError("Missed slot delay must be between 5 and 1440 minutes")
        if not 0 <= int(s["max_replacements_per_slot"]) <= 5:
            raise ValueError("Max replacements per slot must be between 0 and 5")
        if not 0 <= int(s["replacement_cutoff_minutes"]) <= 1440:
            raise ValueError("Replacement cutoff must be between 0 and 1440 minutes")
        if not isinstance(s["topic_sources"], list):
            raise ValueError("Topic sources must be a list")
        if not isinstance(s["youtube_trend_region"], str) or len(s["youtube_trend_region"]) != 2:
            raise ValueError("YouTube trend region must be a two-letter country code")
        if s["visual_source_mode"] not in ("AI First", "Mixed", "Real First"):
            raise ValueError("Invalid visual source mode")
        if s["zero_cost_mode"] and (
            s["llm_provider"] != "ollama"
            or s["tts_provider"] != "kokoro"
            or s["image_provider"] != "cloudflare"
            or s["image_fallback_provider"] != "none"
        ):
            raise ValueError("Zero Cost Mode requires Ollama, Kokoro, Cloudflare, and no image fallback")
        if not isinstance(s["ollama_model"], str) or not s["ollama_model"].strip():
            raise ValueError("Ollama model is required")
        if not isinstance(s["kokoro_voice"], str) or not s["kokoro_voice"].strip():
            raise ValueError("Kokoro voice is required")
        if not isinstance(s["visual_style_preset"], str) or not 1 <= len(s["visual_style_preset"]) <= 500:
            raise ValueError("Visual style must be 1–500 characters")
        if s["image_provider"] not in ("cloudflare", "openai"):
            raise ValueError("Invalid primary image provider")
        if s["image_fallback_provider"] not in ("none", "cloudflare", "openai"):
            raise ValueError("Invalid fallback image provider")
        if not isinstance(s["cloudflare_image_model"], str) or not 1 <= len(s["cloudflare_image_model"]) <= 200:
            raise ValueError("Cloudflare image model is invalid")
        if not 1 <= int(s["cloudflare_image_steps"]) <= 8:
            raise ValueError("Cloudflare image steps must be 1–8")
        if s["image_seed_mode"] not in ("random", "fixed"):
            raise ValueError("Invalid image seed mode")
        if not 1 <= int(s["image_fixed_seed"]) <= 2_147_483_647:
            raise ValueError("Fixed image seed is invalid")
        if not 1 <= int(s["max_images_per_short"]) <= 5:
            raise ValueError("Max images per short must be 1–5")
        return self


class ModeInput(BaseModel):
    mode: Literal["RUNNING", "PAUSED", "STOPPED", "MAINTENANCE", "EMERGENCY_STOP"]
    confirmed: bool = False


class SecretInput(BaseModel):
    name: Literal[
        "OPENAI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PEXELS_API_KEY",
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"
    ]
    value: str = Field(min_length=1, max_length=4096)


class TopicInput(BaseModel):
    title: str = Field(min_length=3, max_length=250)
    summary: str = Field(default="", max_length=12000)
    source_url: str = Field(default="", max_length=2000)
    category: str = "Evergreen"


class JobInput(BaseModel):
    test_mode: bool = False
    ai_visuals: bool = False
    topic_id: str | None = None
    override_duplicate: bool = False


class ReviewInput(BaseModel):
    action: Literal["approve", "reject", "verify_facts", "metadata"]
    title: str | None = None
    description: str | None = None


class PublishInput(BaseModel):
    privacy: Literal["private", "unlisted", "public"] = "private"
    confirmed: bool = False
    publish_at: str | None = None


class ApproveScheduleInput(BaseModel):
    confirmed: bool = False


class RegenerateInput(BaseModel):
    stage: Literal[2, 3, 4, 5, 7]
    scene: int | None = None
    sentences: list[str] | None = Field(default=None, min_length=3, max_length=12)


class ImageProviderTestInput(BaseModel):
    office_id: str
    prompt: str = Field(
        default="A cinematic mysterious view of Saturn above a dark icy moon, vertical educational short, no text",
        min_length=3,
        max_length=2048,
    )


class ProviderTestInput(BaseModel):
    office_id: str


def exists(id):
    office = db.office(id)
    if not office:
        raise HTTPException(404, "Office not found")
    return office


def video(office_id, id):
    found = next((v for v in db.rows("videos", office_id) if v["id"] == id), None)
    if not found:
        raise HTTPException(404, "Video not found in this Office")
    return found["data"]


@app.get("/api/health")
def health():
    return {"service": "Pixel Shorts Factory", "status": "online", "version": "1.0.0"}


attempts = {}


@app.post("/api/auth/login")
def authenticate(body: Login, request: Request):
    address = request.client.host
    now = time.time()
    recent = [v for v in attempts.get(address, []) if now - v < 60]
    if len(recent) >= 10:
        raise HTTPException(429, "Too many sign-in attempts. Wait one minute.")
    attempts[address] = recent + [now]
    return {"token": login(body.password)}


@app.post("/api/auth/logout", dependencies=[Depends(require_owner)])
def logout(request: Request):
    with db.connection() as c:
        c.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (digest(request.headers["Authorization"].removeprefix("Bearer ")),),
        )
    return {"ok": True}


@app.get("/api/offices", dependencies=[Depends(require_owner)])
def list_offices():
    return db.offices()


@app.post("/api/offices", dependencies=[Depends(require_owner)])
def create(body: OfficeInput):
    return db.create_office(body.name, body.settings)


@app.put("/api/offices/{id}", dependencies=[Depends(require_owner)])
def update(id: str, body: OfficeInput):
    old = exists(id)
    with db.connection() as c:
        c.execute("UPDATE offices SET name=? WHERE id=?", (body.name, id))
        c.execute(
            "UPDATE office_settings SET payload=? WHERE office_id=?",
            (json.dumps(old["settings"] | body.settings), id),
        )
    db.event(id, "Office settings updated")
    return db.office(id)


@app.post("/api/offices/{id}/mode", dependencies=[Depends(require_owner)])
def mode(id: str, body: ModeInput):
    office = exists(id)
    if body.mode == "EMERGENCY_STOP" and not body.confirmed:
        raise HTTPException(400, "Explicit emergency confirmation required")
    if body.mode == "RUNNING" and zero_cost_mode():
        llm_provider(id, settings=office["settings"]).health()
        tts_provider(id, settings=office["settings"]).health()
    engine.set_mode(id, body.mode)
    if body.mode == "RUNNING":
        planning.ensure_plan(id)
    return {"mode": body.mode}


@app.post("/api/system/mode", dependencies=[Depends(require_owner)])
def global_mode(body: ModeInput):
    if body.mode == "EMERGENCY_STOP" and not body.confirmed:
        raise HTTPException(400, "Confirmation required")
    for office in db.offices():
        if body.mode == "RUNNING" and zero_cost_mode():
            llm_provider(office["id"], settings=office["settings"]).health()
            tts_provider(office["id"], settings=office["settings"]).health()
        engine.set_mode(office["id"], body.mode)
        if body.mode == "RUNNING":
            planning.ensure_plan(office["id"])
    return {"ok": True}


@app.get("/api/offices/{id}/snapshot", dependencies=[Depends(require_owner)])
def snapshot(id: str):
    office = exists(id)
    with db.connection() as c:
        jobs = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM jobs WHERE office_id=? ORDER BY created DESC LIMIT 100",
                (id,),
            )
        ]
        employees = [
            dict(r)
            for r in c.execute("SELECT * FROM employee_states WHERE office_id=?", (id,))
        ]
        channel = c.execute(
            "SELECT channel_id,channel_title FROM youtube_connections WHERE office_id=?",
            (id,),
        ).fetchone()
    return dict(
        office=office,
        jobs=jobs,
        production_queue=engine.queue_items(id),
        employees=employees,
        youtube=dict(channel) if channel else None,
        **{
            t: db.rows(t, id)[:100]
            for t in [
                "topics",
                "videos",
                "reports",
                "system_events",
                "cost_events",
                "usage_events",
                "analytics_snapshots",
                "errors",
                "daily_production_plans",
                "performance_profiles",
            ]
        }
    )


@app.get("/api/offices/{id}/events", dependencies=[Depends(require_owner)])
async def events(id: str, request: Request):
    exists(id)

    async def stream():
        previous = ""
        while not await request.is_disconnected():
            current = snapshot(id)
            encoded = json.dumps(current)
            if encoded != previous:
                yield "data: " + encoded + "\n\n"
                previous = encoded
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/offices/{id}/jobs", dependencies=[Depends(require_owner)])
def queue(id: str, body: JobInput):
    exists(id)
    try:
        job_id = engine.enqueue(
            id, body.test_mode, body.topic_id, body.ai_visuals,
            manual_queue=not body.test_mode,
            override_duplicate=body.override_duplicate,
        )
        return {"id": job_id, "result": "QUEUED"}
    except engine.AlreadyQueued as exc:
        return {"id": exc.job_id, "result": "ALREADY_QUEUED"}


@app.post(
    "/api/offices/{id}/jobs/{job_id}/{action}", dependencies=[Depends(require_owner)]
)
def job_action(id: str, job_id: str, action: Literal["retry", "cancel", "remove"]):
    exists(id)
    if action == "retry":
        engine.retry(id, job_id)
    elif action == "remove":
        engine.remove_from_queue(id, job_id)
    else:
        engine.cancel_production(id, job_id)
    return {"ok": True}


@app.post("/api/offices/{id}/topics", dependencies=[Depends(require_owner)])
def add_topic(id: str, body: TopicInput):
    exists(id)
    return {
        "id": db.put(
            "topics",
            id,
            body.model_dump() | {"status": "candidate", "provider": "owner"},
        )
    }


@app.post("/api/offices/{id}/discover", dependencies=[Depends(require_owner)])
def discover(id: str):
    exists(id)
    return {"count": engine.discover(id)}


@app.post(
    "/api/offices/{id}/topics/{topic_id}/{action}",
    dependencies=[Depends(require_owner)],
)
def topic_action(id: str, topic_id: str, action: Literal["pin", "reject"]):
    found = next((r for r in db.rows("topics", id) if r["id"] == topic_id), None)
    if not found:
        raise HTTPException(404, "Topic not found")
    db.put(
        "topics",
        id,
        found["data"]
        | ({"pinned": True} if action == "pin" else {"status": "rejected"}),
        id=topic_id,
    )
    return {"ok": True}


@app.post("/api/offices/{id}/inspect", dependencies=[Depends(require_owner)])
def inspect(id: str):
    exists(id)
    return {"id": reports.inspect(id)}


@app.post("/api/offices/{id}/reports/{report_id}/archive", dependencies=[Depends(require_owner)])
def archive_report(id: str, report_id: str):
    exists(id)
    reports.archive(id, report_id)
    return {"ok": True}


@app.post("/api/offices/{id}/analytics/refresh", dependencies=[Depends(require_owner)])
def analytics(id: str):
    exists(id)
    return youtube.OfficialYouTube(id).analytics()


@app.get("/api/offices/{id}/videos/{video_id}", dependencies=[Depends(require_owner)])
def detail(id: str, video_id: str):
    return video(id, video_id) | {
        table: db.rows(table, id, video_id)
        for table in ["qc_checks", "rights_records", "research_claims", "video_assets"]
    }


@app.post(
    "/api/offices/{id}/videos/{video_id}/regenerate",
    dependencies=[Depends(require_owner)],
)
def regenerate(id: str, video_id: str, body: RegenerateInput):
    from .editing import restart

    if body.sentences and any(
        not sentence.strip() or len(sentence) > 800 for sentence in body.sentences
    ):
        raise ValueError(
            "Narration must contain 3–12 nonempty sentences, max 800 characters each"
        )
    restart(id, video_id, body.stage, body.scene, body.sentences)
    return {"ok": True}


@app.get("/api/offices/{id}/videos/{video_id}/scenes/{scene}/image", dependencies=[Depends(require_owner)])
def scene_image(id: str, video_id: str, scene: int):
    v = video(id, video_id)
    if not 0 <= scene < len(v.get("scenes", [])):
        raise HTTPException(404, "Scene not found")
    path = (MEDIA / id / video_id / f"image-{scene}.png").resolve()
    if not path.is_relative_to(MEDIA.resolve()) or not path.is_file():
        raise HTTPException(404, "Scene image not available")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post(
    "/api/offices/{id}/videos/{video_id}/scenes/{scene}/image",
    dependencies=[Depends(require_owner)],
)
async def replace_image(
    id: str, video_id: str, scene: int, file: UploadFile = File(...)
):
    import io
    from PIL import Image
    from .editing import restart

    v = video(id, video_id)
    if not 0 <= scene < len(v.get("scenes", [])):
        raise ValueError("Invalid scene index")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("Image exceeds 20 MB")
    try:
        im = Image.open(io.BytesIO(content))
        im.load()
        im = im.convert("RGB")
        im.thumbnail((2160, 3840))
    except Exception:
        raise ValueError("Image could not be decoded")
    restart(id, video_id, 7, scene=scene, replacement=im)
    return {
        "ok": True,
        "rights": "UNSAFE",
        "message": "Rights must be reviewed before publication",
    }


@app.post(
    "/api/offices/{id}/videos/{video_id}/review", dependencies=[Depends(require_owner)]
)
def review(id: str, video_id: str, body: ReviewInput):
    v = video(id, video_id)
    if body.action == "verify_facts":
        v["facts_verified"] = True
        v.setdefault("fact_check", {})["manual_override"] = True
        if "qc" in v:
            v["qc"]["facts"] = True
    elif body.action == "approve":
        if v.get("test_mode") or not v.get("qc"):
            raise ValueError("Rendered production video required")
        hard_qc = all(
            value for name, value in v["qc"].items() if name != "facts"
        )
        if not hard_qc:
            raise ValueError("Hard QC or rights gates cannot be overridden")
        v["facts_verified"] = True
        v["qc"]["facts"] = True
        v.setdefault("fact_check", {})["manual_override"] = True
        v["manual_review"] = {"decision": "approved", "at": time.time()}
        v["status"] = "READY_FOR_APPROVAL"
    elif body.action == "reject":
        v["status"] = "REJECTED"
        v["manual_review"] = {"decision": "rejected", "at": time.time()}
    else:
        if body.title:
            v["title"] = body.title[:100]
        if body.description is not None:
            v["description"] = body.description[:5000]
    db.put("videos", id, v, video_id, video_id)
    planning.update_video(id, v)
    db.event(id, "CEO action: " + body.action, video_id=video_id)
    return v


@app.get(
    "/api/offices/{id}/videos/{video_id}/schedule-preview",
    dependencies=[Depends(require_owner)],
)
def schedule_preview(id: str, video_id: str):
    exists(id)
    v = video(id, video_id)
    retryable_failure = (
        v.get("status") == "FAILED"
        and bool(v.get("owner_approval"))
        and bool(v.get("upload_failure"))
        and not v.get("youtube_video_id")
    )
    if v.get("test_mode") or (
        v.get("status") != "READY_FOR_APPROVAL" and not retryable_failure
    ):
        raise ValueError("Video is not ready for owner scheduling approval")
    return planning.schedule_preview(id, video_id)


@app.post(
    "/api/offices/{id}/videos/{video_id}/approve-schedule",
    dependencies=[Depends(require_owner)],
)
def approve_schedule(id: str, video_id: str, body: ApproveScheduleInput):
    if not body.confirmed:
        raise ValueError("Confirm approval and scheduling")
    office = exists(id)
    v = video(id, video_id)
    if v.get("youtube_video_id") and v.get("status") in ("SCHEDULED", "PUBLISHED"):
        return v | {"result": "ALREADY_SCHEDULED"}
    retryable_failure = (
        v.get("status") == "FAILED"
        and bool(v.get("owner_approval"))
        and bool(v.get("upload_failure"))
        and not v.get("youtube_video_id")
    )
    if v.get("test_mode") or (
        v.get("status") != "READY_FOR_APPROVAL" and not retryable_failure
    ):
        raise ValueError("Only a completed owner-review video can be approved and scheduled")
    if not v.get("file") or not (MEDIA / v["file"]).is_file():
        raise ValueError("Rendered production file is missing")
    qc = v.get("qc") or {}
    hard_qc = all(value for name, value in qc.items() if name != "facts")
    if not hard_qc or not (v.get("facts_verified") or qc.get("facts")):
        raise ValueError("Review Required: resolve factual, rights, or hard QC exceptions first")

    now = time.time()
    with db.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        claim = connection.execute(
            "SELECT status FROM approval_claims WHERE office_id=? AND video_id=?",
            (id, video_id),
        ).fetchone()
        if claim and claim["status"] in ("in_progress", "complete"):
            current = video(id, video_id)
            if current.get("youtube_video_id"):
                return current | {"result": "ALREADY_SCHEDULED"}
            raise ValueError("Approval and scheduling is already in progress")
        connection.execute(
            "INSERT INTO approval_claims VALUES(?,?,?,?) ON CONFLICT(office_id,video_id) DO UPDATE SET status=excluded.status,updated=excluded.updated",
            (id, video_id, "in_progress", now),
        )

    try:
        schedule = planning.claim_schedule(id, video_id, now)
    except Exception:
        with db.connection() as connection:
            connection.execute(
                "UPDATE approval_claims SET status='failed',updated=? WHERE office_id=? AND video_id=?",
                (time.time(), id, video_id),
            )
        raise
    v.update(
        status="APPROVED",
        owner_approval={"decision": "approved_and_scheduled", "at": now},
        original_slot=schedule["original_slot"],
        final_publish_at=schedule["final_publish_at"],
        scheduled_publish_at=schedule["final_publish_at"],
        reschedule_reason=schedule["reschedule_reason"],
        schedule_timezone=schedule["timezone"],
        youtube_privacy="private",
    )
    db.put("videos", id, v, video_id, video_id)
    planning.update_video(id, v)
    v["status"] = "UPLOADING"
    db.put("videos", id, v, video_id, video_id)
    try:
        result = youtube.OfficialYouTube(id).upload(
            v,
            MEDIA / v["file"],
            "private",
            schedule["final_publish_at"],
            owner_initiated=True,
        )
    except Exception:
        v["status"] = "FAILED"
        v["upload_failure"] = "YouTube scheduling failed; retry after checking the connection"
        db.put("videos", id, v, video_id, video_id)
        with db.connection() as connection:
            connection.execute(
                "UPDATE approval_claims SET status='failed',updated=? WHERE office_id=? AND video_id=?",
                (time.time(), id, video_id),
            )
            connection.execute(
                "UPDATE publish_slot_claims SET status='FAILED',updated=? WHERE office_id=? AND video_id=?",
                (time.time(), id, video_id),
            )
        raise
    v.update(
        status="SCHEDULED",
        youtube_id=result["youtube_id"],
        youtube_video_id=result["youtube_id"],
        youtube_upload_at=result.get("uploaded_at"),
        published_at=result.get("published_at"),
    )
    db.put("videos", id, v, video_id, video_id)
    reports.mark_uploaded(id, video_id, result.get("uploaded_at"))
    planning.update_video(id, v)
    with db.connection() as connection:
        connection.execute(
            "UPDATE approval_claims SET status='complete',updated=? WHERE office_id=? AND video_id=?",
            (time.time(), id, video_id),
        )
        connection.execute(
            "UPDATE publish_slot_claims SET status='SCHEDULED',updated=? WHERE office_id=? AND video_id=?",
            (time.time(), id, video_id),
        )
    db.event(id, "Owner approved video and YouTube scheduled publishing", video_id=video_id)
    return v | {"result": "SCHEDULED"}


@app.post(
    "/api/offices/{id}/videos/{video_id}/publish", dependencies=[Depends(require_owner)]
)
def publish(id: str, video_id: str, body: PublishInput):
    v = video(id, video_id)
    o = exists(id)
    if not body.confirmed:
        raise ValueError("Confirm publication")
    if v.get("test_mode") or not v.get("qc"):
        raise ValueError("QC blocks publication")
    hard_qc = all(value for name, value in v["qc"].items() if name != "facts")
    if not hard_qc or not (v.get("facts_verified") or v["qc"].get("facts")):
        raise ValueError("QC, rights or factual verification blocks publication")
    if v["status"] not in ("READY", "APPROVED"):
        raise ValueError("Approve the video before publication")
    result = youtube.OfficialYouTube(id).upload(
        v, MEDIA / v["file"], body.privacy, body.publish_at
    )
    v["status"] = "SCHEDULED" if body.publish_at else "PUBLISHED"
    v["youtube_id"] = result["youtube_id"]
    v["youtube_video_id"] = result["youtube_id"]
    v["youtube_upload_at"] = result.get("uploaded_at")
    v["scheduled_publish_at"] = body.publish_at or v.get("scheduled_publish_at")
    v["published_at"] = result.get("published_at")
    db.put("videos", id, v, video_id, video_id)
    reports.mark_uploaded(id, video_id, result.get("uploaded_at"))
    planning.update_video(id, v)
    return result


@app.get(
    "/api/offices/{id}/videos/{video_id}/file", dependencies=[Depends(require_owner)]
)
def download(id: str, video_id: str):
    v = video(id, video_id)
    if not v.get("file"):
        raise HTTPException(404, "No rendered video yet")
    path = (MEDIA / v["file"]).resolve()
    if not path.is_relative_to(MEDIA):
        raise HTTPException(400, "Invalid file")
    return FileResponse(path, media_type="video/mp4", filename=video_id + ".mp4")


@app.get(
    "/api/offices/{id}/videos/{video_id}/preview",
    dependencies=[Depends(require_owner)],
)
def preview(id: str, video_id: str):
    v = video(id, video_id)
    if not v.get("preview"):
        raise HTTPException(404, "No rendered preview yet")
    path = (MEDIA / v["preview"]).resolve()
    if not path.is_relative_to(MEDIA.resolve()) or not path.is_file():
        raise HTTPException(404, "Preview file not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/integrations", dependencies=[Depends(require_owner)])
def integrations():
    result = []
    for name in [
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "PEXELS_API_KEY",
    ]:
        try:
            configured = bool(secret(name))
        except Exception:
            configured = False
        result.append(
            dict(
                name=name,
                configured=configured,
                masked="••••••••" if configured else "",
                state=(
                    "Configured; validation available"
                    if configured
                    else "Not configured"
                ),
            )
        )
    return result


@app.get("/api/system/zero-cost-status", dependencies=[Depends(require_owner)])
def zero_cost_status(office_id: str):
    office = exists(office_id)
    status = {
        "zero_cost_mode": zero_cost_mode(),
        "paid_providers": "BLOCKED" if zero_cost_mode() else "OPTIONAL",
        "monetary_spend": 0.0 if zero_cost_mode() else sum(
            float(row["data"].get("estimated_usd", 0)) for row in db.rows("cost_events", office_id)
        ),
        "ollama": "ERROR", "model": office["settings"].get("ollama_model", "qwen3:4b"),
        "kokoro": "ERROR", "voice": office["settings"].get("kokoro_voice", "af_heart"),
        "cloudflare": "READY" if secret("CLOUDFLARE_ACCOUNT_ID") and secret("CLOUDFLARE_API_TOKEN") else "ERROR",
        "youtube": "CONNECTED" if snapshot(office_id)["youtube"] else "ERROR",
    }
    today = time.strftime("%Y-%m-%d", time.gmtime())
    free_usage = [row["data"] for row in db.rows("usage_events", office_id)
                  if time.strftime("%Y-%m-%d", time.gmtime(row["data"].get("recorded_at", 0))) == today]
    status["free_usage"] = {
        "cloudflare_requests_today": sum(int(item.get("request_count", 0)) for item in free_usage if item.get("provider") == "cloudflare"),
        "cloudflare_images_today": sum(int(item.get("image_count", 0)) for item in free_usage if item.get("provider") == "cloudflare"),
        "ollama_inferences_today": sum(1 for item in free_usage if item.get("provider") == "ollama"),
        "kokoro_audio_seconds_today": round(sum(float(item.get("audio_seconds", 0)) for item in free_usage if item.get("provider") == "kokoro"), 2),
        "estimated_neurons_today": None,
        "quota_status": "WAIT" if any("WAITING_FOR_FREE_QUOTA" in row["data"].get("message", "") for row in db.rows("errors", office_id)[:20]) else "AVAILABLE",
    }
    try:
        llm_provider(office_id, settings=office["settings"]).health(); status["ollama"] = "READY"
    except ConfigurationRequired as exc:
        status["ollama_error"] = str(exc)
    try:
        tts_provider(office_id, settings=office["settings"]).health(); status["kokoro"] = "READY"
    except ConfigurationRequired as exc:
        status["kokoro_error"] = str(exc)
    try:
        from . import media
        media.run([media.ffmpeg(), "-version"]); status["ffmpeg"] = "READY"
    except Exception:
        status["ffmpeg"] = "ERROR"
    return status


@app.post("/api/system/test-llm-provider", dependencies=[Depends(require_owner)])
def test_llm_provider(body: ProviderTestInput):
    office = exists(body.office_id)
    provider = llm_provider(body.office_id, "llm-provider-test", office["settings"])
    result = provider.structured(
        'Return JSON with key "status" and value "ready".',
        {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
        "health_test",
    )
    return {"success": result.get("status") == "ready", "provider": "ollama", "model": provider.model}


@app.post("/api/system/test-tts-provider", dependencies=[Depends(require_owner)])
def test_tts_provider(body: ProviderTestInput):
    office = exists(body.office_id)
    directory = (MEDIA / body.office_id / "system").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "kokoro-voice-test.wav"
    provider = tts_provider(body.office_id, "tts-provider-test", office["settings"])
    provider.speak("Curiosity begins with one surprising question.", output)
    return {"success": True, "provider": "kokoro", "voice": provider.voice,
            "preview_url": f"/api/system/test-tts-provider/preview?office_id={body.office_id}"}


@app.get("/api/system/test-tts-provider/preview", dependencies=[Depends(require_owner)])
def tts_provider_preview(office_id: str):
    exists(office_id)
    path = (MEDIA / office_id / "system" / "kokoro-voice-test.wav").resolve()
    if not path.is_relative_to(MEDIA.resolve()) or not path.is_file():
        raise HTTPException(404, "Voice preview not available")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/system/test-image-provider", dependencies=[Depends(require_owner)])
def test_image_provider(body: ImageProviderTestInput):
    from .providers import generate_image

    office = exists(body.office_id)
    directory = (MEDIA / body.office_id / "system").resolve()
    if not directory.is_relative_to(MEDIA.resolve()):
        raise ValueError("Invalid provider test path")
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "image-provider-test.png"
    result = generate_image(
        body.prompt,
        output,
        body.office_id,
        "image-provider-test",
        office["settings"],
        allow_fallback=False,
    )
    return {
        "success": True,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "steps": result.get("steps"),
        "seed": result.get("seed"),
        "fallback_from": result.get("fallback_from"),
        "preview_url": f"/api/system/test-image-provider/preview?office_id={body.office_id}",
    }


@app.get(
    "/api/system/test-image-provider/preview",
    dependencies=[Depends(require_owner)],
)
def image_provider_preview(office_id: str):
    exists(office_id)
    path = (MEDIA / office_id / "system" / "image-provider-test.png").resolve()
    if not path.is_relative_to(MEDIA.resolve()) or not path.is_file():
        raise HTTPException(404, "Provider test image not available")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.put("/api/integrations", dependencies=[Depends(require_owner)])
def integration(body: SecretInput):
    save_secret(body.name, body.value)
    return {"configured": True}


@app.post("/api/integrations/{name}/validate", dependencies=[Depends(require_owner)])
def validate_integration(name: Literal["OPENAI_API_KEY", "PEXELS_API_KEY"]):
    import httpx

    if name == "OPENAI_API_KEY" and zero_cost_mode():
        raise PaidProviderBlocked("ZERO_COST_MODE prevents paid provider usage (openai).")
    value = secret(name)
    if not value:
        raise ValueError("Not configured")
    url = (
        "https://api.openai.com/v1/models"
        if name == "OPENAI_API_KEY"
        else "https://api.pexels.com/v1/search?query=nature&per_page=1"
    )
    r = httpx.get(
        url,
        headers={
            "Authorization": ("Bearer " if name == "OPENAI_API_KEY" else "") + value
        },
        timeout=20,
    )
    return {"valid": r.status_code == 200, "http_status": r.status_code}


@app.post("/api/offices/{id}/youtube/connect", dependencies=[Depends(require_owner)])
def connect(id: str):
    exists(id)
    return {"url": youtube.oauth_start(id)}


@app.delete("/api/offices/{id}/youtube", dependencies=[Depends(require_owner)])
def disconnect(id: str):
    exists(id)
    with db.connection() as c:
        c.execute("DELETE FROM youtube_connections WHERE office_id=?", (id,))
    return {"ok": True}


@app.get("/api/youtube/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, "Google authorization declined")
    youtube.callback(code, state)
    return HTMLResponse(
        "<h1>YouTube connected</h1><p>You may close this tab and return to Pixel Shorts Factory.</p>"
    )
