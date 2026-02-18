# nebula_core/api/plugins.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .security import verify_staff_or_internal
from ..core.context import context

router = APIRouter(prefix="/system/plugins", tags=["Plugins"])


class PluginSyncRequest(BaseModel):
    dry_run: bool = Field(default=True)
    users: Optional[list[dict]] = None
    limit: int = Field(default=0, ge=0, le=10000)


def _authorize(request: Request, token: Optional[str]):
    verify_staff_or_internal(request, token)


def _get_manager():
    manager = getattr(context, "plugin_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager is unavailable")
    return manager


@router.get("")
async def list_plugins(request: Request, x_nebula_token: Optional[str] = Header(default=None)):
    _authorize(request, x_nebula_token)
    manager = _get_manager()
    return {"plugins": manager.list_plugins()}


@router.post("/rescan")
async def rescan_plugins(request: Request, x_nebula_token: Optional[str] = Header(default=None)):
    _authorize(request, x_nebula_token)
    manager = _get_manager()
    plugins = await manager.rescan()
    return {"status": "ok", "plugins": plugins}


@router.get("/{plugin_name}/health")
async def plugin_health(plugin_name: str, request: Request, x_nebula_token: Optional[str] = Header(default=None)):
    _authorize(request, x_nebula_token)
    manager = _get_manager()
    try:
        result = await manager.plugin_health(plugin_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"plugin": plugin_name, "health": result}


@router.post("/{plugin_name}/sync-users")
async def plugin_sync_users(
    plugin_name: str,
    request: Request,
    payload: PluginSyncRequest,
    x_nebula_token: Optional[str] = Header(default=None),
):
    _authorize(request, x_nebula_token)
    manager = _get_manager()

    sync_payload: Dict[str, Any] = {
        "dry_run": bool(payload.dry_run),
        "users": payload.users,
        "limit": int(payload.limit or 0),
    }
    try:
        result = await manager.plugin_sync_users(plugin_name, sync_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"plugin": plugin_name, "result": result}
