# Docker, Runtime, And Deployment Notes

## What Nebula Manages

Nebula is built around single-host Docker management.

The Docker layer currently supports:

- container deployment
- start/stop/restart/delete
- logs
- file listing and file reads
- file save back into the container
- command execution
- app-console input for supported profiles
- restart policy updates
- startup command and allowed port metadata
- presets

Most of this behavior is implemented in `nebula_core/services/docker_service.py`.

## Deployment Profiles And Workspace Logic

Nebula stores profile metadata and workspace-related rules for common container categories such as:

- `minecraft`
- `web`
- `python`
- `database`
- `steam`
- `generic`

Profiles influence:

- user shell policy
- whether app-console is supported
- which workspace roots are visible
- what tools the panel exposes

## Managed Workspaces

The service can create and track workspace directories under:

- `storage/container_workspaces`

Related metadata is stored in `container_storage`.

Nebula also keeps:

- workspace mount path
- explorer root
- console cwd
- disk quota metadata
- profile name
- whether the workspace is managed by Nebula

## Presets

Preset files live in:

- `containers/presets/*.json`

Each preset can include:

- deployment config
- descriptive metadata
- default role permission templates

Core can also save presets through the API.

## Runtime Service Model

The backend runtime is configured in:

- `nebula_core/serviceconfig.yaml`

Important sections:

- `services.heartbeat`
- `services.metrics`
- `mail`
- `plugins`

This file controls internal service intervals and plugin runtime behavior, not the FastAPI network binding itself.

## systemd Support

The most complete operational path is Core running as a `systemd` service.

Benefits:

- auto-start on boot
- easy restart/status/log access
- `Delegate=yes` for plugin cgroup v2 support

Service installation and control are handled by:

- `install/main.py`
- `corectl.sh`
- `install/modules/core_service.py`

## Docker Compose Status

There is a `docker-compose.yml` file in the repo, but at the moment it only contains a placeholder comment.

So today:

- there is no maintained compose stack in the repository
- there are no checked-in Dockerfiles for Core or GUI packaging
- documentation should point operators to Python-based startup or `systemd`

## Internal gRPC

Core also starts an internal observability gRPC server.

Default target:

- `127.0.0.1:50051`

GUI prefers this for telemetry when available and falls back to HTTP.

## Plugin Isolation And cgroup v2

For the process plugin runtime, Nebula can optionally use cgroup v2 for worker isolation.

Relevant config:

- `cgroup_enabled`
- `cgroup_required`
- `cgroup_root`
- `cgroup_cpu_quota_us`
- `cgroup_cpu_period_us`
- `cgroup_pids_max`

This is one of the more mature "core engineering" parts of the codebase and is worth preserving as a first-class deployment path.

## Practical Deployment Advice

- Keep Core bound to localhost and reverse-proxy it if needed.
- Do not expose Core directly to the public Internet.
- Set `NEBULA_SESSION_SECRET` and `NEBULA_INSTALLER_TOKEN` explicitly.
- Enable secure cookies in any non-local environment.
- Prefer `systemd` over ad-hoc background processes on Linux hosts.
- Treat Docker availability as a runtime dependency; many container APIs fail closed when Docker is not reachable.
