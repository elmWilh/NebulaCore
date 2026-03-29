# Nebula Core API Reference

This document describes the HTTP and WebSocket surfaces that are actually wired into `nebula_core/api/__init__.py`.

For installer-specific endpoints, also see [CORE_INSTALL_API.md](./CORE_INSTALL_API.md).
For plugin architecture, also see [PLUGIN_MANAGER_API.md](./PLUGIN_MANAGER_API.md).

## Authentication

Nebula uses two main auth modes:

- signed session cookie: `nebula_session`
- internal header token: `X-Nebula-Token`

### Session cookie

Used by normal browser and GUI traffic.

Resolved session context:

- `username`
- `db_name`
- `is_staff`

### Internal token

Used by installer and trusted automation.

Header:

```http
X-Nebula-Token: <NEBULA_INSTALLER_TOKEN>
```

### Authorization patterns you will see

- public/no-op checks
- authenticated user
- staff-only
- staff-or-internal
- assigned-container access

## API Groups

- `/system`
- `/auth`
- `/users`
- `/roles`
- `/metrics`
- `/logs`
- `/containers`
- `/system/plugins`
- `/projects`
- `/system/internal/core`

---

## `/system`

### `GET /system/status`

Returns overall Core status and host system info.

Typical use:

- bridge startup probe
- health checks
- basic host telemetry

### `GET /system/lookup?username=<name>`

Auth:

- staff session or internal token

Searches for a username across `system.db` and client DBs and returns where the user lives.

---

## `/auth`

### `GET /auth/check`

Minimal auth health endpoint.

Returns:

```json
{ "auth": "ok" }
```

---

## `/users`

### `POST /users/login?db_name=<db|auto>`

Form fields:

- `username`
- `password`
- `otp` optional

Notes:

- supports login against `system.db`
- can search across client DBs when `db_name=auto`
- returns `nebula_session` cookie
- enforces rate limiting
- may require 2FA

### `POST /users/logout`

Deletes `nebula_session`.

### `GET /users/databases`

Auth:

- staff or internal

Returns discovered client databases.

### `GET /users/list?db_name=<client.db>`

Auth:

- staff or internal

Lists users from a client DB and overlays their global `role_tag`.

### `GET /users/detail/{username}?db_name=<db>`

Auth:

- self or staff depending on target

Returns a user profile payload including role tag context.

### `GET /users/identity-tag?username=<name>&db_name=<db>`

Auth:

- self for own identity in same DB
- or staff

Returns current global `role_tag` from `user_identity_tags`.

### `POST /users/identity-tag`

Auth:

- staff only

Sets or updates a user's identity tag.

### `POST /users/create?db_name=<client.db>`

Auth:

- staff or internal

Creates a user in a client DB and writes matching identity metadata into `system.db`.

Body fields:

- `username`
- `email`
- `password`
- `role_tag`

### `POST /users/update`

Auth:

- staff or internal

Supports:

- rename
- email update
- password update
- active state update
- move user between client DBs
- role tag update

### `DELETE /users/terminate?username=<name>&db_name=<client.db>`

Auth:

- staff or internal

Deletes a user from a client DB.

### Password reset flow

#### `POST /users/password-reset/request`

Form fields:

- `username`
- `db_name` optional

Behavior:

- always returns a generic response
- sends a 6-digit code by email if the account is valid and active

#### `POST /users/password-reset/confirm`

Form fields:

- `username`
- `code`
- `new_password`
- `db_name` optional

Behavior:

- validates the latest non-consumed code
- updates password
- clears `password_set_required`

### 2FA

#### `GET /users/2fa/status`

Returns whether TOTP is enabled for the current session user.

#### `POST /users/2fa/setup`

Returns:

- generated secret
- `otpauth_uri`

#### `POST /users/2fa/confirm`

Form field:

- `code`

Enables 2FA after validating TOTP.

#### `POST /users/2fa/disable`

Form field:

- `code`

Disables 2FA after validating TOTP.

---

## `/roles`

### `GET /roles/list`

Auth:

- any authenticated session

Returns the global identity-role catalog from `identity_roles`.

### `POST /roles/create`

Auth:

- staff or internal

Upserts a role in `identity_roles`.

Body fields:

- `name`
- `description`
- `is_staff`

### `POST /roles/assign`

Auth:

- staff or internal

Legacy endpoint for client-DB `roles` / `user_roles`.

Important:

- this is not the primary modern container authorization path
- current panel behavior relies more on `role_tag` and container permission matrices

---

## `/metrics`

### `GET /metrics/current`

Returns current host metrics snapshot.

### `GET /metrics/admin/dashboard`

Auth:

- staff only

Rich admin dashboard payload with:

- overview cards
- RAM history
- network history
- optional container memory breakdown
- optional disk list

Query flags:

- `include_containers`
- `include_disks`

### `GET /metrics/admin/telemetry`

Auth:

- staff only

Lighter-weight telemetry payload for fast refresh paths.

---

## `/logs`

### `GET /logs/history?limit=<n>`

Auth:

- staff session or internal token

Returns the in-memory log ring buffer.

### `WS /logs/stream`

Auth:

- staff session or internal token

Behavior:

- sends initial history snapshot
- then streams new log entries

---

## `/containers`

This is the largest API area in the project.

### Listing and overview

- `GET /containers/list`
- `GET /containers/summary`
- `GET /containers/detail/{container_id}`
- `GET /containers/profile/{container_id}`

`/profile/{container_id}` is especially important for GUI behavior because it merges:

- container profile metadata
- effective permissions
- role tag
- whether shell/console/settings/explorer should be shown

### Workspace and shell operations

- `POST /containers/exec/{container_id}`
- `POST /containers/console-send/{container_id}`
- `GET /containers/files/{container_id}?path=/...`
- `GET /containers/workspace-roots/{container_id}`
- `GET /containers/file-content/{container_id}?path=/...`
- `GET /containers/download-file/{container_id}?path=/...`
- `POST /containers/save-file/{container_id}?path=/...`

These routes are protected by effective container permissions such as:

- `allow_shell`
- `allow_console`
- `allow_explorer`
- `allow_edit_files`

### Audit and logs

- `GET /containers/audit/{container_id}`
- `GET /containers/logs/{container_id}`

Many sensitive workspace actions write audit records into `container_audit_log`.

### Settings and lifecycle

- `GET /containers/settings/{container_id}`
- `POST /containers/settings/{container_id}`
- `GET /containers/restart-policy/{container_id}`
- `POST /containers/restart-policy/{container_id}`
- `POST /containers/restart/{container_id}`
- `POST /containers/start/{container_id}`
- `POST /containers/stop/{container_id}`
- `POST /containers/delete/{container_id}`

These routes typically require:

- container access
- plus `allow_settings`
- plus more specific edit flags for some operations

### Deployment and presets

- `POST /containers/deploy`
- `GET /containers/presets`
- `GET /containers/presets/{preset_name}`
- `POST /containers/presets`

Deployment is staff-only.

Presets are file-backed and live under `containers/presets`.

### Container permission management

- `GET /containers/permissions/{container_id}`
- `POST /containers/permissions/{container_id}`

These are key endpoints for the modern RBAC model.

Returned data includes:

- effective permissions for the current user
- role policy matrix
- user assignments
- resolved role tag

Update body typically contains:

- `role_policies`
- `user_assignments`

---

## `/system/plugins`

Auth:

- staff or internal token

Routes:

- `GET /system/plugins`
- `POST /system/plugins/rescan`
- `GET /system/plugins/{plugin_name}/health`
- `POST /system/plugins/{plugin_name}/sync-users`
- `POST /system/plugins/{plugin_name}/action`
- `GET /system/plugins/{plugin_name}/stats`
- `GET /system/plugins/{plugin_name}/logs?tail=<n>`

See full plugin documentation in [PLUGIN_MANAGER_API.md](./PLUGIN_MANAGER_API.md).

---

## `/projects`

Auth model:

- list is available to any authenticated user, but results are filtered by visible containers
- mutating routes are staff-only

Routes:

- `GET /projects/health`
- `GET /projects`
- `GET /projects/active`
- `GET /projects/containers/available`
- `POST /projects`
- `POST /projects/{project_id}`
- `POST /projects/{project_id}/archive`
- `POST /projects/{project_id}/restore`
- `POST /projects/{project_id}/containers/link`
- `POST /projects/{project_id}/containers/unlink`
- `POST /projects/link-container-bulk`

Project data lives in `system.db` and maps projects to container IDs.

---

## `/system/internal/core`

This group is intended for installer/bootstrap automation and trusted administrative tooling.

Auth:

- internal token only

Routes:

- `GET /system/internal/core/login`
- `POST /system/internal/core/login`
- `POST /system/internal/core/init-admin`
- `POST /system/internal/core/modify-admin`
- `GET /system/internal/core/status`
- `POST /system/internal/core/mail/test`

See [CORE_INSTALL_API.md](./CORE_INSTALL_API.md) for operational details.

---

## Internal gRPC Observability

Besides HTTP, Core also exposes internal observability methods over gRPC:

- `GetCurrentMetrics`
- `GetLogHistory`

Default bind target:

- `127.0.0.1:50051`

The Flask GUI prefers this channel when available.

## Notes For API Consumers

- Many write endpoints accept loose JSON objects rather than strict versioned schemas.
- Several routes are optimized for Nebula GUI expectations rather than third-party public API stability.
- The project is still pre-alpha, so treat response formats as evolving.
- For automation, prefer the internal token only on trusted local paths.
