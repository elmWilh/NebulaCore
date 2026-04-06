# nebula_core/services/docker_service.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import docker
import time
import shlex
import posixpath
import psutil
import os
import re
import shutil
import json
import io
import tarfile
import zipfile
import threading
import uuid
import select
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
import getpass
import grp
from ..db import get_connection, SYSTEM_DB
from ..core.context import context
from .security_service import SecurityService

# DockerService should not crash application startup if the Docker daemon/socket
# is unavailable. Attempt to create client, but fall back to a disabled state
# and provide clear errors from methods when called.

class DockerService:
    _SCHEMA_LOCK = threading.Lock()
    _SCHEMA_READY = False
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
            "shell_exec_profile": "disabled",
            "app_console_supported": True,
            "tools": ["server commands", "log streaming", "file explorer", "restart policy"],
        },
        "web": {
            "label": "Web/Nginx",
            "shell_allowed_for_user": True,
            "shell_exec_profile": "web_ops",
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "python": {
            "label": "Python App",
            "shell_allowed_for_user": True,
            "shell_exec_profile": "python_ops",
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "database": {
            "label": "Database",
            "shell_allowed_for_user": False,
            "shell_exec_profile": "disabled",
            "app_console_supported": False,
            "tools": ["log streaming", "file explorer", "restart policy"],
        },
        "steam": {
            "label": "Steam/Game Dedicated",
            "shell_allowed_for_user": True,
            "shell_exec_profile": "ops_basic",
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
        "generic": {
            "label": "Generic Container",
            "shell_allowed_for_user": True,
            "shell_exec_profile": "ops_basic",
            "app_console_supported": False,
            "tools": ["shell commands", "log streaming", "file explorer", "restart policy"],
        },
    }
    EXEC_PROFILES = {
        "disabled": {
            "label": "Disabled",
            "allowed_commands": (),
        },
        "ops_basic": {
            "label": "Basic Ops",
            "allowed_commands": (
                "pwd", "ls", "find", "cat", "head", "tail", "grep", "sed", "awk",
                "sort", "uniq", "cut", "wc", "stat", "du", "df",
                "mkdir", "cp", "mv", "rm", "touch",
                "tar", "unzip", "zip",
                "ps", "top", "env", "printenv", "echo",
            ),
        },
        "web_ops": {
            "label": "Web Ops",
            "allowed_commands": (
                "pwd", "ls", "find", "cat", "head", "tail", "grep", "sed", "awk",
                "sort", "uniq", "cut", "wc", "stat", "du", "df",
                "mkdir", "cp", "mv", "rm", "touch",
                "tar", "unzip", "zip",
                "ps", "top", "env", "printenv", "echo",
                "nginx", "apachectl", "httpd", "caddy",
            ),
        },
        "python_ops": {
            "label": "Python Ops",
            "allowed_commands": (
                "pwd", "ls", "find", "cat", "head", "tail", "grep", "sed", "awk",
                "sort", "uniq", "cut", "wc", "stat", "du", "df",
                "mkdir", "cp", "mv", "rm", "touch",
                "tar", "unzip", "zip",
                "ps", "top", "env", "printenv", "echo",
                "python", "python3", "pip", "pip3", "pytest",
                "uvicorn", "gunicorn", "flask", "django-admin", "manage.py",
            ),
        },
    }
    SHELL_META_TOKENS = (";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "`", "$(", "\n", "\r")
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
        self.security_service = SecurityService()
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
        self._container_runtime_cache = {}
        self._container_runtime_cache_ttl = 4.0
        self._workspace_usage_cache = {}
        self._workspace_usage_cache_ttl = 15.0
        self._pty_sessions = {}
        self._pty_sessions_lock = threading.Lock()
        self._pty_session_ttl = 120.0
        self._pty_session_buffer_limit = 250000
        os.makedirs(self.PRESETS_BASE_DIR, exist_ok=True)
        self._ensure_container_schema()

    @classmethod
    def _ensure_container_schema(cls):
        if cls._SCHEMA_READY:
            return
        with cls._SCHEMA_LOCK:
            if cls._SCHEMA_READY:
                return
            with get_connection(SYSTEM_DB) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS container_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        container_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        actor_db TEXT NOT NULL DEFAULT 'system.db',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_container_audit_container ON container_audit_log(container_id, created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_container_audit_actor ON container_audit_log(actor, created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_container_audit_action ON container_audit_log(action, created_at DESC)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS container_settings (
                        container_id TEXT PRIMARY KEY,
                        startup_command TEXT,
                        allowed_ports TEXT,
                        project_protocol TEXT,
                        install_command TEXT,
                        domain_name TEXT,
                        launch_url TEXT,
                        updated_by TEXT,
                        updated_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS container_runtime_settings (
                        container_id TEXT PRIMARY KEY,
                        applied_startup_command TEXT,
                        applied_allowed_ports TEXT,
                        applied_project_protocol TEXT,
                        applied_install_command TEXT,
                        applied_domain_name TEXT,
                        applied_launch_url TEXT,
                        applied_by TEXT,
                        applied_at TEXT
                    )
                    """
                )
                existing_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(container_settings)").fetchall()
                }
                for column_name, column_type in (
                    ("project_protocol", "TEXT"),
                    ("install_command", "TEXT"),
                    ("domain_name", "TEXT"),
                    ("launch_url", "TEXT"),
                ):
                    if column_name not in existing_columns:
                        conn.execute(
                            f"ALTER TABLE container_settings ADD COLUMN {column_name} {column_type}"
                        )
            cls._SCHEMA_READY = True

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

    @staticmethod
    def _clean_setting_text(value):
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _path_within_root(root_path: str, candidate_path: str) -> bool:
        try:
            return os.path.commonpath([root_path, candidate_path]) == root_path
        except ValueError:
            return False

    @classmethod
    def _resolve_host_workspace_path(
        cls,
        workspace_root: str,
        host_target: str,
        *,
        follow_leaf: bool = True,
        allow_missing_leaf: bool = False,
    ) -> str:
        root = os.path.realpath(str(workspace_root or "").strip())
        candidate = os.path.abspath(str(host_target or "").strip())
        if not root or not candidate:
            raise RuntimeError("Workspace path resolution failed")
        if not cls._path_within_root(root, candidate):
            raise RuntimeError("Resolved workspace path escapes allowed host workspace")

        if allow_missing_leaf and not os.path.lexists(candidate):
            parent_real = os.path.realpath(os.path.dirname(candidate))
            resolved = os.path.join(parent_real, os.path.basename(candidate))
        elif follow_leaf:
            resolved = os.path.realpath(candidate)
        elif os.path.islink(candidate):
            parent_real = os.path.realpath(os.path.dirname(candidate))
            resolved = os.path.join(parent_real, os.path.basename(candidate))
        else:
            resolved = os.path.realpath(candidate)

        if not cls._path_within_root(root, resolved):
            raise RuntimeError("Resolved workspace path escapes allowed host workspace")
        return resolved

    @classmethod
    def _assert_host_workspace_target(
        cls,
        workspace_root: str,
        host_target: str,
        *,
        follow_leaf: bool = True,
        allow_missing_leaf: bool = False,
    ):
        cls._resolve_host_workspace_path(
            workspace_root,
            host_target,
            follow_leaf=follow_leaf,
            allow_missing_leaf=allow_missing_leaf,
        )

    @staticmethod
    def _resolve_panel_group_name() -> str:
        return str(os.getenv("NEBULA_PANEL_GROUP") or "nebulapanel").strip() or "nebulapanel"

    @classmethod
    def _resolve_panel_group_gid(cls):
        group_name = cls._resolve_panel_group_name()
        try:
            return group_name, grp.getgrnam(group_name).gr_gid
        except KeyError:
            return group_name, None

    @classmethod
    def _prepare_managed_workspace_permissions(cls, workspace_path: str):
        target = os.path.abspath(str(workspace_path or "").strip())
        if not target:
            return {
                "workspace_path": "",
                "panel_group": cls._resolve_panel_group_name(),
                "panel_group_found": False,
                "prepared": False,
            }
        os.makedirs(target, exist_ok=True)
        group_name, gid = cls._resolve_panel_group_gid()
        prepared = False
        try:
            current_mode = os.stat(target).st_mode & 0o7777
            desired_mode = current_mode | 0o2775
            if current_mode != desired_mode:
                os.chmod(target, desired_mode)
            if gid is not None:
                os.chown(target, -1, gid)
            prepared = True
        except PermissionError as exc:
            context.logger.warning("Managed workspace permission preparation skipped for %s: %s", target, exc)
        except OSError as exc:
            context.logger.warning("Managed workspace permission preparation failed for %s: %s", target, exc)
        return {
            "workspace_path": target,
            "panel_group": group_name,
            "panel_group_found": gid is not None,
            "prepared": prepared,
        }

    @staticmethod
    def _compose_runtime_command(project_protocol: str, install_command: str, startup_command: str, workspace_cwd: str = ""):
        protocol = str(project_protocol or "").strip().lower()
        install = str(install_command or "").strip()
        startup = str(startup_command or "").strip()
        cwd = str(workspace_cwd or "").strip()
        if not startup:
            return None
        cwd_step = ""
        if cwd and cwd != "/":
            cwd_step = f"cd {shlex.quote(cwd)} 2>/dev/null || true; "
        if protocol in {"python-flask", "python-fastapi", "python-django", "python-pip"}:
            install_step = install or "python -m pip install -r requirements.txt"
            dependency_check = ""
            dependency_hint = "Python dependencies are not ready yet. Upload requirements.txt or install packages from Container Workspace, then start the project again."
            if protocol == "python-flask":
                dependency_check = (
                    "if ! python -c 'import flask' >/dev/null 2>&1; then "
                    f"echo {shlex.quote(dependency_hint)}; "
                    "exec tail -f /dev/null; "
                    "fi; "
                )
            elif protocol == "python-fastapi":
                dependency_check = (
                    "if ! python -c 'import fastapi' >/dev/null 2>&1; then "
                    f"echo {shlex.quote(dependency_hint)}; "
                    "exec tail -f /dev/null; "
                    "fi; "
                )
            elif protocol == "python-django":
                dependency_check = (
                    "if ! python -c 'import django' >/dev/null 2>&1; then "
                    f"echo {shlex.quote(dependency_hint)}; "
                    "exec tail -f /dev/null; "
                    "fi; "
                )
            script = (
                cwd_step +
                "if [ -f app.py ] || [ -f manage.py ] || [ -f wsgi.py ] || [ -d app ] || [ -d src ]; then "
                "install_ok=1; "
                "if [ -f requirements.txt ]; then "
                f"{install_step} || install_ok=0; "
                "elif [ -f pyproject.toml ]; then python -m pip install . || install_ok=0; "
                "fi; "
                "if [ \"$install_ok\" -ne 1 ]; then "
                "echo 'Nebula could not install Python dependencies automatically. Review requirements.txt or pyproject.toml, then retry from Container Workspace.'; "
                "exec tail -f /dev/null; "
                "fi; "
                f"{dependency_check}"
                f"exec {startup}; "
                "else "
                "echo 'Nebula workspace is empty. Upload your project files, then run install/start from Container Workspace.'; "
                "exec tail -f /dev/null; "
                "fi"
            )
            return f"/bin/sh -lc {shlex.quote(script)}"

        if protocol.startswith("node"):
            script = (
                cwd_step +
                "if [ -f package.json ]; then "
                "install_ok=1; "
                f"{install or 'npm install'} || install_ok=0; "
                "if [ \"$install_ok\" -ne 1 ]; then "
                "echo 'Nebula could not install Node dependencies automatically. Review package.json / lockfiles, then retry from Container Workspace.'; "
                "exec tail -f /dev/null; "
                "fi; "
                f"exec {startup}; "
                "else "
                "echo 'Nebula workspace is empty. Upload package.json and project files, then run install/start from Container Workspace.'; "
                "exec tail -f /dev/null; "
                "fi"
            )
            return f"/bin/sh -lc {shlex.quote(script)}"

        if not install:
            if cwd_step:
                return f"/bin/sh -lc {shlex.quote(cwd_step + 'exec ' + startup)}"
            return startup

        return f'/bin/sh -lc {shlex.quote(cwd_step + install + "; exec " + startup)}'

    def infer_project_protocol(self, profile_name: str, image_name: str = "") -> str:
        profile = self._safe_workspace_token(profile_name or "")
        image = str(image_name or "").lower()
        if profile in {"python-flask", "flask"} or "flask" in image:
            return "python-flask"
        if profile in {"python-fastapi", "fastapi"} or "fastapi" in image or "uvicorn" in image:
            return "python-fastapi"
        if profile in {"django", "python-django"} or "django" in image:
            return "python-django"
        if profile in {"node-vite", "vite"} or "vite" in image:
            return "node-vite"
        if profile in {"node-express", "express"} or "node" in image:
            return "node-npm"
        if profile in {"nginx-static", "caddy-static"}:
            return "static-web"
        base_profile = profile if profile in {"python", "web", "database", "minecraft", "steam"} else self.infer_profile(image_name)
        if base_profile == "python":
            return "python-pip"
        if base_profile == "web":
            return "static-web"
        return "generic"

    def _preset_file_path(self, preset_name: str) -> str:
        token = self._safe_workspace_token(preset_name or "preset")
        return os.path.join(self.PRESETS_BASE_DIR, f"{token}.json")

    def _save_applied_runtime_settings(
        self,
        container_id: str,
        startup_command: str,
        allowed_ports: str,
        project_protocol: str,
        install_command: str,
        domain_name: str,
        launch_url: str,
        applied_by: str,
    ):
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO container_runtime_settings (
                    container_id, applied_startup_command, applied_allowed_ports, applied_project_protocol,
                    applied_install_command, applied_domain_name, applied_launch_url, applied_by, applied_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(container_id) DO UPDATE SET
                    applied_startup_command=excluded.applied_startup_command,
                    applied_allowed_ports=excluded.applied_allowed_ports,
                    applied_project_protocol=excluded.applied_project_protocol,
                    applied_install_command=excluded.applied_install_command,
                    applied_domain_name=excluded.applied_domain_name,
                    applied_launch_url=excluded.applied_launch_url,
                    applied_by=excluded.applied_by,
                    applied_at=datetime('now')
                """,
                (
                    container_id,
                    self._clean_setting_text(startup_command),
                    self._clean_setting_text(allowed_ports),
                    self._clean_setting_text(project_protocol),
                    self._clean_setting_text(install_command),
                    self._clean_setting_text(domain_name),
                    self._clean_setting_text(launch_url),
                    applied_by or "unknown",
                ),
            )

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
                        "ui": raw.get("ui") if isinstance(raw.get("ui"), dict) else {},
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
            "ui": {},
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
        assignments = self.get_container_user_assignments(full_id)
        group_assignments = self.get_container_group_assignments(full_id)
        role_tag = self.resolve_user_role(username, db_name, is_staff)
        access_override = self.security_service.resolve_container_role_override(username, db_name, full_id)
        if access_override and access_override.get("role_tag"):
            role_tag = self._normalize_role_tag(access_override["role_tag"])
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
        policy["user_assignments"] = assignments
        policy["group_assignments"] = group_assignments
        if access_override:
            policy["access_role_source"] = access_override
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

    def get_container_user_assignments(self, container_id: str):
        full_id = self.resolve_container_id(container_id)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT username, db_name, role_tag
                FROM container_permissions
                WHERE container_id = ?
                ORDER BY username ASC
                """,
                (full_id,),
            ).fetchall()
        return [
            {
                "username": str(row["username"] or "").strip(),
                "db_name": str(row["db_name"] or "system.db").strip() or "system.db",
                "role_tag": self._normalize_role_tag(row["role_tag"] or "user"),
            }
            for row in rows
            if str(row["username"] or "").strip()
        ]

    def get_container_group_assignments(self, container_id: str):
        full_id = self.resolve_container_id(container_id)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT ga.group_name, ga.role_tag, g.title, g.priority
                FROM user_group_container_access ga
                LEFT JOIN user_groups g ON g.group_name = ga.group_name
                WHERE ga.container_id = ?
                ORDER BY COALESCE(g.priority, 100) ASC, ga.group_name ASC
                """,
                (full_id,),
            ).fetchall()
        return [
            {
                "group_name": str(row["group_name"] or "").strip(),
                "title": str(row["title"] or row["group_name"] or "").strip(),
                "role_tag": self._normalize_role_tag(row["role_tag"] or "user"),
                "priority": int(row["priority"] or 100),
            }
            for row in rows
            if str(row["group_name"] or "").strip()
        ]

    def append_container_audit_log(self, container_id: str, action: str, actor: str, actor_db: str, details: dict | None = None):
        self._ensure_container_schema()
        full_id = self.resolve_container_id(container_id)
        payload = details if isinstance(details, dict) else {}
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO container_audit_log (container_id, action, actor, actor_db, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    full_id,
                    str(action or "container.event"),
                    str(actor or "system"),
                    str(actor_db or "system.db"),
                    json.dumps(payload, ensure_ascii=True),
                    int(time.time()),
                ),
            )
        return {"status": "logged", "container_id": full_id}

    def list_container_audit_log(self, container_id: str, limit: int = 25):
        self._ensure_container_schema()
        full_id = self.resolve_container_id(container_id)
        capped = max(1, min(int(limit or 25), 200))
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT id, container_id, action, actor, actor_db, details_json, created_at
                FROM container_audit_log
                WHERE container_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (full_id, capped),
            ).fetchall()
        entries = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                details = {}
            entries.append({
                "id": int(row["id"]),
                "container_id": full_id,
                "action": str(row["action"] or "container.event"),
                "actor": str(row["actor"] or "system"),
                "actor_db": str(row["actor_db"] or "system.db"),
                "details": details if isinstance(details, dict) else {},
                "created_at": int(row["created_at"] or 0),
            })
        return {"container_id": full_id, "entries": entries}

    def set_container_access_policies(self, container_id: str, role_policies: dict, user_assignments: list, updated_by: str, group_assignments: list | None = None):
        full_id = self.resolve_container_id(container_id)
        if role_policies is not None and not isinstance(role_policies, dict):
            raise RuntimeError("role_policies must be an object")
        if user_assignments is not None and not isinstance(user_assignments, list):
            raise RuntimeError("user_assignments must be an array")
        if group_assignments is not None and not isinstance(group_assignments, list):
            raise RuntimeError("group_assignments must be an array")

        with get_connection(SYSTEM_DB) as conn:
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

            if isinstance(user_assignments, list):
                conn.execute(
                    "DELETE FROM container_permissions WHERE container_id = ?",
                    (full_id,),
                )
                dedup = {}
                for item in user_assignments:
                    if not isinstance(item, dict):
                        continue
                    username = str(item.get("username") or "").strip()
                    if not username:
                        continue
                    dedup[username] = {
                        "username": username,
                        "db_name": str(item.get("db_name") or "system.db").strip() or "system.db",
                        "role_tag": self._normalize_role_tag(item.get("role_tag") or "user"),
                    }
                for item in dedup.values():
                    conn.execute(
                        """
                        INSERT INTO container_permissions (container_id, username, db_name, role_tag)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(container_id, username) DO UPDATE SET
                            db_name=excluded.db_name,
                            role_tag=excluded.role_tag
                        """,
                        (full_id, item["username"], item["db_name"], item["role_tag"]),
                    )
            if isinstance(group_assignments, list):
                conn.execute(
                    "DELETE FROM user_group_container_access WHERE container_id = ?",
                    (full_id,),
                )
                dedup_groups = {}
                for item in group_assignments:
                    if not isinstance(item, dict):
                        continue
                    try:
                        group_name = self.security_service.normalize_group_name(item.get("group_name") or item.get("name") or "")
                    except Exception:
                        continue
                    if not group_name:
                        continue
                    dedup_groups[group_name] = {
                        "group_name": group_name,
                        "role_tag": self._normalize_role_tag(item.get("role_tag") or "user"),
                    }
                for item in dedup_groups.values():
                    conn.execute(
                        """
                        INSERT INTO user_group_container_access (group_name, container_id, role_tag, access_origin, updated_by, updated_at)
                        VALUES (?, ?, ?, 'group', ?, ?)
                        ON CONFLICT(group_name, container_id) DO UPDATE SET
                            role_tag=excluded.role_tag,
                            updated_by=excluded.updated_by,
                            updated_at=excluded.updated_at
                        """,
                        (item["group_name"], full_id, item["role_tag"], updated_by or "unknown", int(time.time())),
                    )

        return {
            "container_id": full_id,
            "role_policies": self.get_container_role_policies(full_id)[1],
            "user_assignments": self.get_container_user_assignments(full_id),
            "group_assignments": self.get_container_group_assignments(full_id),
        }

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

    def _empty_runtime_metrics(self):
        return {
            "cpu_percent": None,
            "memory_used_mb": None,
            "memory_limit_mb": None,
            "network_tx_mbps": None,
            "network_rx_mbps": None,
        }

    def _get_container_runtime_metrics(self, container, now: float):
        runtime_metrics = self._empty_runtime_metrics()
        cache_key = f"container-runtime:{container.id}"
        cached_runtime = self._container_runtime_cache.get(cache_key)
        if cached_runtime and (now - cached_runtime[0]) <= self._container_runtime_cache_ttl:
            return dict(cached_runtime[1])
        if container.status != "running":
            return runtime_metrics

        try:
            stats = container.stats(stream=False)
            mem = stats.get("memory_stats", {}) or {}
            used = float(mem.get("usage") or 0.0)
            limit = float(mem.get("limit") or 0.0)
            tx_bytes = 0
            rx_bytes = 0
            nets = stats.get("networks") or {}
            for _, n in nets.items():
                tx_bytes += int(n.get("tx_bytes") or 0)
                rx_bytes += int(n.get("rx_bytes") or 0)

            net_key = f"container-list:{container.id}"
            prev = self._net_state.get(net_key)
            tx_mbps = 0.0
            rx_mbps = 0.0
            if prev:
                p_tx, p_rx, p_ts = prev
                dt = max(0.2, now - p_ts)
                tx_mbps = max(0.0, (tx_bytes - p_tx) / dt / 1048576.0)
                rx_mbps = max(0.0, (rx_bytes - p_rx) / dt / 1048576.0)
            self._net_state[net_key] = (tx_bytes, rx_bytes, now)

            runtime_metrics = {
                "cpu_percent": round(self._calc_cpu_percent(stats), 2),
                "memory_used_mb": round(max(0.0, used) / 1048576.0, 2),
                "memory_limit_mb": round(max(0.0, limit) / 1048576.0, 2),
                "network_tx_mbps": round(tx_mbps, 2),
                "network_rx_mbps": round(rx_mbps, 2),
            }
            self._container_runtime_cache[cache_key] = (now, dict(runtime_metrics))
            return runtime_metrics
        except Exception:
            return runtime_metrics

    def _collect_runtime_metrics(self, containers: list):
        now = time.time()
        metrics_by_id = {}
        running = [c for c in containers if c.status == "running"]

        for container in containers:
            metrics_by_id[container.id] = self._empty_runtime_metrics()

        if not running:
            return metrics_by_id

        max_workers = min(8, len(running))
        if max_workers <= 1:
            for container in running:
                metrics_by_id[container.id] = self._get_container_runtime_metrics(container, now)
            return metrics_by_id

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self._get_container_runtime_metrics, container, now): container.id
                for container in running
            }
            for future in as_completed(future_map):
                container_id = future_map[future]
                try:
                    metrics_by_id[container_id] = future.result()
                except Exception:
                    metrics_by_id[container_id] = self._empty_runtime_metrics()

        return metrics_by_id

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
                group_rows = conn.execute(
                    """
                    SELECT DISTINCT ga.container_id
                    FROM user_group_members gm
                    JOIN user_group_container_access ga ON ga.group_name = gm.group_name
                    WHERE gm.username = ? AND gm.db_name = ?
                    """,
                    (username, db_name or "system.db"),
                ).fetchall()
                allowed_ids.update({r["container_id"] for r in group_rows})

        visible_containers = [c for c in containers if is_staff or c.id in allowed_ids]
        runtime_by_id = self._collect_runtime_metrics(visible_containers)

        res = []
        for c in visible_containers:
            res.append({
                "id": c.id[:12],
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "users": perm_map.get(c.id, []) if is_staff else [username],
                **runtime_by_id.get(c.id, self._empty_runtime_metrics()),
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
                group_rows = conn.execute(
                    """
                    SELECT DISTINCT ga.container_id
                    FROM user_group_members gm
                    JOIN user_group_container_access ga ON ga.group_name = gm.group_name
                    WHERE gm.username = ? AND gm.db_name = ?
                    """,
                    (username, db_name or "system.db"),
                ).fetchall()
                allowed_ids.update({r["container_id"] for r in group_rows})
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

    def list_docker_objects_summary(self, limit: int = 12):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        top_n = max(1, min(int(limit or 12), 50))

        images = []
        for image in self.client.images.list():
            tags = list(image.tags or [])
            label = tags[0] if tags else f"sha256:{image.short_id.split(':')[-1]}"
            attrs = image.attrs or {}
            images.append({
                "id": image.short_id,
                "label": label,
                "tags": tags[:5],
                "size_mb": round(float(attrs.get("Size") or 0.0) / 1048576.0, 2),
                "created": attrs.get("Created"),
            })
        images.sort(key=lambda item: str(item.get("label") or "").lower())

        volumes = []
        for volume in self.client.volumes.list():
            attrs = volume.attrs or {}
            volumes.append({
                "name": volume.name,
                "driver": attrs.get("Driver") or "local",
                "mountpoint": attrs.get("Mountpoint") or "",
                "labels": attrs.get("Labels") or {},
                "scope": attrs.get("Scope") or "local",
            })
        volumes.sort(key=lambda item: str(item.get("name") or "").lower())

        networks = []
        for network in self.client.networks.list():
            attrs = network.attrs or {}
            containers = attrs.get("Containers") or {}
            networks.append({
                "id": network.id[:12],
                "name": network.name,
                "driver": attrs.get("Driver") or "",
                "scope": attrs.get("Scope") or "",
                "internal": bool(attrs.get("Internal")),
                "attachable": bool(attrs.get("Attachable")),
                "containers": len(containers) if isinstance(containers, dict) else 0,
            })
        networks.sort(key=lambda item: str(item.get("name") or "").lower())

        return {
            "counts": {
                "images": len(images),
                "volumes": len(volumes),
                "networks": len(networks),
            },
            "images": images[:top_n],
            "volumes": volumes[:top_n],
            "networks": networks[:top_n],
        }

    def list_docker_events(self, limit: int = 50):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        max_items = max(1, min(int(limit or 50), 200))
        stream = self.client.events(decode=True, since=int(time.time()) - 3600)
        events = []
        try:
            for item in stream:
                if not isinstance(item, dict):
                    continue
                actor = item.get("Actor") or {}
                attrs = actor.get("Attributes") or {}
                events.append({
                    "time": int(item.get("time") or 0),
                    "type": str(item.get("Type") or "").strip(),
                    "action": str(item.get("Action") or "").strip(),
                    "id": str(item.get("id") or actor.get("ID") or "")[:12],
                    "name": str(attrs.get("name") or attrs.get("container") or attrs.get("image") or "").strip(),
                    "scope": str(item.get("scope") or "").strip(),
                })
                if len(events) >= max_items:
                    break
        finally:
            try:
                stream.close()
            except Exception:
                pass
        events.sort(key=lambda item: int(item.get("time") or 0), reverse=True)
        return {"events": events}

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
    def _infer_primary_container_port(port_bindings: dict | None):
        bindings = port_bindings or {}
        for container_key in bindings.keys():
            raw = str(container_key or "").strip()
            if not raw:
                continue
            try:
                return int(raw.split("/", 1)[0])
            except Exception:
                continue
        return None

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

    @staticmethod
    def _parse_labels(raw_labels: str):
        if not raw_labels:
            return None
        labels = {}
        for line in str(raw_labels).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
            else:
                key, value = line, ""
            key = str(key or "").strip()
            if not key:
                raise RuntimeError("Label key cannot be empty")
            labels[key] = str(value or "").strip()
        return labels or None

    @staticmethod
    def _parse_healthcheck(raw_healthcheck):
        if not isinstance(raw_healthcheck, dict):
            return None
        test = str(raw_healthcheck.get("test") or "").strip()
        if not test:
            return None

        def _ns(value):
            if value in (None, "", 0, "0"):
                return None
            return max(0, int(float(value) * 1_000_000_000))

        payload = {
            "test": ["CMD-SHELL", test],
            "interval": _ns(raw_healthcheck.get("interval_seconds")),
            "timeout": _ns(raw_healthcheck.get("timeout_seconds")),
            "retries": max(1, int(raw_healthcheck.get("retries") or 3)),
            "start_period": _ns(raw_healthcheck.get("start_period_seconds")),
        }
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def _format_env_lines(env_items):
        rows = []
        for item in env_items or []:
            line = str(item or "")
            if "=" not in line:
                continue
            rows.append(line)
        rows.sort(key=lambda value: value.split("=", 1)[0].lower())
        return "\n".join(rows)

    @staticmethod
    def _format_mount_lines(mounts):
        rows = []
        for mount in mounts or []:
            source = str((mount or {}).get("Source") or "").strip()
            destination = str((mount or {}).get("Destination") or "").strip()
            if not source or not destination:
                continue
            mode = "rw" if bool((mount or {}).get("RW", False)) else "ro"
            rows.append(f"{source}:{destination}:{mode}")
        rows.sort(key=str.lower)
        return "\n".join(rows)

    @staticmethod
    def _format_label_lines(labels: dict | None):
        items = []
        for key, value in (labels or {}).items():
            items.append(f"{key}={value}")
        items.sort(key=str.lower)
        return "\n".join(items)

    @staticmethod
    def _ns_to_seconds(value) -> int:
        if not value:
            return 0
        return int(int(value) / 1_000_000_000)

    def _healthcheck_editor_payload(self, config: dict | None):
        payload = config if isinstance(config, dict) else {}
        test = payload.get("Test") or payload.get("test") or []
        test_command = ""
        if isinstance(test, list) and len(test) >= 2:
            test_command = str(test[-1] or "").strip()
        elif isinstance(test, str):
            test_command = str(test).strip()
        return {
            "test": test_command,
            "interval_seconds": self._ns_to_seconds(payload.get("Interval") or payload.get("interval")),
            "timeout_seconds": self._ns_to_seconds(payload.get("Timeout") or payload.get("timeout")),
            "retries": int(payload.get("Retries") or payload.get("retries") or 0),
            "start_period_seconds": self._ns_to_seconds(payload.get("StartPeriod") or payload.get("start_period")),
        }

    def _ports_from_container_attrs(self, attrs: dict):
        bindings = (((attrs or {}).get("HostConfig") or {}).get("PortBindings") or {})
        rules = {}
        for container_port, binding_rows in bindings.items():
            host_entries = []
            for row in binding_rows or []:
                if not isinstance(row, dict):
                    continue
                host_entries.append({
                    "host_ip": str(row.get("HostIp") or "").strip(),
                    "host_port": str(row.get("HostPort") or "").strip(),
                })
            if host_entries:
                rules[container_port] = host_entries
        return rules or None

    def _container_blueprint(self, container, *, name_override: str | None = None):
        container.reload()
        attrs = container.attrs or {}
        config = attrs.get("Config") or {}
        host_config = attrs.get("HostConfig") or {}
        networks = ((attrs.get("NetworkSettings") or {}).get("Networks") or {})
        mounts = attrs.get("Mounts") or []
        port_bindings = self._ports_from_container_attrs(attrs)
        restart_policy = host_config.get("RestartPolicy") or {}
        healthcheck = self._healthcheck_editor_payload(config.get("Healthcheck") or {})
        network_name = next((str(name).strip() for name in networks.keys() if str(name).strip()), "")

        return {
            "name": name_override or container.name,
            "image": str(config.get("Image") or "").strip() or (container.image.tags[0] if container.image.tags else "unknown"),
            "command": config.get("Cmd"),
            "entrypoint": config.get("Entrypoint"),
            "working_dir": str(config.get("WorkingDir") or "").strip(),
            "ports": port_bindings,
            "environment": self._parse_env(self._format_env_lines(config.get("Env") or [])) or None,
            "mounts": mounts,
            "labels": dict(config.get("Labels") or {}),
            "healthcheck": self._parse_healthcheck(healthcheck),
            "network": network_name or None,
            "restart_policy": {
                "Name": str(restart_policy.get("Name") or "no").strip() or "no",
                "MaximumRetryCount": int(restart_policy.get("MaximumRetryCount") or 0),
            },
            "stdin_open": bool(config.get("OpenStdin", True)),
            "tty": bool(config.get("Tty", False)),
            "hostname": str(config.get("Hostname") or "").strip() or None,
        }

    def _create_container_from_blueprint(self, blueprint: dict, *, start: bool = True):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        command = blueprint.get("command")
        entrypoint = blueprint.get("entrypoint")
        created = self.client.containers.create(
            image=blueprint["image"],
            name=blueprint["name"],
            command=command,
            entrypoint=entrypoint,
            working_dir=blueprint.get("working_dir") or None,
            ports=blueprint.get("ports") or None,
            environment=blueprint.get("environment") or None,
            volumes=self._parse_volumes(self._format_mount_lines(blueprint.get("mounts") or [])) or None,
            labels=blueprint.get("labels") or None,
            healthcheck=blueprint.get("healthcheck") or None,
            restart_policy=blueprint.get("restart_policy") or None,
            stdin_open=bool(blueprint.get("stdin_open", True)),
            tty=bool(blueprint.get("tty", False)),
            hostname=blueprint.get("hostname") or None,
            network=blueprint.get("network") or None,
            detach=True,
        )
        if start:
            created.start()
            created.reload()
        return created

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
        project_protocol = self._clean_setting_text(data.get("project_protocol")) or self.infer_project_protocol(profile_name, data.get("image") or "")
        parsed_volumes = self._parse_volumes(data.get("volumes")) or {}
        parsed_ports = self._parse_ports(data.get("ports"))
        env_map = self._parse_env(data.get("env")) or {}
        labels_map = self._parse_labels(data.get("labels")) or {}
        healthcheck_map = self._parse_healthcheck(data.get("healthcheck"))
        primary_container_port = self._infer_primary_container_port(parsed_ports)
        if project_protocol == "python-flask":
            env_map.setdefault("FLASK_APP", "app:app")
            env_map.setdefault("PORT", str(primary_container_port or 5000))
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
            self._prepare_managed_workspace_permissions(workspace_path)
            parsed_volumes[workspace_path] = {"bind": workspace_mount, "mode": "rw"}
            managed_workspace = True

        run_kwargs = {
            "image": data["image"],
            "name": data["name"],
            "detach": True,
            "stdin_open": True,
            "restart_policy": {"Name": "always"} if data.get("restart") else None,
            "ports": parsed_ports,
            "environment": env_map or None,
            "volumes": parsed_volumes or None,
            "labels": labels_map or None,
            "healthcheck": healthcheck_map,
            "command": self._compose_runtime_command(
                project_protocol,
                data.get("install_command") or "",
                data.get("command") or "",
                self._normalize_explorer_path(console_cwd or workspace_mount or self.DEFAULT_WORKSPACE_MOUNT_PATH),
            ),
            "working_dir": self._normalize_explorer_path(console_cwd or workspace_mount or self.DEFAULT_WORKSPACE_MOUNT_PATH),
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
                    INSERT INTO container_settings (
                        container_id, startup_command, allowed_ports, project_protocol,
                        install_command, domain_name, launch_url, updated_by, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(container_id) DO UPDATE SET
                        startup_command=excluded.startup_command,
                        allowed_ports=excluded.allowed_ports,
                        project_protocol=excluded.project_protocol,
                        install_command=excluded.install_command,
                        domain_name=excluded.domain_name,
                        launch_url=excluded.launch_url,
                        updated_by=excluded.updated_by,
                        updated_at=datetime('now')
                    """,
                    (
                        container.id,
                        self._clean_setting_text(data.get("command")),
                        self._clean_setting_text(data.get("ports")),
                        self._clean_setting_text(data.get("project_protocol")) or self.infer_project_protocol(profile_name, data.get("image") or ""),
                        self._clean_setting_text(data.get("install_command")),
                        self._clean_setting_text(data.get("domain_name")),
                        self._clean_setting_text(data.get("launch_url")),
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
            self._save_applied_runtime_settings(
                container.id,
                startup_command=data.get("command") or "",
                allowed_ports=data.get("ports") or "",
                project_protocol=project_protocol,
                install_command=data.get("install_command") or "",
                domain_name=data.get("domain_name") or "",
                launch_url=data.get("launch_url") or "",
                applied_by="system",
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

    def get_container_inspect_bundle(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.reload()
        attrs = container.attrs or {}
        config = attrs.get("Config") or {}
        state = attrs.get("State") or {}
        host_config = attrs.get("HostConfig") or {}
        mounts = attrs.get("Mounts") or []
        networks = ((attrs.get("NetworkSettings") or {}).get("Networks") or {})
        blueprint = self._container_blueprint(container)

        return {
            "name": container.name,
            "id": container.id,
            "image": str(config.get("Image") or "").strip(),
            "created": attrs.get("Created"),
            "path": config.get("Path"),
            "args": config.get("Args") or [],
            "entrypoint": config.get("Entrypoint"),
            "cmd": config.get("Cmd"),
            "working_dir": config.get("WorkingDir"),
            "labels": config.get("Labels") or {},
            "env_lines": self._format_env_lines(config.get("Env") or []),
            "mount_lines": self._format_mount_lines(mounts),
            "label_lines": self._format_label_lines(config.get("Labels") or {}),
            "healthcheck": self._healthcheck_editor_payload(config.get("Healthcheck") or {}),
            "restart_policy": {
                "name": str((host_config.get("RestartPolicy") or {}).get("Name") or "no").strip() or "no",
                "maximum_retry_count": int((host_config.get("RestartPolicy") or {}).get("MaximumRetryCount") or 0),
            },
            "networks": [
                {
                    "name": network_name,
                    "ip_address": str((payload or {}).get("IPAddress") or "").strip(),
                    "aliases": list((payload or {}).get("Aliases") or []),
                    "gateway": str((payload or {}).get("Gateway") or "").strip(),
                }
                for network_name, payload in networks.items()
            ],
            "mounts": [
                {
                    "type": str((mount or {}).get("Type") or "").strip(),
                    "source": str((mount or {}).get("Source") or "").strip(),
                    "destination": str((mount or {}).get("Destination") or "").strip(),
                    "mode": str((mount or {}).get("Mode") or "").strip(),
                    "rw": bool((mount or {}).get("RW", False)),
                    "propagation": str((mount or {}).get("Propagation") or "").strip(),
                }
                for mount in mounts
            ],
            "state": {
                "status": str(state.get("Status") or container.status or "").strip(),
                "running": bool(state.get("Running")),
                "paused": bool(state.get("Paused")),
                "restarting": bool(state.get("Restarting")),
                "oom_killed": bool(state.get("OOMKilled")),
                "exit_code": int(state.get("ExitCode") or 0),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "health": (
                    {
                        "status": str((state.get("Health") or {}).get("Status") or "").strip(),
                        "failing_streak": int((state.get("Health") or {}).get("FailingStreak") or 0),
                        "log": [
                            {
                                "start": item.get("Start"),
                                "end": item.get("End"),
                                "exit_code": int(item.get("ExitCode") or 0),
                                "output": str(item.get("Output") or "").strip(),
                            }
                            for item in ((state.get("Health") or {}).get("Log") or [])[-5:]
                        ],
                    }
                    if isinstance(state.get("Health"), dict) else None
                ),
            },
            "ports": blueprint.get("ports") or {},
            "blueprint": {
                "env": self._format_env_lines(config.get("Env") or []),
                "mounts": self._format_mount_lines(mounts),
                "labels": self._format_label_lines(config.get("Labels") or {}),
                "healthcheck": self._healthcheck_editor_payload(config.get("Healthcheck") or {}),
                "network": blueprint.get("network") or "",
            },
            "raw_json": json.dumps(attrs, ensure_ascii=True, indent=2, sort_keys=True)[:200000],
        }

    def duplicate_container(self, container_id: str, overrides: dict | None = None):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            source = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        payload = overrides if isinstance(overrides, dict) else {}
        name = str(payload.get("name") or "").strip() or f"{source.name}-copy-{int(time.time())}"
        blueprint = self._container_blueprint(source, name_override=name)
        if "env" in payload:
            blueprint["environment"] = self._parse_env(payload.get("env")) or None
        if "mounts" in payload:
            blueprint["mounts"] = [
                {"Source": host, "Destination": cfg.get("bind"), "RW": cfg.get("mode", "rw") != "ro"}
                for host, cfg in (self._parse_volumes(payload.get("mounts")) or {}).items()
            ]
        if "labels" in payload:
            blueprint["labels"] = self._parse_labels(payload.get("labels")) or {}
        if isinstance(payload.get("healthcheck"), dict):
            blueprint["healthcheck"] = self._parse_healthcheck(payload.get("healthcheck"))
        if payload.get("network"):
            blueprint["network"] = str(payload.get("network") or "").strip() or None

        created = self._create_container_from_blueprint(blueprint, start=True)
        return self.get_container_detail(created.id)

    def recreate_container(self, container_id: str, overrides: dict | None = None):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            source = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        payload = overrides if isinstance(overrides, dict) else {}
        blueprint = self._container_blueprint(source, name_override=source.name)
        if "env" in payload:
            blueprint["environment"] = self._parse_env(payload.get("env")) or None
        if "mounts" in payload:
            blueprint["mounts"] = [
                {"Source": host, "Destination": cfg.get("bind"), "RW": cfg.get("mode", "rw") != "ro"}
                for host, cfg in (self._parse_volumes(payload.get("mounts")) or {}).items()
            ]
        if "labels" in payload:
            blueprint["labels"] = self._parse_labels(payload.get("labels")) or {}
        if isinstance(payload.get("healthcheck"), dict):
            blueprint["healthcheck"] = self._parse_healthcheck(payload.get("healthcheck"))
        if payload.get("network"):
            blueprint["network"] = str(payload.get("network") or "").strip() or None

        old_id = source.id
        source.stop(timeout=15)
        source.remove(force=True)
        recreated = self._create_container_from_blueprint(blueprint, start=True)
        with get_connection(SYSTEM_DB) as conn:
            conn.execute("UPDATE container_permissions SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
            conn.execute("UPDATE container_role_permissions SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
            conn.execute("UPDATE container_settings SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
            conn.execute("UPDATE container_runtime_settings SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
            conn.execute("UPDATE container_storage SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
            conn.execute("UPDATE container_audit_log SET container_id = ? WHERE container_id = ?", (recreated.id, old_id))
        return self.get_container_detail(recreated.id)

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

    def exec_command(self, container_id: str, command: str, detached: bool = False):
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

        if detached:
            launch_log = "/tmp/nebula-startup.log"
            wrapped = (
                f"nohup /bin/sh -lc {shlex.quote(cmd)} "
                f">>{shlex.quote(launch_log)} 2>&1 < /dev/null & echo $!"
            )
            rc, output = self._exec_shell(container, wrapped)
            lines = [line for line in str(output or "").splitlines() if str(line).strip()]
            pid = lines[-1].strip() if lines else ""
            return {
                "id": container.id[:12],
                "name": container.name,
                "exit_code": rc,
                "output": output[-200000:],
                "detached": True,
                "pid": pid,
                "log_path": launch_log,
            }

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
        policy["shell_exec_profile"] = policy.get("shell_exec_profile") or "disabled"
        return policy

    def validate_user_shell_command(self, command: str, profile: str):
        raw_command = str(command or "").strip()
        if not raw_command:
            return False, "Command cannot be empty"

        policy = self.PROFILE_POLICIES.get(profile, self.PROFILE_POLICIES["generic"])
        if not policy.get("shell_allowed_for_user", True):
            return False, f"Shell access is disabled for {policy.get('label', profile)} profile"

        if any(token in raw_command for token in self.SHELL_META_TOKENS):
            return False, "Shell chaining, redirection, and substitutions are not allowed"

        try:
            parts = shlex.split(raw_command, posix=True)
        except ValueError:
            return False, "Command could not be parsed safely"
        if not parts:
            return False, "Command cannot be empty"

        for token in parts:
            if "\x00" in token or "\n" in token or "\r" in token:
                return False, "Command contains unsupported control characters"
        if len(parts) > 1 and "=" in parts[0] and not parts[0].startswith(("/", "./", "../")):
            return False, "Environment prefix assignments are not allowed"

        raw_executable = parts[0]
        if "/" in raw_executable and raw_executable != "./manage.py":
            return False, "Executable path overrides are not allowed"

        executable = posixpath.basename(raw_executable)
        if not executable:
            return False, "Executable is required"

        exec_profile_name = str(policy.get("shell_exec_profile") or "disabled").strip() or "disabled"
        exec_profile = self.EXEC_PROFILES.get(exec_profile_name, self.EXEC_PROFILES["disabled"])
        allowed_commands = set(exec_profile.get("allowed_commands") or ())
        if executable not in allowed_commands:
            return False, f"Command '{executable}' is not allowed by exec profile '{exec_profile_name}'"
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

    def _prune_pty_sessions(self):
        now = time.time()
        stale_ids = []
        with self._pty_sessions_lock:
            for session_id, state in self._pty_sessions.items():
                if state.get("active"):
                    continue
                closed_at = float(state.get("closed_at") or 0.0)
                if closed_at and (now - closed_at) > self._pty_session_ttl:
                    stale_ids.append(session_id)
            for session_id in stale_ids:
                self._pty_sessions.pop(session_id, None)

    def _append_pty_output_locked(self, state: dict, chunk):
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        if not text:
            return
        state["buffer"] = f"{state.get('buffer', '')}{text}"
        overflow = len(state["buffer"]) - self._pty_session_buffer_limit
        if overflow > 0:
            state["buffer"] = state["buffer"][overflow:]
            state["buffer_start"] = int(state.get("buffer_start", 0)) + overflow
        state["updated_at"] = time.time()

    def _close_pty_socket(self, state: dict):
        sock = state.get("socket")
        if not sock:
            return
        state["socket"] = None
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def _finalize_pty_session(self, session_id: str, reason: str | None = None):
        exec_id = None
        with self._pty_sessions_lock:
            state = self._pty_sessions.get(session_id)
            if not state:
                return
            already_closed = bool(state.get("closed"))
            state["active"] = False
            state["closed"] = True
            state["closed_at"] = time.time()
            if reason and not state.get("close_reason"):
                state["close_reason"] = str(reason)
            exec_id = state.get("exec_id")
            self._close_pty_socket(state)
        if already_closed:
            return
        exit_code = None
        if exec_id and self.client is not None:
            try:
                details = self.client.api.exec_inspect(exec_id)
                exit_code = details.get("ExitCode")
            except Exception:
                exit_code = None
        with self._pty_sessions_lock:
            state = self._pty_sessions.get(session_id)
            if state is not None:
                state["exit_code"] = exit_code
                state["updated_at"] = time.time()

    def _pty_reader_loop(self, session_id: str):
        while True:
            with self._pty_sessions_lock:
                state = self._pty_sessions.get(session_id)
                if not state or not state.get("active") or state.get("closed"):
                    break
                sock = state.get("socket")
            if sock is None:
                break
            try:
                readable, _, _ = select.select([sock], [], [], 0.35)
            except Exception:
                break
            if not readable:
                continue
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except BlockingIOError:
                continue
            except Exception as exc:
                self._finalize_pty_session(session_id, str(exc))
                return
            if not chunk:
                break
            with self._pty_sessions_lock:
                state = self._pty_sessions.get(session_id)
                if not state:
                    break
                self._append_pty_output_locked(state, chunk)
        self._finalize_pty_session(session_id)

    def _pty_session_snapshot(self, session_id: str):
        with self._pty_sessions_lock:
            state = self._pty_sessions.get(session_id)
            if not state:
                return None
            return {
                "session_id": state.get("session_id"),
                "container_id": state.get("container_id"),
                "container_name": state.get("container_name"),
                "shell": state.get("shell"),
                "cols": state.get("cols"),
                "rows": state.get("rows"),
                "active": bool(state.get("active")),
                "closed": bool(state.get("closed")),
                "close_reason": state.get("close_reason"),
                "exit_code": state.get("exit_code"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
            }

    def _require_pty_session(self, session_id: str):
        self._prune_pty_sessions()
        with self._pty_sessions_lock:
            state = self._pty_sessions.get(session_id)
            if not state:
                raise RuntimeError("Shell session not found or expired")
            return state

    def _interactive_shell_candidates(self):
        return (
            ("/bin/bash", ["-il"]),
            ("/bin/sh", ["-i"]),
            ("/bin/ash", ["-i"]),
        )

    def start_shell_session(self, container_id: str, cols: int = 120, rows: int = 32):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        try:
            container.reload()
            status = str(container.status or "").lower()
            if status != "running":
                raise RuntimeError(f"Container is not running (current status: {status or 'unknown'})")
        except RuntimeError:
            raise
        except Exception:
            pass

        runtime = self._container_runtime_context(container.id)
        raw_console_cwd = str(runtime.get("console_cwd") or "").strip()
        console_cwd = self._normalize_explorer_path(raw_console_cwd) if raw_console_cwd else None
        width = max(40, min(int(cols or 120), 240))
        height = max(12, min(int(rows or 32), 80))
        errors = []
        for shell_path, shell_args in self._interactive_shell_candidates():
            try:
                exec_data = self.client.api.exec_create(
                    container.id,
                    cmd=[shell_path, *shell_args],
                    stdout=True,
                    stderr=True,
                    stdin=True,
                    tty=True,
                    environment={"TERM": "xterm-256color", "COLUMNS": str(width), "LINES": str(height)},
                    workdir=console_cwd,
                )
                exec_id = exec_data.get("Id")
                if not exec_id:
                    raise RuntimeError("Docker exec session id was not returned")
                sock = self.client.api.exec_start(exec_id, tty=True, socket=True)
                try:
                    sock.settimeout(0.35)
                except Exception:
                    pass
                try:
                    self.client.api.exec_resize(exec_id, height=height, width=width)
                except Exception:
                    pass
                session_id = uuid.uuid4().hex
                state = {
                    "session_id": session_id,
                    "container_id": container.id,
                    "container_name": container.name,
                    "exec_id": exec_id,
                    "socket": sock,
                    "shell": shell_path,
                    "cols": width,
                    "rows": height,
                    "active": True,
                    "closed": False,
                    "close_reason": None,
                    "exit_code": None,
                    "buffer": "",
                    "buffer_start": 0,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "closed_at": None,
                }
                with self._pty_sessions_lock:
                    self._pty_sessions[session_id] = state
                reader = threading.Thread(
                    target=self._pty_reader_loop,
                    args=(session_id,),
                    name=f"nebula-pty-{session_id[:8]}",
                    daemon=True,
                )
                with self._pty_sessions_lock:
                    current = self._pty_sessions.get(session_id)
                    if current is not None:
                        current["reader"] = reader
                reader.start()
                self._prune_pty_sessions()
                return self._pty_session_snapshot(session_id)
            except Exception as exc:
                errors.append(f"{shell_path}: {exc}")
                continue
        detail = errors[-1] if errors else "unknown runtime error"
        raise RuntimeError(f"Interactive shell is not available in this container: {detail}")

    def read_shell_session(self, session_id: str, cursor: int = 0):
        self._require_pty_session(session_id)
        requested_cursor = max(int(cursor or 0), 0)
        with self._pty_sessions_lock:
            state = self._pty_sessions.get(session_id)
            if not state:
                raise RuntimeError("Shell session not found or expired")
            buffer_start = int(state.get("buffer_start", 0))
            buffer_text = str(state.get("buffer") or "")
            if requested_cursor <= buffer_start:
                output = buffer_text
                clipped = requested_cursor < buffer_start
            else:
                output = buffer_text[requested_cursor - buffer_start:]
                clipped = False
            next_cursor = buffer_start + len(buffer_text)
            return {
                "session_id": state.get("session_id"),
                "container_id": state.get("container_id"),
                "container_name": state.get("container_name"),
                "cursor": next_cursor,
                "output": output,
                "clipped": clipped,
                "active": bool(state.get("active")),
                "closed": bool(state.get("closed")),
                "close_reason": state.get("close_reason"),
                "exit_code": state.get("exit_code"),
                "shell": state.get("shell"),
                "updated_at": state.get("updated_at"),
            }

    def write_shell_session(self, session_id: str, data: str):
        state = self._require_pty_session(session_id)
        if state.get("closed") or not state.get("active"):
            raise RuntimeError("Shell session is already closed")
        payload = str(data or "")
        if not payload:
            raise RuntimeError("Shell input cannot be empty")
        sock = state.get("socket")
        if sock is None:
            raise RuntimeError("Shell session socket is unavailable")
        try:
            sock.sendall(payload.encode("utf-8", errors="replace"))
        except Exception as exc:
            self._finalize_pty_session(session_id, str(exc))
            raise RuntimeError(f"Failed to write to shell session: {exc}")
        with self._pty_sessions_lock:
            current = self._pty_sessions.get(session_id)
            if current is not None:
                current["updated_at"] = time.time()
        return self._pty_session_snapshot(session_id)

    def resize_shell_session(self, session_id: str, cols: int = 120, rows: int = 32):
        state = self._require_pty_session(session_id)
        exec_id = state.get("exec_id")
        width = max(40, min(int(cols or 120), 240))
        height = max(12, min(int(rows or 32), 80))
        if not exec_id:
            raise RuntimeError("Shell session is missing exec metadata")
        self.client.api.exec_resize(exec_id, height=height, width=width)
        with self._pty_sessions_lock:
            current = self._pty_sessions.get(session_id)
            if current is not None:
                current["cols"] = width
                current["rows"] = height
                current["updated_at"] = time.time()
        return self._pty_session_snapshot(session_id)

    def close_shell_session(self, session_id: str):
        self._require_pty_session(session_id)
        self._finalize_pty_session(session_id, "closed_by_client")
        return self._pty_session_snapshot(session_id)

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
        host_binding = self._workspace_host_target(container.id, target)
        if host_binding:
            self._assert_host_workspace_target(
                os.path.realpath(host_binding["workspace_path"]),
                host_binding["host_path"],
            )
            return self._list_host_workspace_entries(container, target, host_binding["host_path"])
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
        workspace_path = os.path.abspath(str(runtime.get("workspace_path") or "").strip()) if runtime.get("workspace_path") else ""
        if workspace_path and os.path.isdir(workspace_path):
            roots = []
            for p in (preferred_override, *candidates):
                if p and p not in roots:
                    roots.append(p)
            preferred = self._normalize_explorer_path(preferred_override or runtime.get("workspace_mount") or candidates[0])
            return {
                "id": container.id[:12],
                "profile": profile,
                "base_profile": base_profile,
                "preferred_path": preferred,
                "roots": roots,
                "source": "host-workspace",
            }
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
        host_binding = self._workspace_host_target(container.id, target)
        if host_binding:
            self._assert_host_workspace_target(
                os.path.realpath(host_binding["workspace_path"]),
                host_binding["host_path"],
            )
            return self._read_host_workspace_file(container, target, host_binding["host_path"], max_bytes=max_bytes)

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

    def write_file(self, container_id: str, path: str, content: str, max_bytes: int = 500000):
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
        host_binding = self._workspace_host_target(container.id, target)
        if host_binding:
            self._assert_host_workspace_target(
                os.path.realpath(host_binding["workspace_path"]),
                host_binding["host_path"],
                allow_missing_leaf=not os.path.lexists(host_binding["host_path"]),
            )
            return self._write_host_workspace_file(container, target, host_binding["host_path"], content=content, max_bytes=max_bytes)

        payload = (content or "").encode("utf-8")
        limit = max(1024, min(int(max_bytes), 1000000))
        if len(payload) > limit:
            raise RuntimeError(f"File content exceeds write limit ({limit} bytes)")

        parent = posixpath.dirname(target) or "/"
        file_name = posixpath.basename(target)
        if not file_name or file_name in (".", ".."):
            raise RuntimeError("Target file path is invalid")

        parent_q = shlex.quote(parent)
        rc, output = self._exec_shell(
            container,
            f"if [ -d {parent_q} ] && [ -w {parent_q} ]; then :; else echo '__NEBULA_DIR_NOT_WRITABLE__'; exit 47; fi"
        )
        if rc == 47 or "__NEBULA_DIR_NOT_WRITABLE__" in output:
            raise RuntimeError("Target directory is not writable in this container")

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo(name=file_name)
            info.size = len(payload)
            info.mtime = int(time.time())
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
        stream.seek(0)

        ok = container.put_archive(parent, stream.getvalue())
        if not ok:
            raise RuntimeError("Failed to write file content to container filesystem")

        return {
            "id": container.id[:12],
            "path": target,
            "bytes": len(payload),
        }

    def create_directory(self, container_id: str, path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        target = self._normalize_explorer_path(path)
        if not target or target == "/":
            raise RuntimeError("Target directory path is invalid")
        if not self._is_allowed_explorer_path(target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        binding = self._workspace_host_target(full_id, target)
        if not binding:
            raise RuntimeError("Directory creation is available only for host-mounted workspaces")
        host_target = binding["host_path"]
        workspace_root = os.path.realpath(binding["workspace_path"])
        self._assert_host_workspace_target(workspace_root, host_target, allow_missing_leaf=True)
        if os.path.exists(host_target):
            raise RuntimeError("Target path already exists in workspace")
        parent = os.path.dirname(host_target)
        if not os.path.isdir(parent):
            raise RuntimeError("Parent directory does not exist in workspace")
        if not os.access(parent, os.W_OK):
            raise RuntimeError("Parent directory is not writable in workspace")
        os.makedirs(host_target, exist_ok=False)
        self._prepare_managed_workspace_permissions(host_target)
        return {
            "id": container.id[:12],
            "path": target,
            "created": True,
            "source": "host-workspace",
        }

    def delete_path(self, container_id: str, path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        target = self._normalize_explorer_path(path)
        if not target or target == "/":
            raise RuntimeError("Target path is invalid")
        if not self._is_allowed_explorer_path(target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        binding = self._workspace_host_target(full_id, target)
        if not binding:
            raise RuntimeError("Delete is available only for host-mounted workspaces")
        host_target = binding["host_path"]
        workspace_root = os.path.realpath(binding["workspace_path"])
        self._assert_host_workspace_target(workspace_root, host_target, follow_leaf=False)
        if host_target == workspace_root:
            raise RuntimeError("Workspace root cannot be deleted from this panel")
        if not os.path.lexists(host_target):
            raise RuntimeError("Target path does not exist in workspace")
        parent = os.path.dirname(host_target)
        if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
            raise RuntimeError("Parent directory is not writable in workspace")
        if os.path.islink(host_target) or os.path.isfile(host_target):
            os.unlink(host_target)
        elif os.path.isdir(host_target):
            shutil.rmtree(host_target)
        else:
            raise RuntimeError("Unsupported path type for delete")
        return {
            "id": container.id[:12],
            "path": target,
            "deleted": True,
            "source": "host-workspace",
        }

    def move_path(self, container_id: str, source_path: str, destination_path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        source = self._normalize_explorer_path(source_path)
        destination = self._normalize_explorer_path(destination_path)
        if not source or not destination or source == "/" or destination == "/":
            raise RuntimeError("Source or destination path is invalid")
        if not self._is_allowed_explorer_path(source, allowed_roots) or not self._is_allowed_explorer_path(destination, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        source_binding = self._workspace_host_target(full_id, source)
        destination_binding = self._workspace_host_target(full_id, destination)
        if not source_binding or not destination_binding:
            raise RuntimeError("Move is available only for host-mounted workspaces")
        source_host = source_binding["host_path"]
        destination_host = destination_binding["host_path"]
        workspace_root = os.path.realpath(source_binding["workspace_path"])
        self._assert_host_workspace_target(workspace_root, source_host, follow_leaf=False)
        self._assert_host_workspace_target(workspace_root, destination_host, allow_missing_leaf=True)
        if source_host == workspace_root or destination_host == workspace_root:
            raise RuntimeError("Workspace root cannot be moved or replaced from this panel")
        if not os.path.lexists(source_host):
            raise RuntimeError("Source path does not exist in workspace")
        if os.path.lexists(destination_host):
            raise RuntimeError("Destination path already exists in workspace")
        destination_parent = os.path.dirname(destination_host)
        if not os.path.isdir(destination_parent):
            raise RuntimeError("Destination directory does not exist in workspace")
        if not os.access(destination_parent, os.W_OK):
            raise RuntimeError("Destination directory is not writable in workspace")
        shutil.move(source_host, destination_host)
        return {
            "id": container.id[:12],
            "source_path": source,
            "destination_path": destination,
            "moved": True,
            "source": "host-workspace",
        }

    def copy_path(self, container_id: str, source_path: str, destination_path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        source = self._normalize_explorer_path(source_path)
        destination = self._normalize_explorer_path(destination_path)
        if not source or not destination or source == "/" or destination == "/":
            raise RuntimeError("Source or destination path is invalid")
        if not self._is_allowed_explorer_path(source, allowed_roots) or not self._is_allowed_explorer_path(destination, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        source_binding = self._workspace_host_target(full_id, source)
        destination_binding = self._workspace_host_target(full_id, destination)
        if not source_binding or not destination_binding:
            raise RuntimeError("Copy is available only for host-mounted workspaces")
        source_host = source_binding["host_path"]
        destination_host = destination_binding["host_path"]
        workspace_root = os.path.realpath(source_binding["workspace_path"])
        self._assert_host_workspace_target(workspace_root, source_host, follow_leaf=False)
        self._assert_host_workspace_target(workspace_root, destination_host, allow_missing_leaf=True)
        if not os.path.lexists(source_host):
            raise RuntimeError("Source path does not exist in workspace")
        if os.path.lexists(destination_host):
            raise RuntimeError("Destination path already exists in workspace")
        destination_parent = os.path.dirname(destination_host)
        if not os.path.isdir(destination_parent):
            raise RuntimeError("Destination directory does not exist in workspace")
        if not os.access(destination_parent, os.W_OK):
            raise RuntimeError("Destination directory is not writable in workspace")
        if os.path.isdir(source_host) and not os.path.islink(source_host):
            shutil.copytree(source_host, destination_host, symlinks=True)
        else:
            shutil.copy2(source_host, destination_host, follow_symlinks=False)
        return {
            "id": container.id[:12],
            "source_path": source,
            "destination_path": destination,
            "copied": True,
            "source": "host-workspace",
        }

    def archive_paths(self, container_id: str, source_paths: list[str], destination_path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")
        sources = [self._normalize_explorer_path(item) for item in (source_paths or []) if str(item or "").strip()]
        destination = self._normalize_explorer_path(destination_path)
        if not sources:
            raise RuntimeError("No source paths were provided")
        allowed_roots = self._workspace_roots_for_container(container)
        if any(not self._is_allowed_explorer_path(item, allowed_roots) for item in sources) or not self._is_allowed_explorer_path(destination, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        destination_binding = self._workspace_host_target(full_id, destination)
        if not destination_binding:
            raise RuntimeError("Archive creation is available only for host-mounted workspaces")
        workspace_root = os.path.realpath(destination_binding["workspace_path"])
        destination_host = destination_binding["host_path"]
        self._assert_host_workspace_target(workspace_root, destination_host, allow_missing_leaf=True)
        if os.path.lexists(destination_host):
            raise RuntimeError("Archive destination already exists in workspace")
        destination_parent = os.path.dirname(destination_host)
        if not os.path.isdir(destination_parent):
            raise RuntimeError("Archive destination directory does not exist in workspace")
        if not os.access(destination_parent, os.W_OK):
            raise RuntimeError("Archive destination directory is not writable in workspace")
        resolved_sources = []
        for item in sources:
            binding = self._workspace_host_target(full_id, item)
            if not binding:
                raise RuntimeError("Archive creation is available only for host-mounted workspaces")
            host_path = binding["host_path"]
            self._assert_host_workspace_target(workspace_root, host_path, follow_leaf=False)
            if not os.path.lexists(host_path):
                raise RuntimeError(f"Source path does not exist in workspace: {item}")
            resolved_sources.append((item, host_path))
        with zipfile.ZipFile(destination_host, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source_path, host_path in resolved_sources:
                arcname_base = posixpath.basename(source_path.rstrip("/")) or "item"
                if os.path.isdir(host_path) and not os.path.islink(host_path):
                    for root, dirs, files in os.walk(host_path):
                        rel_root = os.path.relpath(root, host_path)
                        rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")
                        if not files and not dirs:
                            archive.writestr(posixpath.join(arcname_base, rel_root, ""), "")
                        for file_name in files:
                            abs_file = os.path.join(root, file_name)
                            rel_file = posixpath.join(arcname_base, rel_root, file_name) if rel_root else posixpath.join(arcname_base, file_name)
                            archive.write(abs_file, rel_file)
                else:
                    archive.write(host_path, arcname_base)
        return {
            "id": container.id[:12],
            "destination_path": destination,
            "sources": sources,
            "archived": True,
            "source": "host-workspace",
        }

    def extract_archive(self, container_id: str, archive_path: str, destination_path: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        full_id = self.resolve_container_id(container_id)
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")
        archive_target = self._normalize_explorer_path(archive_path)
        destination = self._normalize_explorer_path(destination_path)
        allowed_roots = self._workspace_roots_for_container(container)
        if not self._is_allowed_explorer_path(archive_target, allowed_roots) or not self._is_allowed_explorer_path(destination, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        archive_binding = self._workspace_host_target(full_id, archive_target)
        destination_binding = self._workspace_host_target(full_id, destination)
        if not archive_binding or not destination_binding:
            raise RuntimeError("Archive extraction is available only for host-mounted workspaces")
        workspace_root = os.path.realpath(destination_binding["workspace_path"])
        archive_host = archive_binding["host_path"]
        destination_host = destination_binding["host_path"]
        self._assert_host_workspace_target(workspace_root, archive_host)
        self._assert_host_workspace_target(workspace_root, destination_host)
        if not os.path.isfile(archive_host):
            raise RuntimeError("Archive file does not exist in workspace")
        if not os.path.isdir(destination_host):
            raise RuntimeError("Archive destination directory does not exist in workspace")
        if not os.access(destination_host, os.W_OK):
            raise RuntimeError("Archive destination directory is not writable in workspace")
        with zipfile.ZipFile(archive_host, "r") as archive:
            for member in archive.infolist():
                member_name = member.filename.replace("\\", "/")
                if not member_name or member_name.startswith("/") or ".." in [part for part in member_name.split("/") if part]:
                    raise RuntimeError("Archive contains unsafe paths and cannot be extracted")
                target_host = os.path.abspath(os.path.join(destination_host, member_name.replace("/", os.sep)))
                self._assert_host_workspace_target(workspace_root, target_host, allow_missing_leaf=True)
            archive.extractall(destination_host)
        return {
            "id": container.id[:12],
            "archive_path": archive_target,
            "destination_path": destination,
            "extracted": True,
            "source": "host-workspace",
        }

    def get_container_sftp_info(self, container_id: str):
        full_id = self.resolve_container_id(container_id)
        detail = self.get_container_detail(full_id)
        runtime = self._container_runtime_context(full_id)
        workspace_path = os.path.abspath(str(runtime.get("workspace_path") or "").strip()) if runtime.get("workspace_path") else ""
        workspace_mount = self._normalize_explorer_path(runtime.get("workspace_mount") or self.DEFAULT_WORKSPACE_MOUNT_PATH)
        explorer_root = self._normalize_explorer_path(runtime.get("explorer_root") or workspace_mount)
        host = str(os.getenv("NEBULA_SFTP_HOST") or socket.gethostname() or "localhost").strip()
        port = int(str(os.getenv("NEBULA_SFTP_PORT") or "22").strip() or "22")
        username = str(os.getenv("NEBULA_SFTP_USERNAME") or getpass.getuser() or "").strip() or "user"
        sshd_detected = bool(shutil.which("sshd") or os.path.exists("/usr/sbin/sshd"))
        available = bool(workspace_path and os.path.isdir(workspace_path))
        panel_group, panel_gid = self._resolve_panel_group_gid()
        writable = bool(workspace_path and os.path.isdir(workspace_path) and os.access(workspace_path, os.W_OK))
        owner_name = ""
        group_name = ""
        mode_octal = ""
        if available:
            try:
                stat_info = os.stat(workspace_path)
                mode_octal = oct(stat_info.st_mode & 0o7777)
                try:
                    import pwd
                    owner_name = pwd.getpwuid(stat_info.st_uid).pw_name
                except Exception:
                    owner_name = str(stat_info.st_uid)
                try:
                    group_name = grp.getgrgid(stat_info.st_gid).gr_name
                except Exception:
                    group_name = str(stat_info.st_gid)
            except OSError:
                pass
        return {
            "container_id": full_id,
            "container_name": detail.get("name") or full_id[:12],
            "available": available,
            "writable": writable,
            "host": host,
            "port": port,
            "username": username,
            "workspace_path": workspace_path,
            "workspace_mount": workspace_mount,
            "explorer_root": explorer_root,
            "remote_path_hint": workspace_path,
            "command": f"sftp -P {port} {username}@{host}",
            "sshd_detected": sshd_detected,
            "owner": owner_name,
            "group": group_name,
            "mode": mode_octal,
            "panel_group": panel_group,
            "panel_group_found": panel_gid is not None,
            "note": (
                "Host workspace is ready for SFTP/SSH-based tools."
                if sshd_detected
                else "Workspace path is ready, but an SSH/SFTP server was not detected automatically on this host."
            ),
        }

    def resolve_workspace_host_directory(self, container_id: str, target: str):
        full_id = self.resolve_container_id(container_id)
        normalized_target = self._normalize_explorer_path(target)
        detail = self.get_container_detail(full_id)
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        if not self._is_allowed_explorer_path(normalized_target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        binding = self._workspace_host_target(full_id, normalized_target)
        if not binding:
            raise RuntimeError("Workspace upload is available only for host-mounted workspaces")
        host_path = binding["host_path"]
        managed_root = os.path.realpath(self.WORKSPACES_BASE_DIR)
        workspace_root = os.path.realpath(binding["workspace_path"])
        self._assert_host_workspace_target(workspace_root, host_path)
        if workspace_root == managed_root or workspace_root.startswith(managed_root + os.sep):
            self._prepare_managed_workspace_permissions(workspace_root)
        if os.path.exists(host_path) and not os.path.isdir(host_path):
            raise RuntimeError("Upload target must be a directory")
        if not os.path.exists(host_path):
            raise RuntimeError("Upload target directory does not exist")
        if not os.access(host_path, os.W_OK):
            raise RuntimeError("Upload target directory is not writable")
        return {
            "container_id": full_id,
            "container_name": detail.get("name") or full_id[:12],
            "target": normalized_target,
            "host_path": host_path,
            "workspace_path": binding["workspace_path"],
            "workspace_mount": binding["workspace_mount"],
            "explorer_root": binding["explorer_root"],
        }

    def resolve_workspace_host_path(self, container_id: str, target: str):
        full_id = self.resolve_container_id(container_id)
        normalized_target = self._normalize_explorer_path(target)
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(full_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        allowed_roots = self._workspace_roots_for_container(container)
        if not self._is_allowed_explorer_path(normalized_target, allowed_roots):
            raise RuntimeError("Access outside workspace paths is blocked")
        binding = self._workspace_host_target(full_id, normalized_target)
        if not binding:
            raise RuntimeError("Requested path is not available through host workspace bridge")
        self._assert_host_workspace_target(os.path.realpath(binding["workspace_path"]), binding["host_path"])
        return {
            "container_id": full_id,
            "target": normalized_target,
            "host_path": binding["host_path"],
            "workspace_path": binding["workspace_path"],
            "workspace_mount": binding["workspace_mount"],
            "explorer_root": binding["explorer_root"],
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
                SELECT workspace_path, explorer_root, console_cwd, profile_name, workspace_mount
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

    def _workspace_host_target(self, full_id: str, target: str):
        runtime = self._container_runtime_context(full_id)
        workspace_path = os.path.abspath(str(runtime.get("workspace_path") or "").strip()) if runtime.get("workspace_path") else ""
        if not workspace_path:
            return None
        workspace_mount = self._normalize_explorer_path(runtime.get("workspace_mount") or self.DEFAULT_WORKSPACE_MOUNT_PATH)
        explorer_root = self._normalize_explorer_path(runtime.get("explorer_root") or workspace_mount)
        normalized_target = self._normalize_explorer_path(target)

        base_root = None
        for candidate in (workspace_mount, explorer_root):
            if normalized_target == candidate or normalized_target.startswith(candidate + "/"):
                base_root = candidate
                break
        if not base_root:
            return None

        relative = posixpath.relpath(normalized_target, base_root)
        relative = "" if relative == "." else relative
        host_target = os.path.abspath(os.path.join(workspace_path, relative.replace("/", os.sep)))
        if host_target != workspace_path and not host_target.startswith(workspace_path + os.sep):
            raise RuntimeError("Resolved workspace path escapes allowed host workspace")
        return {
            "host_path": host_target,
            "workspace_path": workspace_path,
            "workspace_mount": workspace_mount,
            "explorer_root": explorer_root,
        }

    @staticmethod
    def _host_entry_type(path: str) -> str:
        if os.path.islink(path):
            return "link"
        if os.path.isdir(path):
            return "dir"
        return "file"

    def _list_host_workspace_entries(self, container, target: str, host_target: str):
        if not os.path.exists(host_target):
            raise RuntimeError("Path not found")
        entries = []
        if os.path.isdir(host_target):
            with os.scandir(host_target) as iterator:
                for item in iterator:
                    try:
                        stat_info = item.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    entries.append({
                        "name": item.name,
                        "type": self._host_entry_type(item.path),
                        "size": str(stat_info.st_size),
                        "owner": str(stat_info.st_uid),
                        "group": str(stat_info.st_gid),
                        "modified": time.strftime("%b %d %H:%M", time.localtime(stat_info.st_mtime)),
                        "perms": oct(stat_info.st_mode & 0o777),
                    })
        else:
            stat_info = os.lstat(host_target)
            entries.append({
                "name": os.path.basename(host_target),
                "type": self._host_entry_type(host_target),
                "size": str(stat_info.st_size),
                "owner": str(stat_info.st_uid),
                "group": str(stat_info.st_gid),
                "modified": time.strftime("%b %d %H:%M", time.localtime(stat_info.st_mtime)),
                "perms": oct(stat_info.st_mode & 0o777),
            })
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
        return {
            "id": container.id[:12],
            "path": target,
            "entries": entries,
            "source": "host-workspace",
        }

    def _read_host_workspace_file(self, container, target: str, host_target: str, max_bytes: int = 200000):
        if os.path.islink(host_target):
            raise RuntimeError("Symlinked files cannot be read through host workspace bridge")
        if not os.path.isfile(host_target):
            raise RuntimeError("Target is not a readable file")
        limit = max(1024, min(int(max_bytes), 500000))
        with open(host_target, "rb") as f:
            payload = f.read(limit + 1)
        return {
            "id": container.id[:12],
            "path": target,
            "content": payload[:limit].decode("utf-8", errors="replace"),
            "truncated": len(payload) > limit,
            "source": "host-workspace",
        }

    def _write_host_workspace_file(self, container, target: str, host_target: str, content: str, max_bytes: int = 500000):
        payload = (content or "").encode("utf-8")
        limit = max(1024, min(int(max_bytes), 1000000))
        if len(payload) > limit:
            raise RuntimeError(f"File content exceeds write limit ({limit} bytes)")
        if os.path.lexists(host_target) and os.path.islink(host_target):
            raise RuntimeError("Symlinked files cannot be modified through host workspace bridge")
        parent = os.path.dirname(host_target)
        if not os.path.isdir(parent):
            raise RuntimeError("Target directory does not exist in workspace")
        if not os.access(parent, os.W_OK):
            raise RuntimeError("Target directory is not writable in workspace")
        with open(host_target, "wb") as f:
            f.write(payload)
        return {
            "id": container.id[:12],
            "path": target,
            "bytes_written": len(payload),
            "source": "host-workspace",
        }

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
        detail = None
        image_name = ""
        try:
            detail = self.get_container_detail(full_id)
            image_name = detail.get("image") or ""
        except Exception:
            image_name = ""
        runtime = self._container_runtime_context(full_id)
        inferred_protocol = self.infer_project_protocol(runtime.get("profile_name") or "", image_name)
        with get_connection(SYSTEM_DB) as conn:
            saved_row = conn.execute(
                """
                SELECT startup_command, allowed_ports, project_protocol, install_command,
                       domain_name, launch_url, updated_by, updated_at
                FROM container_settings
                WHERE container_id = ?
                """,
                (full_id,),
            ).fetchone()
            applied_row = conn.execute(
                """
                SELECT applied_startup_command, applied_allowed_ports, applied_project_protocol,
                       applied_install_command, applied_domain_name, applied_launch_url,
                       applied_by, applied_at
                FROM container_runtime_settings
                WHERE container_id = ?
                """,
                (full_id,),
            ).fetchone()
        available_ports = self._container_available_ports(full_id)
        saved = dict(saved_row) if saved_row else {}
        applied = dict(applied_row) if applied_row else {}
        saved_config = {
            "startup_command": saved.get("startup_command") or "",
            "allowed_ports": saved.get("allowed_ports") or "",
            "project_protocol": saved.get("project_protocol") or inferred_protocol,
            "install_command": saved.get("install_command") or "",
            "domain_name": saved.get("domain_name") or "",
            "launch_url": saved.get("launch_url") or "",
            "updated_by": saved.get("updated_by"),
            "updated_at": saved.get("updated_at"),
        }
        applied_runtime_config = {
            "startup_command": applied.get("applied_startup_command") or "",
            "allowed_ports": applied.get("applied_allowed_ports") or "",
            "project_protocol": applied.get("applied_project_protocol") or inferred_protocol,
            "install_command": applied.get("applied_install_command") or "",
            "domain_name": applied.get("applied_domain_name") or "",
            "launch_url": applied.get("applied_launch_url") or "",
            "applied_by": applied.get("applied_by"),
            "applied_at": applied.get("applied_at"),
        }
        data = dict(saved_config)
        data["available_ports"] = available_ports
        data["saved_config"] = saved_config
        data["applied_runtime_config"] = applied_runtime_config
        return data

    def update_container_settings(
        self,
        container_id: str,
        startup_command: str,
        allowed_ports: str,
        updated_by: str,
        project_protocol: str = "",
        install_command: str = "",
        domain_name: str = "",
        launch_url: str = "",
    ):
        full_id = self.resolve_container_id(container_id)
        detail = None
        image_name = ""
        try:
            detail = self.get_container_detail(full_id)
            image_name = detail.get("image") or ""
        except Exception:
            image_name = ""
        runtime = self._container_runtime_context(full_id)
        normalized_protocol = self._clean_setting_text(project_protocol) or self.infer_project_protocol(runtime.get("profile_name") or "", image_name)
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO container_settings (
                    container_id, startup_command, allowed_ports, project_protocol,
                    install_command, domain_name, launch_url, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(container_id) DO UPDATE SET
                    startup_command=excluded.startup_command,
                    allowed_ports=excluded.allowed_ports,
                    project_protocol=excluded.project_protocol,
                    install_command=excluded.install_command,
                    domain_name=excluded.domain_name,
                    launch_url=excluded.launch_url,
                    updated_by=excluded.updated_by,
                    updated_at=datetime('now')
                """,
                (
                    full_id,
                    self._clean_setting_text(startup_command),
                    self._clean_setting_text(allowed_ports),
                    normalized_protocol,
                    self._clean_setting_text(install_command),
                    self._clean_setting_text(domain_name),
                    self._clean_setting_text(launch_url),
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
                "DELETE FROM container_runtime_settings WHERE container_id = ?",
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
            conn.execute(
                "DELETE FROM container_audit_log WHERE container_id = ?",
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
