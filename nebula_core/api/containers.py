# nebula_core/api/containers.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import os
import posixpath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File, Form
from fastapi.responses import Response
import mimetypes
from ..services.docker_service import DockerService
from ..services.security_service import SecurityService
from ..core.context import context
from .security import require_session

router = APIRouter(prefix="/containers", tags=["Orchestration"])
docker_service = DockerService()
security_service = SecurityService()
WORKSPACE_UPLOAD_LIMIT_BYTES = 1024 * 1024 * 1024


def _session_from_request(request: Request):
    return require_session(request)


def _can_access_container(username: str, db_name: str, is_staff: bool, container_id: str) -> bool:
    if is_staff:
        return True
    try:
        full_id = docker_service.resolve_container_id(container_id)
    except Exception:
        return False
    return security_service.user_has_container_access(username, db_name, full_id)


async def _run_docker(callable_obj, *args, **kwargs):
    return await asyncio.to_thread(callable_obj, *args, **kwargs)


async def _can_access_container_async(username: str, db_name: str, is_staff: bool, container_id: str) -> bool:
    return await asyncio.to_thread(_can_access_container, username, db_name, is_staff, container_id)


def _forbidden_if(condition: bool, message: str):
    if condition:
        raise HTTPException(status_code=403, detail=message)


def _safe_upload_relative_path(raw_path: str) -> str:
    candidate = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if not candidate:
        raise HTTPException(status_code=400, detail="Upload path is missing")
    if "\x00" in candidate:
        raise HTTPException(status_code=400, detail="Upload path contains invalid characters")
    normalized = posixpath.normpath(candidate)
    if normalized in ("", ".", "/") or normalized.startswith("../") or normalized == "..":
        raise HTTPException(status_code=400, detail="Upload path escapes target directory")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Upload path escapes target directory")
    return "/".join(parts)


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

@router.get("/inspect/{container_id}")
async def container_inspect_bundle(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    if not await _can_access_container_async(username, db_name, True, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        result = await _run_docker(docker_service.get_container_inspect_bundle, container_id)
        await _audit_container_event(container_id, username, db_name, "container.inspect.view", {})
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/docker-objects")
async def docker_objects_summary(request: Request, limit: int = Query(12)):
    _, _, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    try:
        return await _run_docker(docker_service.list_docker_objects_summary, limit)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/docker-events")
async def docker_events(request: Request, limit: int = Query(50)):
    _, _, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    try:
        return await _run_docker(docker_service.list_docker_events, limit)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    detached = bool((data or {}).get("detached", False))
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
        result = await _run_docker(docker_service.exec_command, container_id, command, detached)
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.shell.exec",
            {
                "command": command[:180],
                "exit_code": result.get("exit_code"),
                "detached": bool(result.get("detached")),
                "pid": result.get("pid"),
            },
        )
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


@router.post("/pty/start/{container_id}")
async def start_container_pty_session(container_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    payload = data or {}
    cols = payload.get("cols", 120)
    rows = payload.get("rows", 32)
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_shell", False):
            await _audit_container_event(container_id, username, db_name, "workspace.shell.denied", {"reason": "allow_shell=false", "transport": "pty"})
            raise HTTPException(status_code=403, detail="Shell access is disabled for your role")
        if not is_staff:
            await _audit_container_event(container_id, username, db_name, "workspace.shell.denied", {"reason": "interactive_pty_staff_only"})
            raise HTTPException(status_code=403, detail="Interactive PTY shell is available only to staff accounts")
        result = await _run_docker(docker_service.start_shell_session, container_id, cols, rows)
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.shell.pty.start",
            {"session_id": result.get("session_id"), "cols": result.get("cols"), "rows": result.get("rows")},
        )
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pty/read/{session_id}")
async def read_container_pty_session(session_id: str, request: Request, cursor: int = Query(0)):
    username, db_name, is_staff = _session_from_request(request)
    try:
        snapshot = await _run_docker(docker_service.read_shell_session, session_id, cursor)
        container_id = str(snapshot.get("container_id") or "")
        if not await _can_access_container_async(username, db_name, is_staff, container_id):
            raise HTTPException(status_code=403, detail="Access denied for this container")
        if not is_staff:
            raise HTTPException(status_code=403, detail="Interactive PTY shell is available only to staff accounts")
        return snapshot
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pty/input/{session_id}")
async def write_container_pty_session(session_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    payload = data or {}
    input_data = payload.get("data", "")
    try:
        snapshot = await _run_docker(docker_service.read_shell_session, session_id, 0)
        container_id = str(snapshot.get("container_id") or "")
        if not await _can_access_container_async(username, db_name, is_staff, container_id):
            raise HTTPException(status_code=403, detail="Access denied for this container")
        if not is_staff:
            raise HTTPException(status_code=403, detail="Interactive PTY shell is available only to staff accounts")
        return await _run_docker(docker_service.write_shell_session, session_id, input_data)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pty/resize/{session_id}")
async def resize_container_pty_session(session_id: str, data: dict, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    payload = data or {}
    cols = payload.get("cols", 120)
    rows = payload.get("rows", 32)
    try:
        snapshot = await _run_docker(docker_service.read_shell_session, session_id, 0)
        container_id = str(snapshot.get("container_id") or "")
        if not await _can_access_container_async(username, db_name, is_staff, container_id):
            raise HTTPException(status_code=403, detail="Access denied for this container")
        if not is_staff:
            raise HTTPException(status_code=403, detail="Interactive PTY shell is available only to staff accounts")
        return await _run_docker(docker_service.resize_shell_session, session_id, cols, rows)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pty/close/{session_id}")
async def close_container_pty_session(session_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    try:
        snapshot = await _run_docker(docker_service.read_shell_session, session_id, 0)
        container_id = str(snapshot.get("container_id") or "")
        if not await _can_access_container_async(username, db_name, is_staff, container_id):
            raise HTTPException(status_code=403, detail="Access denied for this container")
        if not is_staff:
            raise HTTPException(status_code=403, detail="Interactive PTY shell is available only to staff accounts")
        result = await _run_docker(docker_service.close_shell_session, session_id)
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.shell.pty.close",
            {"session_id": session_id, "exit_code": result.get("exit_code"), "reason": result.get("close_reason")},
        )
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


@router.get("/sftp-info/{container_id}")
async def container_sftp_info(container_id: str, request: Request):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if not perms.get("allow_explorer", True):
            await _audit_container_event(container_id, username, db_name, "workspace.sftp.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        result = await _run_docker(docker_service.get_container_sftp_info, container_id)
        await _audit_container_event(container_id, username, db_name, "workspace.sftp.inspect", {"available": bool(result.get("available"))})
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
    max_bytes: int = Query(50 * 1024 * 1024)
):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.download.denied", {"path": path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        target = str(path or "").strip()
        host_target = None
        try:
            host_binding = await _run_docker(docker_service.resolve_workspace_host_path, container_id, target)
            host_target = str(host_binding.get("host_path") or "").strip()
        except Exception:
            host_target = None

        binary_payload = None
        response_type = "text/plain; charset=utf-8"
        if host_target and os.path.isfile(host_target):
            file_size = os.path.getsize(host_target)
            if file_size > max(1024, min(int(max_bytes), WORKSPACE_UPLOAD_LIMIT_BYTES)):
                raise HTTPException(status_code=413, detail="File is too large to download from this panel")
            with open(host_target, "rb") as handle:
                binary_payload = handle.read()
            guessed_type = mimetypes.guess_type(host_target)[0]
            response_type = guessed_type or "application/octet-stream"
            target = host_binding.get("target") or target
            await _audit_container_event(container_id, username, db_name, "workspace.file.download", {"path": target, "size": len(binary_payload)})
        else:
            data = await _run_docker(docker_service.read_file, container_id, path=path, max_bytes=max_bytes)
            if data.get("truncated"):
                raise HTTPException(status_code=413, detail="File is too large to download from this panel")
            binary_payload = (data.get("content") or "").encode("utf-8", errors="replace")
            target = data.get("path") or path
            await _audit_container_event(container_id, username, db_name, "workspace.file.download", {"path": target, "size": len(binary_payload)})

        file_name = posixpath.basename(target) or "file.txt"
        header_name = quote(file_name, safe="")
        return Response(
            content=binary_payload,
            media_type=response_type,
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


@router.post("/mkdir/{container_id}")
async def create_container_directory(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.dir.create.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.dir.create.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        target_path = str((data or {}).get("path") or "").strip()
        result = await _run_docker(docker_service.create_directory, container_id, target_path)
        await _audit_container_event(container_id, username, db_name, "workspace.dir.create", {"path": target_path})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/move-path/{container_id}")
async def move_container_path(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.move.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.move.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        source_path = str((data or {}).get("source_path") or "").strip()
        destination_path = str((data or {}).get("destination_path") or "").strip()
        result = await _run_docker(docker_service.move_path, container_id, source_path, destination_path)
        await _audit_container_event(container_id, username, db_name, "workspace.path.move", {"source_path": source_path, "destination_path": destination_path})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete-path/{container_id}")
async def delete_container_path(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.delete.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.delete.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        target_path = str((data or {}).get("path") or "").strip()
        result = await _run_docker(docker_service.delete_path, container_id, target_path)
        await _audit_container_event(container_id, username, db_name, "workspace.path.delete", {"path": target_path})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/copy-path/{container_id}")
async def copy_container_path(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.copy.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.path.copy.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        source_path = str((data or {}).get("source_path") or "").strip()
        destination_path = str((data or {}).get("destination_path") or "").strip()
        result = await _run_docker(docker_service.copy_path, container_id, source_path, destination_path)
        await _audit_container_event(container_id, username, db_name, "workspace.path.copy", {"source_path": source_path, "destination_path": destination_path})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/archive-paths/{container_id}")
async def archive_container_paths(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.archive.create.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.archive.create.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        source_paths = [str(item or "").strip() for item in ((data or {}).get("source_paths") or []) if str(item or "").strip()]
        destination_path = str((data or {}).get("destination_path") or "").strip()
        result = await _run_docker(docker_service.archive_paths, container_id, source_paths, destination_path)
        await _audit_container_event(container_id, username, db_name, "workspace.archive.create", {"destination_path": destination_path, "count": len(source_paths)})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-archive/{container_id}")
async def extract_container_archive(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.archive.extract.denied", {"reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.archive.extract.denied", {"reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File write access is disabled for your role")
        archive_path = str((data or {}).get("archive_path") or "").strip()
        destination_path = str((data or {}).get("destination_path") or "").strip()
        result = await _run_docker(docker_service.extract_archive, container_id, archive_path, destination_path)
        await _audit_container_event(container_id, username, db_name, "workspace.archive.extract", {"archive_path": archive_path, "destination_path": destination_path})
        return result
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-files/{container_id}")
async def upload_container_files(
    container_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    target_path: str = Form(...),
    relative_paths: list[str] = Form(default=[]),
):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        perms = await _effective_permissions(username, db_name, is_staff, container_id)
        if (not is_staff) and (not perms.get("allow_explorer", True)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.upload.denied", {"path": target_path, "reason": "allow_explorer=false"})
            raise HTTPException(status_code=403, detail="File explorer access is disabled for your role")
        if not (is_staff or perms.get("allow_edit_files", False)):
            await _audit_container_event(container_id, username, db_name, "workspace.file.upload.denied", {"path": target_path, "reason": "allow_edit_files=false"})
            raise HTTPException(status_code=403, detail="File upload access is disabled for your role")
        if not files:
            raise HTTPException(status_code=400, detail="No files were uploaded")

        binding = await _run_docker(docker_service.resolve_workspace_host_directory, container_id, target_path)
        base_dir = os.path.abspath(binding["host_path"])
        total_written = 0
        saved = []

        for index, upload in enumerate(files):
            rel = relative_paths[index] if index < len(relative_paths) else (upload.filename or "")
            safe_rel = _safe_upload_relative_path(rel)
            file_name = posixpath.basename(safe_rel)
            if not file_name:
                raise HTTPException(status_code=400, detail="Upload file name is invalid")

            destination = os.path.abspath(os.path.join(base_dir, safe_rel.replace("/", os.sep)))
            if destination != base_dir and not destination.startswith(base_dir + os.sep):
                raise HTTPException(status_code=400, detail="Upload path escapes target directory")

            os.makedirs(os.path.dirname(destination), exist_ok=True)
            written_for_file = 0
            try:
                with open(destination, "wb") as handle:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        written_for_file += len(chunk)
                        total_written += len(chunk)
                        if total_written > WORKSPACE_UPLOAD_LIMIT_BYTES:
                            raise HTTPException(status_code=413, detail="Upload exceeds 1 GB request limit")
                        handle.write(chunk)
            finally:
                await upload.close()

            saved.append({
                "relative_path": safe_rel,
                "bytes": written_for_file,
            })

        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.file.upload",
            {"path": target_path, "files": len(saved), "bytes": total_written},
        )
        return {
            "status": "uploaded",
            "target_path": binding["target"],
            "files_saved": len(saved),
            "bytes_written": total_written,
            "entries": saved,
        }
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
        has_command = any(
            bool(str(payload.get(key, "")).strip())
            for key in ("startup_command", "install_command", "project_protocol")
        )
        has_ports = any(
            bool(str(payload.get(key, "")).strip())
            for key in ("allowed_ports", "domain_name", "launch_url")
        )
        if has_command:
            if not perms.get("allow_edit_startup", False):
                await _audit_container_event(container_id, username, db_name, "workspace.settings.update.denied", {"reason": "allow_edit_startup=false"})
                raise HTTPException(status_code=403, detail="Editing startup and protocol settings is disabled for your role")
        if has_ports:
            if not perms.get("allow_edit_ports", False):
                await _audit_container_event(container_id, username, db_name, "workspace.settings.update.denied", {"reason": "allow_edit_ports=false"})
                raise HTTPException(status_code=403, detail="Editing port and domain settings is disabled for your role")
        context.logger.info(f"Container settings updated by {username} for {container_id}")
        result = await _run_docker(
            docker_service.update_container_settings,
            container_id=container_id,
            startup_command=payload.get("startup_command", ""),
            allowed_ports=payload.get("allowed_ports", ""),
            project_protocol=payload.get("project_protocol", ""),
            install_command=payload.get("install_command", ""),
            domain_name=payload.get("domain_name", ""),
            launch_url=payload.get("launch_url", ""),
            updated_by=username,
        )
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.settings.update",
            {
                "startup_command": bool(str(payload.get("startup_command", "")).strip()),
                "allowed_ports": str(payload.get("allowed_ports", "")).strip(),
                "project_protocol": str(payload.get("project_protocol", "")).strip(),
                "domain_name": str(payload.get("domain_name", "")).strip(),
                "launch_url": str(payload.get("launch_url", "")).strip(),
            },
        )
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
            (data or {}).get("group_assignments"),
        )
        security_service.append_audit_event(
            event_kind="user",
            action="container.access.update",
            summary=f"Updated access model for container {container_id}",
            severity="info",
            risk_level="high",
            actor=username,
            actor_db=db_name,
            source_ip=(request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else ""),
            target_type="container",
            target_id=container_id,
            details={
                "role_policies": len((data or {}).get("role_policies") or {}),
                "user_assignments": len((data or {}).get("user_assignments") or []),
                "group_assignments": len((data or {}).get("group_assignments") or []),
            },
        )
        await _audit_container_event(
            container_id,
            username,
            db_name,
            "workspace.permissions.update",
            {
                "role_policies": len((data or {}).get("role_policies") or {}),
                "user_assignments": len((data or {}).get("user_assignments") or []),
                "group_assignments": len((data or {}).get("group_assignments") or []),
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
async def get_container_logs(container_id: str, request: Request, tail: int = Query(200), streaming: bool = Query(False)):
    username, db_name, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, db_name, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        result = await _run_docker(docker_service.get_container_logs, container_id, tail=tail)
        if not streaming:
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

@router.post("/duplicate/{container_id}")
async def duplicate_container(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    if not await _can_access_container_async(username, db_name, True, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        result = await _run_docker(docker_service.duplicate_container, container_id, data or {})
        await _audit_container_event(container_id, username, db_name, "container.lifecycle.duplicate", {"new_container_id": result.get("full_id") or result.get("id")})
        return {"status": "duplicated", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/recreate/{container_id}")
async def recreate_container(container_id: str, request: Request, data: dict):
    username, db_name, is_staff = _session_from_request(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")
    if not await _can_access_container_async(username, db_name, True, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        result = await _run_docker(docker_service.recreate_container, container_id, data or {})
        await _audit_container_event(result.get("full_id") or container_id, username, db_name, "container.lifecycle.recreate", {})
        return {"status": "recreated", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
