import json
import os
import secrets
import time
import threading
from functools import wraps
from urllib.parse import urlencode
import httpx
from . import database as db
from .security import cipher, digest, secret

SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"
UPLOAD_LOCK = threading.Lock()


def serialize_upload(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with UPLOAD_LOCK:
            return fn(*args, **kwargs)

    return wrapped


def oauth_start(office_id):
    client = secret("GOOGLE_CLIENT_ID")
    if not client or not secret("GOOGLE_CLIENT_SECRET"):
        raise ValueError("Configure Google client ID and client secret first")
    state = secrets.token_urlsafe(32)
    with db.connection() as c:
        c.execute(
            "INSERT INTO oauth_states VALUES(?,?,?)",
            (digest(state), office_id, time.time() + 600),
        )
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        dict(
            client_id=client,
            redirect_uri=os.getenv(
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/youtube/oauth/callback",
            ),
            response_type="code",
            scope=SCOPES,
            access_type="offline",
            prompt="consent",
            state=state,
        )
    )


def callback(code, state):
    with db.connection() as c:
        row = c.execute(
            "SELECT * FROM oauth_states WHERE state_hash=?", (digest(state),)
        ).fetchone()
        if not row or row["expires"] < time.time():
            raise ValueError("OAuth state invalid or expired")
        office_id = row["office_id"]
        c.execute("DELETE FROM oauth_states WHERE state_hash=?", (digest(state),))
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data=dict(
            code=code,
            client_id=secret("GOOGLE_CLIENT_ID"),
            client_secret=secret("GOOGLE_CLIENT_SECRET"),
            redirect_uri=os.getenv(
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/youtube/oauth/callback",
            ),
            grant_type="authorization_code",
        ),
        timeout=30,
    )
    if r.status_code != 200:
        raise ValueError("Google token exchange failed")
    tokens = r.json()
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/channels",
        headers={"Authorization": "Bearer " + tokens["access_token"]},
        params=dict(part="snippet", mine="true"),
        timeout=30,
    )
    if r.status_code != 200 or not r.json().get("items"):
        raise ValueError("Google account has no accessible YouTube channel")
    channel = r.json()["items"][0]
    encrypted = cipher().encrypt(json.dumps(tokens).encode()).decode()
    with db.connection() as c:
        c.execute(
            "INSERT INTO youtube_connections VALUES(?,?,?,?) ON CONFLICT(office_id) DO UPDATE SET encrypted=excluded.encrypted,channel_id=excluded.channel_id,channel_title=excluded.channel_title",
            (office_id, encrypted, channel["id"], channel["snippet"]["title"]),
        )
    db.event(office_id, "YouTube channel connected: " + channel["snippet"]["title"])


def access(office_id):
    with db.connection() as c:
        row = c.execute(
            "SELECT encrypted FROM youtube_connections WHERE office_id=?", (office_id,)
        ).fetchone()
    if not row:
        raise ValueError("Connect YouTube for this Office first")
    tokens = json.loads(cipher().decrypt(row[0].encode()))
    if tokens["expires_at"] < time.time() + 60:
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data=dict(
                client_id=secret("GOOGLE_CLIENT_ID"),
                client_secret=secret("GOOGLE_CLIENT_SECRET"),
                refresh_token=tokens.get("refresh_token", ""),
                grant_type="refresh_token",
            ),
            timeout=30,
        )
        if r.status_code != 200:
            raise ValueError("YouTube authorization expired; reconnect")
        tokens.update(r.json())
        tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
        with db.connection() as c:
            c.execute(
                "UPDATE youtube_connections SET encrypted=? WHERE office_id=?",
                (cipher().encrypt(json.dumps(tokens).encode()).decode(), office_id),
            )
    return tokens["access_token"]


class OfficialYouTube:
    def __init__(self, office_id):
        self.office_id = office_id

    @serialize_upload
    def upload(self, video, path, privacy="private", publish_at=None):
        if video.get("test_mode"):
            raise ValueError("TEST RUN videos can never be published")
        if db.office(self.office_id)["mode"] in (
            "EMERGENCY_STOP",
            "MAINTENANCE",
            "STOPPED",
            "PAUSED",
        ):
            raise ValueError("Start Office before uploading")
        headers = {"Authorization": "Bearer " + access(self.office_id)}
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if publish_at:
            status.update(privacyStatus="private", publishAt=publish_at)
        with httpx.Client(timeout=180) as client:
            # Persist session URI encrypted before transmitting bytes. Never start a second upload blindly.
            records = db.rows("uploads", self.office_id, video["id"])
            if records and records[0]["data"].get("youtube_id"):
                return records[0]["data"]
            if records:
                entry = records[0]
                uri = cipher().decrypt(entry["data"]["session"].encode()).decode()
                probe = client.put(
                    uri,
                    headers=headers
                    | {
                        "Content-Range": f"bytes */{path.stat().st_size}",
                        "Content-Length": "0",
                    },
                )
                if probe.status_code in (200, 201):
                    result = {
                        "youtube_id": probe.json()["id"],
                        "privacy": privacy,
                        "uploaded_at": time.time(),
                    }
                    db.put("uploads", self.office_id, result, video["id"], entry["id"])
                    return result
                raise ValueError(
                    "Previous upload requires manual reconciliation; a duplicate upload is blocked"
                )
            response = client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers=headers
                | {
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(path.stat().st_size),
                },
                json={
                    "snippet": {
                        "title": video["title"][:100],
                        "description": video.get("description", "")[:5000],
                        "categoryId": "27",
                    },
                    "status": status,
                },
            )
            if response.status_code not in (200, 201):
                raise ValueError(
                    f"YouTube initiation returned HTTP {response.status_code}"
                )
            uri = response.headers["location"]
            record = db.put(
                "uploads",
                self.office_id,
                {
                    "session": cipher().encrypt(uri.encode()).decode(),
                    "status": "in_progress",
                },
                video["id"],
            )
            if db.office(self.office_id)["mode"] != "RUNNING":
                raise ValueError("Office stopped before upload")
            with path.open("rb") as f:
                r = client.put(
                    uri,
                    headers=headers
                    | {
                        "Content-Type": "video/mp4",
                        "Content-Length": str(path.stat().st_size),
                    },
                    content=f,
                )
            if r.status_code not in (200, 201):
                raise ValueError(
                    f"YouTube upload returned HTTP {r.status_code}; reconciliation required"
                )
            result = {
                "youtube_id": r.json()["id"],
                "privacy": privacy,
                "uploaded_at": time.time(),
            }
            db.put("uploads", self.office_id, result, video["id"], record)
            return result

    def analytics(self):
        uploads = [
            r for r in db.rows("uploads", self.office_id) if r["data"].get("youtube_id")
        ]
        if not uploads:
            return []
        headers = {"Authorization": "Bearer " + access(self.office_id)}
        result = []
        for upload in uploads:
            id = upload["data"]["youtube_id"]
            response = httpx.get(
                "https://www.googleapis.com/youtube/v3/videos",
                headers=headers,
                params=dict(part="statistics", id=id),
                timeout=30,
            )
            if response.status_code != 200:
                raise ValueError("YouTube statistics access failed")
            items = response.json().get("items", [])
            if not items:
                continue
            metrics = {k: int(v) for k, v in items[0]["statistics"].items()}
            r = httpx.get(
                "https://youtubeanalytics.googleapis.com/v2/reports",
                headers=headers,
                params={
                    "ids": "channel==MINE",
                    "startDate": time.strftime(
                        "%Y-%m-%d", time.gmtime(upload["created"])
                    ),
                    "endDate": time.strftime("%Y-%m-%d", time.gmtime()),
                    "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,shares",
                    "filters": "video==" + id,
                },
                timeout=30,
            )
            metrics["analytics_available"] = r.status_code == 200 and bool(
                r.json().get("rows")
            )
            if metrics["analytics_available"]:
                metrics.update(
                    dict(
                        zip(
                            [h["name"] for h in r.json()["columnHeaders"]],
                            r.json()["rows"][0],
                        )
                    )
                )
            db.put("analytics_snapshots", self.office_id, metrics, upload["video_id"])
            result.append(metrics)
        return result
