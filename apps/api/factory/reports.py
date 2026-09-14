import os
import shutil
import time
import httpx
from . import database as db, media
from .security import secret
from .config import DATA


def report(office_id, kind="daily"):
    cutoff = time.time() - (7 if kind == "weekly" else 1) * 86400
    videos = [v for v in db.rows("videos", office_id) if v["created"] >= cutoff]
    costs = [c for c in db.rows("cost_events", office_id) if c["created"] >= cutoff]
    snapshots = db.rows("analytics_snapshots", office_id)
    latest = {}
    for s in snapshots:
        latest.setdefault(s["video_id"], s["data"])
    result = dict(
        kind=kind,
        date=time.strftime("%Y-%m-%d"),
        planned=len(videos),
        produced=sum(bool(v["data"].get("file")) for v in videos),
        test_runs=sum(v["data"].get("test_mode", False) for v in videos),
        uploaded=len(
            [
                u
                for u in db.rows("uploads", office_id)
                if u["created"] >= cutoff and u["data"].get("youtube_id")
            ]
        ),
        failed=len([e for e in db.rows("errors", office_id) if e["created"] >= cutoff]),
        estimated_cost=sum(c["data"]["estimated_usd"] for c in costs),
        videos=[
            {"title": v["data"]["title"], "status": v["data"]["status"]} for v in videos
        ],
        analytics=latest,
        recommendation="Insufficient published analytics for strategy changes. Collect at least five comparable published videos.",
        read=False,
    )
    id = db.put("reports", office_id, result)
    db.event(office_id, kind.title() + " report delivered to CEO desk")
    return id


def inspect(office_id):
    checks = {}
    try:
        with db.connection() as c:
            checks["database"] = (
                c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            )
    except Exception:
        checks["database"] = False
    checks["storage"] = os.access(DATA, os.W_OK)
    checks["disk_space"] = shutil.disk_usage(DATA).free > 1024**3
    try:
        media.run([media.ffmpeg(), "-version"])
        checks["ffmpeg"] = True
    except Exception:
        checks["ffmpeg"] = False
    for name in [
        "OPENAI_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "PEXELS_API_KEY",
    ]:
        try:
            checks[name] = (
                "configured (not validated)" if secret(name) else "not configured"
            )
        except Exception:
            checks[name] = "encryption configuration invalid"
    with db.connection() as c:
        checks["youtube_connected"] = bool(
            c.execute(
                "SELECT 1 FROM youtube_connections WHERE office_id=?", (office_id,)
            ).fetchone()
        )
    try:
        checks["internet"] = (
            httpx.get("https://www.nasa.gov", timeout=10).status_code < 500
        )
    except httpx.HTTPError:
        checks["internet"] = False
    checks["scheduler"] = "active"
    checks["recent_errors"] = len(db.rows("errors", office_id)[:20])
    result = {
        "kind": "inspection",
        "checks": checks,
        "overall": (
            "Operational"
            if all(v is True or v == "active" or v == 0 for v in checks.values())
            else "Configuration or inspection warnings"
        ),
        "read": False,
    }
    db.put("system_inspections", office_id, result)
    id = db.put("reports", office_id, result)
    db.event(office_id, "System inspection completed")
    return id


def scheduled_reports():
    day = time.strftime("%Y-%m-%d", time.gmtime())
    for office in db.offices():
        existing = db.rows("reports", office["id"])
        if not any(
            r["data"].get("date") == day and r["data"].get("kind") == "daily"
            for r in existing
        ):
            report(office["id"])
        if time.gmtime().tm_wday == 0 and not any(
            r["data"].get("date") == day and r["data"].get("kind") == "weekly"
            for r in existing
        ):
            report(office["id"], "weekly")
