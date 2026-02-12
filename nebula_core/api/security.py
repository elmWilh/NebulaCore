import os
from typing import Optional, Tuple

from fastapi import Header, HTTPException, Request

from ..db import SYSTEM_DB, get_connection

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


def parse_session_cookie(raw_cookie: Optional[str]) -> Optional[Tuple[str, str]]:
    if not raw_cookie or ":" not in raw_cookie:
        return None
    username, db_name = raw_cookie.split(":", 1)
    if not username or not db_name:
        return None
    return username, db_name


def is_staff_session(raw_cookie: Optional[str]) -> bool:
    parsed = parse_session_cookie(raw_cookie)
    if not parsed:
        return False
    username, db_name = parsed
    if db_name != "system.db":
        return False
    try:
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE username = ? AND is_staff = 1 AND is_active = 1 LIMIT 1",
                (username,),
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def verify_staff_or_internal(
    request: Request,
    x_nebula_token: Optional[str] = Header(default=None),
):
    if INTERNAL_AUTH_KEY and x_nebula_token == INTERNAL_AUTH_KEY:
        return {"auth": "internal"}

    if is_staff_session(request.cookies.get("nebula_session")):
        return {"auth": "staff"}

    raise HTTPException(status_code=403, detail="Forbidden")
