from fastapi import APIRouter
from ..core.system_info import get_system_info

router = APIRouter(prefix="/system", tags=["System"])

@router.get("/status")
async def system_status():
    return {"status": "ok", "system": get_system_info()}
