# nebula_core/api/admin.py
import os
from typing import Annotated
from fastapi import APIRouter, HTTPException, Header, Depends, Request, Form, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, StringConstraints, Field

from ..db import get_connection, SYSTEM_DB
from ..services.user_service import UserService
from .security import create_session_token
import pyotp

router = APIRouter(prefix="/system/internal/core", tags=["System-Security"])
user_service = UserService()

INTERNAL_AUTH_KEY = os.getenv("NEBULA_INSTALLER_TOKEN", "LOCAL_DEV_KEY_2026")

AdminUsername = Annotated[str, StringConstraints(
    min_length=5, 
    max_length=32, 
    pattern=r"^[a-zA-Z0-9_]+$"
)]

AdminPassword = Annotated[str, StringConstraints(min_length=12)]

class AdminCreate(BaseModel):
    username: AdminUsername
    password: AdminPassword
    security_clearance: int = Field(default=10, ge=1, le=100)

class AdminUpdate(BaseModel):
    new_password: AdminPassword | None = None
    is_active: bool | None = None

def verify_internal_access(x_nebula_token: str = Header(None)):
    if not x_nebula_token or x_nebula_token != INTERNAL_AUTH_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

@router.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    return HTMLResponse("<html><body><h3>Nebula Core Admin Login Endpoint</h3></body></html>")

@router.post("/login")
async def process_login(
    request: Request,
    response: Response,
    admin_id: str = Form(...),
    secure_key: str = Form(...),
    otp: str = Form(default="")
):
    with get_connection(SYSTEM_DB) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_staff = 1 AND is_active = 1", 
            (admin_id,)
        ).fetchone()

        if not user or not user_service.verify_password(secure_key, user["password_hash"]):
            raise HTTPException(status_code=401, detail="INVALID_ACCESS_KEY")

        if bool(user["two_factor_enabled"]):
            if not otp or len(otp.strip()) == 0:
                raise HTTPException(status_code=401, detail="2FA_REQUIRED")
            if not user["two_factor_secret"] or not pyotp.TOTP(user["two_factor_secret"]).verify(otp.strip(), valid_window=1):
                raise HTTPException(status_code=401, detail="INVALID_2FA_CODE")

        secure_cookie = os.getenv("NEBULA_COOKIE_SECURE", "false").strip().lower() == "true"
        response.set_cookie(
            key="nebula_session",
            value=create_session_token(username=admin_id, db_name="system.db"),
            httponly=True,
            max_age=3600,
            samesite="Lax",
            secure=secure_cookie,
        )
        return {"status": "authorized", "admin_id": admin_id}

@router.post("/init-admin")
def create_master_admin(data: AdminCreate, _=Depends(verify_internal_access)):
    with get_connection(SYSTEM_DB) as conn:
        check = conn.execute("SELECT id FROM users WHERE is_staff = 1 LIMIT 1").fetchone()
        if check:
            raise HTTPException(status_code=409, detail="Initialized")

        try:
            from ..models.user import UserCreate
            user_data = UserCreate(username=data.username, password=data.password)
            user_service.create_user(conn, user_data)
            
            conn.execute(
                "UPDATE users SET is_staff = 1, is_active = 1 WHERE username = ?", 
                (data.username,)
            )
            conn.commit()
            return {"status": "success"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@router.post("/modify-admin")
def update_admin_profile(target_username: str, data: AdminUpdate, _=Depends(verify_internal_access)):
    with get_connection(SYSTEM_DB) as conn:
        admin = conn.execute(
            "SELECT id FROM users WHERE username = ? AND is_staff = 1", 
            (target_username,)
        ).fetchone()
        
        if not admin:
            raise HTTPException(status_code=404, detail="Not Found")

        updates = []
        params = []

        if data.new_password:
            p_hash = user_service.hash_password(data.new_password)
            updates.append("password_hash = ?")
            params.append(p_hash)
        
        if data.is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if data.is_active else 0)

        if not updates:
            return {"status": "no_changes"}

        params.append(target_username)
        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ? AND is_staff = 1"
        
        conn.execute(query, params)
        conn.commit()
        return {"status": "success"}

@router.get("/status")
def get_system_health(_=Depends(verify_internal_access)):
    with get_connection(SYSTEM_DB) as conn:
        admin_count = conn.execute("SELECT COUNT(*) as count FROM users WHERE is_staff = 1").fetchone()
        return {
            "database": "system.db",
            "active_admins": admin_count["count"]
        }
