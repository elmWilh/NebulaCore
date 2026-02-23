# nebula_core/plugins/ad_migration_demo/plugin.py
from typing import Any, Dict, List, Optional

PLUGIN_API_VERSION = "v1"


class AdMigrationDemoPlugin:
    """
    Demo/template plugin for AD -> Nebula user migration.

    Replace `_fetch_ad_users()` with real AD/LDAP client logic.
    """

    def __init__(self):
        self.ctx = None

    async def initialize(self, context):
        self.ctx = context
        self.ctx.log("info", "ad_migration_demo initialized")

    async def health(self) -> Dict[str, Any]:
        return {"status": "ok", "plugin": "ad_migration_demo", "runtime": "plugin_runtime_v2"}

    async def sync_users(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        dry_run = bool(payload.get("dry_run", True))
        limit = int(payload.get("limit") or 0)
        cursor = str(payload.get("cursor") or "")
        db_name = str(payload.get("db_name") or "system.db")
        batch_size = max(1, min(int(payload.get("batch_size") or 100), 1000))

        # Example mapping from AD group -> Nebula role tag.
        group_to_role = payload.get("group_role_map") or {
            "CN=Domain Admins": "admin",
            "CN=Developers": "developer",
            "CN=QA": "tester",
        }

        ad_users = self._fetch_ad_users(payload, cursor=cursor, batch_size=batch_size)
        if limit > 0:
            ad_users = ad_users[:limit]

        await self.ctx.emit_event(
            "ad_sync.started",
            {
                "plugin": "ad_migration_demo",
                "dry_run": dry_run,
                "count": len(ad_users),
                "db_name": db_name,
                "cursor": cursor,
            },
        )

        processed: List[Dict[str, Any]] = []
        skipped = 0

        if dry_run:
            for item in ad_users:
                username = str(item.get("username") or "").strip()
                if not username:
                    skipped += 1
                    continue
                role_tag = self._resolve_role_tag(item, group_to_role)
                processed.append(
                    {
                        "action": "would_sync",
                        "username": username,
                        "email": str(item.get("email") or "").strip(),
                        "role_tag": role_tag,
                        "is_active": bool(item.get("is_active", True)),
                        "db_name": db_name,
                    }
                )
            await self.ctx.emit_event(
                "ad_sync.finished",
                {
                    "plugin": "ad_migration_demo",
                    "dry_run": True,
                    "count": len(processed),
                    "skipped": skipped,
                },
            )
            return {
                "status": "dry_run",
                "plugin": "ad_migration_demo",
                "count": len(processed),
                "skipped": skipped,
                "items": processed,
                "next_cursor": self._next_cursor(cursor, len(ad_users)),
            }

        # Ensure known role tags exist before writing user identity tags.
        await self._ensure_roles(group_to_role)

        for item in ad_users:
            username = str(item.get("username") or "").strip()
            if not username:
                skipped += 1
                continue

            role_tag = self._resolve_role_tag(item, group_to_role)
            synced = await self.ctx.sync_user(
                username=username,
                db_name=db_name,
                role_tag=role_tag,
                email=str(item.get("email") or "").strip(),
                is_active=bool(item.get("is_active", True)),
            )
            processed.append(synced)

        await self.ctx.emit_event(
            "ad_sync.finished",
            {
                "plugin": "ad_migration_demo",
                "dry_run": False,
                "count": len(processed),
                "skipped": skipped,
            },
        )
        return {
            "status": "ok",
            "plugin": "ad_migration_demo",
            "count": len(processed),
            "skipped": skipped,
            "items": processed,
            "next_cursor": self._next_cursor(cursor, len(ad_users)),
        }

    async def shutdown(self):
        if self.ctx:
            self.ctx.log("info", "ad_migration_demo shutdown")

    async def _ensure_roles(self, group_to_role: Dict[str, str]):
        unique_roles = sorted({self._normalize_role_tag(v) for v in group_to_role.values()})
        existing = await self.ctx.list_identity_roles()
        existing_names = {str(x.get("name") or "").strip().lower() for x in existing}
        for role in unique_roles:
            if role in existing_names:
                continue
            await self.ctx.upsert_identity_role(
                name=role,
                description=f"Auto-created by ad_migration_demo for AD mapping ({role})",
                is_staff=(role == "admin"),
            )

    def _resolve_role_tag(self, ad_user: Dict[str, Any], group_to_role: Dict[str, str]) -> str:
        groups = ad_user.get("groups") or []
        if not isinstance(groups, list):
            groups = []
        for group in groups:
            key = str(group or "").strip()
            if key in group_to_role:
                return self._normalize_role_tag(str(group_to_role.get(key) or "user"))
        return "user"

    @staticmethod
    def _normalize_role_tag(value: str) -> str:
        token = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in str(value or "").strip().lower())
        token = token.strip("-_")
        return token or "user"

    @staticmethod
    def _next_cursor(cursor: str, count: int) -> str:
        if count <= 0:
            return ""
        current = int(cursor or "0") if str(cursor or "").isdigit() else 0
        return str(current + count)

    def _fetch_ad_users(self, payload: Dict[str, Any], cursor: str, batch_size: int) -> List[Dict[str, Any]]:
        """
        Replace this stub with real AD queries.

        Expected output item schema:
        {
          "username": "j.doe",
          "email": "j.doe@example.com",
          "is_active": true,
          "groups": ["CN=Developers", "CN=QA"]
        }
        """
        provided = payload.get("ad_users")
        if isinstance(provided, list) and provided:
            out: List[Dict[str, Any]] = []
            for item in provided:
                if isinstance(item, dict):
                    out.append(item)
            return out[:batch_size]

        # Demo fallback dataset to test pipeline without AD connectivity.
        demo = [
            {
                "username": "ad.alice",
                "email": "alice@corp.local",
                "is_active": True,
                "groups": ["CN=Developers"],
            },
            {
                "username": "ad.bob",
                "email": "bob@corp.local",
                "is_active": True,
                "groups": ["CN=QA"],
            },
            {
                "username": "ad.root",
                "email": "root@corp.local",
                "is_active": True,
                "groups": ["CN=Domain Admins"],
            },
        ]
        return demo[:batch_size]


def create_plugin():
    return AdMigrationDemoPlugin()
