# Nebula Core Service Automation

This guide sets up Nebula Core as a `systemd` service with fast terminal control.

## What you get

- Auto start on boot.
- Fast restart after code/config changes.
- `status`/`logs` from terminal.
- `Delegate=yes` in unit for plugin cgroup v2 integration.

## 1. Install/update service

Run from project root:

```bash
python3 install/main.py --core-service-install --core-service-name nebula-core
```

Or use helper script:

```bash
./corectl.sh install
```

## 2. Basic control

```bash
./corectl.sh start
./corectl.sh stop
./corectl.sh restart
./corectl.sh status
./corectl.sh logs
```

Without helper:

```bash
python3 install/main.py --core-service-action restart --core-service-name nebula-core
python3 install/main.py --core-service-action status --core-service-name nebula-core
python3 install/main.py --core-service-action logs --core-service-name nebula-core --core-service-log-lines 200
```

## 3. Interactive installer mode

```bash
python3 install/main.py
```

Menu entries:

- `Install / Update Core systemd service`
- `Manage Core service (start/stop/restart/status/logs)`

## 4. Unit details

The installer writes `/etc/systemd/system/nebula-core.service` and configures:

- `WorkingDirectory=<project_root>`
- `ExecStart=<project_root>/.venv/bin/python -m nebula_core`
- `Environment=ENV=production`
- `Delegate=yes`
- `Restart=on-failure`
- output logs in `storage/logs/core.stdout.log` and `storage/logs/core.stderr.log`

## 4.1 Enable cgroup v2 backend for plugins

In `nebula_core/serviceconfig.yaml` under `plugins`:

```yaml
cgroup_enabled: true
cgroup_required: true
cgroup_root: "auto"
```

`auto` uses the delegated service cgroup subtree (requires `Delegate=yes` in systemd unit).

## 5. Typical dev cycle

```bash
# edit code
./corectl.sh restart
./corectl.sh status
./corectl.sh logs
```
