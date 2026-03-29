# Nebula Core Service Guide

## Why Use systemd

For Nebula today, `systemd` is the most complete service-management path on Linux.

It gives you:

- boot-time startup
- supervised restarts
- simple operational commands
- a clean place to enable plugin cgroup delegation

## Install Or Update Service

From the project root:

```bash
python3 install/main.py --core-service-install --core-service-name nebula-core
```

Or:

```bash
./corectl.sh install
```

## Daily Operations

```bash
./corectl.sh start
./corectl.sh stop
./corectl.sh restart
./corectl.sh status
./corectl.sh logs
```

Equivalent direct calls:

```bash
python3 install/main.py --core-service-action start --core-service-name nebula-core
python3 install/main.py --core-service-action restart --core-service-name nebula-core
python3 install/main.py --core-service-action status --core-service-name nebula-core
python3 install/main.py --core-service-action logs --core-service-name nebula-core --core-service-log-lines 200
```

## What The Installer Configures

The generated unit is designed around:

- `WorkingDirectory=<project_root>`
- `ExecStart=<project_root>/.venv/bin/python -m nebula_core`
- `Restart=on-failure`
- `Environment=ENV=production`
- `Delegate=yes`

`Delegate=yes` matters because the plugin process runtime can use cgroup v2 isolation beneath the service.

## Plugin cgroup v2 Support

If you want process-isolated plugins with cgroup limits, configure `nebula_core/serviceconfig.yaml`:

```yaml
plugins:
  enabled: true
  environment: "production"
  process_runtime_enabled: true
  in_process_enabled: false
  cgroup_enabled: true
  cgroup_required: true
  cgroup_root: "auto"
```

`cgroup_root: "auto"` means the plugin manager will try to place workers inside the delegated service subtree.

## Logs

Use:

```bash
./corectl.sh logs
```

Nebula also maintains:

- in-memory log history for `/logs/history`
- plugin runtime log files under the configured plugin log directory

## Recommended Host Layout

- Core bound to localhost
- GUI bound to localhost or reverse-proxied
- Docker daemon available locally
- explicit secrets via environment or `.env`

## Compose Note

Even though the repo contains `docker-compose.yml`, it is currently not a real deployment stack.

So for now, the documented supported paths are:

- direct Python startup for development
- `systemd` for Core on Linux
