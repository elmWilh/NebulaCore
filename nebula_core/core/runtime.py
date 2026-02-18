# nebula_core/core/runtime.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import logging
import signal
from pathlib import Path
from typing import List, Set

from .events import EventBus
from .loader import register_modules
from .plugin_manager import PluginManager
from .service_task import ServiceTask
from ..utils.config import load_yaml_config

logger = logging.getLogger("nebula_core.runtime")

class NebulaRuntime:
    def __init__(self, config_path: str = "serviceconfig.yaml"):
        # Initialize Event Bus
        self.event_bus = EventBus(logger=logging.getLogger("nebula_core.events"))
        
        # Internal state
        self._shutdown_event = asyncio.Event()
        self._tasks: Set[asyncio.Task] = set()
        self._services: List[ServiceTask] = []
        self._started = False

        # Load services configuration
        self.config_path = self._resolve_config_path(config_path)
        self.service_config = {}
        if self.config_path.exists():
            self.service_config = load_yaml_config(self.config_path)
            logger.info(f"Loaded service config from {self.config_path}")
        else:
            logger.warning(f"Service config not found at {self.config_path}, using defaults")

        services_cfg = self.service_config.get("services", self.service_config)

        # Extraction of server parameters for logging and external use
        server_cfg = services_cfg.get("server", {})
        self.host = server_cfg.get("host", "127.0.0.1")
        self.port = server_cfg.get("port", 8080)
        self.debug = server_cfg.get("debug", False)
        self.plugin_config = self.service_config.get("plugins", {})
        self.plugin_manager = PluginManager(config=self.plugin_config, event_bus=self.event_bus)

    @staticmethod
    def _resolve_config_path(config_path: str) -> Path:
        candidate = Path(config_path)
        if candidate.is_absolute():
            return candidate

        cwd_candidate = Path.cwd() / candidate
        if cwd_candidate.exists():
            return cwd_candidate

        module_candidate = Path(__file__).resolve().parents[1] / candidate
        return module_candidate

    async def init(self):
        """Initializes the runtime and registers internal/external modules."""
        logger.info("Runtime init: registering modules")
        await register_modules(self.event_bus)
        logger.info("Runtime init: loading plugin_api_v1 plugins")
        await self.plugin_manager.initialize()

        # Import kernel services locally to avoid circular dependencies
        from nebula_core.services.heartbeat import HeartbeatService
        from nebula_core.services.file_service import FileService
        from nebula_core.services.metrics_service import MetricsService

        # Setup Heartbeat Service
        services_cfg = self.service_config.get("services", self.service_config)

        hb_cfg = services_cfg.get("heartbeat", {})
        heartbeat = None
        if hb_cfg.get("enabled", True):
            heartbeat = HeartbeatService(
                name="heartbeat",
                interval=hb_cfg.get("interval", 3)
            )

        # Setup File Service
        fs_cfg = services_cfg.get("file_service", {})
        file_service = None
        if fs_cfg.get("enabled", True):
            file_service = FileService(
                root_path=fs_cfg.get("root_path", "data/files")
            )

        # Setup Metrics Service
        m_cfg = services_cfg.get("metrics", {})
        metrics_service = None
        if m_cfg.get("enabled", True):
            metrics_service = MetricsService(
                name="metrics",
                interval=m_cfg.get("interval", 5)
            )

        # Register services for lifecycle management
        for svc in (file_service, heartbeat, metrics_service):
            if svc:
                self.register_service(svc)

        logger.info("Runtime init complete")

    async def start(self):
        """Starts the runtime and all registered services."""
        if self._started:
            return
        self._started = True
        
        logger.info(f"Nebula Core running on http://{self.host}:{self.port} (debug={self.debug})")

        # Notify system about startup
        await self.event_bus.emit("system.startup", {"msg": "Nebula runtime started"})

        # Launch all services
        for service in self._services:
            logger.info(f"Starting service: {service.name}")
            self.create_task(service.start())

        # Register OS signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, 
                    lambda: asyncio.create_task(self.request_shutdown())
                )
            except NotImplementedError:
                logger.debug(f"Signal handler for {sig} not supported on this platform")

        logger.info("Runtime started, waiting for shutdown signal")
        
        # Block until request_shutdown() is called
        await self._shutdown_event.wait()
        
        logger.info("Shutdown signal received, proceeding to cleanup")
        await self.shutdown()

    async def request_shutdown(self):
        """Triggers the shutdown sequence."""
        logger.info("Shutdown requested")
        self._shutdown_event.set()

    async def shutdown(self):
        """Gracefully stops all services and cancels remaining tasks."""
        logger.info("Emitting system.shutdown event")
        await self.event_bus.emit("system.shutdown", {"msg": "Nebula runtime shutting down"})

        # Stop all registered services in parallel
        if self._services:
            logger.info("Stopping registered services...")
            stop_tasks = [s.stop() for s in self._services]
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        try:
            await self.plugin_manager.shutdown()
        except Exception:
            logger.exception("Plugin manager shutdown failed")

        # Handle remaining background tasks
        if self._tasks:
            logger.info(f"Cancelling {len(self._tasks)} remaining background tasks")
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait briefly for tasks to acknowledge cancellation
            await asyncio.wait(self._tasks, timeout=3.0)

        self._started = False
        logger.info("Runtime shutdown complete")

    def create_task(self, coro) -> asyncio.Task:
        """Creates a monitored task and adds it to the lifecycle tracking set."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def _on_done(t: asyncio.Task):
            # Safe removal from the tracking set
            self._tasks.discard(t)
            # Log exceptions if the task crashed
            if not t.cancelled() and t.exception():
                logger.error(f"Task {t.get_name()} failed with error", exc_info=t.exception())

        task.add_done_callback(_on_done)
        return task

    def register_service(self, service: ServiceTask):
        """Adds a service to the runtime lifecycle list."""
        self._services.append(service)
        logger.info(f"Registered service: {service.name}")
