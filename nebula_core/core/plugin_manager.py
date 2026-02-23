# nebula_core/core/plugin_manager.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import base64
import importlib.util
import inspect
import json
import logging
import os
import py_compile
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..db import SYSTEM_DB, get_client_db, get_connection, normalize_client_db_name
from ..services.user_service import UserService
from .cgroup_v2 import CgroupV2Manager
from .plugin_api_v1 import ALLOWED_SCOPES, PLUGIN_API_VERSION, PluginError, PluginManifest, PluginPermissionError
from .plugin_grpc_client import GrpcPluginClient, resolve_token

logger = logging.getLogger("nebula_core.plugins")
PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")

PLUGIN_STATE_INITIALIZED = "initialized"
PLUGIN_STATE_HEALTHY = "healthy"
PLUGIN_STATE_DEGRADED = "degraded"
PLUGIN_STATE_UNRESPONSIVE = "unresponsive"
PLUGIN_STATE_CRASHED = "crashed"
PLUGIN_STATE_DISABLED = "disabled"


@dataclass
class PluginRuntime:
    process: Optional[subprocess.Popen] = None
    socket_path: str = ""
    token: str = ""
    plugin_dir: str = ""
    cgroup_path: str = ""
    cgroup_oom_kill_count: int = 0


@dataclass
class PluginRecord:
    name: str
    source: str
    manifest: PluginManifest
    status: str = PLUGIN_STATE_INITIALIZED
    message: str = ""
    error: str = ""
    initialized_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    plugin_obj: Any = None
    runtime: PluginRuntime = field(default_factory=PluginRuntime)
    runtime_version: str = "plugin_api_v1"
    warning: str = ""
    consecutive_timeouts: int = 0
    consecutive_health_failures: int = 0
    consecutive_crashes: int = 0
    restart_count: int = 0

    def as_public(self):
        return {
            "name": self.name,
            "source": self.source,
            "api_version": self.manifest.api_version,
            "runtime_version": self.runtime_version,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "scopes": self.manifest.sanitized_scopes(),
            "status": self.status,
            "message": self.message,
            "warning": self.warning,
            "error": self.error,
            "initialized_at": self.initialized_at,
            "updated_at": self.updated_at,
            "consecutive_timeouts": self.consecutive_timeouts,
            "consecutive_health_failures": self.consecutive_health_failures,
            "consecutive_crashes": self.consecutive_crashes,
            "restart_count": self.restart_count,
        }


class PluginContext:
    def __init__(self, plugin_name: str, scopes: List[str], event_bus: Any = None):
        self.plugin_name = plugin_name
        self.scopes = set(scopes or [])
        self._user_service = UserService()
        self._logger = logging.getLogger(f"nebula_core.plugin.{plugin_name}")
        self._event_bus = event_bus

    def require_scope(self, scope: str):
        if scope not in self.scopes:
            raise PluginPermissionError(f"Plugin '{self.plugin_name}' lacks scope '{scope}'")

    def log(self, level: str, message: str):
        line = str(message or "").strip()
        if not line:
            return
        lvl = str(level or "info").strip().lower()
        fn = getattr(self._logger, lvl, self._logger.info)
        fn(line)

    async def sync_user(self, username: str, db_name: str = "system.db", role_tag: str = "user", email: str = "", is_active: bool = True):
        self.require_scope("users.write")
        self.require_scope("identity_tags.write")
        return await asyncio.to_thread(
            self._sync_user_blocking,
            username,
            db_name,
            role_tag,
            email,
            is_active,
        )

    def _sync_user_blocking(self, username: str, db_name: str, role_tag: str, email: str, is_active: bool):
        clean_username = str(username or "").strip()
        if not clean_username:
            raise PluginError("username is required")

        clean_db = str(db_name or "system.db").strip() or "system.db"
        clean_role = str(role_tag or "user").strip().lower() or "user"
        clean_email = str(email or "").strip() or None

        if clean_db == "system.db":
            db_ctx = get_connection(SYSTEM_DB)
        else:
            db_ctx = get_client_db(clean_db, create_if_missing=True)

        with db_ctx as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? LIMIT 1",
                (clean_username,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET email = ?, is_active = ? WHERE username = ?",
                    (clean_email, 1 if is_active else 0, clean_username),
                )
                user_id = int(row["id"])
                action = "updated"
            else:
                random_password = secrets.token_urlsafe(24)
                password_hash = self._user_service.hash_password(random_password)
                cursor = conn.execute(
                    "INSERT INTO users (username, email, password_hash, is_active, is_staff) VALUES (?, ?, ?, ?, 0)",
                    (clean_username, clean_email, password_hash, 1 if is_active else 0),
                )
                user_id = int(cursor.lastrowid)
                action = "created"

        with get_connection(SYSTEM_DB) as sys_conn:
            sys_conn.execute(
                """
                INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(db_name, username) DO UPDATE SET
                    role_tag=excluded.role_tag,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (clean_db, clean_username, clean_role, f"plugin:{self.plugin_name}"),
            )

        return {
            "action": action,
            "username": clean_username,
            "db_name": clean_db,
            "role_tag": clean_role,
            "user_id": user_id,
        }

    @staticmethod
    def _normalize_role_token(role_tag: str) -> str:
        token = str(role_tag or "").strip().lower()
        token = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in token).strip("-_")
        return token or "user"

    @staticmethod
    def _normalize_db_name(db_name: str) -> str:
        raw = str(db_name or "system.db").strip() or "system.db"
        if raw == "system.db":
            return raw
        return normalize_client_db_name(raw)

    async def list_identity_roles(self):
        self.require_scope("roles.read")
        return await asyncio.to_thread(self._list_identity_roles_blocking)

    def _list_identity_roles_blocking(self):
        with self._readonly_db(SYSTEM_DB) as conn:
            try:
                rows = conn.execute(
                    "SELECT name, description, is_staff FROM identity_roles ORDER BY name ASC"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        return [dict(r) for r in rows]

    async def upsert_identity_role(self, name: str, description: str = "", is_staff: bool = False):
        self.require_scope("roles.write")
        clean_name = self._normalize_role_token(name)
        clean_desc = str(description or "").strip() or None
        return await asyncio.to_thread(
            self._upsert_identity_role_blocking,
            clean_name,
            clean_desc,
            bool(is_staff),
        )

    def _upsert_identity_role_blocking(self, name: str, description: Optional[str], is_staff: bool):
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO identity_roles (name, description, is_staff, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(name) DO UPDATE SET
                    description=excluded.description,
                    is_staff=excluded.is_staff,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (name, description, 1 if is_staff else 0, f"plugin:{self.plugin_name}"),
            )
        return {"status": "upserted", "name": name, "is_staff": bool(is_staff)}

    async def set_identity_tag(self, username: str, db_name: str, role_tag: str):
        self.require_scope("identity_tags.write")
        clean_username = str(username or "").strip()
        clean_db = self._normalize_db_name(db_name)
        clean_role = self._normalize_role_token(role_tag)
        if not clean_username:
            raise PluginError("username is required")
        return await asyncio.to_thread(self._set_identity_tag_blocking, clean_username, clean_db, clean_role)

    def _set_identity_tag_blocking(self, username: str, db_name: str, role_tag: str):
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO user_identity_tags (db_name, username, role_tag, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(db_name, username) DO UPDATE SET
                    role_tag=excluded.role_tag,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (db_name, username, role_tag, f"plugin:{self.plugin_name}"),
            )
        return {"status": "updated", "username": username, "db_name": db_name, "role_tag": role_tag}

    async def list_users(self, db_name: str = "system.db", limit: int = 200, offset: int = 0):
        self.require_scope("users.read")
        self.require_scope("identity_tags.read")
        clean_db = self._normalize_db_name(db_name)
        safe_limit = max(1, min(int(limit), 2000))
        safe_offset = max(0, int(offset))
        return await asyncio.to_thread(self._list_users_blocking, clean_db, safe_limit, safe_offset)

    def _list_users_blocking(self, db_name: str, limit: int, offset: int):
        target = SYSTEM_DB if db_name == "system.db" else os.path.join(os.path.dirname(SYSTEM_DB), "clients", db_name)
        with self._readonly_db(target) as conn:
            rows = conn.execute(
                "SELECT id, username, email, is_staff, is_active FROM users ORDER BY username ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        users = [dict(r) for r in rows]

        with self._readonly_db(SYSTEM_DB) as sys_conn:
            try:
                tag_rows = sys_conn.execute(
                    "SELECT username, role_tag FROM user_identity_tags WHERE db_name = ?",
                    (db_name,),
                ).fetchall()
            except sqlite3.OperationalError:
                tag_rows = []
        tag_map = {r["username"]: r["role_tag"] for r in tag_rows}
        for row in users:
            row["role_tag"] = tag_map.get(row["username"], "admin" if bool(row.get("is_staff")) else "user")
        return {"db_name": db_name, "count": len(users), "items": users}

    @staticmethod
    @contextmanager
    def _readonly_db(path: str):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    async def emit_event(self, event_name: str, payload: Optional[dict] = None):
        self.require_scope("events.emit")
        if self._event_bus is None:
            raise PluginError("Event bus is unavailable")
        clean_event = str(event_name or "").strip()
        if not clean_event:
            raise PluginError("event_name is required")
        safe_event = f"plugin.{self.plugin_name}.{clean_event}"
        await self._event_bus.emit(safe_event, payload or {})
        return {"status": "emitted", "event": safe_event}


class GrpcPluginAdapter:
    api_version = PLUGIN_API_VERSION

    def __init__(self, name: str, endpoint: str, token_env: str = "", token: str = "", allow_remote: bool = False):
        self.name = name
        resolved_token = str(token or "").strip() or resolve_token(token_env)
        self.client = GrpcPluginClient(endpoint=endpoint, token=resolved_token, allow_remote=allow_remote)

    async def initialize(self, context: PluginContext):
        context.log("info", f"gRPC plugin adapter initialized for {self.name}")

    async def health(self, timeout: float = 3.0):
        data = self.client.health(timeout=timeout)
        return data or {"status": "unreachable"}

    async def sync_users(self, payload: Optional[Dict[str, Any]] = None, timeout: float = 10.0):
        data = self.client.sync_users(payload or {}, timeout=timeout)
        if data is None:
            raise PluginError("gRPC plugin did not return response")
        return data

    async def shutdown(self):
        self.client.close()


class PluginManager:
    def __init__(self, config: Optional[dict] = None, event_bus: Any = None):
        self.config = config or {}
        self.event_bus = event_bus
        self.enabled = bool(self.config.get("enabled", True))
        self.environment = str(self.config.get("environment") or os.getenv("ENV", "development")).strip().lower()
        self.dev_mode = self.environment not in ("prod", "production")

        self.in_process_enabled = bool(self.config.get("in_process_enabled", True)) and self.dev_mode
        self.process_runtime_enabled = bool(self.config.get("process_runtime_enabled", not self.dev_mode))
        self.init_timeout_sec = self._clamp_timeout(float(self.config.get("init_timeout_sec", 5.0)))
        self.default_timeout_sec = self._clamp_timeout(float(self.config.get("default_timeout_sec", 10.0)))
        self.max_timeout_sec = self._clamp_timeout(float(self.config.get("max_timeout_sec", 30.0)))
        self.call_timeout_sec = min(self._clamp_timeout(float(self.config.get("call_timeout_sec", self.default_timeout_sec))), self.max_timeout_sec)

        self.allow_remote_grpc = bool(self.config.get("allow_remote_grpc", False))
        self.scan_path = self._resolve_scan_path(str(self.config.get("scan_path", "plugins")))

        self.memory_limit_mb = max(64, int(self.config.get("memory_limit_mb", 128)))
        self.cpu_time_limit_sec = max(1, int(self.config.get("cpu_time_limit_sec", 30)))
        self.health_interval_sec = max(5, int(self.config.get("health_interval_sec", 30)))
        self.max_restarts = max(1, int(self.config.get("max_restarts", 3)))
        self.max_crashes = max(1, int(self.config.get("max_crashes", 3)))
        self.timeout_restart_threshold = max(1, int(self.config.get("timeout_restart_threshold", 3)))
        self.health_restart_threshold = max(1, int(self.config.get("health_restart_threshold", 2)))

        self.runtime_socket_dir = Path(self.config.get("runtime_socket_dir") or "/tmp/nebula/plugins").resolve()
        self.runtime_log_dir = Path(self.config.get("runtime_log_dir") or "/tmp/nebula/plugin-logs").resolve()
        self.runner_command = self._parse_runner_command(
            self.config.get("runner_command") or [sys.executable, "-m", "nebula_core.core.plugin_runner"]
        )
        self.cgroup_enabled = bool(self.config.get("cgroup_enabled", not self.dev_mode))
        self.cgroup_required = bool(self.config.get("cgroup_required", not self.dev_mode))
        self.cgroup_manager = CgroupV2Manager(
            enabled=self.cgroup_enabled,
            required=self.cgroup_required,
            root=str(self.config.get("cgroup_root") or "auto"),
            memory_limit_mb=self.memory_limit_mb,
            cpu_quota_us=int(self.config.get("cgroup_cpu_quota_us", 50000)),
            cpu_period_us=int(self.config.get("cgroup_cpu_period_us", 100000)),
            pids_max=int(self.config.get("cgroup_pids_max", 128)),
        )
        self._cgroup_ready = False

        self._lock = asyncio.Lock()
        self._plugins: Dict[str, PluginRecord] = {}
        self._health_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        if bool(self.config.get("in_process_enabled", True)) and not self.dev_mode:
            logger.warning("In-process plugins are disabled outside DEV mode")

    @staticmethod
    def _parse_runner_command(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        text = str(value or "").strip()
        if not text:
            return [sys.executable, "-m", "nebula_core.core.plugin_runner"]
        return text.split()

    @staticmethod
    def _clamp_timeout(value: float) -> float:
        return max(0.1, min(float(value), 30.0))

    @staticmethod
    def _resolve_scan_path(value: str) -> Path:
        if not value:
            value = "plugins"
        base = Path(__file__).resolve().parents[1]
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (base / candidate).resolve()

    async def initialize(self):
        if not self.enabled:
            logger.info("Plugin manager disabled by config")
            return

        self._cgroup_ready = False
        ok, msg = self.cgroup_manager.initialize()
        if not ok:
            if self.cgroup_enabled and self.cgroup_required:
                raise PluginError(f"cgroup v2 backend initialization failed: {msg}")
            logger.warning("cgroup backend unavailable, continuing without hard isolation: %s", msg)
        else:
            logger.info("cgroup backend: %s", msg)
            self._cgroup_ready = self.cgroup_enabled

        self.runtime_socket_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_log_dir.mkdir(parents=True, exist_ok=True)
        await self.rescan()
        self._stop_event.clear()
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_monitor_loop(), name="plugin-health-monitor")

    async def shutdown(self):
        self._stop_event.set()
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except Exception:
                pass
            self._health_task = None

        async with self._lock:
            items = list(self._plugins.values())
        for rec in items:
            await self._shutdown_record(rec)

    def list_plugins(self) -> List[dict]:
        items = [rec.as_public() for rec in self._plugins.values()]
        return sorted(items, key=lambda x: (x.get("source", ""), x.get("name", "")))

    async def rescan(self):
        if not self.enabled:
            return []
        discovered: Dict[str, PluginRecord] = {}

        if self.in_process_enabled:
            discovered.update(await self._scan_in_process_plugins())

        if self.process_runtime_enabled:
            discovered.update(await self._scan_process_plugins())
        discovered.update(await self._scan_external_grpc_plugins())

        async with self._lock:
            old_plugins = self._plugins
            self._plugins = discovered

        for name, rec in old_plugins.items():
            if name not in discovered:
                await self._shutdown_record(rec)

        return self.list_plugins()

    async def plugin_health(self, name: str) -> dict:
        rec = self._plugins.get(name)
        if not rec:
            raise PluginError("Plugin not found")
        data = await self._safe_call(rec, "health")
        return data if isinstance(data, dict) else {"status": "unknown", "raw": data}

    async def plugin_sync_users(self, name: str, payload: Optional[dict] = None) -> dict:
        rec = self._plugins.get(name)
        if not rec:
            raise PluginError("Plugin not found")
        data = await self._safe_call(rec, "sync_users", payload or {})
        return data if isinstance(data, dict) else {"status": "ok", "raw": data}

    async def _scan_external_grpc_plugins(self) -> Dict[str, PluginRecord]:
        out: Dict[str, PluginRecord] = {}
        external = self.config.get("external")
        if not isinstance(external, list):
            return out

        for item in external:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            endpoint = str(item.get("endpoint") or "").strip()
            if not name or not endpoint:
                continue
            if not PLUGIN_NAME_RE.fullmatch(name):
                logger.warning("Skipped external plugin with invalid name: %s", name)
                continue

            manifest = PluginManifest(
                name=name,
                version=str(item.get("version") or "0.1.0"),
                description=str(item.get("description") or "external gRPC plugin (deprecated runtime v1)"),
                scopes=[s for s in (item.get("scopes") or []) if isinstance(s, str) and s in ALLOWED_SCOPES],
                source="grpc",
            )
            rec = PluginRecord(name=name, source="grpc", manifest=manifest, runtime_version="plugin_api_v1")
            rec.warning = "plugin_api_v1 is deprecated; migrate to plugin_runtime_v2"
            try:
                rec.plugin_obj = GrpcPluginAdapter(
                    name=name,
                    endpoint=endpoint,
                    token_env=str(item.get("token_env") or "").strip(),
                    allow_remote=self.allow_remote_grpc,
                )
                await self._initialize_plugin(rec)
            except Exception as exc:
                rec.status = PLUGIN_STATE_DEGRADED
                rec.error = str(exc)
                rec.message = "gRPC plugin load failed"
                rec.updated_at = time.time()
                logger.exception("Failed to initialize external plugin %s", name)
            out[name] = rec
        return out

    async def _scan_process_plugins(self) -> Dict[str, PluginRecord]:
        out: Dict[str, PluginRecord] = {}
        scan_root = self.scan_path
        if not scan_root.exists() or not scan_root.is_dir():
            logger.warning("Plugin scan path does not exist: %s", scan_root)
            return out

        for entry in sorted(scan_root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            name = entry.name
            if not PLUGIN_NAME_RE.fullmatch(name):
                logger.warning("Skipped plugin with invalid name: %s", name)
                continue

            plugin_file = entry / "plugin.py"
            if not plugin_file.exists():
                continue

            try:
                manifest = self._load_manifest(name, entry, source="process")
            except Exception as exc:
                rec = PluginRecord(
                    name=name,
                    source="process",
                    manifest=PluginManifest(name=name, source="process"),
                    runtime_version="plugin_runtime_v2",
                )
                rec.status = PLUGIN_STATE_DEGRADED
                rec.error = str(exc)
                rec.message = "invalid manifest"
                rec.updated_at = time.time()
                out[name] = rec
                continue

            rec = PluginRecord(name=name, source="process", manifest=manifest, runtime_version="plugin_runtime_v2")
            try:
                await self._start_process_plugin(rec, entry)
                await self._initialize_plugin(rec)
            except Exception as exc:
                rec.status = PLUGIN_STATE_CRASHED
                rec.error = str(exc)
                rec.message = "process plugin start failed"
                rec.updated_at = time.time()
                logger.exception("Failed to start process plugin %s", name)
            out[name] = rec
        return out

    async def _scan_in_process_plugins(self) -> Dict[str, PluginRecord]:
        out: Dict[str, PluginRecord] = {}
        scan_root = self.scan_path
        if not scan_root.exists() or not scan_root.is_dir():
            logger.warning("Plugin scan path does not exist: %s", scan_root)
            return out

        logger.warning("DEV mode: in-process plugins are enabled")
        for entry in sorted(scan_root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            name = entry.name
            if not PLUGIN_NAME_RE.fullmatch(name):
                logger.warning("Skipped plugin with invalid name: %s", name)
                continue

            plugin_file = entry / "plugin.py"
            if not plugin_file.exists():
                logger.info("Plugin %s skipped: plugin.py not found", name)
                continue

            manifest = self._load_manifest(name, entry, source="in_process")
            rec = PluginRecord(name=name, source="in_process", manifest=manifest)
            rec.warning = "DEV ONLY: in-process plugins are forbidden in production"

            try:
                self._compile_plugin(entry)
                rec.status = PLUGIN_STATE_INITIALIZED
                rec.message = "compiled"
                rec.updated_at = time.time()

                module = self._import_plugin_module(name, plugin_file)
                rec.plugin_obj = self._create_plugin_instance(module, name)
                await self._initialize_plugin(rec)
            except Exception as exc:
                rec.status = PLUGIN_STATE_DEGRADED
                rec.error = str(exc)
                rec.message = "load failed"
                rec.updated_at = time.time()
                logger.exception("Plugin %s failed to load", name)

            out[name] = rec
        return out

    def _load_manifest(self, name: str, plugin_dir: Path, source: str = "in_process") -> PluginManifest:
        manifest_file = plugin_dir / "plugin.json"
        raw = {}
        if manifest_file.exists():
            try:
                raw = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise PluginError(f"Invalid plugin.json: {exc}")

        scopes = raw.get("scopes") if isinstance(raw.get("scopes"), list) else []
        scopes = [s for s in scopes if isinstance(s, str) and s in ALLOWED_SCOPES]
        api_version = str(raw.get("api_version") or PLUGIN_API_VERSION).strip() or PLUGIN_API_VERSION
        if api_version != PLUGIN_API_VERSION:
            raise PluginError(f"Unsupported plugin api_version: {api_version}")

        return PluginManifest(
            name=name,
            version=str(raw.get("version") or "0.1.0"),
            description=str(raw.get("description") or ""),
            scopes=scopes,
            api_version=api_version,
            source=source,
        )

    def _compile_plugin(self, plugin_dir: Path):
        for root, _, files in os.walk(plugin_dir):
            for file_name in files:
                if not file_name.endswith(".py"):
                    continue
                target = (Path(root) / file_name).resolve()
                if plugin_dir.resolve() not in target.parents and target != plugin_dir.resolve():
                    raise PluginError("Plugin file escapes plugin directory")
                py_compile.compile(str(target), doraise=True)

    def _import_plugin_module(self, name: str, plugin_file: Path):
        module_name = f"nebula_plugin_{name}_{int(time.time() * 1000)}"
        spec = importlib.util.spec_from_file_location(module_name, str(plugin_file))
        if not spec or not spec.loader:
            raise PluginError("Unable to create module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _create_plugin_instance(self, module: Any, name: str):
        api_version = str(getattr(module, "PLUGIN_API_VERSION", PLUGIN_API_VERSION)).strip()
        if api_version != PLUGIN_API_VERSION:
            raise PluginError(f"Plugin {name} has unsupported API version: {api_version}")

        factory = getattr(module, "create_plugin", None)
        if not callable(factory):
            raise PluginError("Plugin must expose create_plugin()")
        instance = factory()
        if instance is None:
            raise PluginError("create_plugin() returned None")
        return instance

    async def _start_process_plugin(self, rec: PluginRecord, plugin_dir: Path):
        await self._shutdown_process(rec)

        socket_path = self.runtime_socket_dir / f"{rec.name}.sock"
        token = self._generate_scoped_token(rec)
        if socket_path.exists():
            socket_path.unlink(missing_ok=True)

        cmd = list(self.runner_command) + [
            "--plugin-name", rec.name,
            "--plugin-dir", str(plugin_dir),
            "--socket", str(socket_path),
            "--token", token,
            "--memory-mb", str(self.memory_limit_mb),
            "--cpu-seconds", str(self.cpu_time_limit_sec),
            "--log-dir", str(self.runtime_log_dir),
        ]
        logger.info("Starting plugin process %s: %s", rec.name, " ".join(cmd))

        cgroup_path = ""
        if self._cgroup_ready:
            cgroup = self.cgroup_manager.create_group(rec.name)
            cgroup_path = str(cgroup)
            rec.runtime.cgroup_path = cgroup_path

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            if cgroup_path:
                self.cgroup_manager.cleanup_group(cgroup_path)
                rec.runtime.cgroup_path = ""
            raise

        if cgroup_path:
            try:
                self.cgroup_manager.assign_pid(Path(cgroup_path), proc.pid)
                events = self.cgroup_manager.memory_events(cgroup_path)
                rec.runtime.cgroup_oom_kill_count = int(events.get("oom_kill", 0))
            except Exception as exc:
                proc.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=2.0)
                except Exception:
                    proc.kill()
                self.cgroup_manager.cleanup_group(cgroup_path)
                rec.runtime.cgroup_path = ""
                raise PluginError(f"failed to assign process to cgroup: {exc}")

        rec.runtime.process = proc
        rec.runtime.socket_path = str(socket_path)
        rec.runtime.token = token
        rec.runtime.plugin_dir = str(plugin_dir)
        rec.plugin_obj = GrpcPluginAdapter(
            name=rec.name,
            endpoint=f"unix://{socket_path}",
            token=token,
            allow_remote=False,
        )

        deadline = time.time() + self.init_timeout_sec
        last_error = "plugin process failed to start"
        while time.time() < deadline:
            if proc.poll() is not None:
                last_error = f"process exited with code {proc.returncode}"
                break
            try:
                data = await self._invoke(rec.plugin_obj, "health", timeout=min(self.default_timeout_sec, 3.0))
                if isinstance(data, dict):
                    rec.status = PLUGIN_STATE_HEALTHY
                    rec.message = "process started"
                    rec.error = ""
                    rec.updated_at = time.time()
                    return
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(0.2)

        await self._shutdown_process(rec)
        raise PluginError(last_error)

    async def _initialize_plugin(self, rec: PluginRecord):
        if rec.source == "process":
            rec.status = PLUGIN_STATE_INITIALIZED
            rec.message = "initialized"
            rec.initialized_at = time.time()
            rec.updated_at = rec.initialized_at
            logger.info("Plugin initialized (runtime_v2): %s", rec.name)
            return

        context = PluginContext(rec.name, rec.manifest.sanitized_scopes(), event_bus=self.event_bus)
        await self._invoke(rec.plugin_obj, "initialize", context, timeout=self.init_timeout_sec)
        rec.status = PLUGIN_STATE_INITIALIZED
        rec.message = "initialized"
        rec.initialized_at = time.time()
        rec.updated_at = rec.initialized_at
        logger.info("Plugin initialized: %s", rec.name)

    async def _safe_call(self, rec: PluginRecord, method: str, *args):
        if rec.status == PLUGIN_STATE_DISABLED:
            raise PluginError("Plugin is disabled")

        if rec.source == "process" and self._is_oom_killed(rec):
            await self._mark_crashed(rec, "cgroup oom_kill")
            await self._maybe_restart(rec, reason="oom_kill")

        if rec.source == "process" and not self._is_process_alive(rec):
            await self._mark_crashed(rec, "process is not running")
            await self._maybe_restart(rec, reason="crash")

        timeout = min(self.call_timeout_sec, self.max_timeout_sec)
        try:
            invoke_args = args
            if rec.source in ("process", "grpc") and method in ("health", "sync_users"):
                invoke_args = (*args, timeout)
            data = await self._invoke(rec.plugin_obj, method, *invoke_args, timeout=timeout)
            rec.status = PLUGIN_STATE_HEALTHY
            rec.message = f"{method} ok"
            rec.error = ""
            rec.updated_at = time.time()
            rec.consecutive_timeouts = 0
            rec.consecutive_health_failures = 0
            return data
        except asyncio.TimeoutError:
            await self._handle_timeout(rec, method)
            raise PluginError(f"Plugin {method} timed out after {timeout:.1f}s")
        except Exception as exc:
            if rec.source == "process" and not self._is_process_alive(rec):
                await self._mark_crashed(rec, str(exc))
                await self._maybe_restart(rec, reason="crash")
            else:
                rec.status = PLUGIN_STATE_DEGRADED
                rec.message = f"{method} failed"
                rec.error = str(exc)
                rec.updated_at = time.time()
            logger.exception("Plugin %s method %s failed", rec.name, method)
            raise PluginError(str(exc))

    async def _handle_timeout(self, rec: PluginRecord, method: str):
        rec.consecutive_timeouts += 1
        rec.status = PLUGIN_STATE_DEGRADED
        rec.message = f"{method} timeout"
        rec.error = f"timeout ({self.call_timeout_sec:.1f}s)"
        rec.updated_at = time.time()
        logger.warning("Plugin %s timed out (%s), consecutive=%d", rec.name, method, rec.consecutive_timeouts)

        if rec.consecutive_timeouts >= self.timeout_restart_threshold:
            await self._maybe_restart(rec, reason="timeouts")

    async def _mark_crashed(self, rec: PluginRecord, reason: str):
        rec.consecutive_crashes += 1
        rec.status = PLUGIN_STATE_CRASHED
        rec.message = "process crashed"
        rec.error = reason
        rec.updated_at = time.time()
        logger.error("Plugin crashed: %s (%s), consecutive=%d", rec.name, reason, rec.consecutive_crashes)

        if rec.consecutive_crashes >= self.max_crashes:
            rec.status = PLUGIN_STATE_DISABLED
            rec.message = "disabled after repeated crashes"
            logger.error("Plugin disabled after %d crashes: %s", rec.consecutive_crashes, rec.name)

    async def _maybe_restart(self, rec: PluginRecord, reason: str):
        if rec.source != "process":
            return
        if rec.status == PLUGIN_STATE_DISABLED:
            return
        if rec.restart_count >= self.max_restarts:
            rec.status = PLUGIN_STATE_DISABLED
            rec.message = "disabled after restart budget exhaustion"
            rec.error = f"restart budget exceeded ({self.max_restarts})"
            rec.updated_at = time.time()
            logger.error("Plugin disabled due to restart policy: %s", rec.name)
            return

        logger.warning("Restarting plugin %s due to %s", rec.name, reason)
        rec.restart_count += 1
        try:
            await self._start_process_plugin(rec, Path(rec.runtime.plugin_dir))
            rec.status = PLUGIN_STATE_INITIALIZED
            rec.message = f"restarted after {reason}"
            rec.error = ""
            rec.updated_at = time.time()
            rec.consecutive_timeouts = 0
            rec.consecutive_health_failures = 0
        except Exception as exc:
            await self._mark_crashed(rec, str(exc))

    async def _health_monitor_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.health_interval_sec)
            async with self._lock:
                items = list(self._plugins.values())

            for rec in items:
                try:
                    await self._health_check(rec)
                except Exception:
                    logger.exception("Plugin health monitor error for %s", rec.name)

    async def _health_check(self, rec: PluginRecord):
        if rec.status == PLUGIN_STATE_DISABLED:
            return

        if rec.source == "process" and self._is_oom_killed(rec):
            await self._mark_crashed(rec, "cgroup oom_kill")
            await self._maybe_restart(rec, reason="oom_kill")
            return

        if rec.source == "process" and not self._is_process_alive(rec):
            await self._mark_crashed(rec, "health check detected dead process")
            await self._maybe_restart(rec, reason="health failure")
            return

        try:
            invoke_args = ()
            if rec.source in ("process", "grpc"):
                invoke_args = (min(self.default_timeout_sec, 5.0),)
            data = await self._invoke(rec.plugin_obj, "health", *invoke_args, timeout=self.default_timeout_sec)
            if not isinstance(data, dict):
                raise PluginError("invalid health payload")
            rec.consecutive_health_failures = 0
            rec.status = PLUGIN_STATE_HEALTHY
            rec.message = "health ok"
            rec.error = ""
            rec.updated_at = time.time()
        except Exception as exc:
            rec.consecutive_health_failures += 1
            rec.status = PLUGIN_STATE_DEGRADED
            rec.message = "health failed"
            rec.error = str(exc)
            rec.updated_at = time.time()
            logger.warning(
                "Plugin health failure: %s (consecutive=%d)",
                rec.name,
                rec.consecutive_health_failures,
            )
            if rec.consecutive_health_failures >= self.health_restart_threshold:
                rec.status = PLUGIN_STATE_UNRESPONSIVE
                await self._maybe_restart(rec, reason="health failures")

    async def _shutdown_record(self, rec: PluginRecord):
        try:
            await self._invoke(rec.plugin_obj, "shutdown", timeout=3.0)
        except Exception:
            pass
        await self._shutdown_process(rec)

    async def _shutdown_process(self, rec: PluginRecord):
        proc = rec.runtime.process
        rec.runtime.process = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3.0)
            except Exception:
                proc.kill()
                try:
                    await asyncio.to_thread(proc.wait)
                except Exception:
                    pass
        self._cleanup_cgroup(rec)

    def _is_process_alive(self, rec: PluginRecord) -> bool:
        proc = rec.runtime.process
        return proc is not None and proc.poll() is None

    def _cleanup_cgroup(self, rec: PluginRecord):
        path = rec.runtime.cgroup_path
        rec.runtime.cgroup_path = ""
        rec.runtime.cgroup_oom_kill_count = 0
        if not path:
            return
        self.cgroup_manager.cleanup_group(path)

    def _is_oom_killed(self, rec: PluginRecord) -> bool:
        if not rec.runtime.cgroup_path:
            return False
        try:
            events = self.cgroup_manager.memory_events(rec.runtime.cgroup_path)
            current = int(events.get("oom_kill", 0))
            previous = int(rec.runtime.cgroup_oom_kill_count or 0)
            if current > previous:
                rec.runtime.cgroup_oom_kill_count = current
                return True
            return False
        except Exception:
            return False

    def _generate_scoped_token(self, rec: PluginRecord) -> str:
        payload = {
            "plugin_name": rec.name,
            "scopes": rec.manifest.sanitized_scopes(),
            "exp": int(time.time()) + 300,
            "nonce": secrets.token_urlsafe(12),
        }
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")

    async def _invoke(self, plugin_obj: Any, method_name: str, *args, timeout: float = 10.0):
        fn = getattr(plugin_obj, method_name, None)
        if not callable(fn):
            if method_name in ("health", "sync_users"):
                raise PluginError(f"Plugin method {method_name} is not implemented")
            return None

        if inspect.iscoroutinefunction(fn):
            return await asyncio.wait_for(fn(*args), timeout=max(0.1, float(timeout)))

        async def _run_sync():
            return await asyncio.to_thread(fn, *args)

        return await asyncio.wait_for(_run_sync(), timeout=max(0.1, float(timeout)))
