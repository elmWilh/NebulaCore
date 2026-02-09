# nebula_core/api/metrics.py
from fastapi import APIRouter
import psutil
import time

router = APIRouter(prefix="/metrics", tags=["Metrics"])

latest_metrics = {
    "uptime": 0.0,
    "cpu": 0.0,
    "ram_percent": 0.0,
    "disk_percent": 0.0,
    "network": {"sent": 0, "recv": 0},
    "timestamp": 0
}

from nebula_core.core.context import context

def on_metrics_update(data: dict):
    latest_metrics.update(data)
    latest_metrics["timestamp"] = int(time.time())

if context.runtime and context.runtime.event_bus:
    context.runtime.event_bus.on("service.metrics.update", on_metrics_update)


@router.get("/current")
async def get_current_metrics():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    return {
        "cpu": f"{cpu:.1f}%",
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "ram_percent": f"{mem.percent:.1f}%",
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "disk_percent": f"{disk.percent:.1f}%",
        "uptime_seconds": round(latest_metrics["uptime"], 1),
        "network_sent_mb": latest_metrics["network"]["sent"] // 1048576,
        "network_recv_mb": latest_metrics["network"]["recv"] // 1048576,
        "last_update": latest_metrics["timestamp"],
        "core_status": "online"
    }


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "metrics"}