# Plugin System And Plugin Manager API

This document explains the actual plugin architecture present in the repository today.

It covers:

- plugin discovery model
- runtime modes
- manifest and code contract
- scopes and permissions
- health and restart behavior
- HTTP management API

## 1. Plugin System Overview

Plugin code lives under:

- `nebula_core/plugins/<plugin_name>/plugin.py`
- `nebula_core/plugins/<plugin_name>/plugin.json`

Examples already in the repo:

- `nebula_core/plugins/sample_sync`
- `nebula_core/plugins/ad_migration_demo`

The plugin manager is implemented in:

- `nebula_core/core/plugin_manager.py`

The isolated worker runtime is implemented in:

- `nebula_core/core/plugin_runner.py`

## 2. Runtime Modes

Nebula supports two plugin execution styles.

### In-process mode

Characteristics:

- loaded directly into Core
- useful in development
- less isolated
- easier to debug quickly

### Process runtime

Characteristics:

- one subprocess per plugin
- gRPC over Unix socket
- scoped runtime token per plugin
- timeout and health supervision
- optional cgroup v2 isolation

This is the more serious runtime and the one the codebase is clearly evolving toward.

## 3. Runtime Configuration

Plugin runtime behavior is controlled in `nebula_core/serviceconfig.yaml`.

Important keys:

- `enabled`
- `environment`
- `in_process_enabled`
- `process_runtime_enabled`
- `scan_path`
- `init_timeout_sec`
- `default_timeout_sec`
- `max_timeout_sec`
- `call_timeout_sec`
- `memory_limit_mb`
- `cpu_time_limit_sec`
- `health_interval_sec`
- `max_restarts`
- `max_crashes`
- `timeout_restart_threshold`
- `health_restart_threshold`
- `runtime_socket_dir`
- `runtime_log_dir`
- `cgroup_enabled`
- `cgroup_required`
- `cgroup_root`
- `cgroup_cpu_quota_us`
- `cgroup_cpu_period_us`
- `cgroup_pids_max`
- `runner_command`

## 4. Manifest Contract

Example `plugin.json`:

```json
{
  "api_version": "v1",
  "version": "0.1.0",
  "description": "Example plugin",
  "scopes": [
    "users.read",
    "users.write",
    "identity_tags.write"
  ]
}
```

Important notes:

- `api_version` must match current plugin API version
- scopes are filtered against the allowed scope list
- plugin name is derived from directory name and must pass validation

## 5. Python Contract

Example plugin expectations:

```python
PLUGIN_API_VERSION = "v1"

def create_plugin():
    return MyPlugin()
```

The plugin object is expected to implement:

- `initialize(context)`
- `health()`
- `sync_users(payload)`
- `shutdown()`

The project formalizes this in `nebula_core/core/plugin_api_v1.py`.

## 6. Allowed Scopes

Currently allowed scopes are:

- `users.read`
- `users.write`
- `roles.read`
- `roles.write`
- `identity_tags.read`
- `identity_tags.write`
- `events.emit`

If a plugin asks for anything else, it is filtered out.

If plugin code later tries to perform an action without the needed scope, `PluginPermissionError` is raised.

## 7. Plugin Context API

The runtime passes a `PluginContext` to `initialize`.

Important helpers include:

- `require_scope(scope)`
- `log(level, message)`
- `sync_user(...)`
- `list_users(...)`
- `set_identity_tag(...)`
- `list_identity_roles()`
- `upsert_identity_role(...)`

These methods are important because they show what the plugin system is really for right now:

- user synchronization
- identity role and tag management
- event emission

## 8. Process Runtime Flow

For process plugins, the manager roughly does this:

1. validate plugin directory and manifest
2. compile Python files
3. spawn `nebula_core.core.plugin_runner`
4. create a Unix socket and per-plugin token
5. call plugin `initialize`
6. monitor health and call timeouts
7. restart on repeated failures

Worker behavior includes:

- manifest loading
- plugin import
- `create_plugin()` instance construction
- local method invocation
- gRPC exposure for `Health` and `SyncUsers`

## 9. Health, State, And Restart Model

Public states include:

- `initialized`
- `healthy`
- `degraded`
- `unresponsive`
- `crashed`
- `disabled`

Why states change:

- timeout spikes
- repeated health check failures
- worker crash
- crash budget exhaustion
- explicit stop/disable path

Counters tracked per plugin include:

- consecutive timeouts
- consecutive health failures
- consecutive crashes
- restart count

## 10. Resource Isolation

The worker applies process-level resource limits where possible:

- memory limit
- CPU time limit

With cgroup v2 enabled, the system can also manage:

- memory
- CPU quota
- process count
- OOM event detection

This is one of the stronger engineering areas in the current codebase.

## 11. HTTP Plugin Manager API

Base prefix:

- `/system/plugins`

Auth:

- staff session
- or internal `X-Nebula-Token`

### `GET /system/plugins`

Returns all discovered plugins and runtime state.

### `POST /system/plugins/rescan`

Rescans plugin directories and reinitializes runtime state as needed.

### `GET /system/plugins/{plugin_name}/health`

Requests a plugin health check through the manager.

### `POST /system/plugins/{plugin_name}/sync-users`

Triggers the plugin's `sync_users(payload)` method.

Typical body:

```json
{
  "dry_run": true,
  "users": [
    {
      "username": "john.doe",
      "db_name": "client_a.db",
      "role_tag": "developer",
      "email": "john@example.com",
      "is_active": true
    }
  ],
  "limit": 100
}
```

### `POST /system/plugins/{plugin_name}/action`

Manual control action.

Body:

```json
{
  "action": "restart"
}
```

Supported actions in the current API surface:

- `start`
- `stop`
- `restart`

### `GET /system/plugins/{plugin_name}/stats`

Returns runtime stats such as:

- process state
- PID
- memory stats when available
- cgroup/OOM-related data when available
- log path

### `GET /system/plugins/{plugin_name}/logs?tail=200`

Returns recent plugin log lines from the worker log file.

## 12. Practical Development Workflow

1. create `nebula_core/plugins/<name>/`
2. add `plugin.py`
3. add `plugin.json`
4. start Core
5. call plugin rescan
6. inspect plugin list, health, and logs

Example:

```bash
curl -X POST \
  -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" \
  http://127.0.0.1:8000/system/plugins/rescan
```

## 13. Current Scope Of The Plugin System

Today the plugin system is best suited for:

- user sync pipelines
- identity-role enrichment
- external directory integration
- automation that should be isolated from the main Core process

It is not yet a broad extension SDK for every GUI or container feature.

That narrower framing helps keep expectations realistic and matches the codebase much better.
