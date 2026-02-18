# nebula_core/services/file_service.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import asyncio
import os
from pathlib import Path
from typing import Optional, List
from ..core.service_task import ServiceTask
from ..core.context import context

class FileService(ServiceTask):
    """
    Nebula core file service.
    Provides secure asynchronous access to files and directories.
    """
    def __init__(self, name="file_service", root_path: Optional[str] = None):
        super().__init__(name=name)
        self.root_path = Path(root_path or "data/files").resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)
        context.logger.info(f"FileService initialized at {self.root_path}")

    async def start(self):
        context.logger.info(f"FileService {self.name} started")

    async def stop(self):
        context.logger.info(f"FileService {self.name} stopped")

    # --- Working with files ---
    async def read_file(self, relative_path: str) -> str:
        path = self._resolve_path(relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"{path} does not exist or is not a file")
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def write_file(self, relative_path: str, content: str):
        path = self._resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        context.logger.info(f"File written: {path}")
        await context.runtime.event_bus.emit("file.created", {"path": str(path)})

    async def delete_file(self, relative_path: str):
        path = self._resolve_path(relative_path)
        if path.exists() and path.is_file():
            await asyncio.to_thread(path.unlink)
            context.logger.info(f"File deleted: {path}")
            await context.runtime.event_bus.emit("file.deleted", {"path": str(path)})

    # --- Working with directories ---
    async def list_dir(self, relative_path: str = "") -> List[str]:
        path = self._resolve_path(relative_path)
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"{path} does not exist or is not a directory")
        return [str(p.name) for p in path.iterdir()]

    async def make_dir(self, relative_path: str):
        path = self._resolve_path(relative_path)
        path.mkdir(parents=True, exist_ok=True)
        context.logger.info(f"Directory created: {path}")
        await context.runtime.event_bus.emit("directory.created", {"path": str(path)})

    async def delete_dir(self, relative_path: str):
        path = self._resolve_path(relative_path)
        if path.exists() and path.is_dir():
            for child in path.iterdir():
                if child.is_file():
                    await asyncio.to_thread(child.unlink)
                else:
                    await self.delete_dir(str(child.relative_to(self.root_path)))
            await asyncio.to_thread(path.rmdir)
            context.logger.info(f"Directory deleted: {path}")
            await context.runtime.event_bus.emit("directory.deleted", {"path": str(path)})

    # --- Internal methods ---
    def _resolve_path(self, relative_path: str) -> Path:
        """
        Safely convert a relative path to an absolute one,
        ensuring that the root_path cannot be exceeded.
        """
        path = (self.root_path / relative_path).resolve()
        if self.root_path not in path.parents and path != self.root_path:
            raise PermissionError(f"Path {path} is outside of the root directory")
        return path
