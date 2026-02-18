# nebula_core/services/metrics_service.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import psutil
import time
from ..core.context import context 

class MetricsService:
    def __init__(self, name="metrics", interval: int = 5):
        self.name = name
        self.interval = interval
        self._running = False
        self._start_time = time.time()

    async def start(self):
        self._running = True
        context.logger.info(f"{self.name} service initialized.")
        while self._running:
            uptime = round(time.time() - self._start_time, 2)
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
            net = psutil.net_io_counters()
            context.logger.info(
                f"📊 Metrics | Uptime: {uptime}s | CPU: {cpu}% | RAM: {mem}% | Disk: {disk}% | Sent: {net.bytes_sent} | Recv: {net.bytes_recv}"
            )
            if context.runtime and context.runtime.event_bus:
                await context.runtime.event_bus.emit(f"service.metrics.update", {
                    "uptime": uptime,
                    "cpu": cpu,
                    "ram": mem,
                    "disk": disk,
                    "network": {"sent": net.bytes_sent, "recv": net.bytes_recv}
                })
            await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False
        context.logger.info(f"{self.name} service stopped.")

metrics_service = MetricsService()
