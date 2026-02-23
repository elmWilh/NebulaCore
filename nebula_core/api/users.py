# nebula_core/api/users.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os
import sqlite3
import hashlib
import hmac
import secrets
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Form, Response, Depends, Request
from ..services.user_service import UserService
from ..models.user import UserCreate
from ..db import (
    get_client_db,
    list_client_databases,
    get_connection,
    SYSTEM_DB,
    normalize_client_db_name,
    resolve_client_db_path,
)
from .security import create_session_token, require_session, verify_staff_or_internal
from ..utils.mailer import send_password_reset_code
import bcrypt
import pyotp

router = APIRouter(prefix="/users", tags=["Users"])
user_service = UserService()
logger = logging.getLogger("nebula_core.users")


def _session_from_request(request: Request):
    username, db_name, is_staff = require_session(request)
    return username, db_name, is_staff


def _normalize_role_tag(value: str) -> str:
    token = str(value or "").strip().lower()
    token = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in token).strip("-_")
    return token or "user"


def _role_is_staff(role_tag: str) -> bool:
    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            "SELECT is_staff FROM identity_roles WHERE name = ? LIMIT 1",
            (role_tag,),
        ).fetchone()
    return bool(row and row["is_staff"])


def _role_exists(role_tag: str) -> bool:
    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM identity_roles WHERE name = ? LIMIT 1",
            (role_tag,),
        ).fetchone()
    return bool(row)


def _get_user_row(conn, username: str):
    return conn.execute(
        "SELECT id, username, is_staff, is_active, two_factor_secret, two_factor_enabled FROM users WHERE username=?",
        (username,),
    ).fetchone()


def _db_name_variants(db_name: str):
    raw = str(db_name or "").strip()
    if not raw:
        return []
    variants = {raw}
    if raw.endswith(".db"):
        variants.add(raw[:-3])
    else:
        variants.add(f"{raw}.db")
    return [v for v in variants if v]


def _hash_reset_code(db_name: str, username: str, code: str) -> str:
    secret = (
        os.getenv("NEBULA_PASSWORD_RESET_SECRET")
        or os.getenv("NEBULA_SESSION_SECRET")
        or os.getenv("NEBULA_INSTALLER_TOKEN")
        or "nebula-reset-secret-dev"
    )
    payload = f"{db_name}:{username}:{code}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_user_location_for_reset(username: str, db_name: str = ""):
    clean_name = str(username or "").strip()
    if not clean_name:
        return None, None, None

    target_db = str(db_name or "").strip()
    if target_db:
        try:
            normalized = target_db if target_db == "system.db" else normalize_client_db_name(target_db)
        except Exception:
            return None, None, None
        conn_ctx = get_connection(SYSTEM_DB) if normalized == "system.db" else get_client_db(normalized, create_if_missing=False)
        with conn_ctx as conn:
            row = conn.execute(
                "SELECT username, email, is_active FROM users WHERE username = ? LIMIT 1",
                (clean_name,),
            ).fetchone()
        if not row:
            return normalized, None, None
        return normalized, row["email"], bool(row["is_active"])

    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            "SELECT username, email, is_active FROM users WHERE username = ? LIMIT 1",
            (clean_name,),
        ).fetchone()
    if row:
        return "system.db", row["email"], bool(row["is_active"])

    for candidate in list_client_databases():
        try:
            with get_client_db(candidate, create_if_missing=False) as conn:
                row = conn.execute(
                    "SELECT username, email, is_active FROM users WHERE username = ? LIMIT 1",
                    (clean_name,),
                ).fetchone()
            if row:
                return candidate, row["email"], bool(row["is_active"])
        except Exception:
            continue
    return None, None, None


def _resolve_requester_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")

@router.get("/databases")
def get_available_databases(_=Depends(verify_staff_or_internal)):
    return {"databases": list_client_databases()}

@router.get("/list")
def list_users(db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    try:
        normalized_db = normalize_client_db_name(db_name)
        db_path, normalized_db = resolve_client_db_path(normalized_db)
        if normalized_db not in list_client_databases():
            raise HTTPException(status_code=404, detail="Database not found")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT id, username, email, is_staff FROM users").fetchall()
        finally:
            conn.close()
        users = [dict(row) for row in rows]
        variants = _db_name_variants(normalized_db)
        with get_connection(SYSTEM_DB) as sys_conn:
            if variants:
                placeholders = ", ".join("?" for _ in variants)
                query = f"SELECT username, role_tag, db_name FROM user_identity_tags WHERE db_name IN ({placeholders})"
                tag_rows = sys_conn.execute(query, tuple(variants)).fetchall()
            else:
                tag_rows = []
        tag_map = {}
        for r in tag_rows:
            existing = tag_map.get(r["username"])
            if existing is None or r["db_name"] == normalized_db:
                tag_map[r["username"]] = r["role_tag"]
        for u in users:
            u["role_tag"] = tag_map.get(u["username"], "user")
        return users
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/identity-tag")
def get_user_identity_tag(
    request: Request,
    username: str = Query(...),
    db_name: str = Query(None),
):
    session_user, session_db, is_staff = _session_from_request(request)
    target_db = db_name or session_db
    normalized_db = target_db if target_db == "system.db" else normalize_client_db_name(target_db)
    session_db_norm = session_db if session_db == "system.db" else normalize_client_db_name(session_db)
    if not is_staff and (username != session_user or normalized_db != session_db_norm):
        raise HTTPException(status_code=403, detail="Forbidden")
    variants = _db_name_variants(normalized_db)
    with get_connection(SYSTEM_DB) as conn:
        if variants:
            placeholders = ", ".join("?" for _ in variants)
            row = conn.execute(
                f"SELECT role_tag, updated_by, updated_at, db_name FROM user_identity_tags "
                f"WHERE username = ? AND db_name IN ({placeholders}) "
                "ORDER BY CASE WHEN db_name = ? THEN 0 ELSE 1 END LIMIT 1",
                (username, *variants, normalized_db),
            ).fetchone()
        else:
            row = None
    if not row:
        return {"username": username, "db_name": normalized_db, "role_tag": "user", "updated_by": None, "updated_at": None}
    payload = dict(row)
    payload.pop("db_name", None)
    return {"username": username, "db_name": normalized_db, **payload}


@router.post("/identity-tag")
def set_user_identity_tag(
    data: dict,
    request: Request,
):
    session_user, _, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")

    username = str((data or {}).get("username") or "").strip()
    db_name = str((data or {}).get("db_name") or "").strip()
    normalized_db = db_name if db_name == "system.db" else normalize_client_db_name(db_name)
    role_tag = str((data or {}).get("role_tag") or "user").strip().lower()
    if not username or not db_name:
        raise HTTPException(status_code=400, detail="username and db_name are required")

    with get_connection(SYSTEM_DB) as conn:
        conn.execute(
            """
            INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(db_name, username) DO UPDATE SET
                role_tag=excluded.role_tag,
                updated_by=excluded.updated_by,
                updated_at=datetime('now')
            """,
            (normalized_db, username, role_tag, session_user),
        )
    return {"status": "updated", "username": username, "db_name": normalized_db, "role_tag": role_tag}


@router.get("/detail/{username}")
def user_detail(
    username: str,
    request: Request,
    db_name: str = Query(None)
):
    session_user, session_db, is_staff = _session_from_request(request)
    target_db = db_name or session_db

    try:
        normalized_db = target_db if target_db == "system.db" else normalize_client_db_name(target_db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        session_db_norm = session_db if session_db == "system.db" else normalize_client_db_name(session_db)
    except ValueError:
        session_db_norm = session_db

    if not is_staff and (username != session_user or normalized_db != session_db_norm):
        raise HTTPException(status_code=403, detail="Forbidden")

    if normalized_db == "system.db":
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, username, email, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()
    else:
        db_path, resolved_name = resolve_client_db_path(normalized_db)
        available = {name.lower() for name in list_client_databases()}
        if resolved_name.lower() not in available:
            raise HTTPException(status_code=404, detail="Database not found")
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            raise HTTPException(status_code=500, detail="Failed to open database")
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, username, email, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()
        finally:
            conn.close()
        normalized_db = resolved_name

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    db_label = normalized_db.replace(".db", "")
    variants = _db_name_variants(normalized_db)
    with get_connection(SYSTEM_DB) as conn:
        if variants:
            placeholders = ", ".join("?" for _ in variants)
            role_row = conn.execute(
                f"SELECT role_tag, db_name FROM user_identity_tags WHERE username = ? AND db_name IN ({placeholders}) "
                "ORDER BY CASE WHEN db_name = ? THEN 0 ELSE 1 END LIMIT 1",
                (username, *variants, normalized_db),
            ).fetchone()
        else:
            role_row = None
    role_tag = role_row["role_tag"] if role_row and role_row["role_tag"] else ("admin" if bool(row["is_staff"]) else "user")
    return {
        "id": row["id"],
        "username": row["username"],
        "is_staff": bool(row["is_staff"]),
        "role_tag": role_tag,
        "is_active": bool(row["is_active"]),
        "db_name": normalized_db,
        "email": (row["email"] if "email" in row.keys() and row["email"] else f"{row['username']}@{db_label}.nebula.local"),
    }

@router.post("/login")
def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    otp: str = Form(default=""),
    db_name: str = Query("system.db")
):
    secure_cookie = os.getenv("NEBULA_COOKIE_SECURE", "false").strip().lower() == "true"
    try:
        resolved_db = db_name
        if db_name == "system.db":
            with get_connection(SYSTEM_DB) as conn:
                row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        else:
            normalized_db = normalize_client_db_name(db_name)
            db_path, resolved_db = resolve_client_db_path(normalized_db)
            available = {name.lower() for name in list_client_databases()}
            if resolved_db.lower() not in available:
                raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")

            # Login must not mutate client DB schema; open in read-only mode.
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
                if "password_hash" not in cols:
                    raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")
                select_cols = ["id", "username", "password_hash", "is_active", "is_staff"]
                if "password_set_required" in cols:
                    select_cols.append("password_set_required")
                if "two_factor_secret" in cols:
                    select_cols.append("two_factor_secret")
                if "two_factor_enabled" in cols:
                    select_cols.append("two_factor_enabled")
                row = conn.execute(
                    f"SELECT {', '.join(select_cols)} FROM users WHERE username=?",
                    (username,),
                ).fetchone()
            finally:
                conn.close()

        if not row or not user_service.verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")
        if bool(row["password_set_required"]) if "password_set_required" in row.keys() else False:
            raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")

        two_factor_enabled = bool(row["two_factor_enabled"]) if "two_factor_enabled" in row.keys() else False
        two_factor_secret = row["two_factor_secret"] if "two_factor_secret" in row.keys() else None
        if two_factor_enabled:
            if not otp or len(otp.strip()) == 0:
                raise HTTPException(status_code=401, detail="2FA_REQUIRED")
            if not two_factor_secret or not pyotp.TOTP(two_factor_secret).verify(otp.strip(), valid_window=1):
                raise HTTPException(status_code=401, detail="INVALID_2FA_CODE")

        session_token = create_session_token(username=username, db_name=resolved_db)
        response.set_cookie(
            key="nebula_session",
            value=session_token,
            httponly=True,
            max_age=3600,
            samesite="Lax",
            secure=secure_cookie,
        )
        return {"status": "authorized", "redirect": "/dashboard"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")


@router.post("/password-reset/request")
def password_reset_request(
    request: Request,
    username: str = Form(...),
    db_name: str = Form(default=""),
):
    clean_name = str(username or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="username is required")

    requester_ip = _resolve_requester_ip(request)
    ttl_sec = 120
    target_db, target_email, is_active = _resolve_user_location_for_reset(clean_name, db_name)

    # Always return generic response to avoid account enumeration.
    generic_response = {"status": "sent_if_exists", "ttl_sec": ttl_sec}
    if not target_db or not target_email or not bool(is_active):
        return generic_response

    with get_connection(SYSTEM_DB) as conn:
        recent = conn.execute(
            """
            SELECT id FROM password_reset_codes
            WHERE db_name = ? AND username = ? AND consumed_at IS NULL
              AND datetime(created_at) > datetime('now', '-30 seconds')
            ORDER BY id DESC LIMIT 1
            """,
            (target_db, clean_name),
        ).fetchone()
        if recent:
            return generic_response

        conn.execute(
            """
            UPDATE password_reset_codes
            SET consumed_at = datetime('now')
            WHERE db_name = ? AND username = ? AND consumed_at IS NULL
            """,
            (target_db, clean_name),
        )

        code = f"{secrets.randbelow(1000000):06d}"
        code_hash = _hash_reset_code(target_db, clean_name, code)
        expires_at = (_utc_now() + timedelta(seconds=ttl_sec)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO password_reset_codes (db_name, username, email, code_hash, expires_at, requester_ip)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (target_db, clean_name, target_email, code_hash, expires_at, requester_ip),
        )

    try:
        delivered = send_password_reset_code(to_email=target_email, username=clean_name, code=code, ttl_sec=ttl_sec)
        if not delivered:
            logger.warning("Password reset email delivery failed for user=%s db=%s", clean_name, target_db)
    except Exception as exc:
        logger.warning("Password reset email send error for user=%s db=%s: %s", clean_name, target_db, exc)
    return generic_response


@router.post("/password-reset/confirm")
def password_reset_confirm(
    username: str = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    db_name: str = Form(default=""),
):
    clean_name = str(username or "").strip()
    clean_code = str(code or "").strip()
    clean_password = str(new_password or "")
    if not clean_name or not clean_code or not clean_password:
        raise HTTPException(status_code=400, detail="username, code and new_password are required")
    if len(clean_password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters")

    target_db, _, _ = _resolve_user_location_for_reset(clean_name, db_name)
    if not target_db:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            """
            SELECT id, code_hash, attempts
            FROM password_reset_codes
            WHERE db_name = ? AND username = ?
              AND consumed_at IS NULL
              AND datetime(expires_at) >= datetime('now')
            ORDER BY id DESC LIMIT 1
            """,
            (target_db, clean_name),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Invalid or expired code")

        expected_hash = str(row["code_hash"] or "")
        provided_hash = _hash_reset_code(target_db, clean_name, clean_code)
        if not hmac.compare_digest(expected_hash, provided_hash):
            attempts = int(row["attempts"] or 0) + 1
            conn.execute(
                "UPDATE password_reset_codes SET attempts = ? WHERE id = ?",
                (attempts, int(row["id"])),
            )
            if attempts >= 5:
                conn.execute(
                    "UPDATE password_reset_codes SET consumed_at = datetime('now') WHERE id = ?",
                    (int(row["id"]),),
                )
            raise HTTPException(status_code=400, detail="Invalid or expired code")

        conn.execute(
            "UPDATE password_reset_codes SET consumed_at = datetime('now') WHERE id = ?",
            (int(row["id"]),),
        )

    conn_ctx = get_connection(SYSTEM_DB) if target_db == "system.db" else get_client_db(target_db, create_if_missing=False)
    with conn_ctx as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE username = ? LIMIT 1",
            (clean_name,),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        password_hash = user_service.hash_password(clean_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, password_set_required = 0 WHERE username = ?",
            (password_hash, clean_name),
        )

    return {"status": "password_updated"}


@router.get("/2fa/status")
def user_2fa_status(request: Request):
    username, db_name, _ = _session_from_request(request)
    with (get_connection(SYSTEM_DB) if db_name == "system.db" else get_client_db(db_name, create_if_missing=False)) as conn:
        row = _get_user_row(conn, username)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return {"enabled": bool(row["two_factor_enabled"])}


@router.post("/2fa/setup")
def user_2fa_setup(request: Request):
    username, db_name, _ = _session_from_request(request)
    with (get_connection(SYSTEM_DB) if db_name == "system.db" else get_client_db(db_name, create_if_missing=False)) as conn:
        row = _get_user_row(conn, username)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        secret = pyotp.random_base32()
        conn.execute(
            "UPDATE users SET two_factor_secret = ?, two_factor_enabled = 0 WHERE username = ?",
            (secret, username),
        )
        conn.commit()

        issuer = "Nebula Panel"
        account = f"{username}@{db_name.replace('.db', '')}"
        otpauth_uri = pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)
        return {"secret": secret, "otpauth_uri": otpauth_uri}


@router.post("/2fa/confirm")
def user_2fa_confirm(request: Request, code: str = Form(...)):
    username, db_name, _ = _session_from_request(request)
    with (get_connection(SYSTEM_DB) if db_name == "system.db" else get_client_db(db_name, create_if_missing=False)) as conn:
        row = _get_user_row(conn, username)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        secret = row["two_factor_secret"]
        if not secret:
            raise HTTPException(status_code=400, detail="2FA_SETUP_NOT_STARTED")
        if not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
            raise HTTPException(status_code=400, detail="INVALID_2FA_CODE")

        conn.execute("UPDATE users SET two_factor_enabled = 1 WHERE username = ?", (username,))
        conn.commit()
        return {"status": "enabled"}


@router.post("/2fa/disable")
def user_2fa_disable(request: Request, code: str = Form(...)):
    username, db_name, _ = _session_from_request(request)
    with (get_connection(SYSTEM_DB) if db_name == "system.db" else get_client_db(db_name, create_if_missing=False)) as conn:
        row = _get_user_row(conn, username)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if not bool(row["two_factor_enabled"]):
            return {"status": "already_disabled"}
        secret = row["two_factor_secret"]
        if not secret or not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
            raise HTTPException(status_code=400, detail="INVALID_2FA_CODE")

        conn.execute(
            "UPDATE users SET two_factor_enabled = 0, two_factor_secret = NULL WHERE username = ?",
            (username,),
        )
        conn.commit()
        return {"status": "disabled"}

@router.post("/create")
def register_user(data: dict, db_name: str = Query(...), request: Request = None, _=Depends(verify_staff_or_internal)):
    username = str((data or {}).get("username") or "").strip()
    email = str((data or {}).get("email") or "").strip() or None
    password = str((data or {}).get("password") or "")
    role_tag = _normalize_role_tag((data or {}).get("role_tag") or (data or {}).get("role") or "user")
    if not _role_exists(role_tag):
        raise HTTPException(status_code=400, detail=f"Unknown role_tag '{role_tag}'. Create role first.")
    is_staff = _role_is_staff(role_tag) or bool((data or {}).get("is_staff"))
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    payload = UserCreate(username=username, email=email, password=password, is_staff=is_staff)
    actor = "system"
    if request is not None:
        try:
            actor, _, _ = _session_from_request(request)
        except Exception:
            actor = "system"
    try:
        normalized_db = normalize_client_db_name(db_name)
        with get_client_db(normalized_db) as conn:
            user = user_service.create_user(conn, payload)
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(db_name, username) DO UPDATE SET
                    role_tag=excluded.role_tag,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (normalized_db, username, role_tag, actor),
            )
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_staff": bool(user.is_staff),
            "role_tag": role_tag,
            "db_name": normalized_db,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registration failed: {str(e)}")

@router.post("/update")
def update_user(data: dict, _=Depends(verify_staff_or_internal)):
    source_db = data.get("source_db")
    target_db = data.get("target_db")
    old_name = data.get("old_username")
    new_name = data.get("new_username")
    email = str(data.get("email") or "").strip() or None
    new_password = data.get("new_password")
    role_tag = _normalize_role_tag(data.get("role_tag") or data.get("role") or "user")
    if not _role_exists(role_tag):
        raise HTTPException(status_code=400, detail=f"Unknown role_tag '{role_tag}'. Create role first.")
    is_staff = 1 if _role_is_staff(role_tag) else 0
    is_active = 1 if bool(data.get("is_active", True)) else 0

    try:
        normalized_source_db = normalize_client_db_name(source_db)
        normalized_target_db = normalize_client_db_name(target_db)
        with get_client_db(normalized_source_db) as conn_src:
            user = conn_src.execute("SELECT * FROM users WHERE username=?", (old_name,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found in source sector")

            if new_password and len(new_password.strip()) > 0:
                p_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            else:
                p_hash = user["password_hash"]

            current_email = user["email"] if "email" in user.keys() else None
            new_email = email if email is not None else current_email

            if normalized_source_db == normalized_target_db:
                conn_src.execute(
                    """
                    UPDATE users
                    SET username=?, email=?, password_hash=?, is_staff=?, is_active=?
                    WHERE username=?
                    """,
                    (new_name, new_email, p_hash, is_staff, is_active, old_name),
                )
                conn_src.commit()
                with get_connection(SYSTEM_DB) as sys_conn:
                    sys_conn.execute(
                        """
                        INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(db_name, username) DO UPDATE SET
                            role_tag=excluded.role_tag,
                            updated_by=excluded.updated_by,
                            updated_at=datetime('now')
                        """,
                        (normalized_source_db, new_name, role_tag, "system"),
                    )
                    if old_name != new_name:
                        sys_conn.execute(
                            "DELETE FROM user_identity_tags WHERE db_name = ? AND username = ?",
                            (normalized_source_db, old_name),
                        )
                return {"status": "updated", "location": "local"}

            with get_client_db(normalized_target_db) as conn_dst:
                exists = conn_dst.execute("SELECT id FROM users WHERE username=?", (new_name,)).fetchone()
                if exists:
                    raise HTTPException(status_code=400, detail="Identity collision in target sector")

                try:
                    conn_dst.execute(
                        """
                        INSERT INTO users (username, email, password_hash, is_staff)
                        VALUES (?, ?, ?, ?)
                        """,
                        (new_name, new_email, p_hash, is_staff),
                    )
                    conn_dst.execute(
                        "UPDATE users SET is_active = ? WHERE username = ?",
                        (is_active, new_name),
                    )
                    conn_dst.commit()

                    conn_src.execute("DELETE FROM users WHERE username=?", (old_name,))
                    conn_src.commit()
                    with get_connection(SYSTEM_DB) as sys_conn:
                        sys_conn.execute(
                            """
                            INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
                            VALUES (?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(db_name, username) DO UPDATE SET
                                role_tag=excluded.role_tag,
                                updated_by=excluded.updated_by,
                                updated_at=datetime('now')
                            """,
                            (normalized_target_db, new_name, role_tag, "system"),
                        )
                        sys_conn.execute(
                            "DELETE FROM user_identity_tags WHERE db_name = ? AND username = ?",
                            (normalized_source_db, old_name),
                        )
                    return {"status": "moved", "location": normalized_target_db}
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Migration fatal error: {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/terminate")
def delete_user(username: str = Query(...), db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    try:
        with get_client_db(db_name) as conn:
            exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Target not found")
            
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            conn.commit()
            return {"status": "terminated", "target": username}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("nebula_session")
    return {"status": "logged_out"}
