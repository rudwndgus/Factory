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
from . import database as db, engine, reports, youtube
from .config import MEDIA, DEFAULTS
from .security import require_owner, login, save_secret, secret, digest
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
        if not 0 < s["duplicate_threshold"] <= 1:
            raise ValueError("Invalid duplicate threshold")
        if s["privacy"] not in ("private", "unlisted", "public"):
            raise ValueError("Invalid privacy")
        return self


class ModeInput(BaseModel):
    mode: Literal["RUNNING", "PAUSED", "STOPPED", "MAINTENANCE", "EMERGENCY_STOP"]
    confirmed: bool = False


class SecretInput(BaseModel):
    name: Literal[
        "OPENAI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PEXELS_API_KEY"
    ]
    value: str = Field(min_length=1, max_length=4096)


class TopicInput(BaseModel):
    title: str = Field(min_length=3, max_length=250)
    summary: str = Field(default="", max_length=12000)
    source_url: str = Field(default="", max_length=2000)
    category: str = "Evergreen"


class JobInput(BaseModel):
    test_mode: bool = False
    topic_id: str | None = None


class ReviewInput(BaseModel):
    action: Literal["approve", "reject", "verify_facts", "metadata"]
    title: str | None = None
    description: str | None = None


class PublishInput(BaseModel):
    privacy: Literal["private", "unlisted", "public"] = "private"
    confirmed: bool = False
    publish_at: str | None = None


class RegenerateInput(BaseModel):
    stage: Literal[2, 3, 4, 5, 7]
    scene: int | None = None
    sentences: list[str] | None = Field(default=None, min_length=3, max_length=12)


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
    exists(id)
    if body.mode == "EMERGENCY_STOP" and not body.confirmed:
        raise HTTPException(400, "Explicit emergency confirmation required")
    engine.set_mode(id, body.mode)
    return {"mode": body.mode}


@app.post("/api/system/mode", dependencies=[Depends(require_owner)])
def global_mode(body: ModeInput):
    if body.mode == "EMERGENCY_STOP" and not body.confirmed:
        raise HTTPException(400, "Confirmation required")
    for office in db.offices():
        engine.set_mode(office["id"], body.mode)
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
                "analytics_snapshots",
                "errors",
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
    return {"id": engine.enqueue(id, body.test_mode, body.topic_id)}


@app.post(
    "/api/offices/{id}/jobs/{job_id}/{action}", dependencies=[Depends(require_owner)]
)
def job_action(id: str, job_id: str, action: Literal["retry", "cancel"]):
    exists(id)
    if action == "retry":
        engine.retry(id, job_id)
    else:
        with db.connection() as c:
            c.execute(
                "UPDATE jobs SET status='CANCELLED' WHERE id=? AND office_id=? AND status!='COMPLETE'",
                (job_id, id),
            )
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


@app.post("/api/offices/{id}/reports/{kind}", dependencies=[Depends(require_owner)])
def make_report(id: str, kind: Literal["daily", "weekly"]):
    exists(id)
    return {"id": reports.report(id, kind)}


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
        if "qc" in v:
            v["qc"]["facts"] = True
    elif body.action == "approve":
        if v.get("test_mode") or not all(v.get("qc", {}).values()) or not v.get("qc"):
            raise ValueError("All QC gates must pass; test outputs cannot be approved")
        v["status"] = "APPROVED"
    elif body.action == "reject":
        v["status"] = "REJECTED"
    else:
        if body.title:
            v["title"] = body.title[:100]
        if body.description is not None:
            v["description"] = body.description[:5000]
    db.put("videos", id, v, video_id, video_id)
    db.event(id, "CEO action: " + body.action, video_id=video_id)
    return v


@app.post(
    "/api/offices/{id}/videos/{video_id}/publish", dependencies=[Depends(require_owner)]
)
def publish(id: str, video_id: str, body: PublishInput):
    v = video(id, video_id)
    o = exists(id)
    if not body.confirmed:
        raise ValueError("Confirm publication")
    if v.get("test_mode") or not v.get("qc") or not all(v["qc"].values()):
        raise ValueError("QC blocks publication")
    if o["settings"]["review_mode"] and v["status"] != "APPROVED":
        raise ValueError("CEO approval required")
    result = youtube.OfficialYouTube(id).upload(
        v, MEDIA / v["file"], body.privacy, body.publish_at
    )
    v["status"] = "PUBLISHED"
    v["youtube_id"] = result["youtube_id"]
    db.put("videos", id, v, video_id, video_id)
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


@app.get("/api/integrations", dependencies=[Depends(require_owner)])
def integrations():
    result = []
    for name in [
        "OPENAI_API_KEY",
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


@app.put("/api/integrations", dependencies=[Depends(require_owner)])
def integration(body: SecretInput):
    save_secret(body.name, body.value)
    return {"configured": True}


@app.post("/api/integrations/{name}/validate", dependencies=[Depends(require_owner)])
def validate_integration(name: Literal["OPENAI_API_KEY", "PEXELS_API_KEY"]):
    import httpx

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
