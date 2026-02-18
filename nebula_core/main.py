# nebula_core/main.py
import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment: project root first, then override with installer .env if present
load_dotenv()  # root .env
installer_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'install', '.env')
if os.path.exists(installer_env):
    # installer .env may contain NEBULA_INSTALLER_TOKEN — prefer it when present
    load_dotenv(installer_env, override=True)

from .utils.config import settings
from .utils.logger import setup_logger
from .api import api_router
from .core.runtime import NebulaRuntime
from .core.context import context
from .db import init_system_db
from .internal_grpc import InternalGrpcServer

logger = setup_logger("nebula_core")

runtime = NebulaRuntime()
grpc_server = InternalGrpcServer()
context.runtime = runtime
context.event_bus = runtime.event_bus
context.logger = logger
context.plugin_manager = runtime.plugin_manager

app = FastAPI(
    title=settings.APP_NAME, 
    version=settings.APP_VERSION,
    docs_url=None if os.getenv("ENV") == "production" else "/docs",
    redoc_url=None
)

# Middleware
raw_origins = os.getenv("NEBULA_CORS_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000")
allow_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Nebula-Token"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Request: {request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"Response: {response.status_code} {request.url.path}")
    return response

app.include_router(api_router)

@app.on_event("startup")
async def on_startup():
    logger.info("Nebula Core startup: initializing runtime")
    
    init_system_db()
    
    await grpc_server.start()
    logger.info(f"Nebula Core gRPC server started on {grpc_server.bind_target}")

    await runtime.init()
    asyncio.create_task(runtime.start())
    logger.info("Nebula Core runtime launched in background")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Nebula Core shutdown: requesting runtime shutdown")
    await runtime.request_shutdown()
    await grpc_server.stop()
