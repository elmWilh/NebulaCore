# nebula_core/api/users.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os

from fastapi import APIRouter, HTTPException, Query, Form, Response, Depends, Request
from ..services.user_service import UserService
from ..models.user import UserCreate
from ..db import get_client_db, list_client_databases, get_connection, SYSTEM_DB, normalize_client_db_name
from .security import create_session_token, require_session, verify_staff_or_internal
import bcrypt
import pyotp

router = APIRouter(prefix="/users", tags=["Users"])
user_service = UserService()


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

@router.get("/databases")
def get_available_databases(_=Depends(verify_staff_or_internal)):
    return {"databases": list_client_databases()}

@router.get("/list")
def list_users(db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    try:
        normalized_db = normalize_client_db_name(db_name)
        with get_client_db(normalized_db) as conn:
            rows = conn.execute("SELECT id, username, email, is_staff FROM users").fetchall()
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

    if not is_staff and (username != session_user or target_db != session_db):
        raise HTTPException(status_code=403, detail="Forbidden")

    normalized_db = target_db if target_db == "system.db" else normalize_client_db_name(target_db)

    if normalized_db == "system.db":
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, username, email, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()
    else:
        if normalized_db not in list_client_databases():
            raise HTTPException(status_code=404, detail="Database not found")
        with get_client_db(normalized_db, create_if_missing=False) as conn:
            row = conn.execute(
                "SELECT id, username, email, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()

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
        conn_ctx = get_connection(SYSTEM_DB) if db_name == "system.db" else get_client_db(db_name, create_if_missing=False)
        with conn_ctx as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not user_service.verify_password(password, row["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")

            if bool(row["two_factor_enabled"]):
                if not otp or len(otp.strip()) == 0:
                    raise HTTPException(status_code=401, detail="2FA_REQUIRED")
                if not row["two_factor_secret"] or not pyotp.TOTP(row["two_factor_secret"]).verify(otp.strip(), valid_window=1):
                    raise HTTPException(status_code=401, detail="INVALID_2FA_CODE")

            session_token = create_session_token(username=username, db_name=db_name)
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
