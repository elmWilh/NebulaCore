# nebula_core/core/cgroup_v2.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


GROUP_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class CgroupV2Manager:
    def __init__(
        self,
        enabled: bool,
        required: bool,
        root: str = "auto",
        memory_limit_mb: int = 128,
        cpu_quota_us: int = 50000,
        cpu_period_us: int = 100000,
        pids_max: int = 128,
    ):
        self.enabled = bool(enabled)
        self.required = bool(required)
        self.root_cfg = str(root or "auto").strip() or "auto"
        self.memory_limit_mb = max(64, int(memory_limit_mb))
        self.cpu_quota_us = max(1000, int(cpu_quota_us))
        self.cpu_period_us = max(1000, int(cpu_period_us))
        self.pids_max = max(16, int(pids_max))
        self.root_path: Optional[Path] = None
        self.ready = False

    def initialize(self) -> Tuple[bool, str]:
        if not self.enabled:
            self.ready = False
            return True, "cgroup disabled by config"

        try:
            if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
                self.ready = False
                return False, "cgroup v2 is not available"

            root_path = self._resolve_root_path()
            parent = root_path.parent
            if not parent.exists():
                self.ready = False
                return False, f"cgroup parent does not exist: {parent}"

            self._enable_subtree_controllers(parent)
            root_path.mkdir(parents=True, exist_ok=True)
            self._enable_subtree_controllers(root_path)

            self.root_path = root_path
            self.ready = True
            return True, f"cgroup ready at {root_path}"
        except Exception as exc:
            self.ready = False
            return False, str(exc)

    def create_group(self, plugin_name: str) -> Path:
        if not self.ready or self.root_path is None:
            raise RuntimeError("cgroup manager is not ready")
        safe = GROUP_RE.sub("-", str(plugin_name or "").strip()).strip("-._") or "plugin"
        group_name = f"{safe}-{int(time.time() * 1000)}"
        path = self.root_path / group_name
        path.mkdir(parents=False, exist_ok=False)
        self._write_limits(path)
        return path

    @staticmethod
    def assign_pid(path: Path, pid: int):
        (path / "cgroup.procs").write_text(f"{int(pid)}\n", encoding="utf-8")

    @staticmethod
    def cleanup_group(path: Optional[str]):
        if not path:
            return
        target = Path(path)
        if not target.exists():
            return
        try:
            procs_file = target / "cgroup.procs"
            if procs_file.exists():
                pids = [p.strip() for p in procs_file.read_text(encoding="utf-8").splitlines() if p.strip()]
                if pids:
                    return
            target.rmdir()
        except Exception:
            pass

    @staticmethod
    def memory_events(path: Optional[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if not path:
            return out
        target = Path(path) / "memory.events"
        if not target.exists():
            return out
        for line in target.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                out[parts[0]] = int(parts[1])
            except Exception:
                continue
        return out

    def _resolve_root_path(self) -> Path:
        if self.root_cfg != "auto":
            return Path(self.root_cfg).resolve()

        rel = "/"
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                rel = line.split("0::", 1)[1].strip() or "/"
                break
        rel = rel.lstrip("/")
        base = Path("/sys/fs/cgroup")
        if not rel:
            return (base / "nebula-plugins").resolve()
        return (base / rel / "nebula-plugins").resolve()

    @staticmethod
    def _enable_subtree_controllers(path: Path):
        controllers_file = path / "cgroup.controllers"
        subtree_file = path / "cgroup.subtree_control"
        if not controllers_file.exists() or not subtree_file.exists():
            return
        available = set(controllers_file.read_text(encoding="utf-8").split())
        for ctrl in ("cpu", "memory", "pids"):
            if ctrl not in available:
                continue
            try:
                with subtree_file.open("a", encoding="utf-8") as fh:
                    fh.write(f"+{ctrl}\n")
            except Exception:
                continue

    def _write_limits(self, path: Path):
        memory_bytes = self.memory_limit_mb * 1024 * 1024
        (path / "memory.max").write_text(str(memory_bytes), encoding="utf-8")
        (path / "cpu.max").write_text(f"{self.cpu_quota_us} {self.cpu_period_us}", encoding="utf-8")
        (path / "pids.max").write_text(str(self.pids_max), encoding="utf-8")
