"""Security, RBAC extensions, and audit helpers for Nebula Core."""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from typing import Any

from fastapi import Request

from ..db import SYSTEM_DB, get_connection, list_client_databases, resolve_client_db_path


class SecurityService:
    _SCHEMA_LOCK = threading.Lock()
    _SCHEMA_READY = False

    DEFAULT_PERMISSIONS = [
        {
            "key": "users.read",
            "label": "Read users",
            "category": "identity",
            "description": "View user profiles, directories, and identity metadata.",
            "risk_level": "low",
        },
        {
            "key": "users.write",
            "label": "Manage users",
            "category": "identity",
            "description": "Create, edit, disable, and remove users.",
            "risk_level": "high",
        },
        {
            "key": "roles.read",
            "label": "Read roles",
            "category": "rbac",
            "description": "Inspect role catalog and access design.",
            "risk_level": "low",
        },
        {
            "key": "roles.write",
            "label": "Manage roles",
            "category": "rbac",
            "description": "Create roles and update mapped permissions.",
            "risk_level": "high",
        },
        {
            "key": "permissions.write",
            "label": "Manage permissions",
            "category": "rbac",
            "description": "Create and modify reusable permission definitions.",
            "risk_level": "critical",
        },
        {
            "key": "groups.read",
            "label": "Read groups",
            "category": "groups",
            "description": "Inspect user groups and container access bundles.",
            "risk_level": "low",
        },
        {
            "key": "groups.write",
            "label": "Manage groups",
            "category": "groups",
            "description": "Create groups, manage members, and bind container access.",
            "risk_level": "high",
        },
        {
            "key": "containers.access.read",
            "label": "Read container access",
            "category": "containers",
            "description": "View container access assignments and policies.",
            "risk_level": "medium",
        },
        {
            "key": "containers.access.write",
            "label": "Manage container access",
            "category": "containers",
            "description": "Change user/group access and role policy matrices for containers.",
            "risk_level": "critical",
        },
        {
            "key": "audit.read",
            "label": "Read audit log",
            "category": "audit",
            "description": "View security audit history and user actions.",
            "risk_level": "medium",
        },
        {
            "key": "audit.export",
            "label": "Export audit log",
            "category": "audit",
            "description": "Export audit events and connection history to CSV.",
            "risk_level": "high",
        },
        {
            "key": "connections.read",
            "label": "Read connections",
            "category": "audit",
            "description": "Inspect service/database connection activity and network anomalies.",
            "risk_level": "medium",
        },
    ]

    def __init__(self) -> None:
        self.ensure_schema()

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    @staticmethod
    def normalize_token(value: str, fallback: str = "") -> str:
        token = str(value or "").strip().lower()
        token = re.sub(r"[^a-z0-9_.:-]+", "-", token).strip("-_.:")
        return token or fallback

    @staticmethod
    def normalize_group_name(value: str) -> str:
        token = SecurityService.normalize_token(value, "")
        if not token:
            raise ValueError("Invalid group name")
        return token

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    @classmethod
    def ensure_schema(cls) -> None:
        if cls._SCHEMA_READY:
            return
        with cls._SCHEMA_LOCK:
            if cls._SCHEMA_READY:
                return
            with get_connection(SYSTEM_DB) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS identity_roles (
                        name TEXT PRIMARY KEY,
                        description TEXT,
                        is_staff INTEGER NOT NULL DEFAULT 0,
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        updated_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_identity_tags (
                        db_name TEXT NOT NULL,
                        username TEXT NOT NULL,
                        role_tag TEXT NOT NULL DEFAULT 'user',
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        updated_at TEXT,
                        PRIMARY KEY (db_name, username)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS security_permissions (
                        permission_key TEXT PRIMARY KEY,
                        label TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'custom',
                        description TEXT,
                        risk_level TEXT NOT NULL DEFAULT 'medium',
                        created_by TEXT NOT NULL DEFAULT 'system',
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS role_permission_bindings (
                        role_name TEXT NOT NULL,
                        permission_key TEXT NOT NULL,
                        granted INTEGER NOT NULL DEFAULT 1,
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (role_name, permission_key)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_groups (
                        group_name TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT,
                        scope TEXT NOT NULL DEFAULT 'containers',
                        priority INTEGER NOT NULL DEFAULT 100,
                        created_by TEXT NOT NULL DEFAULT 'system',
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_group_members (
                        group_name TEXT NOT NULL,
                        username TEXT NOT NULL,
                        db_name TEXT NOT NULL DEFAULT 'system.db',
                        added_by TEXT NOT NULL DEFAULT 'system',
                        added_at INTEGER NOT NULL,
                        PRIMARY KEY (group_name, username, db_name)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_group_container_access (
                        group_name TEXT NOT NULL,
                        container_id TEXT NOT NULL,
                        role_tag TEXT NOT NULL DEFAULT 'user',
                        access_origin TEXT NOT NULL DEFAULT 'group',
                        updated_by TEXT NOT NULL DEFAULT 'system',
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (group_name, container_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS security_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_kind TEXT NOT NULL,
                        severity TEXT NOT NULL DEFAULT 'info',
                        risk_level TEXT NOT NULL DEFAULT 'low',
                        username TEXT,
                        db_name TEXT,
                        actor TEXT NOT NULL DEFAULT 'system',
                        actor_db TEXT NOT NULL DEFAULT 'system.db',
                        source_ip TEXT,
                        target_type TEXT,
                        target_id TEXT,
                        action TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS connection_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        db_name TEXT,
                        source_ip TEXT NOT NULL,
                        ip_classification TEXT NOT NULL DEFAULT 'system',
                        suspicion_reason TEXT,
                        service_name TEXT NOT NULL,
                        target_label TEXT,
                        request_path TEXT NOT NULL,
                        http_method TEXT NOT NULL,
                        status_code INTEGER NOT NULL DEFAULT 0,
                        request_bytes INTEGER NOT NULL DEFAULT 0,
                        response_bytes INTEGER NOT NULL DEFAULT 0,
                        risk_level TEXT NOT NULL DEFAULT 'low',
                        packet_summary TEXT,
                        user_agent TEXT,
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_ip_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        db_name TEXT NOT NULL DEFAULT 'system.db',
                        ip_address TEXT NOT NULL,
                        first_seen_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        seen_count INTEGER NOT NULL DEFAULT 1,
                        is_current INTEGER NOT NULL DEFAULT 1,
                        last_risk_level TEXT NOT NULL DEFAULT 'low',
                        notes TEXT,
                        UNIQUE (username, db_name, ip_address)
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_security_audit_created ON security_audit_log(created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_security_audit_user ON security_audit_log(username, db_name, created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_audit_created ON connection_audit_log(created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_audit_user ON connection_audit_log(username, db_name, created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_user_ip_history_user ON user_ip_history(username, db_name, last_seen_at DESC)")

                now = cls._now_ts()
                for item in cls.DEFAULT_PERMISSIONS:
                    conn.execute(
                        """
                        INSERT INTO security_permissions (
                            permission_key, label, category, description, risk_level, created_by, updated_by, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, 'system', 'system', ?, ?)
                        ON CONFLICT(permission_key) DO UPDATE SET
                            label=excluded.label,
                            category=excluded.category,
                            description=excluded.description,
                            risk_level=excluded.risk_level,
                            updated_by='system',
                            updated_at=excluded.updated_at
                        """,
                        (
                            item["key"],
                            item["label"],
                            item["category"],
                            item["description"],
                            item["risk_level"],
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO identity_roles (name, description, is_staff, updated_by, updated_at)
                    VALUES ('user', 'Standard user role', 0, 'system', datetime('now'))
                    ON CONFLICT(name) DO NOTHING
                    """
                )
                conn.execute(
                    """
                    INSERT INTO identity_roles (name, description, is_staff, updated_by, updated_at)
                    VALUES ('admin', 'Administrative role', 1, 'system', datetime('now'))
                    ON CONFLICT(name) DO NOTHING
                    """
                )
            cls._SCHEMA_READY = True

    def list_permissions(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT permission_key, label, category, description, risk_level, updated_at
                FROM security_permissions
                ORDER BY category ASC, permission_key ASC
                """
            ).fetchall()
        return [
            {
                "key": row["permission_key"],
                "label": row["label"],
                "category": row["category"],
                "description": row["description"],
                "risk_level": row["risk_level"],
                "updated_at": int(row["updated_at"] or 0),
            }
            for row in rows
        ]

    def upsert_permission(self, data: dict[str, Any], actor: str) -> dict[str, Any]:
        self.ensure_schema()
        key = self.normalize_token((data or {}).get("key"), "")
        if not key:
            raise ValueError("permission key is required")
        label = str((data or {}).get("label") or key).strip() or key
        category = self.normalize_token((data or {}).get("category"), "custom")
        description = str((data or {}).get("description") or "").strip() or None
        risk_level = self.normalize_token((data or {}).get("risk_level"), "medium")
        now = self._now_ts()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO security_permissions (
                    permission_key, label, category, description, risk_level, created_by, updated_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(permission_key) DO UPDATE SET
                    label=excluded.label,
                    category=excluded.category,
                    description=excluded.description,
                    risk_level=excluded.risk_level,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (key, label, category, description, risk_level, actor or "system", actor or "system", now, now),
            )
        return {"key": key, "label": label, "category": category, "description": description, "risk_level": risk_level}

    def list_roles_with_permissions(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            role_rows = conn.execute(
                "SELECT name, description, is_staff, updated_by, updated_at FROM identity_roles ORDER BY is_staff DESC, name ASC"
            ).fetchall()
            permission_rows = conn.execute(
                """
                SELECT role_name, permission_key
                FROM role_permission_bindings
                WHERE granted = 1
                ORDER BY role_name ASC, permission_key ASC
                """
            ).fetchall()
        perms_by_role: dict[str, list[str]] = {}
        for row in permission_rows:
            perms_by_role.setdefault(str(row["role_name"]), []).append(str(row["permission_key"]))
        return [
            {
                "name": row["name"],
                "description": row["description"],
                "is_staff": bool(row["is_staff"]),
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
                "permissions": perms_by_role.get(str(row["name"]), []),
                "permission_count": len(perms_by_role.get(str(row["name"]), [])),
            }
            for row in role_rows
        ]

    def set_role_permissions(self, role_name: str, permission_keys: list[str], actor: str) -> list[str]:
        self.ensure_schema()
        role = self.normalize_token(role_name, "")
        if not role:
            raise ValueError("role_name is required")
        cleaned = sorted({self.normalize_token(item, "") for item in (permission_keys or []) if self.normalize_token(item, "")})
        now = self._now_ts()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute("DELETE FROM role_permission_bindings WHERE role_name = ?", (role,))
            for item in cleaned:
                conn.execute(
                    """
                    INSERT INTO role_permission_bindings (role_name, permission_key, granted, updated_by, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (role, item, actor or "system", now),
                )
        return cleaned

    def list_groups(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT group_name, title, description, scope, priority, updated_by, updated_at
                FROM user_groups
                ORDER BY priority ASC, title ASC, group_name ASC
                """
            ).fetchall()
            member_counts = conn.execute(
                "SELECT group_name, COUNT(*) AS total FROM user_group_members GROUP BY group_name"
            ).fetchall()
            access_counts = conn.execute(
                "SELECT group_name, COUNT(*) AS total FROM user_group_container_access GROUP BY group_name"
            ).fetchall()
        members_map = {str(row["group_name"]): int(row["total"] or 0) for row in member_counts}
        access_map = {str(row["group_name"]): int(row["total"] or 0) for row in access_counts}
        return [
            {
                "group_name": row["group_name"],
                "title": row["title"],
                "description": row["description"],
                "scope": row["scope"],
                "priority": int(row["priority"] or 100),
                "updated_by": row["updated_by"],
                "updated_at": int(row["updated_at"] or 0),
                "members_count": members_map.get(str(row["group_name"]), 0),
                "containers_count": access_map.get(str(row["group_name"]), 0),
            }
            for row in rows
        ]

    def upsert_group(self, data: dict[str, Any], actor: str) -> dict[str, Any]:
        self.ensure_schema()
        group_name = self.normalize_group_name((data or {}).get("group_name") or (data or {}).get("name"))
        title = str((data or {}).get("title") or group_name).strip() or group_name
        description = str((data or {}).get("description") or "").strip() or None
        scope = self.normalize_token((data or {}).get("scope"), "containers")
        priority = int((data or {}).get("priority") or 100)
        now = self._now_ts()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO user_groups (group_name, title, description, scope, priority, created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_name) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    scope=excluded.scope,
                    priority=excluded.priority,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (group_name, title, description, scope, priority, actor or "system", actor or "system", now, now),
            )
        return {
            "group_name": group_name,
            "title": title,
            "description": description,
            "scope": scope,
            "priority": priority,
        }

    def set_group_members(self, group_name: str, members: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        group = self.normalize_group_name(group_name)
        dedup: dict[tuple[str, str], dict[str, str]] = {}
        for item in members or []:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            db_name = str(item.get("db_name") or "system.db").strip() or "system.db"
            if not username:
                continue
            dedup[(username, db_name)] = {"username": username, "db_name": db_name}
        now = self._now_ts()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute("DELETE FROM user_group_members WHERE group_name = ?", (group,))
            for item in dedup.values():
                conn.execute(
                    """
                    INSERT INTO user_group_members (group_name, username, db_name, added_by, added_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (group, item["username"], item["db_name"], actor or "system", now),
                )
        return self.list_group_members(group)

    def list_group_members(self, group_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        group = self.normalize_group_name(group_name)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT username, db_name, added_by, added_at
                FROM user_group_members
                WHERE group_name = ?
                ORDER BY db_name ASC, username ASC
                """,
                (group,),
            ).fetchall()
        return [
            {
                "username": row["username"],
                "db_name": row["db_name"],
                "added_by": row["added_by"],
                "added_at": int(row["added_at"] or 0),
            }
            for row in rows
        ]

    def set_group_container_access(self, group_name: str, assignments: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        group = self.normalize_group_name(group_name)
        dedup: dict[str, dict[str, str]] = {}
        for item in assignments or []:
            if not isinstance(item, dict):
                continue
            container_id = str(item.get("container_id") or "").strip()
            if not container_id:
                continue
            dedup[container_id] = {
                "container_id": container_id,
                "role_tag": self.normalize_token(item.get("role_tag"), "user"),
            }
        now = self._now_ts()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute("DELETE FROM user_group_container_access WHERE group_name = ?", (group,))
            for item in dedup.values():
                conn.execute(
                    """
                    INSERT INTO user_group_container_access (group_name, container_id, role_tag, access_origin, updated_by, updated_at)
                    VALUES (?, ?, ?, 'group', ?, ?)
                    """,
                    (group, item["container_id"], item["role_tag"], actor or "system", now),
                )
        return self.list_group_container_access(group)

    def list_group_container_access(self, group_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        group = self.normalize_group_name(group_name)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT container_id, role_tag, updated_by, updated_at
                FROM user_group_container_access
                WHERE group_name = ?
                ORDER BY container_id ASC
                """,
                (group,),
            ).fetchall()
        return [
            {
                "container_id": row["container_id"],
                "role_tag": row["role_tag"],
                "updated_by": row["updated_by"],
                "updated_at": int(row["updated_at"] or 0),
            }
            for row in rows
        ]

    def get_user_group_memberships(self, username: str, db_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT g.group_name, g.title, g.priority
                FROM user_group_members gm
                JOIN user_groups g ON g.group_name = gm.group_name
                WHERE gm.username = ? AND gm.db_name = ?
                ORDER BY g.priority ASC, g.group_name ASC
                """,
                (username, db_name or "system.db"),
            ).fetchall()
        return [
            {
                "group_name": row["group_name"],
                "title": row["title"],
                "priority": int(row["priority"] or 100),
            }
            for row in rows
        ]

    def resolve_container_group_access(self, username: str, db_name: str, container_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT ga.group_name, ga.container_id, ga.role_tag, g.title, g.priority
                FROM user_group_members gm
                JOIN user_group_container_access ga ON ga.group_name = gm.group_name
                JOIN user_groups g ON g.group_name = gm.group_name
                WHERE gm.username = ? AND gm.db_name = ? AND ga.container_id = ?
                ORDER BY g.priority ASC, ga.group_name ASC
                """,
                (username, db_name or "system.db", container_id),
            ).fetchall()
        return [
            {
                "group_name": row["group_name"],
                "title": row["title"],
                "priority": int(row["priority"] or 100),
                "container_id": row["container_id"],
                "role_tag": self.normalize_token(row["role_tag"], "user"),
            }
            for row in rows
        ]

    def user_has_container_access(self, username: str, db_name: str, container_id: str) -> bool:
        self.ensure_schema()
        if not username:
            return False
        with get_connection(SYSTEM_DB) as conn:
            direct = conn.execute(
                """
                SELECT 1
                FROM container_permissions
                WHERE username = ? AND container_id = ? AND (db_name = ? OR db_name IS NULL OR db_name = '')
                LIMIT 1
                """,
                (username, container_id, db_name or "system.db"),
            ).fetchone()
        if direct:
            return True
        return bool(self.resolve_container_group_access(username, db_name, container_id))

    def resolve_container_role_override(self, username: str, db_name: str, container_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                """
                SELECT role_tag, 'direct' AS source_label
                FROM container_permissions
                WHERE username = ? AND container_id = ? AND (db_name = ? OR db_name IS NULL OR db_name = '')
                LIMIT 1
                """,
                (username, container_id, db_name or "system.db"),
            ).fetchone()
        if row:
            return {"role_tag": self.normalize_token(row["role_tag"], "user"), "source": "direct"}
        groups = self.resolve_container_group_access(username, db_name, container_id)
        if groups:
            first = groups[0]
            return {
                "role_tag": self.normalize_token(first.get("role_tag"), "user"),
                "source": "group",
                "group_name": first.get("group_name"),
            }
        return None

    def _service_name_from_path(self, path: str) -> str:
        token = str(path or "/").strip().lower()
        if token.startswith("/containers"):
            return "containers"
        if token.startswith("/users"):
            return "users"
        if token.startswith("/roles") or token.startswith("/security"):
            return "rbac"
        if token.startswith("/logs"):
            return "logs"
        if token.startswith("/metrics"):
            return "metrics"
        if token.startswith("/projects"):
            return "projects"
        if token.startswith("/system"):
            return "system"
        return "core"

    def observe_user_ip(self, username: str, db_name: str, ip_address: str) -> dict[str, Any]:
        self.ensure_schema()
        username = str(username or "").strip()
        db_name = str(db_name or "system.db").strip() or "system.db"
        ip_address = str(ip_address or "").strip()
        if not username or not ip_address:
            return {"risk_level": "low", "classification": "system", "reason": ""}
        now = self._now_ts()
        risk_level = "low"
        classification = "known"
        reason = ""
        with get_connection(SYSTEM_DB) as conn:
            latest = conn.execute(
                """
                SELECT ip_address, last_seen_at
                FROM user_ip_history
                WHERE username = ? AND db_name = ?
                ORDER BY last_seen_at DESC, id DESC
                LIMIT 1
                """,
                (username, db_name),
            ).fetchone()
            current = conn.execute(
                """
                SELECT id, seen_count
                FROM user_ip_history
                WHERE username = ? AND db_name = ? AND ip_address = ?
                LIMIT 1
                """,
                (username, db_name, ip_address),
            ).fetchone()
            if current:
                conn.execute(
                    """
                    UPDATE user_ip_history
                    SET last_seen_at = ?, seen_count = seen_count + 1, is_current = 1, last_risk_level = ?
                    WHERE id = ?
                    """,
                    (now, risk_level, int(current["id"])),
                )
            else:
                classification = "new"
                risk_level = "medium"
                reason = "new_ip_for_user"
                if latest and str(latest["ip_address"] or "").strip() and str(latest["ip_address"]) != ip_address:
                    if now - int(latest["last_seen_at"] or 0) < 6 * 3600:
                        risk_level = "elevated"
                        reason = "rapid_ip_change"
                conn.execute(
                    """
                    INSERT INTO user_ip_history (username, db_name, ip_address, first_seen_at, last_seen_at, seen_count, is_current, last_risk_level)
                    VALUES (?, ?, ?, ?, ?, 1, 1, ?)
                    """,
                    (username, db_name, ip_address, now, now, risk_level),
                )
            conn.execute(
                "UPDATE user_ip_history SET is_current = CASE WHEN ip_address = ? THEN 1 ELSE 0 END WHERE username = ? AND db_name = ?",
                (ip_address, username, db_name),
            )
        return {"risk_level": risk_level, "classification": classification, "reason": reason}

    def append_audit_event(
        self,
        *,
        event_kind: str,
        action: str,
        summary: str,
        severity: str = "info",
        risk_level: str = "low",
        username: str | None = None,
        db_name: str | None = None,
        actor: str = "system",
        actor_db: str = "system.db",
        source_ip: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        payload = details if isinstance(details, dict) else {}
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO security_audit_log (
                    event_kind, severity, risk_level, username, db_name, actor, actor_db, source_ip,
                    target_type, target_id, action, summary, details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.normalize_token(event_kind, "event"),
                    self.normalize_token(severity, "info"),
                    self.normalize_token(risk_level, "low"),
                    username,
                    db_name,
                    actor or "system",
                    actor_db or "system.db",
                    source_ip,
                    target_type,
                    target_id,
                    action or "security.event",
                    summary or action or "Security event",
                    self._json(payload),
                    self._now_ts(),
                ),
            )
        return {"status": "logged"}

    def append_connection_event(
        self,
        *,
        username: str | None,
        db_name: str | None,
        source_ip: str,
        request_path: str,
        http_method: str,
        status_code: int,
        request_bytes: int,
        response_bytes: int,
        risk_level: str,
        ip_classification: str,
        suspicion_reason: str,
        user_agent: str,
    ) -> dict[str, Any]:
        self.ensure_schema()
        service_name = self._service_name_from_path(request_path)
        packet_summary = f"req={max(0, int(request_bytes or 0))}B res={max(0, int(response_bytes or 0))}B"
        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                """
                INSERT INTO connection_audit_log (
                    username, db_name, source_ip, ip_classification, suspicion_reason, service_name, target_label,
                    request_path, http_method, status_code, request_bytes, response_bytes, risk_level, packet_summary, user_agent, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    db_name,
                    source_ip or "unknown",
                    self.normalize_token(ip_classification, "system"),
                    suspicion_reason or None,
                    service_name,
                    request_path,
                    request_path,
                    str(http_method or "GET").upper(),
                    int(status_code or 0),
                    max(0, int(request_bytes or 0)),
                    max(0, int(response_bytes or 0)),
                    self.normalize_token(risk_level, "low"),
                    packet_summary,
                    str(user_agent or "")[:255],
                    self._now_ts(),
                ),
            )
        return {"status": "logged"}

    def observe_request(self, request: Request, response, session_ctx: tuple[str, str, bool] | None = None) -> None:
        self.ensure_schema()
        try:
            source_ip = (
                (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
                or (request.client.host if request.client else "")
                or "unknown"
            )
            username = session_ctx[0] if session_ctx else None
            db_name = session_ctx[1] if session_ctx else None
            ip_meta = {"risk_level": "low", "classification": "system", "reason": ""}
            if username and db_name:
                ip_meta = self.observe_user_ip(username, db_name, source_ip)
            elif int(getattr(response, "status_code", 0) or 0) >= 401:
                ip_meta = {"risk_level": "elevated", "classification": "unknown", "reason": "unauthenticated_request"}
            request_bytes = int(request.headers.get("content-length") or 0)
            response_bytes = int(response.headers.get("content-length") or 0) if getattr(response, "headers", None) else 0
            self.append_connection_event(
                username=username,
                db_name=db_name,
                source_ip=source_ip,
                request_path=str(request.url.path or "/"),
                http_method=str(request.method or "GET"),
                status_code=int(getattr(response, "status_code", 0) or 0),
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                risk_level=ip_meta.get("risk_level") or "low",
                ip_classification=ip_meta.get("classification") or "system",
                suspicion_reason=ip_meta.get("reason") or "",
                user_agent=str(request.headers.get("user-agent") or "")[:255],
            )
        except Exception:
            return

    def list_user_ip_history(self, username: str, db_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                """
                SELECT ip_address, first_seen_at, last_seen_at, seen_count, is_current, last_risk_level, notes
                FROM user_ip_history
                WHERE username = ? AND db_name = ?
                ORDER BY last_seen_at DESC, id DESC
                """,
                (username, db_name or "system.db"),
            ).fetchall()
        return [
            {
                "ip_address": row["ip_address"],
                "first_seen_at": int(row["first_seen_at"] or 0),
                "last_seen_at": int(row["last_seen_at"] or 0),
                "seen_count": int(row["seen_count"] or 0),
                "is_current": bool(row["is_current"]),
                "risk_level": row["last_risk_level"] or "low",
                "notes": row["notes"],
            }
            for row in rows
        ]

    def list_user_audit_events(self, limit: int = 100, username: str = "", db_name: str = "", risk_level: str = "") -> list[dict[str, Any]]:
        self.ensure_schema()
        capped = max(1, min(int(limit or 100), 500))
        clauses = ["event_kind = 'user'"]
        params: list[Any] = []
        if username:
            clauses.append("(username = ? OR actor = ?)")
            params.extend([username, username])
        if db_name:
            clauses.append("(db_name = ? OR actor_db = ?)")
            params.extend([db_name, db_name])
        if risk_level:
            clauses.append("risk_level = ?")
            params.append(risk_level)
        where = " AND ".join(clauses)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                f"""
                SELECT id, event_kind, severity, risk_level, username, db_name, actor, actor_db, source_ip, target_type, target_id, action, summary, details_json, created_at
                FROM security_audit_log
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*params, capped),
            ).fetchall()
        return [self._audit_row_to_dict(row) for row in rows]

    def list_connection_audit_events(self, limit: int = 100, username: str = "", risk_level: str = "", service_name: str = "") -> list[dict[str, Any]]:
        self.ensure_schema()
        capped = max(1, min(int(limit or 100), 500))
        clauses = ["1=1"]
        params: list[Any] = []
        if username:
            clauses.append("username = ?")
            params.append(username)
        if risk_level:
            clauses.append("risk_level = ?")
            params.append(risk_level)
        if service_name:
            clauses.append("service_name = ?")
            params.append(service_name)
        where = " AND ".join(clauses)
        with get_connection(SYSTEM_DB) as conn:
            rows = conn.execute(
                f"""
                SELECT id, username, db_name, source_ip, ip_classification, suspicion_reason, service_name, target_label,
                       request_path, http_method, status_code, request_bytes, response_bytes, risk_level, packet_summary, user_agent, created_at
                FROM connection_audit_log
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*params, capped),
            ).fetchall()
        return [
            {
                "id": int(row["id"] or 0),
                "username": row["username"],
                "db_name": row["db_name"],
                "source_ip": row["source_ip"],
                "ip_classification": row["ip_classification"],
                "suspicion_reason": row["suspicion_reason"],
                "service_name": row["service_name"],
                "target_label": row["target_label"],
                "request_path": row["request_path"],
                "http_method": row["http_method"],
                "status_code": int(row["status_code"] or 0),
                "request_bytes": int(row["request_bytes"] or 0),
                "response_bytes": int(row["response_bytes"] or 0),
                "risk_level": row["risk_level"],
                "packet_summary": row["packet_summary"],
                "user_agent": row["user_agent"],
                "created_at": int(row["created_at"] or 0),
            }
            for row in rows
        ]

    def _audit_row_to_dict(self, row) -> dict[str, Any]:
        try:
            details = json.loads(row["details_json"] or "{}")
        except Exception:
            details = {}
        return {
            "id": int(row["id"] or 0),
            "event_kind": row["event_kind"],
            "severity": row["severity"],
            "risk_level": row["risk_level"],
            "username": row["username"],
            "db_name": row["db_name"],
            "actor": row["actor"],
            "actor_db": row["actor_db"],
            "source_ip": row["source_ip"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "action": row["action"],
            "summary": row["summary"],
            "details": details if isinstance(details, dict) else {},
            "created_at": int(row["created_at"] or 0),
        }

    def export_csv(self, kind: str, limit: int = 1000) -> str:
        safe_kind = self.normalize_token(kind, "users")
        rows: list[dict[str, Any]]
        if safe_kind == "connections":
            rows = self.list_connection_audit_events(limit=limit)
        else:
            rows = self.list_user_audit_events(limit=limit)
        output = io.StringIO()
        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = ["empty"]
            rows = [{"empty": ""}]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (self._json(value) if isinstance(value, dict) else value) for key, value in row.items()})
        return output.getvalue()

    def _count_users_in_db(self, db_name: str) -> int:
        if db_name == "system.db":
            with get_connection(SYSTEM_DB) as conn:
                row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
            return int(row["total"] or 0) if row else 0
        db_path, resolved_name = resolve_client_db_path(db_name)
        if resolved_name not in list_client_databases():
            return 0
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        finally:
            conn.close()
        return int(row["total"] or 0) if row else 0

    def list_access_users(self, db_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        import sqlite3

        target_db = str(db_name or "").strip() or "system.db"
        if target_db == "system.db":
            with get_connection(SYSTEM_DB) as conn:
                rows = conn.execute("SELECT id, username, email, is_staff, is_active FROM users ORDER BY username ASC").fetchall()
        else:
            db_path, resolved_name = resolve_client_db_path(target_db)
            target_db = resolved_name
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT id, username, email, is_staff, is_active FROM users ORDER BY username ASC").fetchall()
            finally:
                conn.close()
        result = []
        with get_connection(SYSTEM_DB) as sys_conn:
            for row in rows:
                username = str(row["username"] or "").strip()
                role_row = sys_conn.execute(
                    "SELECT role_tag FROM user_identity_tags WHERE username = ? AND db_name = ? LIMIT 1",
                    (username, target_db),
                ).fetchone()
                ip_row = sys_conn.execute(
                    """
                    SELECT ip_address, last_seen_at, last_risk_level
                    FROM user_ip_history
                    WHERE username = ? AND db_name = ?
                    ORDER BY last_seen_at DESC, id DESC
                    LIMIT 1
                    """,
                    (username, target_db),
                ).fetchone()
                group_rows = sys_conn.execute(
                    """
                    SELECT g.group_name
                    FROM user_group_members gm
                    JOIN user_groups g ON g.group_name = gm.group_name
                    WHERE gm.username = ? AND gm.db_name = ?
                    ORDER BY g.priority ASC, g.group_name ASC
                    """,
                    (username, target_db),
                ).fetchall()
                container_direct = sys_conn.execute(
                    "SELECT COUNT(*) AS total FROM container_permissions WHERE username = ? AND db_name = ?",
                    (username, target_db),
                ).fetchone()
                container_group = sys_conn.execute(
                    """
                    SELECT COUNT(DISTINCT ga.container_id) AS total
                    FROM user_group_members gm
                    JOIN user_group_container_access ga ON ga.group_name = gm.group_name
                    WHERE gm.username = ? AND gm.db_name = ?
                    """,
                    (username, target_db),
                ).fetchone()
                result.append(
                    {
                        "id": int(row["id"] or 0),
                        "username": username,
                        "email": row["email"],
                        "is_staff": bool(row["is_staff"]),
                        "is_active": bool(row["is_active"]),
                        "role_tag": self.normalize_token(role_row["role_tag"], "admin" if row["is_staff"] else "user") if role_row else ("admin" if row["is_staff"] else "user"),
                        "last_ip": ip_row["ip_address"] if ip_row else None,
                        "last_ip_seen_at": int(ip_row["last_seen_at"] or 0) if ip_row else 0,
                        "network_risk_level": ip_row["last_risk_level"] if ip_row else "low",
                        "group_names": [g["group_name"] for g in group_rows],
                        "groups_count": len(group_rows),
                        "direct_containers_count": int(container_direct["total"] or 0) if container_direct else 0,
                        "group_containers_count": int(container_group["total"] or 0) if container_group else 0,
                    }
                )
        return result

    def build_access_control_overview(self, db_name: str = "") -> dict[str, Any]:
        self.ensure_schema()
        target_db = str(db_name or "").strip() or ((list_client_databases() or ["system.db"])[0] if list_client_databases() else "system.db")
        if target_db != "system.db" and not target_db.endswith(".db"):
            target_db = f"{target_db}.db"
        users = self.list_access_users(target_db)
        roles = self.list_roles_with_permissions()
        permissions = self.list_permissions()
        groups = self.list_groups()
        user_events = self.list_user_audit_events(limit=12)
        connection_events = self.list_connection_audit_events(limit=12)
        risk_counts = {"low": 0, "medium": 0, "elevated": 0, "high": 0, "critical": 0}
        for item in connection_events:
            level = self.normalize_token(item.get("risk_level"), "low")
            risk_counts[level] = risk_counts.get(level, 0) + 1
        return {
            "db_name": target_db,
            "summary": {
                "users": len(users),
                "groups": len(groups),
                "roles": len(roles),
                "permissions": len(permissions),
                "audited_user_events": len(user_events),
                "audited_connections": len(connection_events),
                "db_users_total": self._count_users_in_db(target_db),
                "risk_counts": risk_counts,
            },
            "roles": roles,
            "permissions": permissions,
            "groups": [
                {
                    **group,
                    "members": self.list_group_members(group["group_name"]),
                    "container_access": self.list_group_container_access(group["group_name"]),
                }
                for group in groups
            ],
            "users": users,
            "audit": {
                "user_events": user_events,
                "connection_events": connection_events,
            },
        }
