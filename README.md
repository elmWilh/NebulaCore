# Nebula Panel

Nebula Panel is an infrastructure management panel split into:

- `nebula_core` (FastAPI): core, API, users, roles, access control, Docker integration.
- `nebula_gui_flask` (Flask): web interface, login, user and container management.

## Short project and core overview

- Core runs as `python -m nebula_core` and serves API on `127.0.0.1:8000`.
- GUI runs as `python app.py` from `nebula_gui_flask` and serves on `127.0.0.1:5000`.
- Data is stored in SQLite:
- `storage/databases/system.db` - system database (admins, system-level access).
- `storage/databases/clients/*.db` - client user databases.
- GUI auto-detects Core on ports `8000`, `8080`, `5000`.

## Full panel installation on Ubuntu

### 1. Required dependencies

`curl` is mandatory.

```bash
sudo apt update
sudo apt install -y curl
sudo apt install -y python3 python3-venv python3-pip
```

### 2. Create group and system user for the panel

```bash
sudo groupadd --force nebulapanel
sudo useradd -m -s /bin/bash -g nebulapanel -G sudo nebulapanel
sudo passwd nebulapanel
```

If the user already exists:

```bash
sudo usermod -aG nebulapanel <your_user>
```

### 3. Grant access to the project folder

```bash
sudo chown -R nebulapanel:nebulapanel /home/gufugu/Projects/NebulaCore
sudo find /home/gufugu/Projects/NebulaCore -type d -exec chmod 770 {} \;
sudo find /home/gufugu/Projects/NebulaCore -type f -exec chmod 660 {} \;
sudo chmod +x /home/gufugu/Projects/NebulaCore/startcore.sh
```

### 4. Install Python project dependencies

```bash
cd /home/gufugu/Projects/NebulaCore
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r nebula_gui_flask/requirements.txt
```

### 5. Install Docker and grant user access

Option A (via built-in Nebula installer):

```bash
cd /home/gufugu/Projects/NebulaCore
source .venv/bin/activate
python install/main.py
```

In the menu, select `3` (`Install / Start Docker Daemon`).

Option B (manual):

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo systemctl enable --now docker
sudo usermod -aG docker nebulapanel
newgrp docker
docker info
```

### 6. Run Nebula Core

```bash
cd /home/gufugu/Projects/NebulaCore
source .venv/bin/activate
python -m nebula_core
```

Or run as a specific user:

```bash
./startcore.sh nebulapanel
```

### 7. Initial setup (create first administrator)

Core must already be running.

```bash
cd /home/gufugu/Projects/NebulaCore
source .venv/bin/activate
python install/main.py
```

In the menu:

- `1` - `Run First-Time Setup / Create Admin`
- enter `Username` and `Password`

Important:

- admin password is validated by Core (minimum 12 characters);
- if needed, generate installer master key (`Generate Installer Master Key`).

### 8. Run panel GUI

In a separate terminal:

```bash
cd /home/gufugu/Projects/NebulaCore/nebula_gui_flask
source ../.venv/bin/activate
python app.py
```

Open in browser:

- `http://127.0.0.1:5000`

## How to create a new user

### Via GUI (recommended)

1. Login as administrator.
2. Open `Users` -> `Add User`.
3. Select `Database`.
4. Enter `Username`, `Password`, `Role` (`staff` or `user`).
5. Save.

### Via API

```bash
curl -X POST "http://127.0.0.1:8000/users/create?db_name=client_a.db" \
  -H "Content-Type: application/json" \
  -d '{"username":"user1","password":"StrongPassword123!","is_staff":false}'
```

## How to grant permissions, create groups, and set access

### 1. Create role in a database

```bash
curl -X POST "http://127.0.0.1:8000/roles/create?db_name=client_a.db&name=DEVOPS"
```

### 2. Assign role to a user

```bash
curl -X POST "http://127.0.0.1:8000/roles/assign?db_name=client_a.db&username=user1&role_name=DEVOPS"
```

### 3. Create Linux system group for the project (if separate group is needed)

```bash
sudo groupadd --force nebula-project
sudo usermod -aG nebula-project nebulapanel
```

### 4. Grant group access to the project

```bash
sudo chgrp -R nebula-project /home/gufugu/Projects/NebulaCore
sudo chmod -R 770 /home/gufugu/Projects/NebulaCore
```

### 5. Grant Docker access

```bash
sudo usermod -aG docker nebulapanel
newgrp docker
docker ps
```

If the user is not in the `docker` group, container operations from Core may fail with `permission denied`.

## Post-install verification

```bash
cd /home/gufugu/Projects/NebulaCore
source .venv/bin/activate
python -m nebula_core
```

In another terminal:

```bash
cd /home/gufugu/Projects/NebulaCore/nebula_gui_flask
source ../.venv/bin/activate
python app.py
```

Checks:

- GUI opens at `http://127.0.0.1:5000`.
- Admin login works.
- User creation works.
- `docker info` and `docker ps` run without `sudo` for the user running Core.
