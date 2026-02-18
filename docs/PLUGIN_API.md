# Plugin API (plugin_api_v1)

This document describes the plugin system API available in Nebula Core.

## Overview

Plugin management endpoints are exposed by Core under `/system/plugins`.

Access policy:
- Staff session cookie (`nebula_session`) OR
- Internal token header: `X-Nebula-Token: <NEBULA_INSTALLER_TOKEN>`

## Base Endpoints

### 1. List plugins

- Method: `GET`
- URL: `/system/plugins`
- Response:

```json
{
  "plugins": [
    {
      "name": "sample_sync",
      "source": "in_process",
      "api_version": "v1",
      "version": "0.1.0",
      "description": "Sample user sync plugin for plugin_api_v1",
      "scopes": ["users.read", "users.write"],
      "status": "initialized",
      "message": "initialized",
      "error": "",
      "initialized_at": 1739980000.12,
      "updated_at": 1739980000.12
    }
  ]
}
```

### 2. Rescan plugins

- Method: `POST`
- URL: `/system/plugins/rescan`
- Purpose:
  - Rescan plugin directories
  - Compile and reload plugins
  - Refresh external gRPC plugin bindings

### 3. Plugin health

- Method: `GET`
- URL: `/system/plugins/{plugin_name}/health`
- Response:

```json
{
  "plugin": "sample_sync",
  "health": {
    "status": "ok"
  }
}
```

### 4. Trigger user sync

- Method: `POST`
- URL: `/system/plugins/{plugin_name}/sync-users`
- Body:

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
  "limit": 0
}
```

- Response:

```json
{
  "plugin": "sample_sync",
  "result": {
    "status": "dry_run",
    "count": 1,
    "items": [
      {
        "action": "would_sync",
        "username": "john.doe",
        "db_name": "system.db",
        "role_tag": "developer"
      }
    ]
  }
}
```

## Scope Model

Allowed scopes in v1:
- `users.read`
- `users.write`
- `roles.read`
- `roles.write`
- `identity_tags.read`
- `identity_tags.write`
- `events.emit`

Plugins receive only the scopes declared in `plugin.json` and allowed by Core.

## Plugin Contract (in-process)

Each plugin should provide:

- `PLUGIN_API_VERSION = "v1"`
- `create_plugin()` factory
- async methods:
  - `initialize(context)`
  - `health()`
  - `sync_users(payload)`
  - `shutdown()`

## Plugin Context Capabilities

Scope-guarded context helpers:
- `sync_user(...)`
- `list_users(...)`
- `set_identity_tag(...)`
- `list_identity_roles()`
- `upsert_identity_role(...)`
- `emit_event(name, payload)`

Event names are automatically namespaced:
- `plugin.<plugin_name>.<event_name>`

## Security Notes

- Plugin load failures are isolated per plugin.
- Plugin code is syntax-compiled before import.
- Runtime calls are timeout-limited.
- API access is restricted to staff/internal token.
- For external plugins (gRPC), remote endpoints are blocked by default unless explicitly enabled in config.

## gRPC External Plugin Contract

Proto file:
- `nebula_core/grpc/plugin_api_v1.proto`

Service:
- `nebula.plugin.v1.PluginService`
  - `Health(google.protobuf.Empty) -> google.protobuf.Struct`
  - `SyncUsers(google.protobuf.Struct) -> google.protobuf.Struct`

## Quick cURL examples

```bash
curl -s -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" \
  http://127.0.0.1:8000/system/plugins
```

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -H "X-Nebula-Token: $NEBULA_INSTALLER_TOKEN" \
  -d '{"dry_run":true}' \
  http://127.0.0.1:8000/system/plugins/sample_sync/sync-users
```
