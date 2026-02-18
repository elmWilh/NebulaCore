# nebula_core

`nebula_core` is the FastAPI backend engine of Nebula Panel.

## What This Component Does

- Provides API endpoints for users, roles, containers, and system operations.
- Integrates with Docker for deploy/start/stop/restart/logs/delete.
- Aggregates host metrics and user container metrics.
- Runs internal services (heartbeat, file service, metrics service).

## Entry Points

- Module launch: `python -m nebula_core`
- ASGI entrypoint: `nebula_core.main:app`

## Important Directories

- `api/` - HTTP and WebSocket API.
- `services/` - business-logic services (docker, metrics, files, users).
- `core/` - runtime, event bus, lifecycle.
- `plugins/` - in-process plugins implementing `plugin_api_v1`.
- `db.py` and `db/` - SQLite access and schema handling.

## Plugin API v1

- In-process plugins are discovered in `nebula_core/plugins/*/plugin.py`.
- Plugin contract:
  - module variable `PLUGIN_API_VERSION = "v1"`
  - factory `create_plugin()`
  - async methods: `initialize(context)`, `health()`, `sync_users(payload)`, `shutdown()`
- `PluginContext` (scope-guarded helpers):
  - `sync_user(...)`, `list_users(...)`
  - `set_identity_tag(...)`
  - `list_identity_roles()`, `upsert_identity_role(...)`
  - `emit_event(\"<name>\", payload)` -> emitted as `plugin.<plugin_name>.<name>`
- Runtime compiles plugin Python files before import and isolates failures per plugin.
- Plugin management API (staff or internal token):
  - `GET /system/plugins`
  - `POST /system/plugins/rescan`
  - `GET /system/plugins/{name}/health`
  - `POST /system/plugins/{name}/sync-users`
- Example plugin: `nebula_core/plugins/sample_sync`.

## Security

- For production, run behind a reverse proxy.
- Restrict `NEBULA_CORS_ORIGINS` and external Core port exposure.
- Use and rotate `NEBULA_INSTALLER_TOKEN`.
