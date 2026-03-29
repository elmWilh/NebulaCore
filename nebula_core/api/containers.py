# nebula_core/api/containers.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import posixpath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from ..services.docker_service import DockerService
from ..core.context import context
from ..db import get_connection, SYSTEM_DB
from .security import require_session

router = APIRouter(prefix="/containers", tags=["Orchestration"])
docker_service = DockerService()


def _session_from_request(request: Request):
    return require_session(request)


def _can_access_container(username: str, db_name: str, is_staff: bool, container_id: str) -> bool:
    if is_staff:
        return True
    try:
        full_id = docker_service.resolve_container_id(container_id)
    except Exception:
        return False
    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM container_permissions
            WHERE username = ? AND container_id = ? AND (db_name = ? OR db_name IS NULL OR db_name = '')
            LIMIT 1
            """,
            (username, full_id, db_name or "system.db")
        ).fetchone()
    return bool(row)


async def _run_docker(callable_obj, *args, **kwargs):
    return await asyncio.to_thread(callable_obj, *args, **kwargs)


async def _can_access_container_async(username: str, db_name: str, is_staff: bool, container_id: str) -> bool:
    return await asyncio.to_thread(_can_access_container, username, db_name, is_staff, container_id)


def _forbidden_if(condition: bool, message: str):
    if condition:
        raise HTTPException(status_code=403, detail=message)


def _classify_deploy_error(raw_error: str):
    text = str(raw_error or "").strip()
    lower = text.lower()
    if "invalid_container_name" in lower or "invalid container name" in lower:
        return {
            "code": "invalid_container_name",
            "title": "Invalid Container Name",
            "summary": "Container name contains unsupported characters or trailing spaces.",
            "hint": "Use letters/numbers and . _ - only, no spaces.",
            "raw_error": text,
        }
    if "db_registration_failed" in lower:
        return {
            "code": "db_registration_failed",
            "title": "Database Registration Failed",
            "summary": "Container runtime was created but metadata could not be written to DB.",
            "hint": "Check database availability and permissions; runtime object was rolled back automatically.",
            "raw_error": text,
        }
    if "docker daemon not available" in lower:
        return {
            "code": "docker_unavailable",
            "title": "Docker Daemon Unavailable",
            "summary": "Nebula Core could not connect to Docker daemon.",
            "hint": "Ensure Docker is running and API socket is reachable.",
            "raw_error": text,
        }
    if "image not found" in lower or "pull access denied" in lower:
        return {
            "code": "image_unavailable",
            "title": "Docker Image Unavailable",
            "summary": "Image is missing locally and pull failed.",
            "hint": "Check image name/tag and registry access.",
            "raw_error": text,
        }
    if "port is already allocated" in lower:
        return {
            "code": "port_conflict",
            "title": "Port Conflict",
            "summary": "One or more host ports are already in use.",
            "hint": "Change port bindings in deployment settings.",
            "raw_error": text,
        }
    return {
        "code": "deploy_failed",
        "title": "Deployment Failed",
        "summary": "Container deployment failed due to runtime validation or Docker API error.",
        "hint": "Open full error log for technical details.",
        "raw_error": text,
    }


async def _effective_permissions(username: str, db_name: str, is_staff: bool, container_id: str):
    return await _run_docker(
        docker_service.get_effective_container_permissions,
        container_id,
        username,
        db_name,
        is_staff,
    )


async def _audit_container_event(container_id: str, actor: str, actor_db: str, action: str, details: dict | None = None):
    try:
        await _run_docker(
            docker_service.append_container_audit_log,
            container_id,
            action,
            actor,
            actor_db,
            details if isinstance(details, dict) else {},
        )
    except Exception as exc:
        context.logger.warning(f"Container audit log write failed for {container_id}: {exc}")

@router.get("/list")
async def list_containers(request: Request):
    username, db_name, is_staff = _session_from_request(request)
    
    try:
        return await _run_docker(docker_service.list_containers, username, db_name, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/summary")
async def containers_summary(request: Request):
    username, db_name, is_staff = _session_from_request(request)
    try:
        return await _run_docker(docker_service.get_usage_summary, username, db_name, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/detail/{container_id}")
async def container_detail(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.get_container_detail, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/profile/{container_id}")
async def container_profile_policy(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        policy = await _run_docker(docker_service.get_profile_policy, container_id)
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        policy["shell_allowed"] = bool(perms.get("allow_shell", False) and (is_staff or policy.get("shell_allowed_for_user", True)))
        policy["console_allowed"] = bool(perms.get("allow_console", True) and policy.get("app_console_supported", False))
        policy["settings_allowed"] = bool(perms.get("allow_settings", False))
        policy["explorer_allowed"] = bool(perms.get("allow_explorer", True))
        policy["role_tag"] = perms.get("role_tag", "user")
        policy["permissions"] = perms
        policy["is_staff"] = bool(is_staff)
        return policy
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/exec/{container_id}")
async def exec_container_command(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    command = (data or {}).get("command", "")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_shell", False):
            await _audit_container_event(container_id, username, db_name, "workspace.shell.denied", {"reason": "allow_shell=false"})
            raise HTTPException(status_code=403, detail="Shell access is disabled for your role")
        if not is_staff:
            policy = await _run_docker(docker_service.get_profile_policy, container_id)
            profile = policy.get("profile", "generic")
            ok, reason = await _run_docker(docker_service.validate_user_shell_command, command, profile)
            if not ok:
                await _audit_container_event(container_id, username, db_name, "workspace.shell.denied", {"reason": reason, "command": command[:180]})
                raise HTTPException(status_code=403, detail=reason)
        context.logger.info(f"Container exec requested by {username} on {container_id}: {command}")
        result = await _run_docker(docker_service.exec_command, container_id, command)
        await _audit_container_event(container_id, username, db_name, "workspace.shell.exec", {"command": command[:180], "exit_code": result.get("exit_code")})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/console-send/{container_id}")
async def send_container_console_command(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    command = (data or {}).get("command", "")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_console", True):
            await _audit_container_event(container_id, username, db_name, "workspace.console.denied", {"reason": "allow_console=false"})
            raise HTTPException(status_code=403, detail="Console access is disabled for your role")
        if not is_staff:
            policy = await _run_docker(docker_service.get_profile_policy, container_id)
            if not policy.get("app_console_supported", False):
                await _audit_container_event(container_id, username, db_name, "workspace.console.denied", {"reason": "profile_console_unsupported"})
                raise HTTPException(status_code=403, detail="Application console mode is not supported for this profile")
        context.logger.info(f"Container console input by {username} on {container_id}: {command}")
        result = await _run_docker(docker_service.send_console_input, container_id, command)
        await _audit_container_event(container_id, username, db_name, "workspace.console.send", {"command": command[:180], "transport": result.get("transport")})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/{container_id}")
async def list_container_files(container_id: str, request: Request, path: str = Query("/")):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_explorer", True):
            await _audit_container_event(container_id, username, db_name, "workspace.files.list.denied", {"path": path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        target_path = (path or "").strip() or "/"
        if target_path == "/" and not perms.get("allow_root_explorer", False):
            await _audit_container_event(container_id, username, db_name, "workspace.files.list.denied", {"path": target_path, "reason": "allow_root_explorer=false"})
            raise HTTPException(status_code=403, detail="Root explorer access is disabled for your role")
        result = await _run_docker(docker_service.list_files, container_id, path=path)
        await _audit_container_event(container_id, username, db_name, "workspace.files.list", {"path": result.get("path") or target_path, "entries": len(result.get("entries") or [])})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workspace-roots/{container_id}")
async def container_workspace_roots(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_explorer", True):
            await _audit_container_event(container_id, username, db_name, "workspace.roots.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        result = await _run_docker(docker_service.detect_workspace_roots, container_id)
        await _audit_container_event(container_id, username, db_name, "workspace.roots.inspect", {"roots": len(result.get("roots") or [])})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/{container_id}")
async def container_audit_log(container_id: str, request: Request, limit: int = Query(25)):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.list_container_audit_log, container_id, limit)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/file-content/{container_id}")
async def read_container_file(
    container_id: str,
    request: Request,
    path: str = Query(...),
    max_bytes: int = Query(200000)
):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_explorer", True):
            await _audit_container_event(container_id, username, db_name, "workspace.file.read.denied", {"path": path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        result = await _run_docker(docker_service.read_file, container_id, path=path, max_bytes=max_bytes)
        await _audit_container_event(container_id, username, db_name, "workspace.file.read", {"path": path, "truncated": bool(result.get("truncated"))})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download-file/{container_id}")
async def download_container_file(
    container_id: str,
    request: Request,
    path: str = Query(...),
    max_bytes: int = Query(500000)
):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.download.denied", {"path": path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.download.denied", {"path": path, "reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File download is disabled for your role")
        data = await _run_docker(docker_service.read_file, container_id, path=path, max_bytes=max_bytes)
        if data.get("truncated"):
            raise HTTPException(status_code=413, detail="File is too large to download from this panel")
        await _audit_container_event(container_id, username, db_name, "workspace.file.download", {"path": path, "size": len(data.get("content") or "")})
        target = data.get("path") or path
        file_name = posixpath.basename(target) or "file.txt"
        header_name = quote(file_name, safe="")
        return Response(
            content=(data.get("content") or "").encode("utf-8", errors="replace"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{header_name}"},
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save-file/{container_id}")
async def save_container_file(
    container_id: str,
    request: Request,
    data: dict,
    path: str = Query(...)
):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.write.denied", {"path": path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.write.denied", {"path": path, "reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        content = (data or {}).get("content", "")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="File content must be text")
        result = await _run_docker(docker_service.write_file, container_id, path=path, content=content)
        result["saved_by"] = username
        await _audit_container_event(container_id, username, db_name, "workspace.file.write", {"path": path, "chars": len(content)})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{container_id}")
async def get_container_settings(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "workspace.settings.view.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Settings access is disabled for your role")
        result = await _run_docker(docker_service.get_container_settings, container_id)
        await _audit_container_event(container_id, username, db_name, "workspace.settings.view", {})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/{container_id}")
async def update_container_settings(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "workspace.settings.update.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Settings access is disabled for your role")
        payload = data or {}
        has_command = bool(str(payload.get("startup_command", "")).strip())
        has_ports = bool(str(payload.get("allowed_ports", "")).strip())
        if has_command:
            if not perms.get("allow_edit_startup", False):
                await _audit_container_event(container_id, username, db_name, "workspace.settings.update.denied", {"reason": "allow_edit_startup=false"})
                raise HTTPException(status_code=403, detail="Editing startup command is disabled for your role")
        if has_ports:
            if not perms.get("allow_edit_ports", False):
                await _audit_container_event(container_id, username, db_name, "workspace.settings.update.denied", {"reason": "allow_edit_ports=false"})
                raise HTTPException(status_code=403, detail="Editing port selection is disabled for your role")
        context.logger.info(f"Container settings updated by {username} for {container_id}")
        result = await _run_docker(
            docker_service.update_container_settings,
            container_id=container_id,
            startup_command=payload.get("startup_command", ""),
            allowed_ports=payload.get("allowed_ports", ""),
            updated_by=username,
        )
        await _audit_container_event(container_id, username, db_name, "workspace.settings.update", {"startup_command": bool(str(payload.get("startup_command", "")).strip()), "allowed_ports": str(payload.get("allowed_ports", "")).strip()})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/restart-policy/{container_id}")
async def get_container_restart_policy(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "workspace.restart_policy.view.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Settings access is disabled for your role")
        result = await _run_docker(docker_service.get_restart_policy, container_id)
        await _audit_container_event(container_id, username, db_name, "workspace.restart_policy.view", {"restart_policy": result.get("restart_policy")})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart-policy/{container_id}")
async def update_container_restart_policy(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    policy = (data or {}).get("restart_policy", "no")
    retries = (data or {}).get("maximum_retry_count", 0)
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "workspace.restart_policy.update.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Settings access is disabled for your role")
        if not perms.get("allow_edit_startup", False):
            await _audit_container_event(container_id, username, db_name, "workspace.restart_policy.update.denied", {"reason": "allow_edit_startup=false"})
            raise HTTPException(status_code=403, detail="Editing startup settings is disabled for your role")
        context.logger.info(f"Restart policy updated by {username} for {container_id}: {policy}")
        result = await _run_docker(docker_service.update_restart_policy, container_id, policy, retries)
        await _audit_container_event(container_id, username, db_name, "workspace.restart_policy.update", {"restart_policy": policy, "maximum_retry_count": retries})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/deploy")
async def deploy_container(data: dict, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not is_staff:
        context.logger.warning(f"Unauthorized deploy attempt by {username}")
        raise HTTPException(status_code=403, detail="Staff clearance required")

    try:
        context.logger.info(f"Initiating deployment: {data.get('name')} by {username}")
        result = await _run_docker(docker_service.deploy, data)

        if context.event_bus:
            await context.event_bus.emit("container_deployed", {"id": result, "by": username})

        return {"status": "success", "id": result}
    except RuntimeError as e:
        detail = _classify_deploy_error(str(e))
        context.logger.error(f"Deployment failed (runtime): {detail.get('raw_error')}")
        status = 503 if detail.get("code") == "docker_unavailable" else 400
        raise HTTPException(status_code=status, detail=detail)
    except Exception as e:
        detail = _classify_deploy_error(str(e))
        context.logger.error(f"Deployment failed: {detail.get('raw_error')}")
        raise HTTPException(status_code=500, detail=detail)


@router.get("/presets")
async def list_container_presets(request: Request):
    _, _, _ = _session_from_request(request)
    try:
        return await _run_docker(docker_service.list_container_presets)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presets/{preset_name}")
async def get_container_preset(preset_name: str, request: Request):
    _, _, _ = _session_from_request(request)
    try:
        return await _run_docker(docker_service.get_container_preset, preset_name)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presets")
async def save_container_preset(data: dict, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    payload = data or {}
    try:
        return await _run_docker(
            docker_service.save_container_preset,
            payload.get("name"),
            payload.get("title"),
            payload.get("description"),
            payload.get("config"),
            payload.get("permissions"),
            username,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/permissions/{container_id}")
async def get_container_permissions(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _effective_permissions(username, db_name, is_staff, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/permissions/{container_id}")
async def update_container_permissions(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    if not await _can_access_container_async(username, db_name, True, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        result = await _run_docker(
            docker_service.set_container_access_policies,
            container_id,
            (data or {}).get("role_policies"),
            (data or {}).get("user_assignments"),
            username,
        )
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.permissions.update",
            {
                "role_policies": len((data or {}).get("role_policies") or {}),
                "user_assignments": len((data or {}).get("user_assignments") or []),
            },
        )
        return {"status": "updated", **result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart/{container_id}")
async def restart_container(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not is_staff and not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "container.lifecycle.restart.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Restart access is disabled for your role")
        context.logger.info(f"Restart requested for {container_id} by {username}")
        result = await _run_docker(docker_service.restart_container, container_id)
        await _audit_container_event(container_id, username, db_name, "container.lifecycle.restart", {"status": result.get("status")})
        return {"status": "restarted", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start/{container_id}")
async def start_container(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not is_staff and not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "container.lifecycle.start.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Start access is disabled for your role")
        context.logger.info(f"Start requested for {container_id} by {username}")
        result = await _run_docker(docker_service.start_container, container_id)
        await _audit_container_event(container_id, username, db_name, "container.lifecycle.start", {"status": result.get("status")})
        return {"status": "started", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop/{container_id}")
async def stop_container(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not is_staff and not perms.get("allow_settings", False):
            await _audit_container_event(container_id, username, db_name, "container.lifecycle.stop.denied", {"reason": "allow_settings=false"})
            raise HTTPException(status_code=403, detail="Stop access is disabled for your role")
        context.logger.info(f"Stop requested for {container_id} by {username}")
        result = await _run_docker(docker_service.stop_container, container_id)
        await _audit_container_event(container_id, username, db_name, "container.lifecycle.stop", {"status": result.get("status")})
        return {"status": "stopped", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{container_id}")
async def get_container_logs(container_id: str, request: Request, tail: int = Query(200)):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        result = await _run_docker(docker_service.get_container_logs, container_id, tail=tail)
        await _audit_container_event(container_id, username, db_name, "workspace.logs.view", {"tail": tail})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete/{container_id}")
async def delete_container(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")

    try:
        context.logger.warning(f"Delete requested for {container_id} by {username}")
        await _audit_container_event(container_id, username, "system.db", "container.lifecycle.delete.requested", {})
        result = await _run_docker(docker_service.delete_container, container_id, force=True)
        return {"status": "deleted", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
