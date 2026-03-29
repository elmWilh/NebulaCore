# NebulaCore Architecture

## Overview

NebulaCore is organized as a split control plane:

- `nebula_core`: backend control layer
- `nebula_gui_flask`: browser-facing panel
- `install/`: operational bootstrap and service automation

The GUI is not just a static frontend. It actively:

- authenticates users against Core
- stores the Core session cookie in Flask session state
- proxies most API calls to Core
- uses Core gRPC observability when available

## Main Components

### 1. FastAPI Core

`nebula_core/main.py` starts:

- FastAPI app
- CORS middleware
- HTTP request logging middleware
- runtime initialization on startup
- internal gRPC observability server

### 2. Runtime Layer

`nebula_core/core/runtime.py` is the central lifecycle manager.

It owns:

- event bus
- background services
- plugin manager
- startup/shutdown coordination

During startup it:

1. loads `nebula_core/serviceconfig.yaml`
2. registers modules
3. initializes plugins
4. starts heartbeat, file, and metrics services

### 3. Docker Service

`nebula_core/services/docker_service.py` is the biggest operational service and effectively acts as the orchestration backbone.

It provides:

- Docker client access
- container deploy/start/stop/restart/delete
- file explorer and file read/write helpers
- container settings and restart-policy handling
- preset management
- workspace metadata
- role capability resolution per container
- container audit logging

### 4. Plugin Manager

`nebula_core/core/plugin_manager.py` discovers and supervises plugins.

Modes:

- in-process mode for development
- process runtime for stronger isolation

The process runtime uses:

- `nebula_core/core/plugin_runner.py`
- gRPC over Unix sockets
- runtime-scoped auth tokens
- restart and health policy

### 5. Flask GUI

`nebula_gui_flask/app.py` provides:

- browser session handling
- rate limiting for GUI login flow
- Socket.IO dashboard streams
- CSP setup
- page rendering
- route registration

`nebula_gui_flask/core/bridge.py` is the bridge between GUI and Core.

## Request Flow

### Admin login

1. Browser posts credentials to Flask GUI.
2. GUI sends them to Core `/system/internal/core/login`.
3. Core validates password and optional TOTP.
4. Core returns `nebula_session` cookie.
5. GUI stores user context in Flask session and keeps the Core cookie for future proxy calls.

### User login

1. Browser posts credentials to GUI.
2. GUI calls Core `/users/login?db_name=...`.
3. Core resolves the correct DB and validates password and optional TOTP.
4. GUI asks Core for `/users/identity-tag` to resolve the user's role tag.
5. GUI stores user metadata and session linkage locally.

### Container workspace operation

1. Browser calls Flask API route.
2. Flask proxies to Core container endpoint.
3. Core resolves session user and DB.
4. Core checks direct container assignment.
5. Core computes effective role permissions for the container.
6. Docker service executes or rejects the action.
7. Core writes an audit event where applicable.

## Data Layout

### `system.db`

Holds global and administrative state, including:

- system users and staff users
- `identity_roles`
- `user_identity_tags`
- container access metadata
- container role policies
- projects
- password reset codes
- audit metadata

### client databases

Each DB under `storage/databases/clients/*.db` acts as an isolated user store for non-staff users.

These DBs may contain classic role tables such as:

- `roles`
- `permissions`
- `user_roles`
- `role_permissions`

In practice, the panel currently relies more heavily on global `role_tag` and container policy matrices than on those classic tables.

## Observability

There are two observability paths:

- HTTP metrics and log APIs from Core
- internal gRPC service in `nebula_core/internal_grpc.py`

The GUI prefers gRPC for fast internal dashboard reads and falls back to HTTP when needed.

## Current Constraints

- primary target is single-host Docker management
- no complete `docker-compose.yml` deployment recipe yet
- no multi-node orchestration layer
- some modules in navigation are still placeholders

## Recommended Reading Order

- [RBAC model](./RBAC_MODEL.md)
- [Plugin system](./PLUGIN_MANAGER_API.md)
- [GUI, i18n, and themes](./GUI_I18N_THEMES.md)
- [Docker runtime notes](./DOCKER_RUNTIME.md)
- [API reference](./API_DOCS.md)
