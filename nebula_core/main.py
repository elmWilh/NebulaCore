# nebula_core/main.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
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
from .utils.logger import (
    setup_logger,
    register_lifecycle_start,
    register_lifecycle_shutdown,
)
from .api import api_router
from .core.runtime import NebulaRuntime
from .core.context import context
from .internal_grpc import InternalGrpcServer

logger = setup_logger("nebula_core")
lifecycle_logger = setup_logger("nebula_core.lifecycle", with_console=False)
COPYRIGHT_NOTICE = "Copyright (c) 2026 Monolink Systems"
LICENSE_NOTICE = "Nebula Open Source Edition (non-corporate) • Licensed under AGPLv3"

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
    lifecycle = register_lifecycle_start("nebula_core")
    lifecycle_event = lifecycle.get("event", "startup").upper()
    lifecycle_starts = lifecycle.get("starts", 1)
    lifecycle_pid = lifecycle.get("pid")
    lifecycle_at = lifecycle.get("at_utc")
    lifecycle_line = (
        f"[LIFECYCLE] {lifecycle_event} | starts={lifecycle_starts} "
        f"| pid={lifecycle_pid} | at={lifecycle_at}"
    )
    logger.info(lifecycle_line)
    lifecycle_logger.info(lifecycle_line)
    logger.info("Nebula Core startup: initializing runtime")
    logger.info(COPYRIGHT_NOTICE)
    logger.info(LICENSE_NOTICE)
    
    await grpc_server.start()
    logger.info(f"Nebula Core gRPC server started on {grpc_server.bind_target}")

    await runtime.init()
    asyncio.create_task(runtime.start())
    logger.info("Nebula Core runtime launched in background")

@app.on_event("shutdown")
async def on_shutdown():
    lifecycle = register_lifecycle_shutdown("nebula_core")
    lifecycle_line = (
        f"[LIFECYCLE] SHUTDOWN | pid={lifecycle.get('pid')} | at={lifecycle.get('at_utc')}"
    )
    logger.info(lifecycle_line)
    lifecycle_logger.info(lifecycle_line)
    logger.info("Nebula Core shutdown: requesting runtime shutdown")
    await runtime.request_shutdown()
    await grpc_server.stop()
