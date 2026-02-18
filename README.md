# Nebula Panel

Nebula Panel is a project-oriented infrastructure management platform for small and medium-sized companies.

It allows teams to deploy, organize, and delegate Docker-based environments using a secure, role-based access model, without the complexity of enterprise orchestration systems.

Nebula is not just a container GUI.  
It is a structured control layer for real-world infrastructure.

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

## License

See `LICENSE`.
