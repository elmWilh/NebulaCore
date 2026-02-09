import asyncio
from .context import context

class LifecycleManager:
    def __init__(self):
        self._on_start_callbacks = []
        self._on_stop_callbacks = []

    def on_startup(self, func):
        self._on_start_callbacks.append(func)

    def on_shutdown(self, func):
        self._on_stop_callbacks.append(func)

    async def startup(self):
        context.logger.info("NebulaCore starting up...")
        for func in self._on_start_callbacks:
            await func()
        context.logger.info("NebulaCore started successfully.")

    async def shutdown(self):
        context.logger.info("NebulaCore shutting down...")
        for func in self._on_stop_callbacks:
            await func()
        context.logger.info("NebulaCore stopped.")

lifecycle = LifecycleManager()
