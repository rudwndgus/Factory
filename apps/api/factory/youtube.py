import json
import os
import secrets
import time
import threading
import logging
import re
from functools import wraps
from urllib.parse import urlencode
import httpx
from . import database as db
from .security import cipher, digest, secret

REQUIRED_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPES = REQUIRED_SCOPES + " openid email"
logger = logging.getLogger("uvicorn.error")


class OAuthAccessFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn otherwise writes the authorization code in the callback query.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if str(args[2]).split("?")[0] == "/api/youtube/oauth/callback":
                args[2] = "/api/youtube/oauth/callback"
                record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(OAuthAccessFilter())


def sanitized(value, sensitive=()):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in {
            "access_token", "refresh_token", "id_token", "client_secret",
            "authorization", "authorization_code", "code_verifier",
        } else sanitized(v, sensitive)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitized(v, sensitive) for v in value]
    if isinstance(value, str):
        for item in sensitive:
            if item:
                value = value.replace(item, "[REDACTED]")
        return re.sub(r"(?i)(Bearer\s+|(?:access_token|refresh_token|client_secret|code)=)[^\s&\"<>]+", r"\1[REDACTED]", value)
    return value


def google_json(response, operation, sensitive=(), log_response=False):
    try:
        body = response.json()
    except ValueError:
        raise ValueError(f"{operation}: Google returned HTTP {response.status_code}, non-JSON response") from None
    safe = sanitized(body, sensitive)
    if log_response:
        logger.info("%s HTTP %s items=%s response=%s", operation,
                    response.status_code, len(body.get("items", [])), json.dumps(safe, ensure_ascii=True))
    if response.status_code != 200 or body.get("error"):
        # Never expose the token response itself, only Google's sanitized error.
        detail = {k: safe[k] for k in ("error", "error_description") if k in safe}
        raise ValueError(f"{operation}: Google HTTP {response.status_code}: {json.dumps(detail, ensure_ascii=True)}")
    return body
UPLOAD_LOCK = threading.Lock()


def claim_upload(office_id, video_id):
    """Claim an upload across threads and server processes before any Google call."""
    now = time.time()
    with db.connection() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO upload_claims VALUES(?,?,?,?)",
            (office_id, video_id, "in_progress", now),
        )
        if cursor.rowcount:
            return
        claim = connection.execute(
            "SELECT status,updated FROM upload_claims WHERE office_id=? AND video_id=?",
            (office_id, video_id),
        ).fetchone()
        if claim["status"] == "complete" or (
            claim["status"] == "in_progress" and now - claim["updated"] < 1800
        ):
            raise ValueError("This video upload is already in progress or complete")
        connection.execute(
            "UPDATE upload_claims SET status='in_progress',updated=? WHERE office_id=? AND video_id=?",
            (now, office_id, video_id),
        )


def finish_upload_claim(office_id, video_id, status):
    with db.connection() as connection:
        connection.execute(
            "UPDATE upload_claims SET status=?,updated=? WHERE office_id=? AND video_id=?",
            (status, time.time(), office_id, video_id),
        )


def serialize_upload(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with UPLOAD_LOCK:
            provider, video = args[0], args[1]
            claim_upload(provider.office_id, video["id"])
            try:
                result = fn(*args, **kwargs)
            except Exception:
                finish_upload_claim(provider.office_id, video["id"], "needs_reconciliation")
                raise
            finish_upload_claim(provider.office_id, video["id"], "complete")
            return result

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
    sensitive = [code, secret("GOOGLE_CLIENT_SECRET")]
    tokens = google_json(r, "OAuth token exchange", sensitive)
    sensitive += [tokens.get(k, "") for k in ("access_token", "refresh_token", "id_token")]
    if not tokens.get("access_token"):
        raise ValueError("Google token exchange returned no access token; reconnect")
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    email = None
    try:
        identity = httpx.get("https://openidconnect.googleapis.com/v1/userinfo",
                             headers=headers, timeout=30)
        if identity.status_code == 200:
            email = identity.json().get("email")
            logger.info("YouTube OAuth authenticated email=%s", sanitized(email, sensitive))
        else:
            logger.info("YouTube OAuth identity unavailable HTTP %s", identity.status_code)
    except (httpx.HTTPError, ValueError):
        logger.info("YouTube OAuth identity unavailable (request failed)")
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/channels",
        headers=headers,
        params=dict(part="id,snippet,contentDetails", mine="true"),
        timeout=30,
    )
    result = google_json(r, "YouTube channels.list(mine=true)", sensitive, log_response=True)
    if "scope" in tokens:
        missing = set(REQUIRED_SCOPES.split()) - set(tokens["scope"].split())
        if missing:
            raise ValueError("Google did not grant required scopes: " + ", ".join(sorted(missing)) + "; reconnect and approve all requested permissions")
    if not result.get("items"):
        raise ValueError("YouTube channels.list succeeded (HTTP 200) but returned 0 channels for "
                         + (sanitized(email, sensitive) or "the authenticated account")
                         + ". Reconnect and select the account/channel that owns your YouTube channel.")
    if len(result["items"]) != 1 or result.get("nextPageToken"):
        raise ValueError("Google returned multiple channels; reconnect selecting the intended YouTube channel")
    if not tokens.get("refresh_token"):
        raise ValueError("Google returned no refresh token. Remove this app's Google account access and reconnect with consent; existing connection was not changed.")
    channel = result["items"][0]
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
    def upload(
        self,
        video,
        path,
        privacy="private",
        publish_at=None,
        owner_initiated=False,
    ):
        if video.get("test_mode"):
            raise ValueError("TEST RUN videos can never be published")
        office_mode = db.office(self.office_id)["mode"]
        blocked_modes = ("EMERGENCY_STOP", "MAINTENANCE") if owner_initiated else (
            "EMERGENCY_STOP",
            "MAINTENANCE",
            "STOPPED",
            "PAUSED",
        )
        if office_mode in blocked_modes:
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
                        "scheduled_publish_at": publish_at,
                    }
                    db.put("uploads", self.office_id, result, video["id"], entry["id"])
                    db.put("usage_events", self.office_id,
                           {"provider": "youtube", "type": "quota_request", "operation": "upload_reconcile",
                            "request_count": 1, "monetary_usd": 0.0, "recorded_at": time.time()}, video["id"])
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
            if (
                not owner_initiated
                and db.office(self.office_id)["mode"] != "RUNNING"
            ) or db.office(self.office_id)["mode"] in ("EMERGENCY_STOP", "MAINTENANCE"):
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
                "scheduled_publish_at": publish_at,
            }
            verification = client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                headers=headers,
                params={"part": "id,status,snippet", "id": result["youtube_id"]},
            )
            if verification.status_code != 200 or not verification.json().get("items"):
                raise ValueError("YouTube accepted upload bytes but video verification failed")
            item = verification.json()["items"][0]
            result["verified"] = item.get("id") == result["youtube_id"]
            result["youtube_status"] = item.get("status", {})
            if item.get("status", {}).get("privacyStatus") == "public":
                result["published_at"] = time.time()
            db.put("uploads", self.office_id, result, video["id"], record)
            db.put("usage_events", self.office_id,
                   {"provider": "youtube", "type": "quota_request", "operation": "video_upload",
                    "request_count": 3, "monetary_usd": 0.0, "recorded_at": time.time()}, video["id"])
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
                params=dict(part="statistics,status,snippet", id=id),
                timeout=30,
            )
            if response.status_code != 200:
                raise ValueError("YouTube statistics access failed")
            items = response.json().get("items", [])
            if not items:
                continue
            metrics = {k: int(v) for k, v in items[0]["statistics"].items()}
            metrics["youtube_status"] = items[0].get("status", {})
            metrics["youtube_published_at"] = items[0].get("snippet", {}).get("publishedAt")
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
            from . import analytics as analyst

            analyzed = analyst.process_snapshot(
                self.office_id,
                upload["video_id"],
                metrics,
                upload["data"].get("uploaded_at") or upload["created"],
            )
            db.put("usage_events", self.office_id,
                   {"provider": "youtube", "type": "quota_request", "operation": "analytics",
                    "request_count": 2, "monetary_usd": 0.0, "recorded_at": time.time()}, upload["video_id"])
            result.append(analyzed)
        return result
