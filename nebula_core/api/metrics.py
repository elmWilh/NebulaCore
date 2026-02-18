# nebula_core/api/metrics.py
from collections import deque
from fastapi import APIRouter, HTTPException, Request
import psutil
import time
import threading

from .security import require_session
from ..services.docker_service import DockerService

router = APIRouter(prefix="/metrics", tags=["Metrics"])

# State for network I/O speed calculation
net_io_state = {
    "prev_sent": psutil.net_io_counters().bytes_sent,
    "prev_recv": psutil.net_io_counters().bytes_recv,
    "prev_time": time.time()
}
admin_history = {
    "ram_percent": deque(maxlen=40),
    "network_tx_mbps": deque(maxlen=40),
    "network_rx_mbps": deque(maxlen=40),
}
docker_service = DockerService()
IGNORED_FS_TYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay",
    "squashfs", "nsfs", "tracefs", "mqueue", "hugetlbfs", "ramfs",
    "autofs", "fusectl", "debugfs", "securityfs", "pstore", "configfs", "efivarfs"
}

latest_metrics = {"uptime": 0.0, "timestamp": 0}
from nebula_core.core.context import context
_metrics_listener_bound = False
admin_heavy_cache = {
    "ts": 0.0,
    "containers_memory": [],
    "disks": [],
}
admin_heavy_cache_lock = threading.Lock()
ADMIN_HEAVY_CACHE_TTL = 12.0

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
    return await collect_current_metrics()


async def collect_current_metrics():
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


@router.get("/admin/dashboard")
async def get_admin_dashboard_metrics(request: Request):
    username, _, is_staff = require_session(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")

    mem = psutil.virtual_memory()
    io_now = psutil.net_io_counters()
    now = time.time()
    dt = now - net_io_state["prev_time"]
    if dt < 0.1:
        dt = 1.0

    sent_speed = max(0.0, (io_now.bytes_sent - net_io_state["prev_sent"]) / dt / 1048576.0)
    recv_speed = max(0.0, (io_now.bytes_recv - net_io_state["prev_recv"]) / dt / 1048576.0)
    net_io_state.update({
        "prev_sent": io_now.bytes_sent,
        "prev_recv": io_now.bytes_recv,
        "prev_time": now,
    })

    point_ts = int(now)
    admin_history["ram_percent"].append({"t": point_ts, "v": round(float(mem.percent), 2)})
    admin_history["network_tx_mbps"].append({"t": point_ts, "v": round(sent_speed, 3)})
    admin_history["network_rx_mbps"].append({"t": point_ts, "v": round(recv_speed, 3)})

    with admin_heavy_cache_lock:
        cached = dict(admin_heavy_cache)
    if (now - float(cached.get("ts") or 0.0)) <= ADMIN_HEAVY_CACHE_TTL:
        container_memory = list(cached.get("containers_memory") or [])
        disks = list(cached.get("disks") or [])
    else:
        container_memory = []
        try:
            container_memory = docker_service.get_container_memory_breakdown()
        except Exception:
            container_memory = []

        disks = []
        seen_mounts = set()
        for part in psutil.disk_partitions(all=False):
            mount = (part.mountpoint or "").strip()
            if not mount or mount in seen_mounts:
                continue
            seen_mounts.add(mount)
            if (part.fstype or "").lower() in IGNORED_FS_TYPES:
                continue
            try:
                usage = psutil.disk_usage(mount)
            except Exception:
                continue
            disks.append({
                "device": part.device or "unknown",
                "mountpoint": mount,
                "fstype": part.fstype or "unknown",
                "total_gb": round(usage.total / 1024**3, 2),
                "used_gb": round(usage.used / 1024**3, 2),
                "free_gb": round(usage.free / 1024**3, 2),
                "percent": round(float(usage.percent), 2),
            })

        disks.sort(key=lambda d: d["percent"], reverse=True)
        with admin_heavy_cache_lock:
            admin_heavy_cache["ts"] = now
            admin_heavy_cache["containers_memory"] = container_memory
            admin_heavy_cache["disks"] = disks
    return {
        "scope": "admin_server",
        "generated_by": username,
        "ram": {
            "used_gb": round(mem.used / 1024**3, 2),
            "total_gb": round(mem.total / 1024**3, 2),
            "percent": round(float(mem.percent), 2),
            "history": list(admin_history["ram_percent"]),
        },
        "network": {
            "tx_mbps": round(sent_speed, 3),
            "rx_mbps": round(recv_speed, 3),
            "history_tx": list(admin_history["network_tx_mbps"]),
            "history_rx": list(admin_history["network_rx_mbps"]),
        },
        "containers_memory": container_memory,
        "disks": disks,
        "updated_at": point_ts,
    }
