# nebula_core/api/auth.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.get("/check")
async def auth_check():
    return {"auth": "ok"}
