import asyncio
import importlib.util
import inspect
import json
import logging
import os
import py_compile
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..db import SYSTEM_DB, get_client_db, get_connection, normalize_client_db_name
from ..services.user_service import UserService
from .plugin_api_v1 import ALLOWED_SCOPES, PLUGIN_API_VERSION, PluginError, PluginManifest, PluginPermissionError
from .plugin_grpc_client import GrpcPluginClient, resolve_token

logger = logging.getLogger("nebula_core.plugins")
PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")


@dataclass
class PluginRecord:
    name: str
    source: str
    manifest: PluginManifest
    status: str = "discovered"
    message: str = ""
    error: str = ""
    initialized_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    plugin_obj: Any = None

    def as_public(self):
        return {
            "name": self.name,
            "source": self.source,
            "api_version": self.manifest.api_version,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "scopes": self.manifest.sanitized_scopes(),
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "initialized_at": self.initialized_at,
            "updated_at": self.updated_at,
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

    def __init__(self, name: str, endpoint: str, token_env: str = "", allow_remote: bool = False):
        self.name = name
        self.client = GrpcPluginClient(endpoint=endpoint, token=resolve_token(token_env), allow_remote=allow_remote)

    async def initialize(self, context: PluginContext):
        context.log("info", f"gRPC plugin adapter initialized for {self.name}")

    async def health(self):
        data = self.client.health()
        return data or {"status": "unreachable"}

    async def sync_users(self, payload: Optional[Dict[str, Any]] = None):
        data = self.client.sync_users(payload or {})
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
        self.in_process_enabled = bool(self.config.get("in_process_enabled", True))
        self.init_timeout_sec = float(self.config.get("init_timeout_sec", 5.0))
        self.call_timeout_sec = float(self.config.get("call_timeout_sec", 20.0))
        self.allow_remote_grpc = bool(self.config.get("allow_remote_grpc", False))
        self.scan_path = self._resolve_scan_path(str(self.config.get("scan_path", "plugins")))
        self._lock = asyncio.Lock()
        self._plugins: Dict[str, PluginRecord] = {}

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
        await self.rescan()

    async def shutdown(self):
        async with self._lock:
            items = list(self._plugins.values())
        for rec in items:
            try:
                await self._safe_call(rec, "shutdown")
            except Exception:
                pass

    def list_plugins(self) -> List[dict]:
        items = [rec.as_public() for rec in self._plugins.values()]
        return sorted(items, key=lambda x: (x.get("source", ""), x.get("name", "")))

    async def rescan(self):
        if not self.enabled:
            return []
        discovered: Dict[str, PluginRecord] = {}

        if self.in_process_enabled:
            discovered.update(await self._scan_in_process_plugins())

        discovered.update(await self._scan_external_grpc_plugins())

        async with self._lock:
            old_plugins = self._plugins
            self._plugins = discovered

        for name, rec in old_plugins.items():
            if name not in discovered:
                try:
                    await self._safe_call(rec, "shutdown")
                except Exception:
                    pass

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
                description=str(item.get("description") or "external gRPC plugin"),
                scopes=[s for s in (item.get("scopes") or []) if isinstance(s, str) and s in ALLOWED_SCOPES],
                source="grpc",
            )
            rec = PluginRecord(name=name, source="grpc", manifest=manifest)
            try:
                rec.plugin_obj = GrpcPluginAdapter(
                    name=name,
                    endpoint=endpoint,
                    token_env=str(item.get("token_env") or "").strip(),
                    allow_remote=self.allow_remote_grpc,
                )
                await self._initialize_plugin(rec)
            except Exception as exc:
                rec.status = "failed"
                rec.error = str(exc)
                rec.message = "gRPC plugin load failed"
                rec.updated_at = time.time()
                logger.exception("Failed to initialize external plugin %s", name)
            out[name] = rec
        return out

    async def _scan_in_process_plugins(self) -> Dict[str, PluginRecord]:
        out: Dict[str, PluginRecord] = {}
        scan_root = self.scan_path
        if not scan_root.exists() or not scan_root.is_dir():
            logger.warning("Plugin scan path does not exist: %s", scan_root)
            return out

        logger.info("Scanning plugins in %s", scan_root)
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

            manifest = self._load_manifest(name, entry)
            rec = PluginRecord(name=name, source="in_process", manifest=manifest)

            try:
                self._compile_plugin(entry)
                rec.status = "compiled"
                rec.message = "compiled"
                rec.updated_at = time.time()
                logger.info("Plugin %s compiled", name)

                module = self._import_plugin_module(name, plugin_file)
                rec.plugin_obj = self._create_plugin_instance(module, name)
                await self._initialize_plugin(rec)
            except Exception as exc:
                rec.status = "failed"
                rec.error = str(exc)
                rec.message = "load failed"
                rec.updated_at = time.time()
                logger.exception("Plugin %s failed to load", name)

            out[name] = rec
        return out

    def _load_manifest(self, name: str, plugin_dir: Path) -> PluginManifest:
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
            source="in_process",
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

    async def _initialize_plugin(self, rec: PluginRecord):
        context = PluginContext(rec.name, rec.manifest.sanitized_scopes(), event_bus=self.event_bus)
        await self._invoke(rec.plugin_obj, "initialize", context, timeout=self.init_timeout_sec)
        rec.status = "initialized"
        rec.message = "initialized"
        rec.initialized_at = time.time()
        rec.updated_at = rec.initialized_at
        logger.info("Plugin initialized: %s", rec.name)

    async def _safe_call(self, rec: PluginRecord, method: str, *args):
        try:
            data = await self._invoke(rec.plugin_obj, method, *args, timeout=self.call_timeout_sec)
            rec.status = "healthy"
            rec.message = f"{method} ok"
            rec.error = ""
            rec.updated_at = time.time()
            return data
        except Exception as exc:
            rec.status = "error"
            rec.message = f"{method} failed"
            rec.error = str(exc)
            rec.updated_at = time.time()
            logger.exception("Plugin %s method %s failed", rec.name, method)
            raise PluginError(str(exc))

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
