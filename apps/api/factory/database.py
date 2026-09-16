import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .config import DATA, DEFAULTS, ROLES

DB = DATA / "factory.sqlite3"
TABLES = "topics topic_sources research_claims videos video_scenes video_assets video_scripts job_attempts errors qc_checks rights_records uploads analytics_snapshots strategy_changes reports cost_events usage_events system_events system_inspections daily_production_plans performance_profiles".split()


@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def migrate():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied REAL);
        CREATE TABLE IF NOT EXISTS offices(id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS office_settings(office_id TEXT PRIMARY KEY REFERENCES offices(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS employee_states(office_id TEXT REFERENCES offices(id), role TEXT, status TEXT, job_id TEXT, stage TEXT, updated REAL, PRIMARY KEY(office_id,role));
        CREATE TABLE IF NOT EXISTS integrations(name TEXT PRIMARY KEY, encrypted TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS youtube_connections(office_id TEXT PRIMARY KEY REFERENCES offices(id), encrypted TEXT NOT NULL, channel_id TEXT, channel_title TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires REAL);
        CREATE TABLE IF NOT EXISTS oauth_states(state_hash TEXT PRIMARY KEY, office_id TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, office_id TEXT REFERENCES offices(id), kind TEXT, status TEXT, stage INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, next_run REAL DEFAULT 0, payload TEXT, created REAL, updated REAL, error TEXT);
        CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,next_run);
        CREATE TABLE IF NOT EXISTS upload_claims(office_id TEXT NOT NULL, video_id TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(office_id,video_id));
        CREATE TABLE IF NOT EXISTS active_topic_jobs(office_id TEXT NOT NULL, topic_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(office_id,topic_id));
        CREATE TABLE IF NOT EXISTS publish_slot_claims(office_id TEXT NOT NULL, publish_at TEXT NOT NULL, video_id TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(office_id,publish_at), UNIQUE(office_id,video_id));
        CREATE TABLE IF NOT EXISTS approval_claims(office_id TEXT NOT NULL, video_id TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(office_id,video_id));
        """)
        for table in TABLES:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, office_id TEXT NOT NULL REFERENCES offices(id), video_id TEXT, payload TEXT NOT NULL, created REAL NOT NULL)"
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_office ON {table}(office_id,created)"
            )
        db.execute("INSERT OR IGNORE INTO migrations VALUES(1,?)", (time.time(),))
        for row in db.execute("SELECT office_id,payload FROM office_settings").fetchall():
            settings = json.loads(row["payload"])
            had_review_policy = "review_policy" in settings
            for key, value in DEFAULTS.items():
                settings.setdefault(key, value)
            if os.getenv("ZERO_COST_MODE", "true").lower() == "true":
                settings.update(
                    zero_cost_mode=True,
                    llm_provider="ollama",
                    tts_provider="kokoro",
                    image_provider="cloudflare",
                    image_fallback_provider="none",
                    daily_budget=0.0,
                    monthly_budget=0.0,
                )
            if not had_review_policy:
                office_name = db.execute(
                    "SELECT name FROM offices WHERE id=?", (row["office_id"],)
                ).fetchone()[0]
                if office_name == "Curiosity Room":
                    settings["review_policy"] = "Review Exceptions Only"
                else:
                    settings["review_policy"] = (
                        "Review Everything"
                        if settings.get("review_mode", True)
                        else "Fully Automatic"
                    )
            db.execute("UPDATE office_settings SET payload=? WHERE office_id=?",
                       (json.dumps(settings), row["office_id"]))
        db.execute("INSERT OR IGNORE INTO migrations VALUES(3,?)", (time.time(),))
        db.execute("INSERT OR IGNORE INTO migrations VALUES(4,?)", (time.time(),))
        db.execute("INSERT OR IGNORE INTO migrations VALUES(5,?)", (time.time(),))
        # Rebuild active topic claims from durable jobs. This also cleans up
        # pre-migration duplicates while preserving completed history.
        db.execute("DELETE FROM active_topic_jobs")
        active = ("QUEUED", "PLANNED", "RUNNING", "RETRYING", "WAITING")
        for job in db.execute(
            "SELECT * FROM jobs WHERE status IN (?,?,?,?,?) ORDER BY created,id", active
        ).fetchall():
            payload = json.loads(job["payload"] or "{}")
            topic_id = payload.get("topic_id")
            if not topic_id or payload.get("test_mode"):
                continue
            claimed = db.execute(
                "SELECT job_id FROM active_topic_jobs WHERE office_id=? AND topic_id=?",
                (job["office_id"], topic_id),
            ).fetchone()
            if claimed:
                db.execute(
                    "UPDATE jobs SET status='CANCELLED',error=?,updated=? WHERE id=?",
                    ("Duplicate active topic queue entry removed during migration", time.time(), job["id"]),
                )
                video_row = db.execute("SELECT payload FROM videos WHERE id=?", (job["id"],)).fetchone()
                if video_row:
                    video = json.loads(video_row["payload"])
                    video["status"] = "CANCELLED"
                    video["queue_cleanup_reason"] = "Duplicate active topic queue entry"
                    db.execute("UPDATE videos SET payload=? WHERE id=?", (json.dumps(video), job["id"]))
                db.execute(
                    "INSERT INTO system_events VALUES(?,?,?,?,?)",
                    (uid(), job["office_id"], job["id"], json.dumps({
                        "message": "Duplicate active queue item cancelled; earliest request retained",
                        "severity": "warning",
                    }), time.time()),
                )
                continue
            db.execute(
                "INSERT INTO active_topic_jobs VALUES(?,?,?,?,?)",
                (job["office_id"], topic_id, job["id"], job["status"], time.time()),
            )
        # Rebuild pending slot claims while preserving completed YouTube schedules.
        db.execute("DELETE FROM publish_slot_claims WHERE status IN ('PLANNED','PRODUCING')")
        for job in db.execute(
            "SELECT * FROM jobs WHERE status IN (?,?,?,?,?) ORDER BY created,id", active
        ).fetchall():
            payload = json.loads(job["payload"] or "{}")
            publish_at = payload.get("scheduled_publish_at")
            if not publish_at or payload.get("test_mode"):
                continue
            cursor = db.execute(
                "INSERT OR IGNORE INTO publish_slot_claims VALUES(?,?,?,?,?)",
                (job["office_id"], publish_at, job["id"], "PLANNED", time.time()),
            )
            if not cursor.rowcount:
                owner = db.execute(
                    "SELECT video_id FROM publish_slot_claims WHERE office_id=? AND publish_at=?",
                    (job["office_id"], publish_at),
                ).fetchone()
                if owner and owner["video_id"] != job["id"]:
                    db.execute(
                        "UPDATE jobs SET status='CANCELLED',error=?,updated=? WHERE id=?",
                        ("Duplicate publish slot removed during migration", time.time(), job["id"]),
                    )
                    db.execute("DELETE FROM active_topic_jobs WHERE job_id=?", (job["id"],))
                    video_row = db.execute("SELECT payload FROM videos WHERE id=?", (job["id"],)).fetchone()
                    if video_row:
                        video = json.loads(video_row["payload"])
                        video["status"] = "CANCELLED"
                        video["queue_cleanup_reason"] = "Duplicate publish slot"
                        db.execute("UPDATE videos SET payload=? WHERE id=?", (json.dumps(video), job["id"]))
                    db.execute(
                        "INSERT INTO system_events VALUES(?,?,?,?,?)",
                        (uid(), job["office_id"], job["id"], json.dumps({
                            "message": "Duplicate publish slot cancelled; earliest request retained",
                            "severity": "warning",
                        }), time.time()),
                    )
        for row in db.execute(
            "SELECT office_id,id,payload FROM videos ORDER BY created,id"
        ).fetchall():
            video = json.loads(row["payload"])
            if video.get("status") not in ("SCHEDULED", "PUBLISHED"):
                continue
            publish_at = video.get("scheduled_publish_at") or video.get("final_publish_at")
            if publish_at:
                db.execute(
                    "INSERT OR IGNORE INTO publish_slot_claims VALUES(?,?,?,?,?)",
                    (row["office_id"], publish_at, row["id"], video["status"], time.time()),
                )
        db.execute("INSERT OR IGNORE INTO migrations VALUES(6,?)", (time.time(),))
    if not offices():
        create_office("Amazing Things", {})


def uid():
    return uuid.uuid4().hex


def offices():
    with connection() as db:
        return [
            dict(row) | {"settings": json.loads(row["payload"])}
            for row in db.execute(
                "SELECT o.*,s.payload FROM offices o JOIN office_settings s ON o.id=s.office_id ORDER BY created"
            )
        ]


def office(id):
    return next((o for o in offices() if o["id"] == id), None)


def create_office(name, settings):
    id = uid()
    initial = DEFAULTS | settings
    if name == "Curiosity Room" and "review_policy" not in settings:
        initial["review_policy"] = "Review Exceptions Only"
    with connection() as db:
        db.execute(
            "INSERT INTO offices VALUES(?,?,?,?)", (id, name, "STOPPED", time.time())
        )
        db.execute(
            "INSERT INTO office_settings VALUES(?,?)",
            (id, json.dumps(initial)),
        )
        db.executemany(
            "INSERT INTO employee_states VALUES(?,?,?,?,?,?)",
            [(id, r, "IDLE", None, None, time.time()) for r in ROLES],
        )
    event(id, "Office created. Factory stopped; review mode enabled.")
    return office(id)


def put(table, office_id, payload, video_id=None, id=None):
    if table not in TABLES:
        raise ValueError("Unknown table")
    id = id or uid()
    with connection() as db:
        db.execute(
            f"INSERT INTO {table} VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (id, office_id, video_id, json.dumps(payload), time.time()),
        )
    return id


def rows(table, office_id, video_id=None):
    if table not in TABLES:
        raise ValueError("Unknown table")
    with connection() as db:
        sql = f"SELECT * FROM {table} WHERE office_id=?"
        args = [office_id]
        if video_id is not None:
            sql += " AND video_id=?"
            args.append(video_id)
        return [
            dict(r) | {"data": json.loads(r["payload"])}
            for r in db.execute(sql + " ORDER BY created DESC", args)
        ]


def event(office_id, message, severity="info", video_id=None):
    put("system_events", office_id, dict(message=message, severity=severity), video_id)
