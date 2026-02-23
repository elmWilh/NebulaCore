# nebula_core/api/security.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Optional, Tuple

from fastapi import Header, HTTPException, Request

from ..db import (
    SYSTEM_DB,
    get_connection,
    list_client_databases,
    resolve_client_db_path,
)

def _read_env_value(file_path: str, key: str) -> Optional[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def _resolve_internal_auth_key() -> str:
    env_key = os.getenv("NEBULA_INSTALLER_TOKEN")
    if env_key:
        return env_key

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for candidate in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "install", ".env"),
    ]:
        val = _read_env_value(candidate, "NEBULA_INSTALLER_TOKEN")
        if val:
            return val
    return ""


INTERNAL_AUTH_KEY = _resolve_internal_auth_key()


def _resolve_session_secret() -> str:
    env_key = os.getenv("NEBULA_SESSION_SECRET")
    if env_key:
        return env_key

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for candidate in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "install", ".env"),
    ]:
        val = _read_env_value(candidate, "NEBULA_SESSION_SECRET")
        if val:
            return val

    # Fallback keeps app operable, but all sessions are invalidated after restart.
    return secrets.token_urlsafe(32)


SESSION_SECRET = _resolve_session_secret().encode("utf-8")
SESSION_TTL_SECONDS = int(os.getenv("NEBULA_SESSION_TTL_SECONDS", "3600"))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * ((4 - (len(raw) % 4)) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def create_session_token(username: str, db_name: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    payload = {
        "u": username,
        "d": db_name,
        "exp": int(time.time()) + max(60, int(ttl_seconds)),
        "n": secrets.token_hex(8),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(SESSION_SECRET, payload_raw, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_raw)}.{_b64url_encode(sig)}"


def parse_session_cookie(raw_cookie: Optional[str]) -> Optional[Tuple[str, str]]:
    if not raw_cookie or "." not in raw_cookie:
        return None
    try:
        payload_b64, sig_b64 = raw_cookie.split(".", 1)
        payload_raw = _b64url_decode(payload_b64)
        sig_raw = _b64url_decode(sig_b64)
    except Exception:
        return None

    expected_sig = hmac.new(SESSION_SECRET, payload_raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig_raw, expected_sig):
        return None

    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception:
        return None

    username = str(payload.get("u") or "").strip()
    db_name = str(payload.get("d") or "").strip()
    exp = int(payload.get("exp") or 0)
    if not username or not db_name or exp <= int(time.time()):
        return None
    return username, db_name


def get_session_context(raw_cookie: Optional[str]) -> Optional[Tuple[str, str, bool]]:
    parsed = parse_session_cookie(raw_cookie)
    if not parsed:
        return None
    username, db_name = parsed

    try:
        if db_name == "system.db":
            with get_connection(SYSTEM_DB) as conn:
                row = conn.execute(
                    "SELECT is_staff, is_active FROM users WHERE username = ? LIMIT 1",
                    (username,),
                ).fetchone()
            if not row or not bool(row["is_active"]):
                return None
            return username, db_name, bool(row["is_staff"])

        db_path, resolved_name = resolve_client_db_path(db_name)
        available = {name.lower() for name in list_client_databases()}
        if resolved_name.lower() not in available:
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT is_active FROM users WHERE username = ? LIMIT 1",
                (username,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not bool(row["is_active"]):
            return None
        return username, resolved_name, False
    except Exception:
        return None


def require_session(request: Request) -> Tuple[str, str, bool]:
    ctx = get_session_context(request.cookies.get("nebula_session"))
    if not ctx:
        raise HTTPException(status_code=401, detail="No active session")
    return ctx


def is_staff_session(raw_cookie: Optional[str]) -> bool:
    ctx = get_session_context(raw_cookie)
    return bool(ctx and ctx[2])


def verify_staff_or_internal(
    request: Request,
    x_nebula_token: Optional[str] = Header(default=None),
):
    if INTERNAL_AUTH_KEY and x_nebula_token == INTERNAL_AUTH_KEY:
        return {"auth": "internal"}

    if is_staff_session(request.cookies.get("nebula_session")):
        return {"auth": "staff"}

    raise HTTPException(status_code=403, detail="Forbidden")
