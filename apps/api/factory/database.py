import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .config import DATA, DEFAULTS, ROLES

DB = DATA / "factory.sqlite3"
TABLES = "topics topic_sources research_claims videos video_scenes video_assets video_scripts job_attempts errors qc_checks rights_records uploads analytics_snapshots strategy_changes reports cost_events system_events system_inspections".split()


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
        """)
        for table in TABLES:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, office_id TEXT NOT NULL REFERENCES offices(id), video_id TEXT, payload TEXT NOT NULL, created REAL NOT NULL)"
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_office ON {table}(office_id,created)"
            )
        db.execute("INSERT OR IGNORE INTO migrations VALUES(1,?)", (time.time(),))
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
    with connection() as db:
        db.execute(
            "INSERT INTO offices VALUES(?,?,?,?)", (id, name, "STOPPED", time.time())
        )
        db.execute(
            "INSERT INTO office_settings VALUES(?,?)",
            (id, json.dumps(DEFAULTS | settings)),
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
