# nebula_core/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import asyncio

from .utils.config import settings
from .utils.logger import setup_logger
from .api import api_router
from .core.runtime import NebulaRuntime
from .core.context import context

logger = setup_logger("nebula_core")

runtime = NebulaRuntime()
context.runtime = runtime
context.event_bus = runtime.event_bus
context.logger = logger

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    await runtime.init()

    asyncio.create_task(runtime.start())
    logger.info("Nebula Core runtime запущен в фоне")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Nebula Core shutdown: requesting runtime shutdown")
    await runtime.request_shutdown("fastapi_shutdown")