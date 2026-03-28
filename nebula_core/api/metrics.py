# nebula_core/api/metrics.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import threading

from fastapi import APIRouter, HTTPException, Query, Request
import psutil
import time

from .security import require_session
from ..services.docker_service import DockerService
from ..services.metrics_service import metrics_service

router = APIRouter(prefix="/metrics", tags=["Metrics"])

docker_service = DockerService()
IGNORED_FS_TYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay",
    "squashfs", "nsfs", "tracefs", "mqueue", "hugetlbfs", "ramfs",
    "autofs", "fusectl", "debugfs", "securityfs", "pstore", "configfs", "efivarfs"
}

admin_cache = {
    "containers": {"ts": 0.0, "data": [], "summary": {}, "summary_ts": 0.0},
    "disks": {"ts": 0.0, "data": []},
}
admin_cache_lock = threading.Lock()
ADMIN_SUMMARY_CACHE_TTL = 8.0
ADMIN_CONTAINERS_CACHE_TTL = 12.0
ADMIN_DISKS_CACHE_TTL = 45.0

@router.get("/current")
async def get_current_metrics():
    return await collect_current_metrics()


async def collect_current_metrics():
    snapshot = metrics_service.get_snapshot()
    if int(snapshot.get("timestamp") or 0) <= 0:
        snapshot = await asyncio.to_thread(metrics_service._collect_snapshot)
    return snapshot


def _health_status(cpu_percent: float, ram_percent: float, disk_percent: float) -> str:
    max_pressure = max(cpu_percent or 0.0, ram_percent or 0.0, disk_percent or 0.0)
    if max_pressure >= 90:
        return "critical"
    if max_pressure >= 75:
        return "elevated"
    if max_pressure >= 55:
        return "stable"
    return "optimal"


def _get_cached_admin_containers(username: str, db_name: str) -> tuple[dict, list]:
    now = time.time()
    with admin_cache_lock:
        cached = dict(admin_cache["containers"])
    if (now - float(cached.get("ts") or 0.0)) <= ADMIN_CONTAINERS_CACHE_TTL:
        return dict(cached.get("summary") or {}), list(cached.get("data") or [])

    summary = _get_cached_admin_summary(username, db_name)

    container_memory = []
    try:
        container_memory = docker_service.get_container_memory_breakdown()
    except Exception:
        container_memory = list(cached.get("data") or [])

    with admin_cache_lock:
        admin_cache["containers"] = {
            "ts": now,
            "summary": summary,
            "data": container_memory,
            "summary_ts": float(cached.get("summary_ts") or 0.0),
        }
    return summary, container_memory


def _get_cached_admin_summary(username: str, db_name: str) -> dict:
    now = time.time()
    with admin_cache_lock:
        cached = dict(admin_cache["containers"])

    cached_summary = dict(cached.get("summary") or {})
    summary_ts = float(cached.get("summary_ts") or 0.0)
    if cached_summary and (now - summary_ts) <= ADMIN_SUMMARY_CACHE_TTL:
        return cached_summary

    try:
        summary = docker_service.get_usage_summary(username, db_name, True)
    except Exception:
        return cached_summary

    with admin_cache_lock:
        current = dict(admin_cache["containers"])
        admin_cache["containers"] = {
            "ts": float(current.get("ts") or 0.0),
            "summary": summary,
            "data": list(current.get("data") or []),
            "summary_ts": now,
        }
    return dict(summary)


def _get_cached_admin_disks() -> list:
    now = time.time()
    with admin_cache_lock:
        cached = dict(admin_cache["disks"])
    if (now - float(cached.get("ts") or 0.0)) <= ADMIN_DISKS_CACHE_TTL:
        return list(cached.get("data") or [])

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
    with admin_cache_lock:
        admin_cache["disks"] = {"ts": now, "data": disks}
    return disks


@router.get("/admin/dashboard")
async def get_admin_dashboard_metrics(
    request: Request,
    include_containers: bool = Query(default=True),
    include_disks: bool = Query(default=True),
):
    username, db_name, is_staff = require_session(request)
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")

    snapshot = await collect_current_metrics()
    dashboard_history = metrics_service.get_dashboard_history()
    point_ts = int(snapshot.get("timestamp") or time.time())
    summary = {}
    container_memory = None
    disks = None

    summary = await asyncio.to_thread(_get_cached_admin_summary, username, db_name)

    thread_jobs = []
    if include_containers:
        thread_jobs.append(asyncio.to_thread(_get_cached_admin_containers, username, db_name))
    if include_disks:
        thread_jobs.append(asyncio.to_thread(_get_cached_admin_disks))

    if thread_jobs:
        results = await asyncio.gather(*thread_jobs, return_exceptions=True)
        idx = 0
        if include_containers:
            result = results[idx]
            idx += 1
            if not isinstance(result, Exception):
                _, container_memory = result
        if include_disks:
            result = results[idx]
            if not isinstance(result, Exception):
                disks = result

    cpu_percent = float(snapshot.get("cpu_percent") or 0.0)
    ram_percent = float(snapshot.get("ram_percent_value") or 0.0)
    disk_percent = float(snapshot.get("disk_percent_value") or 0.0)
    return {
        "scope": "admin_server",
        "generated_by": username,
        "overview": {
            "cpu": snapshot.get("cpu", "0.0%"),
            "ram": snapshot.get("ram_percent", "0.0%"),
            "disk": snapshot.get("disk_percent", "0.0%"),
            "network": f"↑ {float(snapshot.get('network_sent_mb') or 0.0):.2f} MB/s  ↓ {float(snapshot.get('network_recv_mb') or 0.0):.2f} MB/s",
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "disk_percent": disk_percent,
            "network_sent_mb": round(float(snapshot.get("network_sent_mb") or 0.0), 3),
            "network_recv_mb": round(float(snapshot.get("network_recv_mb") or 0.0), 3),
            "ram_used_gb": round(float(snapshot.get("ram_used_gb") or 0.0), 2),
            "ram_total_gb": round(float(snapshot.get("ram_total_gb") or 0.0), 2),
            "disk_used_gb": round(float(snapshot.get("disk_used_gb") or 0.0), 2),
            "disk_total_gb": round(float(snapshot.get("disk_total_gb") or 0.0), 2),
            "containers": int((summary or {}).get("total_containers") or 0),
            "active_containers": int((summary or {}).get("running_containers") or 0),
            "servers": 1,
            "alerts": 0,
            "tasks": 0,
            "health_status": _health_status(cpu_percent, ram_percent, disk_percent),
        },
        "ram": dashboard_history["ram"],
        "network": dashboard_history["network"],
        "containers_memory": container_memory,
        "disks": disks,
        "included": {
            "containers": bool(include_containers),
            "disks": bool(include_disks),
        },
        "updated_at": point_ts,
    }
