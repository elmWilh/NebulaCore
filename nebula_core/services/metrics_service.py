# nebula_core/services/metrics_service.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import threading
import time
from collections import deque

import psutil

from ..core.context import context


class MetricsService:
    def __init__(self, name: str = "metrics", interval: int = 3, history_limit: int = 60):
        self.name = name
        self.interval = max(1, int(interval))
        self.history_limit = max(10, int(history_limit))
        self._running = False
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._prev_sent = None
        self._prev_recv = None
        self._prev_time = None
        self._snapshot = self._build_empty_snapshot()
        self._history = {
            "ram_percent": deque(maxlen=self.history_limit),
            "network_tx_mbps": deque(maxlen=self.history_limit),
            "network_rx_mbps": deque(maxlen=self.history_limit),
        }

    def _build_empty_snapshot(self) -> dict:
        return {
            "uptime": 0.0,
            "timestamp": 0,
            "cpu_percent": 0.0,
            "cpu": "0.0%",
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "ram_percent_value": 0.0,
            "ram_percent": "0.0%",
            "disk_used_gb": 0.0,
            "disk_total_gb": 0.0,
            "disk_percent_value": 0.0,
            "disk_percent": "0.0%",
            "network_sent_mb": 0.0,
            "network_recv_mb": 0.0,
            "core_status": "starting",
        }

    def configure(self, interval: int | None = None):
        if interval is not None:
            self.interval = max(1, int(interval))

    def _collect_snapshot(self) -> dict:
        now = time.time()
        cpu = float(psutil.cpu_percent(interval=None) or 0.0)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        io_now = psutil.net_io_counters()

        prev_sent = self._prev_sent
        prev_recv = self._prev_recv
        prev_time = self._prev_time

        sent_speed = 0.0
        recv_speed = 0.0
        if prev_sent is not None and prev_recv is not None and prev_time is not None:
            dt = max(0.2, now - prev_time)
            sent_speed = max(0.0, (io_now.bytes_sent - prev_sent) / dt / 1048576.0)
            recv_speed = max(0.0, (io_now.bytes_recv - prev_recv) / dt / 1048576.0)

        self._prev_sent = io_now.bytes_sent
        self._prev_recv = io_now.bytes_recv
        self._prev_time = now

        point_ts = int(now)
        snapshot = {
            "uptime": round(now - self._start_time, 2),
            "timestamp": point_ts,
            "cpu_percent": round(cpu, 2),
            "cpu": f"{cpu:.1f}%",
            "ram_used_gb": round(mem.used / 1024**3, 2),
            "ram_total_gb": round(mem.total / 1024**3, 2),
            "ram_percent_value": round(float(mem.percent), 2),
            "ram_percent": f"{float(mem.percent):.1f}%",
            "disk_used_gb": round(disk.used / 1024**3, 2),
            "disk_total_gb": round(disk.total / 1024**3, 2),
            "disk_percent_value": round(float(disk.percent), 2),
            "disk_percent": f"{float(disk.percent):.1f}%",
            "network_sent_mb": round(sent_speed, 3),
            "network_recv_mb": round(recv_speed, 3),
            "core_status": "online",
        }

        with self._lock:
            self._snapshot = snapshot
            self._history["ram_percent"].append({"t": point_ts, "v": snapshot["ram_percent_value"]})
            self._history["network_tx_mbps"].append({"t": point_ts, "v": snapshot["network_sent_mb"]})
            self._history["network_rx_mbps"].append({"t": point_ts, "v": snapshot["network_recv_mb"]})
        return snapshot

    def get_snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def get_dashboard_history(self) -> dict:
        with self._lock:
            snapshot = dict(self._snapshot)
            ram_history = list(self._history["ram_percent"])
            tx_history = list(self._history["network_tx_mbps"])
            rx_history = list(self._history["network_rx_mbps"])
        return {
            "ram": {
                "used_gb": snapshot["ram_used_gb"],
                "total_gb": snapshot["ram_total_gb"],
                "percent": snapshot["ram_percent_value"],
                "history": ram_history,
            },
            "network": {
                "tx_mbps": snapshot["network_sent_mb"],
                "rx_mbps": snapshot["network_recv_mb"],
                "history_tx": tx_history,
                "history_rx": rx_history,
            },
        }

    async def start(self):
        self._running = True
        context.logger.info("%s service initialized.", self.name)
        while self._running:
            snapshot = self._collect_snapshot()
            if context.runtime and context.runtime.event_bus:
                await context.runtime.event_bus.emit(
                    "service.metrics.update",
                    {
                        "uptime": snapshot["uptime"],
                        "cpu": snapshot["cpu_percent"],
                        "ram": snapshot["ram_percent_value"],
                        "disk": snapshot["disk_percent_value"],
                        "network": {
                            "sent": snapshot["network_sent_mb"],
                            "recv": snapshot["network_recv_mb"],
                        },
                        "timestamp": snapshot["timestamp"],
                    },
                )
            await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False
        context.logger.info("%s service stopped.", self.name)


metrics_service = MetricsService()
