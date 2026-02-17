import docker
import time
import shlex
import posixpath
import psutil
import os
import re
import shutil
import json
from ..db import get_connection, SYSTEM_DB
from ..core.context import context

# DockerService should not crash application startup if the Docker daemon/socket
# is unavailable. Attempt to create client, but fall back to a disabled state
# and provide clear errors from methods when called.

class DockerService:
    WORKSPACES_BASE_DIR = os.path.join("storage", "container_workspaces")
    PRESETS_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "containers", "presets"))
    DEFAULT_WORKSPACE_MOUNT_PATH = "/data"
    EXPLORER_ALLOWED_ROOTS = (
        "/data",
        "/workspace",
        "/app",
        "/srv",
        "/minecraft",
        "/server",
    )
    PROFILE_WORKSPACE_ROOTS = {
        "minecraft": ("/data", "/minecraft"),
        "steam": ("/server", "/data"),
        "web": ("/app", "/srv", "/data"),
        "python": ("/app", "/workspace", "/data"),
        "database": ("/data",),
        "generic": EXPLORER_ALLOWED_ROOTS,
    }
    PROFILE_POLICIES = {
        "minecraft": {
            "label": "Minecraft Server",
            "shell_allowed_for_user": False,
            "app_console_supported": True,
            "tools": ["server commands", "log streaming", "file explorer", "restart policy"],
        },
        "web": {
            "label": "Web/Nginx",
            "shell_allowed_for_user": True,
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "python": {
            "label": "Python App",
            "shell_allowed_for_user": True,
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "database": {
            "label": "Database",
            "shell_allowed_for_user": False,
            "app_console_supported": False,
            "tools": ["log streaming", "file explorer", "restart policy"],
        },
        "steam": {
            "label": "Steam/Game Dedicated",
            "shell_allowed_for_user": True,
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "generic": {
            "label": "Generic Container",
            "shell_allowed_for_user": True,
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
    }
    USER_BLOCKED_SHELL_PATTERNS = (
        "sudo",
        " su ",
        " useradd",
        " usermod",
        " passwd",
        " chown ",
        " chmod ",
        " mount ",
        " umount",
        " systemctl",
        " service ",
        " docker ",
        " podman ",
        "/etc/",
        "/root",
        "/proc/",
        "/sys/",
        "/var/lib/",
    )
    CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    DEFAULT_ROLE_PERMISSIONS = {
        "user": {
            "allow_explorer": True,
            "allow_root_explorer": False,
            "allow_console": True,
            "allow_shell": False,
            "allow_settings": False,
            "allow_edit_files": False,
            "allow_edit_startup": False,
            "allow_edit_ports": False,
        },
        "moderator": {
            "allow_explorer": True,
            "allow_root_explorer": False,
            "allow_console": True,
            "allow_shell": True,
            "allow_settings": True,
            "allow_edit_files": False,
            "allow_edit_startup": True,
            "allow_edit_ports": True,
        },
        "developer": {
            "allow_explorer": True,
            "allow_root_explorer": False,
            "allow_console": True,
            "allow_shell": True,
            "allow_settings": True,
            "allow_edit_files": True,
            "allow_edit_startup": True,
            "allow_edit_ports": True,
        },
        "tester": {
            "allow_explorer": True,
            "allow_root_explorer": False,
            "allow_console": True,
            "allow_shell": False,
            "allow_settings": False,
            "allow_edit_files": False,
            "allow_edit_startup": False,
            "allow_edit_ports": False,
        },
        "admin": {
            "allow_explorer": True,
            "allow_root_explorer": True,
            "allow_console": True,
            "allow_shell": True,
            "allow_settings": True,
            "allow_edit_files": True,
            "allow_edit_startup": True,
            "allow_edit_ports": True,
        },
    }

    def __init__(self):
        try:
            self.client = docker.from_env()
            self.available = True
        except Exception as e:
            context.logger.warning(f"Docker client init failed: {e}")
            self.client = None
            self.available = False
        self._net_state = {}
        self._summary_cache = {}
        self._summary_cache_ttl = 4.0
        self._workspace_usage_cache = {}
        self._workspace_usage_cache_ttl = 2.0
        os.makedirs(self.PRESETS_BASE_DIR, exist_ok=True)

    @staticmethod
    def _normalize_role_tag(role_tag: str) -> str:
        raw = str(role_tag or "").strip().lower()
        if not raw:
            return "user"
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
        return cleaned or "user"

    @staticmethod
    def _to_bool(value, default=False):
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        txt = str(value).strip().lower()
        if txt in ("1", "true", "yes", "y", "on"):
            return True
        if txt in ("0", "false", "no", "n", "off", ""):
            return False
        return bool(default)

    def _default_presets(self):
        # Presets are file-driven; keep empty defaults by product requirement.
        return {}

    def _preset_file_path(self, preset_name: str) -> str:
        token = self._safe_workspace_token(preset_name or "preset")
        return os.path.join(self.PRESETS_BASE_DIR, f"{token}.json")

    def list_container_presets(self):
        merged = {}
        source_dirs = [self.PRESETS_BASE_DIR]
        try:
            for base_dir in source_dirs:
                if not os.path.isdir(base_dir):
                    continue
                for fn in os.listdir(base_dir):
                    if not fn.endswith(".json"):
                        continue
                    path = os.path.join(base_dir, fn)
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if not isinstance(raw, dict):
                        continue
                    name = self._safe_workspace_token(raw.get("name") or fn[:-5])
                    merged[name] = {
                        "name": name,
                        "title": str(raw.get("title") or name).strip() or name,
                        "description": str(raw.get("description") or "").strip(),
                        "config": raw.get("config") if isinstance(raw.get("config"), dict) else {},
                        "permissions": raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {},
                        "source": "file",
                    }
        except Exception:
            pass
        presets = []
        for item in merged.values():
            data = dict(item)
            data.setdefault("source", "builtin")
            presets.append(data)
        presets.sort(key=lambda p: str(p.get("title") or p.get("name") or "").lower())
        return presets

    def get_container_preset(self, preset_name: str):
        token = self._safe_workspace_token(preset_name or "")
        if not token:
            raise RuntimeError("Preset name is required")
        for preset in self.list_container_presets():
            if self._safe_workspace_token(preset.get("name")) == token:
                return preset
        raise RuntimeError("Preset not found")

    def save_container_preset(self, name: str, title: str, description: str, config: dict, permissions: dict, saved_by: str):
        token = self._safe_workspace_token(name)
        if not token:
            raise RuntimeError("Invalid preset name")
        payload = {
            "name": token,
            "title": str(title or token).strip() or token,
            "description": str(description or "").strip(),
            "config": config if isinstance(config, dict) else {},
            "permissions": permissions if isinstance(permissions, dict) else {},
            "saved_by": saved_by or "unknown",
            "saved_at": int(time.time()),
        }
        path = self._preset_file_path(token)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2, sort_keys=True)
            f.write("\n")
        return {"status": "saved", "name": token, "path": path}

    def resolve_user_role(self, username: str, db_name: str, is_staff: bool) -> str:
        if is_staff:
            return "admin"
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT role_tag FROM user_identity_tags WHERE db_name = ? AND username = ? LIMIT 1",
                (db_name or "system.db", username),
            ).fetchone()
        if row and str(row["role_tag"] or "").strip():
            return self._normalize_role_tag(row["role_tag"])
        return "user"

    def get_container_role_policies(self, container_id: str):
        full_id = self.resolve_container_id(container_id)
        policies = {k: dict(v) for k, v in self.DEFAULT_ROLE_PERMISSIONS.items()}
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT role_tag, allow_explorer, allow_root_explorer, allow_console, allow_shell,
                       allow_settings, allow_edit_files, allow_edit_startup, allow_edit_ports
                FROM container_role_permissions
                WHERE container_id = ?
                """,
                (full_id,),
            ).fetchall()
        for row in rows:
            role = self._normalize_role_tag(row["role_tag"])
            base = dict(self.DEFAULT_ROLE_PERMISSIONS.get(role, self.DEFAULT_ROLE_PERMISSIONS["user"]))
            base.update({
                "allow_explorer": self._to_bool(row["allow_explorer"], base["allow_explorer"]),
                "allow_root_explorer": self._to_bool(row["allow_root_explorer"], base["allow_root_explorer"]),
                "allow_console": self._to_bool(row["allow_console"], base["allow_console"]),
                "allow_shell": self._to_bool(row["allow_shell"], base["allow_shell"]),
                "allow_settings": self._to_bool(row["allow_settings"], base["allow_settings"]),
                "allow_edit_files": self._to_bool(row["allow_edit_files"], base["allow_edit_files"]),
                "allow_edit_startup": self._to_bool(row["allow_edit_startup"], base["allow_edit_startup"]),
                "allow_edit_ports": self._to_bool(row["allow_edit_ports"], base["allow_edit_ports"]),
            })
            policies[role] = base
        return full_id, policies

    def get_effective_container_permissions(self, container_id: str, username: str, db_name: str, is_staff: bool):
        full_id, policies = self.get_container_role_policies(container_id)
        role_tag = self.resolve_user_role(username, db_name, is_staff)
        base = dict(self.DEFAULT_ROLE_PERMISSIONS.get(role_tag, self.DEFAULT_ROLE_PERMISSIONS["user"]))
        policy = dict(base)
        policy.update(policies.get(role_tag, {}))
        if is_staff:
            for key in list(policy.keys()):
                policy[key] = True
        policy["role_tag"] = role_tag
        policy["container_id"] = full_id
        policy["is_staff"] = bool(is_staff)
        policy["role_policies"] = policies
        return policy

    def set_container_role_policies(self, container_id: str, role_policies: dict, updated_by: str):
        full_id = self.resolve_container_id(container_id)
        if not isinstance(role_policies, dict):
            raise RuntimeError("role_policies must be an object")
        with get_connection(SYSTEM_DB) as conn:
            for role, values in role_policies.items():
                role_tag = self._normalize_role_tag(role)
                base = dict(self.DEFAULT_ROLE_PERMISSIONS.get(role_tag, self.DEFAULT_ROLE_PERMISSIONS["user"]))
                raw = values if isinstance(values, dict) else {}
                row = {
                    "allow_explorer": self._to_bool(raw.get("allow_explorer"), base["allow_explorer"]),
                    "allow_root_explorer": self._to_bool(raw.get("allow_root_explorer"), base["allow_root_explorer"]),
                    "allow_console": self._to_bool(raw.get("allow_console"), base["allow_console"]),
                    "allow_shell": self._to_bool(raw.get("allow_shell"), base["allow_shell"]),
                    "allow_settings": self._to_bool(raw.get("allow_settings"), base["allow_settings"]),
                    "allow_edit_files": self._to_bool(raw.get("allow_edit_files"), base["allow_edit_files"]),
                    "allow_edit_startup": self._to_bool(raw.get("allow_edit_startup"), base["allow_edit_startup"]),
                    "allow_edit_ports": self._to_bool(raw.get("allow_edit_ports"), base["allow_edit_ports"]),
                }
                conn.execute(
                    """
                    INSERT INTO container_role_permissions (
                        container_id, role_tag, allow_explorer, allow_root_explorer, allow_console, allow_shell,
                        allow_settings, allow_edit_files, allow_edit_startup, allow_edit_ports, updated_by, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(container_id, role_tag) DO UPDATE SET
                        allow_explorer=excluded.allow_explorer,
                        allow_root_explorer=excluded.allow_root_explorer,
                        allow_console=excluded.allow_console,
                        allow_shell=excluded.allow_shell,
                        allow_settings=excluded.allow_settings,
                        allow_edit_files=excluded.allow_edit_files,
                        allow_edit_startup=excluded.allow_edit_startup,
                        allow_edit_ports=excluded.allow_edit_ports,
                        updated_by=excluded.updated_by,
                        updated_at=datetime('now')
                    """,
                    (
                        full_id, role_tag,
                        1 if row["allow_explorer"] else 0,
                        1 if row["allow_root_explorer"] else 0,
                        1 if row["allow_console"] else 0,
                        1 if row["allow_shell"] else 0,
                        1 if row["allow_settings"] else 0,
                        1 if row["allow_edit_files"] else 0,
                        1 if row["allow_edit_startup"] else 0,
                        1 if row["allow_edit_ports"] else 0,
                        updated_by or "unknown",
                    ),
                )
        return self.get_container_role_policies(full_id)[1]

    def ensure_client(self):
        """Attempt to (re)initialize docker client on demand."""
        if self.client is not None:
            return True
        try:
            self.client = docker.from_env()
            self.available = True
            context.logger.info("Docker client connected on-demand")
            return True
        except Exception as e:
            context.logger.debug(f"Docker client on-demand init failed: {e}")
            self.client = None
            self.available = False
            return False

    def resolve_container_id(self, container_id: str) -> str:
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
            return container.id
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

    def list_containers(self, username: str, db_name: str, is_staff: bool):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        containers = self.client.containers.list(all=True)
        
        with get_connection(SYSTEM_DB) as conn:
            if is_staff:
                permissions = conn.execute("SELECT container_id, username, role_tag FROM container_permissions").fetchall()
                perm_map = {}
                for p in permissions:
                    role = self._normalize_role_tag(p["role_tag"])
                    label = f"{p['username']} ({role})" if role else p["username"]
                    perm_map.setdefault(p["container_id"], []).append(label)
            else:
                allowed = conn.execute(
                    """
                    SELECT container_id FROM container_permissions
                    WHERE username = ? AND (db_name = ? OR db_name IS NULL OR db_name = '')
                    """,
                    (username, db_name or "system.db"),
                ).fetchall()
                allowed_ids = {r["container_id"] for r in allowed}

        res = []
        for c in containers:
            if not is_staff and c.id not in allowed_ids:
                continue
            
            res.append({
                "id": c.id[:12],
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "users": perm_map.get(c.id, []) if is_staff else [username]
            })
        return res

    def get_usage_summary(self, username: str, db_name: str, is_staff: bool):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        cache_key = f"{db_name}:{username}:{'staff' if is_staff else 'user'}"
        now = time.time()
        cached = self._summary_cache.get(cache_key)
        if cached:
            ts, payload = cached
            if (now - ts) <= self._summary_cache_ttl:
                return dict(payload)

        containers = self.client.containers.list(all=True)

        if not is_staff:
            with get_connection(SYSTEM_DB) as conn:
                allowed = conn.execute(
                    """
                    SELECT container_id FROM container_permissions
                    WHERE username = ? AND (db_name = ? OR db_name IS NULL OR db_name = '')
                    """,
                    (username, db_name or "system.db")
                ).fetchall()
                allowed_ids = {r["container_id"] for r in allowed}
            containers = [c for c in containers if c.id in allowed_ids]

        total = len(containers)
        running = 0
        cpu_total = 0.0
        mem_used_bytes = 0
        mem_limit_bytes = 0
        net_tx_bytes = 0
        net_rx_bytes = 0

        for c in containers:
            try:
                if c.status != "running":
                    continue
                running += 1
                stats = c.stats(stream=False)
                cpu_total += self._calc_cpu_percent(stats)

                mem = stats.get("memory_stats", {}) or {}
                used = float(mem.get("usage") or 0.0)
                limit = float(mem.get("limit") or 0.0)
                mem_used_bytes += max(0.0, used)
                mem_limit_bytes += max(0.0, limit)

                nets = stats.get("networks") or {}
                for _, n in nets.items():
                    net_tx_bytes += int(n.get("tx_bytes") or 0)
                    net_rx_bytes += int(n.get("rx_bytes") or 0)
            except Exception:
                continue

        key = cache_key
        prev = self._net_state.get(key)
        tx_mbps = 0.0
        rx_mbps = 0.0
        if prev:
            p_tx, p_rx, p_ts = prev
            dt = max(0.2, now - p_ts)
            tx_mbps = max(0.0, (net_tx_bytes - p_tx) / dt / 1048576.0)
            rx_mbps = max(0.0, (net_rx_bytes - p_rx) / dt / 1048576.0)
        self._net_state[key] = (net_tx_bytes, net_rx_bytes, now)

        mem_percent = (mem_used_bytes / mem_limit_bytes * 100.0) if mem_limit_bytes > 0 else 0.0

        payload = {
            "total_containers": total,
            "running_containers": running,
            "cpu_percent": round(cpu_total, 2),
            "memory_used_mb": round(mem_used_bytes / 1048576.0, 2),
            "memory_limit_mb": round(mem_limit_bytes / 1048576.0, 2),
            "memory_percent": round(mem_percent, 2),
            "network_tx_mbps": round(tx_mbps, 2),
            "network_rx_mbps": round(rx_mbps, 2),
        }
        self._summary_cache[cache_key] = (now, payload)
        return payload

    def get_container_memory_breakdown(self):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        host_mem_total = float(psutil.virtual_memory().total or 0.0)
        containers = self.client.containers.list(all=True)
        with get_connection(SYSTEM_DB) as conn:
            storage_rows = conn.execute(
                "SELECT container_id, workspace_path, disk_quota_mb FROM container_storage"
            ).fetchall()
        storage_map = {r["container_id"]: dict(r) for r in storage_rows}
        rows = []

        for c in containers:
            try:
                c.reload()
                stats = c.stats(stream=False)
                mem = stats.get("memory_stats", {}) or {}
                used_bytes = float(mem.get("usage") or 0.0)
                limit_bytes = float(mem.get("limit") or 0.0)
                disk_rw_bytes = 0.0
                disk_rootfs_bytes = 0.0
                try:
                    inspected = self.client.api.inspect_container(c.id, size=True)
                    disk_rw_bytes = float(inspected.get("SizeRw") or 0.0)
                    disk_rootfs_bytes = float(inspected.get("SizeRootFs") or 0.0)
                except Exception:
                    pass

                storage_meta = storage_map.get(c.id) or {}
                workspace_path = (storage_meta.get("workspace_path") or "").strip()
                if not workspace_path:
                    mounts = (getattr(c, "attrs", {}) or {}).get("Mounts", []) or []
                    preferred = ("/data", "/workspace")
                    for dest in preferred:
                        found = next((m for m in mounts if (m or {}).get("Destination") == dest), None)
                        if found and (found.get("Source") or "").strip():
                            workspace_path = (found.get("Source") or "").strip()
                            break
                disk_quota_mb = int(storage_meta.get("disk_quota_mb") or 0)
                workspace_used_bytes = self._workspace_size_bytes(workspace_path) if workspace_path else 0
                disk_used_bytes = workspace_used_bytes if workspace_used_bytes > 0 else max(0.0, disk_rw_bytes)
                disk_used_mb = round(max(0.0, disk_used_bytes) / 1048576.0, 2)
                disk_usage_percent = round((disk_used_mb / disk_quota_mb * 100.0), 2) if disk_quota_mb > 0 else 0.0

                rows.append({
                    "id": c.id[:12],
                    "name": c.name,
                    "status": c.status,
                    "memory_used_mb": round(max(0.0, used_bytes) / 1048576.0, 2),
                    "memory_limit_mb": round(max(0.0, limit_bytes) / 1048576.0, 2),
                    "memory_host_percent": round((used_bytes / host_mem_total * 100.0), 2) if host_mem_total > 0 else 0.0,
                    "disk_rw_mb": disk_used_mb,
                    "disk_used_mb": disk_used_mb,
                    "disk_rootfs_mb": round(max(0.0, disk_rootfs_bytes) / 1048576.0, 2),
                    "disk_quota_mb": disk_quota_mb,
                    "disk_usage_percent": disk_usage_percent,
                    "workspace_path": workspace_path,
                })
            except Exception:
                continue

        rows.sort(key=lambda r: r["memory_used_mb"], reverse=True)
        return rows

    @staticmethod
    def _safe_workspace_token(name: str) -> str:
        token = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(name or "").strip()).strip("-._").lower()
        return token[:42] or "container"

    def _workspace_size_bytes(self, path: str) -> int:
        clean = (path or "").strip()
        if not clean or not os.path.isdir(clean):
            return 0
        now = time.time()
        cached = self._workspace_usage_cache.get(clean)
        if cached:
            ts, size_b = cached
            if (now - ts) <= self._workspace_usage_cache_ttl:
                return int(size_b)

        total = 0
        for root, _, files in os.walk(clean):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    if os.path.islink(fp):
                        continue
                    total += int(os.path.getsize(fp))
                except Exception:
                    continue
        self._workspace_usage_cache[clean] = (now, total)
        return total

    @staticmethod
    def _calc_cpu_percent(stats: dict) -> float:
        try:
            cpu_stats = stats.get("cpu_stats", {}) or {}
            precpu = stats.get("precpu_stats", {}) or {}
            cpu_delta = float(cpu_stats.get("cpu_usage", {}).get("total_usage", 0.0)) - float(
                precpu.get("cpu_usage", {}).get("total_usage", 0.0)
            )
            system_delta = float(cpu_stats.get("system_cpu_usage", 0.0)) - float(
                precpu.get("system_cpu_usage", 0.0)
            )
            online_cpus = float(cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or [1]))
            if cpu_delta > 0 and system_delta > 0 and online_cpus > 0:
                return (cpu_delta / system_delta) * online_cpus * 100.0
        except Exception:
            return 0.0
        return 0.0

    @staticmethod
    def _to_int(value, default=None):
        if value is None or value == "":
            return default
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _to_float(value, default=None):
        if value is None or value == "":
            return default
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _parse_ports(raw_ports: str):
        if not raw_ports:
            return None

        result = {}
        for token in [p.strip() for p in str(raw_ports).split(",") if p.strip()]:
            parts = token.split(":")
            if len(parts) not in (2, 3):
                raise RuntimeError(f"Invalid port mapping '{token}'. Use host:container or ip:host:container")

            host_ip = None
            if len(parts) == 3:
                host_ip, host_port, container_part = parts
            else:
                host_port, container_part = parts

            container_part = container_part.strip()
            if "/" in container_part:
                container_port, proto = container_part.split("/", 1)
                container_key = f"{int(container_port)}/{proto.strip().lower()}"
            else:
                container_key = f"{int(container_part)}/tcp"

            host_port = int(host_port)
            if host_ip:
                result[container_key] = (host_ip.strip(), host_port)
            else:
                result[container_key] = host_port
        return result or None

    @staticmethod
    def _parse_env(raw_env: str):
        if not raw_env:
            return None
        env_map = {}
        for line in str(raw_env).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise RuntimeError(f"Invalid env line '{line}'. Use KEY=VALUE")
            key, val = line.split("=", 1)
            key = key.strip()
            if not key:
                raise RuntimeError("Environment variable key cannot be empty")
            env_map[key] = val
        return env_map or None

    @staticmethod
    def _parse_volumes(raw_volumes: str):
        if not raw_volumes:
            return None
        volumes = {}
        for line in str(raw_volumes).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) < 2:
                raise RuntimeError(f"Invalid volume '{line}'. Use /host:/container[:ro|rw]")
            mode = "rw"
            if len(parts) >= 3 and parts[-1] in ("ro", "rw"):
                mode = parts[-1]
                host_path = ":".join(parts[:-2]).strip()
                cont_path = parts[-2].strip()
            else:
                host_path = ":".join(parts[:-1]).strip()
                cont_path = parts[-1].strip()
            if not host_path or not cont_path:
                raise RuntimeError(f"Invalid volume '{line}'")
            volumes[host_path] = {"bind": cont_path, "mode": mode}
        return volumes or None

    def deploy(self, data: dict):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        clean_name = str((data or {}).get("name") or "").strip()
        if not clean_name:
            raise RuntimeError("INVALID_CONTAINER_NAME: Container name is required")
        if not self.CONTAINER_NAME_RE.fullmatch(clean_name):
            raise RuntimeError(
                "INVALID_CONTAINER_NAME: Use only letters, numbers, ., _, - and no spaces"
            )
        data = dict(data or {})
        data["name"] = clean_name

        image_name = str((data or {}).get("image") or "").strip()
        if not image_name:
            raise RuntimeError("INVALID_IMAGE_NAME: Docker image is required")
        data["image"] = image_name

        try:
            self.client.images.get(data["image"])
        except docker.errors.ImageNotFound:
            context.logger.info(f"Image {data['image']} not found locally. Pulling...")
            self.client.images.pull(data["image"])

        mem_mb = self._to_int(data.get("ram"), 512)
        swap_mb = self._to_int(data.get("swap"), None)
        disk_gb = self._to_int(data.get("disk"), None)
        cpu_weight = self._to_int(data.get("cpu"), 1024)
        cpu_limit = self._to_float(data.get("cpu_limit"), None)
        cpu_quota = self._to_int(data.get("cpu_quota"), None)
        cpu_period = self._to_int(data.get("cpu_period"), None)
        cpuset = (data.get("cpuset") or "").strip() or None
        pids_limit = self._to_int(data.get("pids_limit"), None)
        shm_mb = self._to_int(data.get("shm"), None)
        workspace_mount = (data.get("workspace_mount") or self.DEFAULT_WORKSPACE_MOUNT_PATH).strip() or self.DEFAULT_WORKSPACE_MOUNT_PATH
        explorer_root = (data.get("explorer_root") or workspace_mount).strip() or workspace_mount
        console_cwd = (data.get("console_cwd") or explorer_root or workspace_mount).strip() or workspace_mount
        profile_name = self._safe_workspace_token(data.get("profile_name") or data.get("preset") or "")
        if not profile_name:
            profile_name = self.infer_profile(data.get("image") or "")
        parsed_volumes = self._parse_volumes(data.get("volumes")) or {}
        managed_workspace = False
        workspace_path = ""
        disk_quota_mb = max(0, int((disk_gb or 0) * 1024))

        for host_path, mount_cfg in parsed_volumes.items():
            if (mount_cfg or {}).get("bind") == workspace_mount:
                workspace_path = host_path
                break

        if not workspace_path:
            token = self._safe_workspace_token(data.get("name") or "container")
            workspace_path = os.path.abspath(os.path.join(self.WORKSPACES_BASE_DIR, f"{token}-{int(time.time() * 1000)}"))
            os.makedirs(workspace_path, exist_ok=True)
            parsed_volumes[workspace_path] = {"bind": workspace_mount, "mode": "rw"}
            managed_workspace = True

        run_kwargs = {
            "image": data["image"],
            "name": data["name"],
            "detach": True,
            "stdin_open": True,
            "restart_policy": {"Name": "always"} if data.get("restart") else None,
            "ports": self._parse_ports(data.get("ports")),
            "environment": self._parse_env(data.get("env")),
            "volumes": parsed_volumes or None,
            "command": (data.get("command") or "").strip() or None,
            "mem_limit": f"{max(64, mem_mb)}m",
            "cpu_shares": max(2, cpu_weight),
            "cpuset_cpus": cpuset,
            "pids_limit": pids_limit if pids_limit and pids_limit > 0 else None,
            "shm_size": f"{max(16, shm_mb)}m" if shm_mb else None,
        }

        if swap_mb is not None and swap_mb > 0:
            run_kwargs["memswap_limit"] = f"{swap_mb}m"

        if cpu_limit is not None and cpu_limit > 0:
            run_kwargs["nano_cpus"] = int(cpu_limit * 1_000_000_000)

        if cpu_quota is not None and cpu_quota > 0:
            run_kwargs["cpu_quota"] = cpu_quota
        if cpu_period is not None and cpu_period > 0:
            run_kwargs["cpu_period"] = cpu_period

        if disk_gb is not None and disk_gb > 0:
            run_kwargs["storage_opt"] = {"size": f"{disk_gb}G"}

        run_kwargs = {k: v for k, v in run_kwargs.items() if v is not None}
        try:
            container = self.client.containers.run(**run_kwargs)
        except docker.errors.APIError as e:
            detail = str(e)
            if managed_workspace and workspace_path:
                try:
                    if os.path.isdir(workspace_path):
                        shutil.rmtree(workspace_path, ignore_errors=True)
                except Exception:
                    pass
            if "storage-opt" in detail.lower() or "size" in detail.lower():
                context.logger.warning(
                    "storage_opt size unsupported on this host; relying on managed workspace quota tracking"
                )
                run_kwargs.pop("storage_opt", None)
                try:
                    container = self.client.containers.run(**run_kwargs)
                except docker.errors.APIError as e2:
                    raise RuntimeError(str(e2))
            else:
                raise RuntimeError(detail)

        user_assignments = data.get("user_assignments")
        if not isinstance(user_assignments, list):
            user_assignments = []
            for u in data.get("users", []):
                user_assignments.append({
                    "username": str(u or "").strip(),
                    "db_name": "system.db",
                    "role_tag": "user",
                })

        try:
            with get_connection(SYSTEM_DB) as conn:
                for item in user_assignments:
                    username = str((item or {}).get("username") or "").strip()
                    if not username:
                        continue
                    db_name = str((item or {}).get("db_name") or "system.db").strip() or "system.db"
                    role_tag = self._normalize_role_tag((item or {}).get("role_tag") or "user")
                    conn.execute(
                        """
                        INSERT INTO container_permissions (container_id, username, db_name, role_tag)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(container_id, username) DO UPDATE SET
                            db_name=excluded.db_name,
                            role_tag=excluded.role_tag
                        """,
                        (container.id, username, db_name, role_tag),
                    )
                role_policies = data.get("role_permissions")
                if isinstance(role_policies, dict):
                    for role, values in role_policies.items():
                        role_tag = self._normalize_role_tag(role)
                        base = dict(self.DEFAULT_ROLE_PERMISSIONS.get(role_tag, self.DEFAULT_ROLE_PERMISSIONS["user"]))
                        raw = values if isinstance(values, dict) else {}
                        row = {
                            "allow_explorer": self._to_bool(raw.get("allow_explorer"), base["allow_explorer"]),
                            "allow_root_explorer": self._to_bool(raw.get("allow_root_explorer"), base["allow_root_explorer"]),
                            "allow_console": self._to_bool(raw.get("allow_console"), base["allow_console"]),
                            "allow_shell": self._to_bool(raw.get("allow_shell"), base["allow_shell"]),
                            "allow_settings": self._to_bool(raw.get("allow_settings"), base["allow_settings"]),
                            "allow_edit_files": self._to_bool(raw.get("allow_edit_files"), base["allow_edit_files"]),
                            "allow_edit_startup": self._to_bool(raw.get("allow_edit_startup"), base["allow_edit_startup"]),
                            "allow_edit_ports": self._to_bool(raw.get("allow_edit_ports"), base["allow_edit_ports"]),
                        }
                        conn.execute(
                            """
                            INSERT INTO container_role_permissions (
                                container_id, role_tag, allow_explorer, allow_root_explorer, allow_console, allow_shell,
                                allow_settings, allow_edit_files, allow_edit_startup, allow_edit_ports, updated_by, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(container_id, role_tag) DO UPDATE SET
                                allow_explorer=excluded.allow_explorer,
                                allow_root_explorer=excluded.allow_root_explorer,
                                allow_console=excluded.allow_console,
                                allow_shell=excluded.allow_shell,
                                allow_settings=excluded.allow_settings,
                                allow_edit_files=excluded.allow_edit_files,
                                allow_edit_startup=excluded.allow_edit_startup,
                                allow_edit_ports=excluded.allow_edit_ports,
                                updated_by=excluded.updated_by,
                                updated_at=datetime('now')
                            """,
                            (
                                container.id, role_tag,
                                1 if row["allow_explorer"] else 0,
                                1 if row["allow_root_explorer"] else 0,
                                1 if row["allow_console"] else 0,
                                1 if row["allow_shell"] else 0,
                                1 if row["allow_settings"] else 0,
                                1 if row["allow_edit_files"] else 0,
                                1 if row["allow_edit_startup"] else 0,
                                1 if row["allow_edit_ports"] else 0,
                                "system",
                            ),
                        )
                conn.execute(
                    """
                    INSERT INTO container_settings (container_id, startup_command, allowed_ports, updated_by, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(container_id) DO UPDATE SET
                        startup_command=excluded.startup_command,
                        allowed_ports=excluded.allowed_ports,
                        updated_by=excluded.updated_by,
                        updated_at=datetime('now')
                    """,
                    (
                        container.id,
                        (data.get("command") or "").strip() or None,
                        (data.get("ports") or "").strip() or None,
                        "system",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO container_storage (
                        container_id, workspace_path, workspace_mount, disk_quota_mb,
                        explorer_root, console_cwd, profile_name, managed_workspace, updated_by, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(container_id) DO UPDATE SET
                        workspace_path=excluded.workspace_path,
                        workspace_mount=excluded.workspace_mount,
                        disk_quota_mb=excluded.disk_quota_mb,
                        explorer_root=excluded.explorer_root,
                        console_cwd=excluded.console_cwd,
                        profile_name=excluded.profile_name,
                        managed_workspace=excluded.managed_workspace,
                        updated_by=excluded.updated_by,
                        updated_at=datetime('now')
                    """,
                    (
                        container.id,
                        workspace_path or None,
                        workspace_mount,
                        disk_quota_mb,
                        explorer_root,
                        console_cwd,
                        profile_name,
                        1 if managed_workspace else 0,
                        "system",
                    ),
                )
        except Exception as db_error:
            # Keep DB and runtime in sync: rollback runtime object if metadata registration fails.
            try:
                container.remove(force=True)
            except Exception:
                pass
            if managed_workspace and workspace_path:
                try:
                    if os.path.isdir(workspace_path):
                        shutil.rmtree(workspace_path, ignore_errors=True)
                except Exception:
                    pass
            raise RuntimeError(f"DB_REGISTRATION_FAILED: {db_error}")
        return container.id

    def get_container_detail(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        with get_connection(SYSTEM_DB) as conn:
            users = conn.execute(
                "SELECT username, role_tag FROM container_permissions WHERE container_id = ?",
                (container.id,),
            ).fetchall()

        return {
            "id": container.id[:12],
            "full_id": container.id,
            "name": container.name,
            "status": container.status,
            "image": container.image.tags[0] if container.image.tags else "unknown",
            "users": [f"{u['username']} ({self._normalize_role_tag(u['role_tag'])})" for u in users],
        }

    def _exec_shell(self, container, shell_command: str):
        try:
            container.reload()
            status = str(container.status or "").lower()
            if status != "running":
                raise RuntimeError(f"Container is not running (current status: {status or 'unknown'})")
        except RuntimeError:
            raise
        except Exception:
            pass

        # Try common shells in order.
        errors = []
        for shell in ("/bin/sh", "/bin/bash", "/bin/ash"):
            try:
                rc, output = container.exec_run(
                    cmd=[shell, "-lc", shell_command],
                    stdout=True,
                    stderr=True,
                    demux=False,
                )
                text = output.decode("utf-8", errors="replace") if isinstance(output, (bytes, bytearray)) else str(output)
                return int(rc), text
            except Exception as e:
                errors.append(str(e))
                continue
        detail = errors[-1] if errors else "unknown runtime error"
        raise RuntimeError(f"Container shell is not available: {detail}")

    def exec_command(self, container_id: str, command: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        cmd = (command or "").strip()
        if not cmd:
            raise RuntimeError("Command cannot be empty")
        runtime = self._container_runtime_context(container.id)
        raw_console_cwd = str(runtime.get("console_cwd") or "").strip()
        console_cwd = self._normalize_explorer_path(raw_console_cwd) if raw_console_cwd else ""
        if console_cwd and console_cwd != "/":
            cmd = f"cd {shlex.quote(console_cwd)} 2>/dev/null || true; {cmd}"

        rc, output = self._exec_shell(container, cmd)
        return {
            "id": container.id[:12],
            "name": container.name,
            "exit_code": rc,
            "output": output[-200000:],
        }

    def infer_profile(self, image_name: str) -> str:
        img = str(image_name or "").lower()
        if "minecraft" in img or "paper" in img or "spigot" in img:
            return "minecraft"
        if any(x in img for x in ("python", "django", "flask", "fastapi", "uvicorn", "gunicorn")):
            return "python"
        if "nginx" in img or "apache" in img or "caddy" in img or "traefik" in img:
            return "web"
        if any(x in img for x in ("mysql", "mariadb", "postgres", "mongo", "redis")):
            return "database"
        if "steam" in img or "gameserver" in img or "srcds" in img:
            return "steam"
        return "generic"

    def get_profile_policy(self, container_id: str):
        detail = self.get_container_detail(container_id)
        runtime = self._container_runtime_context(detail.get("full_id") or container_id)
        profile = runtime.get("profile_name") or self.infer_profile(detail.get("image") or "")
        base_profile = profile if profile in self.PROFILE_POLICIES else self.infer_profile(detail.get("image") or "")
        policy = dict(self.PROFILE_POLICIES.get(base_profile, self.PROFILE_POLICIES["generic"]))
        policy["profile"] = profile
        policy["base_profile"] = base_profile
        policy["image"] = detail.get("image") or ""
        policy["explorer_root"] = runtime.get("explorer_root")
        policy["console_cwd"] = runtime.get("console_cwd")
        return policy

    def validate_user_shell_command(self, command: str, profile: str):
        cmd = f" {str(command or '').lower().strip()} "
        if not cmd.strip():
            return False, "Command cannot be empty"

        policy = self.PROFILE_POLICIES.get(profile, self.PROFILE_POLICIES["generic"])
        if not policy.get("shell_allowed_for_user", True):
            return False, f"Shell access is disabled for {policy.get('label', profile)} profile"

        for pattern in self.USER_BLOCKED_SHELL_PATTERNS:
            if pattern in cmd:
                return False, "Command contains blocked operation for user role"
        return True, ""

    def send_console_input(self, container_id: str, command: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        line = (command or "").strip()
        if not line:
            raise RuntimeError("Console command cannot be empty")

        cmd_q = shlex.quote(line)
        script = (
            "if [ -w /proc/1/fd/0 ]; then "
            f"printf '%s\\n' {cmd_q} > /proc/1/fd/0; "
            "else echo '__NEBULA_STDIN_UNAVAILABLE__'; exit 46; fi"
        )
        rc, output = self._exec_shell(container, script)
        if rc == 46 or "__NEBULA_STDIN_UNAVAILABLE__" in output:
            fallback = self._try_profile_console_fallback(container, line)
            if fallback:
                return {
                    "id": container.id[:12],
                    "name": container.name,
                    "status": "sent",
                    "command": line,
                    "transport": fallback.get("transport", "profile-fallback"),
                    "output": fallback.get("output", ""),
                }
            raise RuntimeError(
                "Application console stdin is not available in this container. "
                "Recreate the container with stdin enabled (OpenStdin/stdin_open=true) for live console input."
            )
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": "sent",
            "command": line,
            "transport": "stdin",
            "output": "",
        }

    def _try_profile_console_fallback(self, container, line: str):
        image = (container.image.tags[0] if container.image.tags else "") or ""
        profile = self.infer_profile(image)
        if profile != "minecraft":
            return None

        cmd_q = shlex.quote(line)
        attempts = (
            ("mc-send-to-console", f"if command -v mc-send-to-console >/dev/null 2>&1; then mc-send-to-console {cmd_q}; else exit 127; fi"),
            ("mc-send-to-console", f"if [ -x /usr/local/bin/mc-send-to-console ]; then /usr/local/bin/mc-send-to-console {cmd_q}; else exit 127; fi"),
            ("rcon-cli", f"if command -v rcon-cli >/dev/null 2>&1; then rcon-cli {cmd_q}; else exit 127; fi"),
            ("rcon-cli", f"if [ -x /usr/local/bin/rcon-cli ]; then /usr/local/bin/rcon-cli {cmd_q}; else exit 127; fi"),
        )
        for transport, script in attempts:
            try:
                rc, out = self._exec_shell(container, script)
                if rc == 0:
                    return {
                        "transport": transport,
                        "output": (out or "").strip(),
                    }
            except Exception:
                continue
        return None

    def list_files(self, container_id: str, path: str = "/"):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        target = self._normalize_explorer_path(path)
        if not self._is_allowed_explorer_path(target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        target_q = shlex.quote(target)
        script = (
            f"if [ -d {target_q} ]; then ls -la {target_q}; "
            f"elif [ -e {target_q} ]; then ls -la {target_q}; "
            "else echo '__NEBULA_NOT_FOUND__'; exit 44; fi"
        )
        rc, output = self._exec_shell(container, script)
        if rc == 44 or "__NEBULA_NOT_FOUND__" in output:
            raise RuntimeError("Path not found")

        entries = []
        lines = output.splitlines()
        for line in lines:
            line = line.rstrip()
            if not line or line.startswith("total "):
                continue
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                continue
            perms, _, owner, group, size, month, day, clock_or_year, name = parts
            if name in (".", ".."):
                continue
            if perms.startswith("d"):
                entry_type = "dir"
            elif perms.startswith("l"):
                entry_type = "link"
            else:
                entry_type = "file"
            entries.append({
                "name": name,
                "type": entry_type,
                "size": size,
                "owner": owner,
                "group": group,
                "modified": f"{month} {day} {clock_or_year}",
                "perms": perms,
            })

        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
        return {
            "id": container.id[:12],
            "path": target,
            "entries": entries,
        }

    def detect_workspace_roots(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        runtime = self._container_runtime_context(container.id)
        profile = runtime.get("profile_name") or self.infer_profile((container.image.tags[0] if container.image.tags else "") or "")
        base_profile = profile if profile in self.PROFILE_WORKSPACE_ROOTS else self.infer_profile((container.image.tags[0] if container.image.tags else "") or "")
        candidates = list(self.PROFILE_WORKSPACE_ROOTS.get(base_profile, self.EXPLORER_ALLOWED_ROOTS))
        raw_preferred_override = str(runtime.get("explorer_root") or "").strip()
        preferred_override = self._normalize_explorer_path(raw_preferred_override) if raw_preferred_override else ""
        if preferred_override and preferred_override not in candidates:
            candidates.insert(0, preferred_override)
        args = " ".join(shlex.quote(c) for c in candidates)
        script = (
            "for d in " + args + "; do "
            "if [ -d \"$d\" ]; then "
            "if [ -r \"$d\" ]; then printf '%s\\n' \"$d\"; fi; "
            "fi; "
            "done"
        )
        _, output = self._exec_shell(container, script)
        existing = [line.strip() for line in output.splitlines() if line.strip()]
        existing_set = set(existing)

        preferred = next(
            (
                p for p in candidates if p in existing_set
            ),
            preferred_override or candidates[0]
        )

        roots = []
        for p in (preferred, *candidates):
            if p in existing_set:
                if p not in roots:
                    roots.append(p)

        return {
            "id": container.id[:12],
            "profile": profile,
            "base_profile": base_profile,
            "preferred_path": preferred,
            "roots": roots,
        }

    def read_file(self, container_id: str, path: str, max_bytes: int = 200000):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        target = self._normalize_explorer_path(path)
        if not target:
            raise RuntimeError("Path is required")
        if not self._is_allowed_explorer_path(target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")

        limit = max(1024, min(int(max_bytes), 500000))
        target_q = shlex.quote(target)
        script = f"if [ -f {target_q} ]; then head -c {limit} {target_q}; else echo '__NEBULA_NOT_FILE__'; exit 45; fi"
        rc, output = self._exec_shell(container, script)
        if rc == 45 or "__NEBULA_NOT_FILE__" in output:
            raise RuntimeError("Target is not a readable file")

        return {
            "id": container.id[:12],
            "path": target,
            "content": output,
            "truncated": len(output.encode("utf-8", errors="ignore")) >= limit,
        }

    @staticmethod
    def _normalize_explorer_path(path: str) -> str:
        raw = (path or "").strip()
        if not raw:
            raw = "/data"
        if not raw.startswith("/"):
            raw = "/" + raw
        normalized = posixpath.normpath(raw)
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        return normalized

    @classmethod
    def _is_allowed_explorer_path(cls, path: str, roots=None) -> bool:
        normalized_path = cls._normalize_explorer_path(path)
        active_roots = tuple(roots or cls.EXPLORER_ALLOWED_ROOTS)
        for root in active_roots:
            normalized_root = cls._normalize_explorer_path(root)
            if normalized_root == "/":
                return True
            if normalized_path == normalized_root or normalized_path.startswith(normalized_root + "/"):
                return True
        return False

    def _workspace_roots_for_container(self, container):
        runtime = self._container_runtime_context(container.id)
        image = (container.image.tags[0] if container.image.tags else "") or ""
        profile = runtime.get("profile_name") or self.infer_profile(image)
        base_profile = profile if profile in self.PROFILE_WORKSPACE_ROOTS else self.infer_profile(image)
        roots = list(self.PROFILE_WORKSPACE_ROOTS.get(base_profile, self.EXPLORER_ALLOWED_ROOTS))
        raw_preferred = str(runtime.get("explorer_root") or "").strip()
        preferred = self._normalize_explorer_path(raw_preferred) if raw_preferred else ""
        if preferred and preferred not in roots:
            roots.insert(0, preferred)
        return tuple(roots)

    def _container_runtime_context(self, full_id: str):
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                """
                SELECT explorer_root, console_cwd, profile_name, workspace_mount
                FROM container_storage
                WHERE container_id = ?
                """,
                (full_id,),
            ).fetchone()
        if not row:
            return {}
        payload = dict(row)
        if payload.get("workspace_mount") and not payload.get("explorer_root"):
            payload["explorer_root"] = payload["workspace_mount"]
        if payload.get("explorer_root") and not payload.get("console_cwd"):
            payload["console_cwd"] = payload["explorer_root"]
        return payload

    @staticmethod
    def _normalize_host_ip(host_ip: str) -> str:
        ip = str(host_ip or "").strip()
        if ip in ("", "0.0.0.0", "::", "::0"):
            return ""
        return ip

    def _container_available_ports(self, full_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                return []
        try:
            container = self.client.containers.get(full_id)
            container.reload()
        except Exception:
            return []

        ports = ((container.attrs or {}).get("NetworkSettings", {}) or {}).get("Ports", {}) or {}
        rules = []
        for container_port, bindings in ports.items():
            if not bindings:
                continue
            proto = "tcp"
            container_num = str(container_port)
            if "/" in container_num:
                container_num, proto = container_num.split("/", 1)
            for b in bindings:
                host_port = str((b or {}).get("HostPort") or "").strip()
                if not host_port:
                    continue
                host_ip = self._normalize_host_ip((b or {}).get("HostIp") or "")
                if host_ip:
                    rule = f"{host_ip}:{host_port}:{container_num}"
                else:
                    rule = f"{host_port}:{container_num}"
                if proto != "tcp":
                    rule = f"{rule}/{proto}"
                rules.append(rule)
        return sorted(set(rules))

    def get_container_settings(self, container_id: str):
        full_id = self.resolve_container_id(container_id)
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT startup_command, allowed_ports, updated_by, updated_at FROM container_settings WHERE container_id = ?",
                (full_id,),
            ).fetchone()
        available_ports = self._container_available_ports(full_id)
        if not row:
            return {
                "startup_command": "",
                "allowed_ports": "",
                "updated_by": None,
                "updated_at": None,
                "available_ports": available_ports,
            }
        data = dict(row)
        data["available_ports"] = available_ports
        return data

    def update_container_settings(self, container_id: str, startup_command: str, allowed_ports: str, updated_by: str):
        full_id = self.resolve_container_id(container_id)
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO container_settings (container_id, startup_command, allowed_ports, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(container_id) DO UPDATE SET
                    startup_command=excluded.startup_command,
                    allowed_ports=excluded.allowed_ports,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (
                    full_id,
                    (startup_command or "").strip() or None,
                    (allowed_ports or "").strip() or None,
                    updated_by or "unknown",
                ),
            )
        return self.get_container_settings(full_id)

    def get_restart_policy(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.reload()
        policy = (
            (container.attrs or {})
            .get("HostConfig", {})
            .get("RestartPolicy", {})
        ) or {}
        name = (policy.get("Name") or "no").strip() or "no"
        retry_count = int(policy.get("MaximumRetryCount") or 0)
        return {
            "id": container.id[:12],
            "name": container.name,
            "restart_policy": name,
            "maximum_retry_count": retry_count,
        }

    def update_restart_policy(self, container_id: str, restart_policy: str, maximum_retry_count: int = 0):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        policy_name = (restart_policy or "").strip().lower()
        if policy_name not in {"no", "always", "unless-stopped", "on-failure"}:
            raise RuntimeError("Invalid restart policy")

        payload = {"Name": policy_name}
        if policy_name == "on-failure":
            payload["MaximumRetryCount"] = max(0, int(maximum_retry_count or 0))

        container.update(restart_policy=payload)
        return self.get_restart_policy(container.id)

    def restart_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.restart()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def start_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.start()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def stop_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.stop()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def get_container_logs(self, container_id: str, tail: int = 200):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        raw = container.logs(tail=max(1, min(int(tail), 2000)), timestamps=True)
        logs = raw.decode("utf-8", errors="replace")
        return {
            "id": container.id[:12],
            "name": container.name,
            "logs": logs
        }

    def delete_container(self, container_id: str, force: bool = True):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        full_id = container.id
        workspace_row = None
        with get_connection(SYSTEM_DB) as conn:
            workspace_row = conn.execute(
                "SELECT workspace_path, managed_workspace FROM container_storage WHERE container_id = ?",
                (full_id,)
            ).fetchone()
        container.remove(force=force)

        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                "DELETE FROM container_permissions WHERE container_id = ?",
                (full_id,)
            )
            conn.execute(
                "DELETE FROM container_settings WHERE container_id = ?",
                (full_id,)
            )
            conn.execute(
                "DELETE FROM container_storage WHERE container_id = ?",
                (full_id,)
            )
            conn.execute(
                "DELETE FROM container_role_permissions WHERE container_id = ?",
                (full_id,)
            )

        if workspace_row:
            workspace_path = (workspace_row["workspace_path"] or "").strip()
            managed_workspace = bool(workspace_row["managed_workspace"])
            if managed_workspace and workspace_path:
                abs_ws = os.path.abspath(workspace_path)
                abs_base = os.path.abspath(self.WORKSPACES_BASE_DIR)
                if abs_ws.startswith(abs_base + os.sep):
                    try:
                        shutil.rmtree(abs_ws, ignore_errors=True)
                    except Exception:
                        pass

        return {
            "id": full_id[:12],
            "status": "deleted"
        }
