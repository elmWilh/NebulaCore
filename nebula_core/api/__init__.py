# nebula_core/api/__init__.py
from fastapi import APIRouter
from .system import router as system_router
from .auth import router as auth_router
from .metrics import router as metrics_router
from .logs import router as logs_router

api_router = APIRouter()

api_router.include_router(system_router)
api_router.include_router(auth_router)
api_router.include_router(metrics_router)  
api_router.include_router(logs_router)