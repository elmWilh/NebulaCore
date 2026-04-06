from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .security import require_session, verify_staff_or_internal
from ..services.security_service import SecurityService

router = APIRouter(prefix="/security", tags=["Security"])
security_service = SecurityService()


def _actor_from_request(request: Request) -> tuple[str, str]:
    try:
        username, db_name, _ = require_session(request)
        return username, db_name
    except Exception:
        return "system", "system.db"


@router.get("/overview")
def security_overview(request: Request, db_name: str = Query(default="system.db"), _=Depends(verify_staff_or_internal)):
    require_session(request)
    return security_service.build_access_control_overview(db_name=db_name)


@router.get("/permissions")
def security_permissions(request: Request, _=Depends(verify_staff_or_internal)):
    require_session(request)
    return security_service.list_permissions()


@router.post("/permissions")
def security_permission_upsert(data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    actor, actor_db = _actor_from_request(request)
    try:
        payload = security_service.upsert_permission(data or {}, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    security_service.append_audit_event(
        event_kind="user",
        action="permission.upsert",
        summary=f"Permission {payload['key']} updated",
        severity="info",
        risk_level="high",
        actor=actor,
        actor_db=actor_db,
        source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
        target_type="permission",
        target_id=payload["key"],
        details=payload,
    )
    return {"status": "upserted", **payload}


@router.get("/roles")
def security_roles(request: Request, _=Depends(verify_staff_or_internal)):
    require_session(request)
    return security_service.list_roles_with_permissions()


@router.post("/roles/{role_name}/permissions")
def security_role_permissions(role_name: str, data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    actor, actor_db = _actor_from_request(request)
    permissions = (data or {}).get("permissions")
    if not isinstance(permissions, list):
        raise HTTPException(status_code=400, detail="permissions must be an array")
    try:
        updated = security_service.set_role_permissions(role_name, permissions, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    security_service.append_audit_event(
        event_kind="user",
        action="role.permissions.update",
        summary=f"Permissions updated for role {role_name}",
        severity="info",
        risk_level="high",
        actor=actor,
        actor_db=actor_db,
        source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
        target_type="role",
        target_id=role_name,
        details={"permissions": updated},
    )
    return {"status": "updated", "role_name": role_name, "permissions": updated}


@router.get("/groups")
def security_groups(request: Request, _=Depends(verify_staff_or_internal)):
    require_session(request)
    groups = security_service.list_groups()
    return [
        {
            **group,
            "members": security_service.list_group_members(group["group_name"]),
            "container_access": security_service.list_group_container_access(group["group_name"]),
        }
        for group in groups
    ]


@router.post("/groups")
def security_group_upsert(data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    actor, actor_db = _actor_from_request(request)
    try:
        payload = security_service.upsert_group(data or {}, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    security_service.append_audit_event(
        event_kind="user",
        action="group.upsert",
        summary=f"Group {payload['group_name']} updated",
        severity="info",
        risk_level="high",
        actor=actor,
        actor_db=actor_db,
        source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
        target_type="group",
        target_id=payload["group_name"],
        details=payload,
    )
    return {"status": "upserted", **payload}


@router.post("/groups/{group_name}/members")
def security_group_members(group_name: str, data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    actor, actor_db = _actor_from_request(request)
    members = (data or {}).get("members")
    if not isinstance(members, list):
        raise HTTPException(status_code=400, detail="members must be an array")
    try:
        payload = security_service.set_group_members(group_name, members, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    security_service.append_audit_event(
        event_kind="user",
        action="group.members.update",
        summary=f"Updated members for group {group_name}",
        severity="info",
        risk_level="high",
        actor=actor,
        actor_db=actor_db,
        source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
        target_type="group",
        target_id=group_name,
        details={"members": payload},
    )
    return {"status": "updated", "group_name": group_name, "members": payload}


@router.post("/groups/{group_name}/containers")
def security_group_containers(group_name: str, data: dict, request: Request, _=Depends(verify_staff_or_internal)):
    actor, actor_db = _actor_from_request(request)
    items = (data or {}).get("container_access")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="container_access must be an array")
    try:
        payload = security_service.set_group_container_access(group_name, items, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    security_service.append_audit_event(
        event_kind="user",
        action="group.container_access.update",
        summary=f"Updated container access for group {group_name}",
        severity="info",
        risk_level="critical",
        actor=actor,
        actor_db=actor_db,
        source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
        target_type="group",
        target_id=group_name,
        details={"container_access": payload},
    )
    return {"status": "updated", "group_name": group_name, "container_access": payload}


@router.get("/users")
def security_users(request: Request, db_name: str = Query(default="system.db"), _=Depends(verify_staff_or_internal)):
    require_session(request)
    return security_service.list_access_users(db_name)


@router.get("/users/{username}/history")
def security_user_history(username: str, request: Request, db_name: str = Query(default="system.db"), _=Depends(verify_staff_or_internal)):
    require_session(request)
    return {
        "username": username,
        "db_name": db_name,
        "ip_history": security_service.list_user_ip_history(username, db_name),
        "audit_events": security_service.list_user_audit_events(limit=100, username=username, db_name=db_name),
        "groups": security_service.get_user_group_memberships(username, db_name),
    }


@router.get("/audit/users")
def security_audit_users(
    request: Request,
    limit: int = Query(default=100),
    username: str = Query(default=""),
    db_name: str = Query(default=""),
    risk_level: str = Query(default=""),
    _=Depends(verify_staff_or_internal),
):
    require_session(request)
    return security_service.list_user_audit_events(limit=limit, username=username, db_name=db_name, risk_level=risk_level)


@router.get("/audit/connections")
def security_audit_connections(
    request: Request,
    limit: int = Query(default=100),
    username: str = Query(default=""),
    service_name: str = Query(default=""),
    risk_level: str = Query(default=""),
    _=Depends(verify_staff_or_internal),
):
    require_session(request)
    return security_service.list_connection_audit_events(limit=limit, username=username, service_name=service_name, risk_level=risk_level)


@router.get("/audit/export")
def security_audit_export(
    request: Request,
    kind: str = Query(default="users"),
    limit: int = Query(default=1000),
    _=Depends(verify_staff_or_internal),
):
    require_session(request)
    csv_payload = security_service.export_csv(kind=kind, limit=limit)
    safe_kind = "connections" if str(kind).strip().lower() == "connections" else "users"
    return Response(
        content=csv_payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=nebula-audit-{safe_kind}.csv"},
    )
