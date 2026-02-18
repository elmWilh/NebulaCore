![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Core%20API-009688?logo=fastapi&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-GUI-000000?logo=flask&logoColor=white)
![gRPC](https://img.shields.io/badge/gRPC-Plugin%20Bridge-244C5A?logo=grpc&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containers-2496ED?logo=docker&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Storage-003B57?logo=sqlite&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-Live%20Logs-FF6A00?logo=socketdotio&logoColor=white)
![RBAC](https://img.shields.io/badge/Auth-RBAC-5B4B8A)
![2FA](https://img.shields.io/badge/Security-TOTP%202FA-2E8B57)
![License: AGPLv3](https://img.shields.io/badge/License-AGPLv3-A42E2B)

# Nebula Panel

Nebula Panel is a project-oriented infrastructure management platform for small and medium-sized companies.

It allows teams to deploy, organize, and delegate Docker-based environments using a secure, role-based access model, without the complexity of enterprise orchestration systems.

Nebula is not just a container GUI.  
It is a structured control layer for real-world infrastructure.

## Screenshots

![Nebula Panel Dashboard](docs/images/demo.png)


## What Nebula Can Do Right Now

- Run a full Core + GUI stack:
  - `nebula_core` (FastAPI): infrastructure API, Docker operations, RBAC/session security, metrics, plugins.
  - `nebula_gui_flask` (Flask): web panel for admins and users.
- Manage container lifecycle:
  - deploy, start, stop, restart, delete;
  - view container logs;
  - read/edit selected runtime settings (with permission checks);
  - configure restart policy.
- Work with container presets:
  - built-in preset catalog (`containers/presets/*.json`);
  - create/update presets from API.
- Delegate access per container with role-aware policies:
  - shell access;
  - app console access;
  - file explorer access;
  - settings access and granular edit rights.
- Use in-container workspace tools:
  - list files;
  - read file content;
  - detect workspace roots.
- Operate user and identity management:
  - login/logout via signed session cookie;
  - per-user role tags (`identity_roles`, `user_identity_tags`);
  - user create/update/move/delete across client databases;
  - TOTP 2FA setup/confirm/disable.
- Observe system state:
  - host metrics (`/metrics/current`);
  - admin dashboard metrics with RAM/network/disks/container memory breakdown;
  - buffered logs and live log stream via WebSocket.
- Extend behavior with Plugin API v1:
  - list/rescan plugin modules;
  - plugin health checks;
  - trigger `sync-users`;
  - support in-process and gRPC plugin contract (`nebula_core/grpc/plugin_api_v1.proto`).

> [!IMPORTANT]
> Nebula Core (`:8000`) should not be exposed directly to the public Internet. Put it behind a reverse proxy, HTTPS, and firewall rules.

> [!IMPORTANT]
> Set strong secrets before production: `NEBULA_SESSION_SECRET`, `NEBULA_INSTALLER_TOKEN`, and secure cookie mode (`NEBULA_COOKIE_SECURE=true`).

> [!IMPORTANT]
> Nebula currently targets single-host Docker management. It is not a Kubernetes replacement and does not provide multi-node orchestration out of the box.

## Data Layout

- `storage/databases/system.db`: system users, admin accounts, identity roles, global mappings.
- `storage/databases/clients/*.db`: tenant/project user databases.
- `containers/presets/*.json`: container deployment templates.

## Quick Start (Ubuntu)

### 1. Install dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl
```

### 2. Prepare project env

```bash
export PROJECT_DIR=/opt/NebulaCore
cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r nebula_gui_flask/requirements.txt
```

### 3. Install Docker

Option A:

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

Option B:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
docker info
```

### 4. Start Core

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python -m nebula_core
```

### 5. Run initial admin setup

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

Then select `Run First-Time Setup / Create Admin`.

### 6. Start GUI (second terminal)

```bash
cd "$PROJECT_DIR/nebula_gui_flask"
source ../.venv/bin/activate
python app.py
```

Open `http://127.0.0.1:5000`.

## Main URLs

- GUI: `http://127.0.0.1:5000`
- Core API: `http://127.0.0.1:8000`
- Plugin API docs: `docs/PLUGIN_API.md`

## About Monolink Systems

Monolink Systems is an independent infrastructure software initiative focused on building structured, project-oriented control platforms for modern containerized environments.

## License & Copyright

- Copyright (c) 2026 Monolink Systems
- Nebula Open Source Edition (non-corporate)
- Licensed under AGPLv3 (see `LICENSE`)

Founded by GuFugu  
GitHub: https://github.com/elmWilh
