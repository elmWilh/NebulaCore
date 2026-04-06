# nebula_core/api/roles.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from fastapi import APIRouter, HTTPException, Query, Depends, Request

from ..db import SYSTEM_DB, get_client_db, get_connection
from .security import verify_staff_or_internal, require_session
from ..services.security_service import SecurityService

router = APIRouter(prefix="/roles", tags=["Roles"])
security_service = SecurityService()


def _normalize_role_name(name: str) -> str:
    token = str(name or "").strip().lower()
    token = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in token).strip("-_")
    if not token:
        raise HTTPException(status_code=400, detail="Invalid role name")
    return token


@router.get("/list")
def list_identity_roles(request: Request):
    # Any authenticated user can read role catalog for UI rendering.
    require_session(request)
    return security_service.list_roles_with_permissions()


@router.post("/create")
def create_identity_role(data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    # Supports both JSON body and legacy query style.
    role_name = (data or {}).get("name")
    description = (data or {}).get("description")
    is_staff = bool((data or {}).get("is_staff", False))
    if not role_name:
        role_name = data.get("role") if isinstance(data, dict) else None
    role_name = _normalize_role_name(role_name)
    description = str(description or "").strip() or None
    try:
        actor, _, _ = require_session(request)
    except Exception:
        actor = "system"

    with get_connection(SYSTEM_DB) as conn:
        conn.execute(
            """
            INSERT INTO identity_roles (name, description, is_staff, updated_by, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                is_staff=excluded.is_staff,
                updated_by=excluded.updated_by,
                updated_at=datetime('now')
            """,
            (role_name, description, 1 if is_staff else 0, actor),
        )
    permissions = (data or {}).get("permissions")
    if isinstance(permissions, list):
        security_service.set_role_permissions(role_name, permissions, actor)
    security_service.append_audit_event(
        event_kind="user",
        action="role.upsert",
        summary=f"Role {role_name} updated",
        severity="info",
        risk_level="medium" if not is_staff else "high",
        actor=actor,
        actor_db="system.db",
        target_type="role",
        target_id=role_name,
        details={"description": description, "is_staff": is_staff, "permissions": permissions if isinstance(permissions, list) else None},
    )
    return {"status": "upserted", "name": role_name, "description": description, "is_staff": is_staff, "permissions": permissions if isinstance(permissions, list) else []}


@router.post("/assign")
def assign_role(username: str, role_name: str, db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    # Legacy endpoint: keep compatibility for client DB roles/user_roles.
    try:
        with get_client_db(db_name) as conn:
            user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            role = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()

            if not user or not role:
                raise HTTPException(status_code=404, detail="User or Role not found")

            conn.execute(
                "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user["id"], role["id"]),
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"username": username, "role": role_name, "status": "assigned"}
