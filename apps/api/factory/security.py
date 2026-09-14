import hashlib
import os
import secrets
import time
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from .database import connection


def cipher():
    key = os.getenv("MASTER_KEY", "")
    if not key:
        raise ValueError("MASTER_KEY must be configured before storing credentials")
    return Fernet(key.encode())


def save_secret(name, value):
    encrypted = cipher().encrypt(value.encode()).decode()
    with connection() as db:
        db.execute(
            "INSERT INTO integrations VALUES(?,?) ON CONFLICT(name) DO UPDATE SET encrypted=excluded.encrypted",
            (name, encrypted),
        )


def secret(name):
    with connection() as db:
        row = db.execute(
            "SELECT encrypted FROM integrations WHERE name=?", (name,)
        ).fetchone()
    return cipher().decrypt(row[0].encode()).decode() if row else os.getenv(name, "")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def login(password):
    expected = os.getenv("OWNER_PASSWORD", "")
    if len(expected) < 16:
        raise HTTPException(
            503, "Set OWNER_PASSWORD to at least 16 characters on the server"
        )
    if not secrets.compare_digest(password, expected):
        raise HTTPException(401, "Invalid password")
    token = secrets.token_urlsafe(40)
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute(
            "INSERT INTO sessions VALUES(?,?)", (digest(token), time.time() + 43200)
        )
    return token


def require_owner(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    with connection() as db:
        row = db.execute(
            "SELECT expires FROM sessions WHERE token_hash=?", (digest(token),)
        ).fetchone()
    if not row or row[0] < time.time():
        raise HTTPException(401, "Sign in to the production server")
