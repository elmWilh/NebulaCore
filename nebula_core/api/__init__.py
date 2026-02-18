from fastapi import APIRouter
from .system import router as system_router
from .auth import router as auth_router
from .metrics import router as metrics_router
from .logs import router as logs_router
from .users import router as users_router
from .roles import router as roles_router
from .admin import router as admin_router
from .containers import router as containers_router
from .plugins import router as plugins_router

api_router = APIRouter()

# Routers for public API endpoints
api_router.include_router(users_router) 
api_router.include_router(roles_router)
api_router.include_router(system_router)
api_router.include_router(auth_router)
api_router.include_router(metrics_router)  
api_router.include_router(logs_router)
api_router.include_router(containers_router)
api_router.include_router(plugins_router)

# System-Security router for admin operations (hidden from public docs)
api_router.include_router(admin_router)
