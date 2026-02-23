# Plugin Manager API and Developer Guide

This document defines the Nebula plugin management API and how developers should build plugins for Nebula runtime.

## 1. Runtime Model

Nebula Core supports two plugin runtimes:

- `plugin_runtime_v2` (recommended / production):
  - each plugin in a separate process
  - gRPC over Unix socket
  - timeout enforcement, health monitoring, restart policy
  - optional cgroup v2 resource isolation
- `plugin_api_v1` (legacy compatibility):
  - in-process mode (DEV only)
  - external gRPC endpoints (`host:port`) still supported but deprecated

Production expectation:
- set `plugins.environment: production`
- use `process_runtime_enabled: true`
- disable `in_process_enabled`

## 2. HTTP API

Base prefix:

- `/system/plugins`

Auth:
- staff session cookie, or
- internal token header `X-Nebula-Token`

Source: `nebula_core/api/plugins.py`.

### 2.1 GET `/system/plugins`

List discovered plugins and runtime state.

Response example:

```json
{
  "plugins": [
    {
      "name": "sample_sync",
      "source": "process",
      "api_version": "v1",
      "runtime_version": "plugin_runtime_v2",
      "version": "0.1.0",
      "description": "Sample user sync plugin for plugin_api_v1",
      "scopes": ["users.read", "users.write", "identity_tags.write", "events.emit"],
      "status": "healthy",
      "message": "health ok",
      "warning": "",
      "error": "",
      "initialized_at": 1739980000.12,
      "updated_at": 1739980030.05,
      "consecutive_timeouts": 0,
      "consecutive_health_failures": 0,
      "consecutive_crashes": 0,
      "restart_count": 0
    }
  ]
}
```

### 2.2 POST `/system/plugins/rescan`

Rescans plugin directories and runtime bindings.

Typical use:
- after adding/updating plugin code or `plugin.json`.

### 2.3 GET `/system/plugins/{plugin_name}/health`

Requests plugin `Health()` through manager.

### 2.4 POST `/system/plugins/{plugin_name}/sync-users`

Triggers plugin `sync_users(payload)`.

Request model:

```json
{
  "dry_run": true,
  "users": [
    {
      "username": "john.doe",
      "db_name": "system.db",
      "role_tag": "developer",
      "email": "john@example.com",
      "is_active": true
    }
  ],
  "limit": 100
}
```

Validation:
- `limit` range: `0..10000`.

### 2.5 POST `/system/plugins/{plugin_name}/action`

Manual runtime action:

```json
{
  "action": "restart"
}
```

Supported actions:
- `start`
- `stop`
- `restart`

### 2.6 GET `/system/plugins/{plugin_name}/stats`

Returns runtime stats (pid, alive state, rss/vm memory if available, cgroup events, log path).

### 2.7 GET `/system/plugins/{plugin_name}/logs?tail=300`

Returns plugin log lines from runtime log file.

## 3. State Machine

States used by manager:

- `initialized`
- `healthy`
- `degraded`
- `unresponsive`
- `crashed`
- `disabled`

Key transitions:
- timeout -> `degraded`
- repeated timeout -> restart
- health failures -> `unresponsive` -> restart
- process exit / crash -> `crashed`
- crash budget exceeded -> `disabled`

## 4. Timeouts and Restart Policy

Config keys in `serviceconfig.yaml` -> `plugins`:

- `default_timeout_sec` (default 10)
- `max_timeout_sec` (default 30)
- `call_timeout_sec`
- `timeout_restart_threshold` (default 3)
- `health_interval_sec` (default 30)
- `health_restart_threshold` (default 2)
- `max_restarts` (default 3)
- `max_crashes` (default 3)

## 5. Scopes and Permissions

Allowed scopes:

- `users.read`
- `users.write`
- `roles.read`
- `roles.write`
- `identity_tags.read`
- `identity_tags.write`
- `events.emit`

How scopes become active:

1. Plugin declares scopes in `plugin.json`.
2. Core filters against allowed scope set.
3. Runtime context enforces access with `require_scope` checks.

If a scope is missing, plugin receives a permission error.

## 6. Plugin Developer Contract

Plugin files:

- `nebula_core/plugins/<plugin_name>/plugin.py`
- `nebula_core/plugins/<plugin_name>/plugin.json`

`plugin.py` requirements:

- `PLUGIN_API_VERSION = "v1"`
- `create_plugin()` factory
- plugin object methods:
  - `initialize(context)`
  - `health()`
  - `sync_users(payload)`
  - `shutdown()`

`plugin.json` example:

```json
{
  "api_version": "v1",
  "version": "0.1.0",
  "description": "Example plugin",
  "scopes": [
    "users.read",
    "users.write",
    "identity_tags.write",
    "events.emit"
  ]
}
```

## 7. Context API (what plugin code can call)

Main helper methods (scope-guarded):

- `sync_user(...)`
- `list_users(...)`
- `set_identity_tag(...)`
- `list_identity_roles()`
- `upsert_identity_role(...)`
- `emit_event(name, payload)`

Event names are namespaced by manager as:
- `plugin.<plugin_name>.<event_name>`

## 8. Runtime Isolation

For `plugin_runtime_v2` manager does:

- spawns separate worker process
- uses Unix socket gRPC channel
- passes scoped token
- monitors process health
- enforces timeout and restart
- optional cgroup v2 limits:
  - memory
  - cpu quota
  - max processes
- reads `memory.events` to detect OOM kill

cgroup config keys:

- `cgroup_enabled`
- `cgroup_required`
- `cgroup_root`
- `cgroup_cpu_quota_us`
- `cgroup_cpu_period_us`
- `cgroup_pids_max`

## 9. Local developer workflow

1. Create plugin folder in `nebula_core/plugins/<name>/`.
2. Add `plugin.py` + `plugin.json`.
3. Rescan plugins:

```bash
curl -s -X POST -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" http://127.0.0.1:8000/system/plugins/rescan
```

4. Verify status:

```bash
curl -s -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" http://127.0.0.1:8000/system/plugins
```

5. Run sync:

```bash
curl -s -X POST -H "Content-Type: application/json" -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" -d '{"dry_run":true,"limit":10}' http://127.0.0.1:8000/system/plugins/sample_sync/sync-users
```

## 10. gRPC Contract Reference

Proto file:

- `nebula_core/grpc/plugin_api_v1.proto`

Service:

- `nebula.plugin.v1.PluginService`
  - `Health(google.protobuf.Empty) -> google.protobuf.Struct`
  - `SyncUsers(google.protobuf.Struct) -> google.protobuf.Struct`
