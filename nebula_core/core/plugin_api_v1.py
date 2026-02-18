from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


PLUGIN_API_VERSION = "v1"

ALLOWED_SCOPES = {
    "users.read",
    "users.write",
    "roles.read",
    "roles.write",
    "identity_tags.read",
    "identity_tags.write",
    "events.emit",
}


class PluginError(RuntimeError):
    pass


class PluginPermissionError(PluginError):
    pass


@dataclass
class PluginManifest:
    name: str
    version: str = "0.1.0"
    description: str = ""
    scopes: List[str] = None
    api_version: str = PLUGIN_API_VERSION
    source: str = "in_process"

    def sanitized_scopes(self) -> List[str]:
        items = self.scopes or []
        return [s for s in items if s in ALLOWED_SCOPES]


@runtime_checkable
class PluginV1(Protocol):
    async def initialize(self, context: Any) -> None:
        ...

    async def health(self) -> Dict[str, Any]:
        ...

    async def sync_users(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ...

    async def shutdown(self) -> None:
        ...
