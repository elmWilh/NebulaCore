# Core Install And Bootstrap API

This document covers the parts of Nebula that are used to install, initialize, and operate the Core service.

It spans two surfaces:

- installer CLI in `install/main.py`
- internal bootstrap endpoints under `/system/internal/core`

## 1. Installer CLI

Main entrypoint:

```bash
python3 install/main.py
```

The interactive installer currently provides:

- guided full install for Core + GUI
- first-time admin setup
- system status checks
- Docker install/start helper
- Core + GUI `systemd` install/update
- service control

Fast path:

```bash
./panelctl.sh install
```

## 2. Health Check Mode

```bash
python3 install/main.py --check
```

Exit codes:

- `0`: `.env` and `storage/databases/system.db` exist
- `2`: setup artifacts are missing

## 3. Core systemd Install

```bash
python3 install/main.py --core-service-install --core-service-name nebula-core
python3 install/main.py --gui-service-install --gui-service-name nebula-gui
```

Optional args:

- `--core-service-user <user>`
- `--core-service-project-dir <path>`
- `--core-service-env <development|production>`

Behavior:

- writes `/etc/systemd/system/<service>.service`
- reloads `systemd`
- enables the service

Recommended wrappers:

```bash
./panelctl.sh install
./corectl.sh install
```

## 4. Core Service Control

```bash
python3 install/main.py --core-service-action restart --core-service-name nebula-core
python3 install/main.py --core-service-action status --core-service-name nebula-core
python3 install/main.py --core-service-action logs --core-service-name nebula-core --core-service-log-lines 200
python3 install/main.py --gui-service-action restart --gui-service-name nebula-gui
python3 install/main.py --gui-service-action status --gui-service-name nebula-gui
python3 install/main.py --gui-service-action logs --gui-service-name nebula-gui --gui-service-log-lines 200
```

Wrapper shortcuts:

```bash
./panelctl.sh start
./panelctl.sh stop
./panelctl.sh restart
./panelctl.sh status
./panelctl.sh logs
./corectl.sh start
./corectl.sh stop
./corectl.sh restart
./corectl.sh status
./corectl.sh logs
```

## 5. Internal Bootstrap HTTP API

Base prefix:

- `/system/internal/core`

Authentication:

```http
X-Nebula-Token: <NEBULA_INSTALLER_TOKEN>
```

Important note:

- `admin.py` still contains a development fallback token string, but production docs should always assume you explicitly set `NEBULA_INSTALLER_TOKEN`.

## 6. Endpoints

### `GET /system/internal/core/status`

Returns bootstrap status data.

Example:

```json
{
  "database": "system.db",
  "active_admins": 1
}
```

### `POST /system/internal/core/init-admin`

Creates the first staff admin if none exists yet.

Request body:

```json
{
  "username": "master_admin",
  "password": "StrongPassword123!",
  "security_clearance": 10
}
```

Responses:

- success: `{ "status": "success" }`
- `409`: already initialized

### `POST /system/internal/core/modify-admin`

Updates an existing staff user.

Query params:

- `target_username`

Request body:

```json
{
  "new_password": "AnotherStrongPassword123!",
  "is_active": true
}
```

### `POST /system/internal/core/login`

Form-based login for the GUI admin flow.

Form fields:

- `admin_id`
- `secure_key`
- `otp` optional unless 2FA is enabled

Response:

- sets `nebula_session` cookie
- returns authorization payload

### `POST /system/internal/core/mail/test`

Sends a test email using the configured mailer.

Request body:

```json
{
  "email": "operator@example.com"
}
```

## 7. Docker Helper Scope

The installer also includes helper logic for:

- detecting whether Docker is installed
- installing Docker with the official convenience script
- starting and enabling Docker through `systemctl`
- adding the current user to the `docker` group

This is useful for local and lab setups, but still fairly operator-driven rather than fully declarative.

## 8. Recommended Production Path

1. run `./panelctl.sh install`
2. let the installer prepare `.venv`, dependencies, `.env`, Docker checks, and services
3. create the first admin when prompted
4. open the GUI on `http://127.0.0.1:5000`
5. optionally place the GUI behind a reverse proxy later

## 9. Related Docs

- [Core service automation](./CORE_SERVICE.md)
- [Docker and runtime notes](./DOCKER_RUNTIME.md)
- [Core API reference](./API_DOCS.md)
