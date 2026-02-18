# nebula_core/services/heartbeat.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import asyncio
import psutil
import time

from nebula_core.core.context import context


class HeartbeatService:
    """
    Periodic system monitor that reports uptime, CPU usage, and memory load.

    Emits: system.heartbeat event
    """

    def __init__(self, name: str = "heartbeat", interval: int = 5):
        self.name = name
        self.interval = interval
        self._running = False
        self._start_time = time.time()

    async def start(self):
        """Start the heartbeat loop."""
        if self._running:
            context.logger.warning(f"{self.name}: already running, skipping launch.")
            return

        self._running = True
        context.logger.info(f"{self.name}: service started.")

        try:
            while self._running:
                uptime = round(time.time() - self._start_time, 2)
                cpu = psutil.cpu_percent()
                mem = psutil.virtual_memory().percent

                context.logger.info(
                    f"[Heartbeat] Uptime={uptime}s | CPU={cpu}% | RAM={mem}%"
                )

                await context.event_bus.emit(
                    "system.heartbeat",
                    {"uptime": uptime, "cpu": cpu, "ram": mem},
                )

                await asyncio.sleep(self.interval)

        except asyncio.CancelledError:
            context.logger.info(f"{self.name}: cancelled.")
        except Exception as e:
            context.logger.exception(f"{self.name}: error occurred: {e}")
        finally:
            self._running = False
            context.logger.info(f"{self.name}: service stopped.")

    async def stop(self):
        """Stop the heartbeat loop."""
        if not self._running:
            return

        self._running = False
        context.logger.info(f"{self.name}: stop requested.")



heartbeat_service = HeartbeatService(interval=5)
