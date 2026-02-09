from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.get("/check")
async def auth_check():
    return {"auth": "ok"}
