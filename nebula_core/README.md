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
- `plugins/` - plugin sources used by DEV in-process mode and `plugin_runtime_v2` worker mode.
- `db.py` and `db/` - SQLite access and schema handling.

## Plugin Runtime

- `plugin_api_v1` remains supported for compatibility.
- `plugin_runtime_v2` (default for production) runs each plugin in a separate process via gRPC over Unix socket.
- In-process plugins are DEV-only and blocked when `plugins.environment` is `production`.
- Optional cgroup v2 backend can enforce per-plugin limits (`memory.max`, `cpu.max`, `pids.max`).
- Plugin manager provides:
  - subprocess spawn for each plugin
  - per-call timeout enforcement (default 10s, max 30s)
  - state machine (`initialized`, `healthy`, `degraded`, `unresponsive`, `crashed`, `disabled`)
  - health monitoring with restart policy
  - restart/crash counters and disable-after-threshold handling
  - worker resource limits (memory/cpu)

Runtime config (`serviceconfig.yaml`, `plugins`):
- `cgroup_enabled`: enable cgroup v2 backend for process plugins.
- `cgroup_required`: fail startup if cgroup backend is unavailable.
- `cgroup_root`: cgroup root path (`auto` uses delegated service cgroup subtree).
- `cgroup_cpu_quota_us`, `cgroup_cpu_period_us`, `cgroup_pids_max`: process limits.

## Plugin API v1

- Plugin source discovery path: `nebula_core/plugins/*/plugin.py`.
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

## License & Copyright

- Copyright (c) 2026 Monolink Systems
- Nebula Open Source Edition (non-corporate)
- Licensed under AGPLv3
