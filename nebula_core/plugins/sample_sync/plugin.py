# nebula_core/plugins/sample_sync/plugin.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from typing import Any, Dict, Optional

PLUGIN_API_VERSION = "v1"


class SampleSyncPlugin:
    def __init__(self):
        self.ctx = None

    async def initialize(self, context):
        self.ctx = context
        self.ctx.log("info", "sample_sync initialized")

    async def health(self) -> Dict[str, Any]:
        return {"status": "ok", "plugin": "sample_sync"}

    async def sync_users(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        dry_run = bool(payload.get("dry_run", False))
        users = payload.get("users")
        if not isinstance(users, list) or not users:
            users = [
                {"username": "demo.alice", "db_name": "system.db", "role_tag": "developer", "email": "alice@example.local"},
                {"username": "demo.bob", "db_name": "system.db", "role_tag": "tester", "email": "bob@example.local"}
            ]

        result = []
        await self.ctx.emit_event("sync.started", {"plugin": "sample_sync", "dry_run": dry_run})
        if dry_run:
            roles = await self.ctx.list_identity_roles()
            preview = await self.ctx.list_users(db_name="system.db", limit=10, offset=0)
            for item in users:
                result.append({
                    "action": "would_sync",
                    "username": str(item.get("username") or "").strip(),
                    "db_name": str(item.get("db_name") or "system.db"),
                    "role_tag": str(item.get("role_tag") or "user").lower(),
                })
            await self.ctx.emit_event("sync.finished", {"plugin": "sample_sync", "dry_run": True, "count": len(result)})
            return {
                "status": "dry_run",
                "count": len(result),
                "items": result,
                "roles_available": len(roles),
                "users_preview_count": int(preview.get("count", 0)),
            }

        await self.ctx.upsert_identity_role(
            name="contractor",
            description="External contractor role managed by sample plugin",
            is_staff=False,
        )
        for item in users:
            username = str(item.get("username") or "").strip()
            if not username:
                continue
            synced = await self.ctx.sync_user(
                username=username,
                db_name=str(item.get("db_name") or "system.db"),
                role_tag=str(item.get("role_tag") or "user").lower(),
                email=str(item.get("email") or "").strip(),
                is_active=bool(item.get("is_active", True)),
            )
            result.append(synced)

        await self.ctx.emit_event("sync.finished", {"plugin": "sample_sync", "dry_run": False, "count": len(result)})
        return {"status": "ok", "count": len(result), "items": result}

    async def shutdown(self):
        if self.ctx:
            self.ctx.log("info", "sample_sync shutdown")


def create_plugin():
    return SampleSyncPlugin()
