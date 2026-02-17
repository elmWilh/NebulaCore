import asyncio

from fastapi import APIRouter, HTTPException, Request, Query
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
        _forbidden_if(not perms.get("allow_shell", False), "Shell access is disabled for your role")
        if not is_staff:
            policy = await _run_docker(docker_service.get_profile_policy, container_id)
            profile = policy.get("profile", "generic")
            ok, reason = await _run_docker(docker_service.validate_user_shell_command, command, profile)
            if not ok:
                raise HTTPException(status_code=403, detail=reason)
        context.logger.info(f"Container exec requested by {username} on {container_id}: {command}")
        return await _run_docker(docker_service.exec_command, container_id, command)
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
        _forbidden_if(not perms.get("allow_console", True), "Console access is disabled for your role")
        if not is_staff:
            policy = await _run_docker(docker_service.get_profile_policy, container_id)
            if not policy.get("app_console_supported", False):
                raise HTTPException(status_code=403, detail="Application console mode is not supported for this profile")
        context.logger.info(f"Container console input by {username} on {container_id}: {command}")
        return await _run_docker(docker_service.send_console_input, container_id, command)
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
        _forbidden_if(not perms.get("allow_explorer", True), "File explorer access is disabled for your role")
        target_path = (path or "").strip() or "/"
        if target_path == "/" and not perms.get("allow_root_explorer", False):
            raise HTTPException(status_code=403, detail="Root explorer access is disabled for your role")
        return await _run_docker(docker_service.list_files, container_id, path=path)
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
        _forbidden_if(not perms.get("allow_explorer", True), "File explorer access is disabled for your role")
        return await _run_docker(docker_service.detect_workspace_roots, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        _forbidden_if(not perms.get("allow_explorer", True), "File explorer access is disabled for your role")
        return await _run_docker(docker_service.read_file, container_id, path=path, max_bytes=max_bytes)
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
        _forbidden_if(not perms.get("allow_settings", False), "Settings access is disabled for your role")
        return await _run_docker(docker_service.get_container_settings, container_id)
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
        _forbidden_if(not perms.get("allow_settings", False), "Settings access is disabled for your role")
        payload = data or {}
        has_command = bool(str(payload.get("startup_command", "")).strip())
        has_ports = bool(str(payload.get("allowed_ports", "")).strip())
        if has_command:
            _forbidden_if(not perms.get("allow_edit_startup", False), "Editing startup command is disabled for your role")
        if has_ports:
            _forbidden_if(not perms.get("allow_edit_ports", False), "Editing port selection is disabled for your role")
        context.logger.info(f"Container settings updated by {username} for {container_id}")
        return await _run_docker(
            docker_service.update_container_settings,
            container_id=container_id,
            startup_command=payload.get("startup_command", ""),
            allowed_ports=payload.get("allowed_ports", ""),
            updated_by=username,
        )
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
        _forbidden_if(not perms.get("allow_settings", False), "Settings access is disabled for your role")
        return await _run_docker(docker_service.get_restart_policy, container_id)
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
        _forbidden_if(not perms.get("allow_settings", False), "Settings access is disabled for your role")
        _forbidden_if(not perms.get("allow_edit_startup", False), "Editing startup settings is disabled for your role")
        context.logger.info(f"Restart policy updated by {username} for {container_id}: {policy}")
        return await _run_docker(docker_service.update_restart_policy, container_id, policy, retries)
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
        policies = await _run_docker(
            docker_service.set_container_role_policies,
            container_id,
            (data or {}).get("role_policies"),
            username,
        )
        return {"status": "updated", "container_id": container_id, "role_policies": policies}
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
        context.logger.info(f"Restart requested for {container_id} by {username}")
        result = await _run_docker(docker_service.restart_container, container_id)
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
        context.logger.info(f"Start requested for {container_id} by {username}")
        result = await _run_docker(docker_service.start_container, container_id)
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
        context.logger.info(f"Stop requested for {container_id} by {username}")
        result = await _run_docker(docker_service.stop_container, container_id)
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
        return await _run_docker(docker_service.get_container_logs, container_id, tail=tail)
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
        result = await _run_docker(docker_service.delete_container, container_id, force=True)
        return {"status": "deleted", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
