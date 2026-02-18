# nebula_core/core/service_task.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import traceback
from typing import Optional
from ..utils.logger import get_logger

logger = get_logger("nebula_core.service")


class ServiceTask:
    def __init__(self, name: str, interval: float = 5.0):
        self.name = name
        self.interval = interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def on_start(self):
        logger.info(f"[{self.name}] on_start")

    async def on_stop(self):
        logger.info(f"[{self.name}] on_stop")

    async def tick(self):
        raise NotImplementedError

    async def _run(self):
        await self.on_start()
        self._running = True
        logger.info(f"[{self.name}] started")

        try:
            while self._running:
                await self.tick()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.name}] error: {e}\n{traceback.format_exc()}")
        finally:
            await self.on_stop()
            logger.info(f"[{self.name}] stopped")

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.sleep(0)
