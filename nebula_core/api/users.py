# nebula_core/api/users.py
import os

from fastapi import APIRouter, HTTPException, Query, Form, Response, Depends, Request
from ..services.user_service import UserService
from ..models.user import UserCreate
from ..db import get_client_db, list_client_databases, get_connection, SYSTEM_DB
from .security import create_session_token, require_session, verify_staff_or_internal
import bcrypt
import pyotp

router = APIRouter(prefix="/users", tags=["Users"])
user_service = UserService()


def _session_from_request(request: Request):
    username, db_name, is_staff = require_session(request)
    return username, db_name, is_staff


def _get_user_row(conn, username: str):
    return conn.execute(
        "SELECT id, username, is_staff, is_active, two_factor_secret, two_factor_enabled FROM users WHERE username=?",
        (username,),
    ).fetchone()

@router.get("/databases")
def get_available_databases(_=Depends(verify_staff_or_internal)):
    return {"databases": list_client_databases()}

@router.get("/list")
def list_users(db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    try:
        with get_client_db(db_name) as conn:
            rows = conn.execute("SELECT id, username, is_staff FROM users").fetchall()
            return [dict(row) for row in rows]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

    if target_db == "system.db":
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, username, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()
    else:
        if target_db not in list_client_databases():
            raise HTTPException(status_code=404, detail="Database not found")
        with get_client_db(target_db, create_if_missing=False) as conn:
            row = conn.execute(
                "SELECT id, username, is_staff, is_active FROM users WHERE username=?",
                (username,)
            ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    db_label = target_db.replace(".db", "")
    return {
        "id": row["id"],
        "username": row["username"],
        "is_staff": bool(row["is_staff"]),
        "is_active": bool(row["is_active"]),
        "db_name": target_db,
        "email": f"{row['username']}@{db_label}.nebula.local",
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
def register_user(data: UserCreate, db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    try:
        with get_client_db(db_name) as conn:
            user = user_service.create_user(conn, data)
            return user
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
    new_password = data.get("new_password")
    role = data.get("role")

    is_staff = 1 if role in ["staff", "moderator"] else 0

    try:
        with get_client_db(source_db) as conn_src:
            user = conn_src.execute("SELECT * FROM users WHERE username=?", (old_name,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found in source sector")

            if new_password and len(new_password.strip()) > 0:
                p_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            else:
                p_hash = user["password_hash"]

            if source_db == target_db:
                conn_src.execute(
                    """
                    UPDATE users
                    SET username=?, password_hash=?, is_staff=?
                    WHERE username=?
                    """,
                    (new_name, p_hash, is_staff, old_name),
                )
                conn_src.commit()
                return {"status": "updated", "location": "local"}

            with get_client_db(target_db) as conn_dst:
                exists = conn_dst.execute("SELECT id FROM users WHERE username=?", (new_name,)).fetchone()
                if exists:
                    raise HTTPException(status_code=400, detail="Identity collision in target sector")

                try:
                    conn_dst.execute(
                        """
                        INSERT INTO users (username, password_hash, is_staff)
                        VALUES (?, ?, ?)
                        """,
                        (new_name, p_hash, is_staff),
                    )
                    conn_dst.commit()

                    conn_src.execute("DELETE FROM users WHERE username=?", (old_name,))
                    conn_src.commit()
                    return {"status": "moved", "location": target_db}
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
