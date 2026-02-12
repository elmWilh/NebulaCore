from fastapi import APIRouter, HTTPException, Request, Query
from ..services.docker_service import DockerService
from ..core.context import context
from ..db import get_connection, SYSTEM_DB

router = APIRouter(prefix="/containers", tags=["Orchestration"])
docker_service = DockerService()


def _session_from_request(request: Request):
    session = request.cookies.get("nebula_session")
    if not session:
        raise HTTPException(status_code=401, detail="No active session")
    return session.split(":")


def _can_access_container(username: str, is_staff: bool, container_id: str) -> bool:
    if is_staff:
        return True
    full_id = docker_service.resolve_container_id(container_id)
    with get_connection(SYSTEM_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM container_permissions WHERE username = ? AND container_id = ? LIMIT 1",
            (username, full_id)
        ).fetchone()
    return bool(row)

@router.get("/list")
async def list_containers(request: Request):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    
    try:
        return docker_service.list_containers(username, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/summary")
async def containers_summary(request: Request):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    try:
        return docker_service.get_usage_summary(username, is_staff)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@router.post("/deploy")
async def deploy_container(data: dict, request: Request):
    username, db_name = _session_from_request(request)
    if db_name != "system.db":
        context.logger.warning(f"Unauthorized deploy attempt by {username}")
        raise HTTPException(status_code=403, detail="Staff clearance required")

    try:
        context.logger.info(f"Initiating deployment: {data.get('name')} by {username}")
        result = docker_service.deploy(data)

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
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    if not _can_access_container(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        context.logger.info(f"Restart requested for {container_id} by {username}")
        result = docker_service.restart_container(container_id)
        return {"status": "restarted", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start/{container_id}")
async def start_container(container_id: str, request: Request):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    if not _can_access_container(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        context.logger.info(f"Start requested for {container_id} by {username}")
        result = docker_service.start_container(container_id)
        return {"status": "started", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop/{container_id}")
async def stop_container(container_id: str, request: Request):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    if not _can_access_container(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        context.logger.info(f"Stop requested for {container_id} by {username}")
        result = docker_service.stop_container(container_id)
        return {"status": "stopped", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{container_id}")
async def get_container_logs(container_id: str, request: Request, tail: int = Query(200)):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    if not _can_access_container(username, is_staff, container_id):
        raise HTTPException(status_code=403, detail="Access denied for this container")

    try:
        return docker_service.get_container_logs(container_id, tail=tail)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete/{container_id}")
async def delete_container(container_id: str, request: Request):
    username, db_name = _session_from_request(request)
    is_staff = (db_name == "system.db")
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")

    try:
        context.logger.warning(f"Delete requested for {container_id} by {username}")
        result = docker_service.delete_container(container_id, force=True)
        return {"status": "deleted", "container": result}
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
