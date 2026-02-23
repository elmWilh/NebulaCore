# Core Install API

This document describes how Nebula Core is installed, initialized, and controlled through terminal automation and internal bootstrap endpoints.

## 1. Installer CLI API

Installer entrypoint:

- `python3 install/main.py`

### 1.1 Health/check mode

- Command: `python3 install/main.py --check`
- Exit codes:
  - `0`: `.env` and `storage/databases/system.db` exist
  - `2`: setup artifacts missing

### 1.2 Core service install/update (systemd)

- Command:

```bash
python3 install/main.py --core-service-install --core-service-name nebula-core
```

- Optional args:
  - `--core-service-user <user>`
  - `--core-service-project-dir <path>`
  - `--core-service-env <development|production>`

Behavior:
- writes `/etc/systemd/system/<service>.service`
- runs `systemctl daemon-reload`
- runs `systemctl enable <service>`

### 1.3 Core service action API

- Command:

```bash
python3 install/main.py --core-service-action <start|stop|restart|status|logs|enable|disable> --core-service-name nebula-core
```

- Log tail size:
  - `--core-service-log-lines 200`

### 1.4 Convenience wrapper

- Script: `./corectl.sh`
- Commands:
  - `./corectl.sh install`
  - `./corectl.sh start`
  - `./corectl.sh stop`
  - `./corectl.sh restart`
  - `./corectl.sh status`
  - `./corectl.sh logs`

## 2. Internal Bootstrap HTTP API

Base prefix:

- `/system/internal/core`

Auth model:
- header `X-Nebula-Token: <NEBULA_INSTALLER_TOKEN>`
- fallback default in development environments may be `LOCAL_DEV_KEY_2026`

Source: `nebula_core/api/admin.py`.

### 2.1 GET `/system/internal/core/status`

Purpose:
- installer health for system DB and admin count.

Response example:

```json
{
  "database": "system.db",
  "active_admins": 1
}
```

### 2.2 POST `/system/internal/core/init-admin`

Purpose:
- one-time first admin creation.

Request:

```json
{
  "username": "master_admin",
  "password": "StrongPassword123!",
  "security_clearance": 10
}
```

Response:

```json
{ "status": "success" }
```

Errors:
- `409 Initialized` if staff admin already exists.
- `403 Forbidden` if token invalid.

### 2.3 POST `/system/internal/core/modify-admin`

Purpose:
- update admin password or active state.

Notes:
- endpoint expects `target_username` query parameter.

Request body:

```json
{
  "new_password": "AnotherStrongPassword123!",
  "is_active": true
}
```

## 3. Production install profile (recommended)

1. Create venv and install dependencies.
2. Install systemd service via installer CLI.
3. Set `plugins.environment: "production"` in `nebula_core/serviceconfig.yaml`.
4. Enable process plugin runtime and cgroup backend.
5. Restart core service.

Suggested plugin runtime settings:

```yaml
plugins:
  process_runtime_enabled: true
  in_process_enabled: false
  cgroup_enabled: true
  cgroup_required: true
```

## 4. Operational commands

```bash
./corectl.sh restart
./corectl.sh status
./corectl.sh logs
```

For detailed systemd behavior, see `docs/CORE_SERVICE.md`.
