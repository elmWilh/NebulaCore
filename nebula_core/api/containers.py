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


def _can_access_container(username: str, is_staff: bool, container_id: str) -> bool:
    if is_staff:
        return True
    try:
        full_id = docker_service.resolve_container_id(container_id)
    except Exception:
        return False
    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM container_permissions WHERE username = ? AND container_id = ? LIMIT 1",
            (username, full_id)
        ).fetchone()
    return bool(row)


async def _run_docker(callable_obj, *args, **kwargs):
    return await asyncio.to_thread(callable_obj, *args, **kwargs)


async def _can_access_container_async(username: str, is_staff: bool, container_id: str) -> bool:
    return await asyncio.to_thread(_can_access_container, username, is_staff, container_id)

@router.get("/list")
async def list_containers(request: Request):
    username, _, is_staff = _session_from_request(request)
    
    try:
        return await _run_docker(docker_service.list_containers, username, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/summary")
async def containers_summary(request: Request):
    username, _, is_staff = _session_from_request(request)
    try:
        return await _run_docker(docker_service.get_usage_summary, username, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/detail/{container_id}")
async def container_detail(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.get_container_detail, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/profile/{container_id}")
async def container_profile_policy(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        policy = await _run_docker(docker_service.get_profile_policy, container_id)
        policy["shell_allowed"] = bool(is_staff or policy.get("shell_allowed_for_user", True))
        policy["is_staff"] = bool(is_staff)
        return policy
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/exec/{container_id}")
async def exec_container_command(container_id: str, data: dict, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    command = (data or {}).get("command", "")
    try:
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    command = (data or {}).get("command", "")
    try:
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.list_files, container_id, path=path)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workspace-roots/{container_id}")
async def container_workspace_roots(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.read_file, container_id, path=path, max_bytes=max_bytes)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{container_id}")
async def get_container_settings(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.get_container_settings, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/{container_id}")
async def update_container_settings(container_id: str, data: dict, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        context.logger.info(f"Container settings updated by {username} for {container_id}")
        return await _run_docker(
            docker_service.update_container_settings,
            container_id=container_id,
            startup_command=(data or {}).get("startup_command", ""),
            allowed_ports=(data or {}).get("allowed_ports", ""),
            updated_by=username,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/restart-policy/{container_id}")
async def get_container_restart_policy(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    try:
        return await _run_docker(docker_service.get_restart_policy, container_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart-policy/{container_id}")
async def update_container_restart_policy(container_id: str, data: dict, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")
    policy = (data or {}).get("restart_policy", "no")
    retries = (data or {}).get("maximum_retry_count", 0)
    try:
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
        # Likely Docker not available
        context.logger.error(f"Deployment failed (runtime): {str(e)}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        context.logger.error(f"Deployment failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart/{container_id}")
async def restart_container(container_id: str, request: Request):
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
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
    username, _, is_staff = _session_from_request(request)
    if not await _can_access_container_async(username, is_staff, container_id):
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
