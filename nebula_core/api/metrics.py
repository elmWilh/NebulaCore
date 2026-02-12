# nebula_core/api/metrics.py
from fastapi import APIRouter
import psutil
import time

router = APIRouter(prefix="/metrics", tags=["Metrics"])

# State for network I/O speed calculation
net_io_state = {
    "prev_sent": psutil.net_io_counters().bytes_sent,
    "prev_recv": psutil.net_io_counters().bytes_recv,
    "prev_time": time.time()
}

latest_metrics = {"uptime": 0.0, "timestamp": 0}
from nebula_core.core.context import context
_metrics_listener_bound = False

def on_metrics_update(data: dict):
    latest_metrics.update(data)
    latest_metrics["timestamp"] = int(time.time())

async def _ensure_metrics_listener():
    global _metrics_listener_bound
    if _metrics_listener_bound:
        return
    if context.runtime and context.runtime.event_bus:
        await context.runtime.event_bus.subscribe("service.metrics.update", on_metrics_update)
        _metrics_listener_bound = True

@router.get("/current")
async def get_current_metrics():
    global net_io_state
    await _ensure_metrics_listener()
    
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Network I/O for speed calculation
    io_now = psutil.net_io_counters()
    time_now = time.time()
    dt = time_now - net_io_state["prev_time"]
    if dt < 0.1: dt = 1.0

    sent_speed = (io_now.bytes_sent - net_io_state["prev_sent"]) / dt / 1048576
    recv_speed = (io_now.bytes_recv - net_io_state["prev_recv"]) / dt / 1048576

    net_io_state.update({
        "prev_sent": io_now.bytes_sent,
        "prev_recv": io_now.bytes_recv,
        "prev_time": time_now
    })

    return {
        "cpu": f"{cpu:.1f}%",
        "ram_used_gb": round(mem.used / 1024**3, 1),
        "ram_total_gb": round(mem.total / 1024**3, 1),
        "ram_percent": f"{mem.percent:.1f}%",
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "disk_percent": f"{disk.percent:.1f}%",
        "network_sent_mb": round(sent_speed, 2),
        "network_recv_mb": round(recv_speed, 2),
        "core_status": "online"
    }
