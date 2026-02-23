# nebula_core/core/plugin_runner.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import argparse
import asyncio
import importlib.util
import json
import logging
import os
import py_compile
import re
import signal
import sys
import time
from concurrent import futures
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import grpc
from google.protobuf import empty_pb2, struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from .plugin_api_v1 import ALLOWED_SCOPES, PLUGIN_API_VERSION, PluginError, PluginManifest
from .plugin_manager import PluginContext

SERVICE_NAME = "nebula.plugin.v1.PluginService"
METHOD_HEALTH = "Health"
METHOD_SYNC_USERS = "SyncUsers"
PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")


class PluginWorker:
    def __init__(self, plugin_name: str, plugin_dir: Path, token: str, logger: logging.Logger):
        self.plugin_name = plugin_name
        self.plugin_dir = plugin_dir
        self.token = token
        self.logger = logger
        self.manifest: Optional[PluginManifest] = None
        self.plugin_obj: Any = None

    def load(self):
        plugin_file = self.plugin_dir / "plugin.py"
        if not plugin_file.exists():
            raise PluginError("plugin.py not found")

        self.manifest = self._load_manifest()
        self._compile_plugin(self.plugin_dir)
        module = self._import_plugin_module(plugin_file)
        self.plugin_obj = self._create_plugin_instance(module)

    async def initialize(self):
        if self.plugin_obj is None or self.manifest is None:
            raise PluginError("plugin is not loaded")
        context = PluginContext(self.plugin_name, self.manifest.sanitized_scopes(), event_bus=None)
        await self._invoke("initialize", context, timeout=5.0)

    async def shutdown(self):
        try:
            await self._invoke("shutdown", timeout=3.0)
        except Exception:
            pass

    async def health(self) -> Dict[str, Any]:
        data = await self._invoke("health", timeout=5.0)
        if not isinstance(data, dict):
            return {"status": "unknown", "raw": data}
        return data

    async def sync_users(self, payload: Optional[dict]) -> Dict[str, Any]:
        data = await self._invoke("sync_users", payload or {}, timeout=10.0)
        if not isinstance(data, dict):
            return {"status": "ok", "raw": data}
        return data

    def _load_manifest(self) -> PluginManifest:
        manifest_file = self.plugin_dir / "plugin.json"
        raw = {}
        if manifest_file.exists():
            raw = json.loads(manifest_file.read_text(encoding="utf-8"))

        scopes = raw.get("scopes") if isinstance(raw.get("scopes"), list) else []
        scopes = [s for s in scopes if isinstance(s, str) and s in ALLOWED_SCOPES]
        api_version = str(raw.get("api_version") or PLUGIN_API_VERSION).strip() or PLUGIN_API_VERSION
        if api_version != PLUGIN_API_VERSION:
            raise PluginError(f"Unsupported plugin api_version: {api_version}")

        return PluginManifest(
            name=self.plugin_name,
            version=str(raw.get("version") or "0.1.0"),
            description=str(raw.get("description") or ""),
            scopes=scopes,
            api_version=api_version,
            source="process",
        )

    @staticmethod
    def _compile_plugin(plugin_dir: Path):
        for root, _, files in os.walk(plugin_dir):
            for file_name in files:
                if not file_name.endswith(".py"):
                    continue
                target = (Path(root) / file_name).resolve()
                if plugin_dir.resolve() not in target.parents and target != plugin_dir.resolve():
                    raise PluginError("Plugin file escapes plugin directory")
                py_compile.compile(str(target), doraise=True)

    def _import_plugin_module(self, plugin_file: Path):
        module_name = f"nebula_plugin_worker_{self.plugin_name}_{int(time.time() * 1000)}"
        spec = importlib.util.spec_from_file_location(module_name, str(plugin_file))
        if not spec or not spec.loader:
            raise PluginError("Unable to create module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _create_plugin_instance(self, module: Any):
        api_version = str(getattr(module, "PLUGIN_API_VERSION", PLUGIN_API_VERSION)).strip()
        if api_version != PLUGIN_API_VERSION:
            raise PluginError(f"Plugin {self.plugin_name} has unsupported API version: {api_version}")

        factory = getattr(module, "create_plugin", None)
        if not callable(factory):
            raise PluginError("Plugin must expose create_plugin()")
        instance = factory()
        if instance is None:
            raise PluginError("create_plugin() returned None")
        return instance

    async def _invoke(self, method_name: str, *args, timeout: float = 10.0):
        fn = getattr(self.plugin_obj, method_name, None)
        if not callable(fn):
            if method_name in ("health", "sync_users"):
                raise PluginError(f"Plugin method {method_name} is not implemented")
            return None

        if asyncio.iscoroutinefunction(fn):
            return await asyncio.wait_for(fn(*args), timeout=timeout)

        async def _run_sync():
            return await asyncio.to_thread(fn, *args)

        return await asyncio.wait_for(_run_sync(), timeout=timeout)


class PluginService:
    def __init__(self, worker: PluginWorker):
        self.worker = worker

    def _authorized(self, context) -> bool:
        metadata = dict(context.invocation_metadata())
        token = metadata.get("x-nebula-token") or ""
        return token == self.worker.token

    def Health(self, request, context):
        if not self._authorized(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid plugin token")
        try:
            data = asyncio.run(self.worker.health())
            msg = struct_pb2.Struct()
            ParseDict(data, msg, ignore_unknown_fields=False)
            return msg
        except Exception as exc:
            self.worker.logger.warning("Health failed: %s", exc)
            context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

    def SyncUsers(self, request, context):
        if not self._authorized(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid plugin token")
        try:
            payload = MessageToDict(request)
            data = asyncio.run(self.worker.sync_users(payload))
            msg = struct_pb2.Struct()
            ParseDict(data, msg, ignore_unknown_fields=False)
            return msg
        except Exception as exc:
            self.worker.logger.warning("SyncUsers failed: %s", exc)
            context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nebula plugin runtime v2 worker")
    parser.add_argument("--plugin-name", required=True)
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--memory-mb", type=int, default=128)
    parser.add_argument("--cpu-seconds", type=int, default=30)
    parser.add_argument("--log-dir", default="/tmp/nebula/plugin-logs")
    parser.add_argument("--allow-root", action="store_true")
    return parser.parse_args()


def _setup_logger(plugin_name: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"nebula_core.plugin_runner.{plugin_name}")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_dir / f"{plugin_name}.log", maxBytes=10 * 1024 * 1024, backupCount=5)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.handlers = [handler]
    logger.propagate = False
    return logger


def _apply_resource_limits(memory_mb: int, cpu_seconds: int, logger: logging.Logger):
    try:
        import resource
    except Exception:
        logger.warning("resource module unavailable; resource limits not applied")
        return

    mem_bytes = max(64, int(memory_mb)) * 1024 * 1024
    cpu_limit = max(1, int(cpu_seconds))

    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except Exception as exc:
        logger.warning("Failed to apply memory limit: %s", exc)

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except Exception as exc:
        logger.warning("Failed to apply CPU limit: %s", exc)


def _build_server(service: PluginService) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    handlers = {
        METHOD_HEALTH: grpc.unary_unary_rpc_method_handler(
            service.Health,
            request_deserializer=empty_pb2.Empty.FromString,
            response_serializer=lambda msg: msg.SerializeToString(),
        ),
        METHOD_SYNC_USERS: grpc.unary_unary_rpc_method_handler(
            service.SyncUsers,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=lambda msg: msg.SerializeToString(),
        ),
    }
    generic = grpc.method_handlers_generic_handler(SERVICE_NAME, handlers)
    server.add_generic_rpc_handlers((generic,))
    return server


def main() -> int:
    args = _parse_args()

    plugin_name = str(args.plugin_name).strip()
    if not PLUGIN_NAME_RE.fullmatch(plugin_name):
        print("invalid plugin name", file=sys.stderr)
        return 2

    if hasattr(os, "geteuid") and os.geteuid() == 0 and not bool(args.allow_root):
        print("plugin runner must not run as root", file=sys.stderr)
        return 3

    logger = _setup_logger(plugin_name, Path(args.log_dir))
    _apply_resource_limits(args.memory_mb, args.cpu_seconds, logger)

    plugin_dir = Path(args.plugin_dir).resolve()
    socket_path = Path(args.socket).resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)

    worker = PluginWorker(plugin_name=plugin_name, plugin_dir=plugin_dir, token=str(args.token), logger=logger)

    try:
        worker.load()
        asyncio.run(worker.initialize())
    except Exception as exc:
        logger.exception("Plugin initialization failed")
        print(f"plugin initialization failed: {exc}", file=sys.stderr)
        return 4

    service = PluginService(worker)
    server = _build_server(service)
    bind_target = f"unix://{socket_path}"
    if server.add_insecure_port(bind_target) <= 0:
        logger.error("Failed to bind socket %s", bind_target)
        return 5

    stop_event = {"stopping": False}

    def _stop_handler(signum, frame):
        if stop_event["stopping"]:
            return
        stop_event["stopping"] = True
        logger.info("Received signal %s, shutting down", signum)
        try:
            asyncio.run(worker.shutdown())
        except Exception:
            pass
        server.stop(grace=1)

    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)

    logger.info("Plugin worker started: %s", plugin_name)
    server.start()
    try:
        server.wait_for_termination()
    finally:
        socket_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
