# Nebula Panel: Installation & Configuration Guide

Nebula Panel is a dual-component infrastructure management suite designed for security and scalability.

### System Architecture

* **`nebula_core` (FastAPI):** The brain of the operation. Handles the API, RBAC (Role-Based Access Control), and direct Docker socket integration. Runs on port `8000`.
* **`nebula_gui_flask` (Flask):** The visual interface. Handles user sessions and provides a dashboard for container management. Runs on port `5000`.
* **Storage:** Distributed SQLite architecture.
* `system.db`: Global admin and system settings.
* `clients/*.db`: Isolated databases for specific client environments.



---

## Phase 1: Environment Preparation

### 1. System Dependencies

Ensure your Ubuntu system is up to date and has the necessary runtimes.

```bash
sudo apt update && sudo apt install -y curl python3 python3-venv python3-pip

```

### 2. Service User Setup

Create a dedicated system user to run the panel securely.

```bash
# Create the group and user
sudo groupadd --force nebulapanel
sudo useradd -m -s /bin/bash -g nebulapanel -G sudo nebulapanel

# Set a password for the service user
sudo passwd nebulapanel

```

### 3. Directory Permissions

Replace `/path/to/NebulaCore` with your actual installation directory.

```bash
sudo chown -R nebulapanel:nebulapanel /path/to/NebulaCore
sudo find /path/to/NebulaCore -type d -exec chmod 770 {} \;
sudo find /path/to/NebulaCore -type f -exec chmod 660 {} \;
sudo chmod +x /path/to/NebulaCore/startcore.sh

```

---

## Phase 2: Installation

### 4. Python Environment

Set up the virtual environment and install dependencies for both Core and GUI.

```bash
cd /path/to/NebulaCore
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install -r nebula_gui_flask/requirements.txt

```

### 5. Docker Integration

Nebula requires Docker access to manage containers. You can install it via the built-in script or manually.

| Method | Command |
| --- | --- |
| **Auto (Recommended)** | `python install/main.py` -> Select Option `3` |
| **Manual** | `curl -fsSL https://get.docker.com |

**Crucial:** Add the panel user to the Docker group:

```bash
sudo usermod -aG docker nebulapanel
newgrp docker

```

---

## Phase 3: Initialization & Launch

### 6. Start the Core Service

The Core must be running before the GUI can function.

```bash
# Option A: Direct
python -m nebula_core

# Option B: Via helper script
./startcore.sh nebulapanel

```

### 7. First-Time Setup (Admin Creation)

With the Core running in one terminal, open another to create your root administrator.

1. Run the installer: `python install/main.py`
2. Select **Option 1**: `Run First-Time Setup / Create Admin`.
3. **Note:** Passwords must be at least **12 characters** long.

### 8. Start the Web GUI

```bash
cd /path/to/NebulaCore/nebula_gui_flask
source ../.venv/bin/activate
python app.py

```

Access the panel at: **`http://127.0.0.1:5000`**

---

## User & Access Management

### Creating Users

| Method | Steps |
| --- | --- |
| **Web UI** | Go to `Users` -> `Add User` -> Select DB -> Save. |
| **API (curl)** | `curl -X POST "http://localhost:8000/users/create?db_name=client.db" -H "Content-Type: application/json" -d '{"username":"dev_user","password":"StrongPassword123!","is_staff":false}'` |

### Role Assignment (RBAC)

To grant specific permissions via the API:

1. **Create Role:**
`curl -X POST "http://localhost:8000/roles/create?db_name=client.db&name=DEVOPS"`
2. **Assign to User:**
`curl -X POST "http://localhost:8000/roles/assign?db_name=client.db&username=dev_user&role_name=DEVOPS"`

---

## Post-Installation Checklist

* [ ] GUI loads at port `5000`.
* [ ] Admin login successful.
* [ ] `docker ps` runs without `sudo` for the `nebulapanel` user.
* [ ] System and Client databases are created in `storage/databases/`.
