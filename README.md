# Nebula Panel

Nebula Panel is an infrastructure and container management platform with role-based access for administrators and regular users.

## Architecture

- `nebula_core` (FastAPI): API layer, user and role management, Docker orchestration, system and container metrics.
- `nebula_gui_flask` (Flask): web interface for authentication, user and container management, and monitoring.

## Key Features

- Administrator and user authentication.
- Container access split:
  - admins can view the full server and all containers;
  - users can only view containers assigned to them.
- Container lifecycle management:
  - deploy with progress and deployment logs;
  - start/stop/restart;
  - container log viewing;
  - container deletion with confirmation.
- User and role system (SQLite).
- Metrics:
  - admins see host-wide metrics;
  - users see aggregated metrics for their own containers.
- Baseline security restrictions on critical APIs (`users/roles/files/logs`).

## Data Storage

- `storage/databases/system.db` - system database (administrators, system permissions).
- `storage/databases/clients/*.db` - client user databases.

## Quick Start (Ubuntu)

### 1. Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl
```

### 2. Project Setup

```bash
export PROJECT_DIR=/opt/NebulaCore
cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r nebula_gui_flask/requirements.txt
```

### 3. Docker

Option A (project installer):

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

Option B (manual):

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

### 5. Initial Admin Setup

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

In the installer menu, choose:

- `Run First-Time Setup / Create Admin`

### 6. Start GUI

In a separate terminal:

```bash
cd "$PROJECT_DIR/nebula_gui_flask"
source ../.venv/bin/activate
python app.py
```

Open in browser:

- `http://127.0.0.1:5000`

## Main URLs

- GUI: `http://127.0.0.1:5000`
- Core API: `http://127.0.0.1:8000`

## Security (Production Recommendations)

- Do not expose Core directly to the public internet without a reverse proxy and firewall.
- Configure environment variables and secrets in `.env`:
  - `NEBULA_INSTALLER_TOKEN`
  - `NEBULA_CORS_ORIGINS`
  - `NEBULA_CORE_HOST`
  - `NEBULA_CORE_PORT`
  - `NEBULA_CORE_RELOAD=false`
- Use HTTPS at the external edge.
- Rotate administrator tokens and passwords regularly.

## Role Capabilities

### Administrator

- Manage users, roles, and container assignments.
- Full access to container operations.
- View host-wide system metrics.
- Access administrative logs.

### User

- View only assigned containers.
- Operate assigned containers (within granted interface permissions).
- View only their own aggregated metrics.

## Project Roadmap

Planned directions:

- Full authorization model with signed server-side sessions/JWT.
- Extended RBAC/ABAC model with fine-grained container operation permissions.
- Action audit trail (who/when/what changed).
- Built-in quota and policy controls for CPU/RAM/Storage per user and group.
- Multi-node/cluster support with real-time status.
- Improved observability layer (alerts, charts, retention, Prometheus export).
- Migration to a more fault-tolerant configuration backend.

## License

See `LICENSE`.
