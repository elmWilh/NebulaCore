# install/main.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os
import sys
import requests
import argparse
import time
import sqlite3
import subprocess
import shutil
import platform
from modules.security import generate_installer_key, verify_master_key, get_core_token
from modules.core_service import (
    default_project_dir,
    detect_run_user,
    install_or_update_service,
    service_action,
    systemd_available,
)

CORE_API_URL = "http://127.0.0.1:8000/system/internal/core"
ENV_PATH = ".env"
DB_PATH = "storage/databases/system.db"

def header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("="*65)
    print("      NEBULA SYSTEMS - INSTALLER & MANAGEMENT v2026")
    print("="*65)

def check_system():
    if not os.path.exists(ENV_PATH):
        return 2
    if not os.path.exists(DB_PATH):
        return 2
    return 0

def verify_admin_exists():
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_staff = 1")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False

def ask_confirm(msg="Confirm action?"):
    ans = input(f"{msg} (YES/NO): ").strip().upper()
    return ans == "YES"

def first_run_setup():
    header()
    print("[!] SYSTEM INITIALIZATION")
    print("-" * 65)
    
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    ans = input("Generate Installer Master Key? (YES/NO): ").strip().upper()
    if ans == "YES":
        new_key = generate_installer_key()
        print(f"\n[!!!] YOUR MASTER KEY: {new_key}\n")
    
    print("--- Create Master System Operator ---")
    user = input("Username: ")
    pwd = input("Password: ")
    
    print("\n[!] Connecting to Nebula Core...")
    token = get_core_token()
    headers = {"X-Nebula-Token": token}
    
    try:
        r = requests.post(
            f"{CORE_API_URL}/init-admin", 
            json={"username": user, "password": pwd}, 
            headers=headers,
            timeout=5
        )
        if r.status_code in [200, 201]:
            print("[OK] Admin created via Core.")
        elif r.status_code == 409:
            print("[!] Core: Already initialized.")
        else:
            print(f"[!] Core Error: {r.text}")
    except:
        print("[ERROR] Core is offline. Check if Nebula Core is running.")
    
    input("\nPress Enter to return to menu...")


def is_docker_installed():
    return shutil.which("docker") is not None


def detect_distro():
    try:
        if os.path.exists('/etc/os-release'):
            with open('/etc/os-release') as f:
                data = f.read()
            if 'ID_LIKE' in data and 'debian' in data.lower():
                return 'debian'
            if 'ID_LIKE' in data and ('rhel' in data.lower() or 'fedora' in data.lower()):
                return 'rhel'
    except:
        pass
    return platform.system().lower()


def install_docker():
    distro = detect_distro()
    print(f"Detected platform: {distro}")
    if distro in ('debian', 'ubuntu', 'linux'):
        print("Installing Docker using official convenience script...")
        try:
            subprocess.run(["/bin/sh", "-c", "curl -fsSL https://get.docker.com -o get-docker.sh"], check=True)
            subprocess.run(["sudo", "sh", "get-docker.sh"], check=True)
            os.remove('get-docker.sh')
            print("Docker installed (or attempted).")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Installation failed: {e}")
            return False
    else:
        print("Automatic installer not available for this OS. Please follow Docker docs:")
        print("https://docs.docker.com/engine/install/")
        return False


def start_docker_service():
    try:
        subprocess.run(["sudo", "systemctl", "start", "docker"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "docker"], check=True)
        print("Docker service started and enabled.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to start Docker via systemctl: {e}")
        return False


def check_docker_status():
    try:
        out = subprocess.run(["docker", "info"], capture_output=True, text=True)
        return out.returncode == 0, out.stdout + out.stderr
    except FileNotFoundError:
        return False, "docker binary not found"


def manage_docker_interactive():
    print("\n=== Docker Installer / Manager ===")
    installed = is_docker_installed()
    print(f"Docker installed: {installed}")
    ok, info = check_docker_status() if installed else (False, "Not installed")
    print(f"Docker daemon running: {ok}")
    if installed and ok:
        print("Docker is already installed and running.")
        return

    print("Options:")
    print(" [1] Install Docker (uses official script)")
    print(" [2] Start Docker service")
    print(" [3] Install + Start (recommended)")
    print(" [0] Back")
    choice = input("Select >> ")
    if choice == '1':
        if install_docker():
            print("Installation finished. You may need to log out/in if you added your user to docker group.")
    elif choice == '2':
        start_docker_service()
    elif choice == '3':
        if not installed:
            install_docker()
        start_docker_service()
    else:
        return

    print("Checking status...")
    ok, info = check_docker_status()
    print("OK:" if ok else "ERROR:")
    print(info)

    # If docker is installed but permission denied, offer to add user to `docker` group
    if not ok and 'permission denied' in info.lower():
        ans = input("Permission denied to access Docker socket. Add current user to 'docker' group? (YES/NO): ").strip().upper()
        if ans == 'YES':
            add_user_to_docker_group()


def add_user_to_docker_group():
    username = os.getenv('SUDO_USER') or os.getenv('USER')
    if not username:
        try:
            username = os.getlogin()
        except Exception:
            username = None

    if not username:
        print("Could not determine current user. Please run: sudo usermod -aG docker <your-user>")
        return False

    try:
        print(f"Adding user '{username}' to group 'docker'...")
        subprocess.run(["sudo", "usermod", "-aG", "docker", username], check=True)
        print("User added to 'docker' group. You must log out and log back in (or run 'newgrp docker') for changes to take effect.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to add user to group: {e}")
        return False


def manage_core_service_install():
    print("\n=== Nebula Core systemd install ===")
    if not systemd_available():
        print("systemd/systemctl is not available on this host.")
        return

    project_dir_default = str(default_project_dir())
    user_default = detect_run_user()
    project_dir = input(f"Project dir [{project_dir_default}]: ").strip() or project_dir_default
    run_user = input(f"Run user [{user_default}]: ").strip() or user_default
    service_name = input("Service name [nebula-core]: ").strip() or "nebula-core"
    env_mode = input("ENV mode [production]: ").strip() or "production"

    ok, msg = install_or_update_service(
        project_dir=project_dir,
        run_user=run_user,
        service_name=service_name,
        env_mode=env_mode,
    )
    print("[OK]" if ok else "[ERROR]", msg)
    if ok:
        ok2, msg2 = service_action(service_name, "status")
        print("\n--- service status ---")
        print(msg2 if msg2 else ("active" if ok2 else "unknown"))


def manage_core_service_actions():
    print("\n=== Nebula Core service control ===")
    if not systemd_available():
        print("systemd/systemctl is not available on this host.")
        return

    service_name = input("Service name [nebula-core]: ").strip() or "nebula-core"
    print(" [1] start")
    print(" [2] stop")
    print(" [3] restart")
    print(" [4] status")
    print(" [5] logs (last 100)")
    print(" [0] back")
    action_choice = input("Select >> ").strip()
    mapping = {
        "1": "start",
        "2": "stop",
        "3": "restart",
        "4": "status",
        "5": "logs",
    }
    action = mapping.get(action_choice)
    if not action:
        return
    ok, output = service_action(service_name, action, lines=100)
    print("[OK]" if ok else "[ERROR]")
    print(output or "(no output)")

def run_interactive():
    if os.path.exists(ENV_PATH):
        try:
            import dotenv
            dotenv.load_dotenv()
            master = os.getenv("INSTALLER_MASTER_KEY")
            if master:
                attempt = input("Enter Installer Master Key: ")
                if not verify_master_key(attempt):
                    print("ACCESS DENIED.")
                    time.sleep(2)
                    return
        except:
            pass

    while True:
        header()
        db_status = "FOUND" if os.path.exists(DB_PATH) else "MISSING"
        admin_status = "YES" if verify_admin_exists() else "NO"
        print(f" DATABASE: {db_status} | ADMIN CONFIGURED: {admin_status}")
        print("-" * 65)
        print(" [1] Run First-Time Setup / Create Admin")
        print(" [2] View System Status")
        print(" [3] Install / Start Docker Daemon")
        print(" [4] Install / Update Core systemd service")
        print(" [5] Manage Core service (start/stop/restart/status/logs)")
        print(" [7] HARD RESET (Delete Database)")
        print(" [0] Exit")
        print("-" * 65)
        
        choice = input("SELECT >> ")
        token = get_core_token()
        headers = {"X-Nebula-Token": token}

        if choice == "1":
            first_run_setup()
        elif choice == "2":
            try:
                r = requests.get(f"{CORE_API_URL}/status", headers=headers)
                print(f"Core Response: {r.json()}")
            except:
                print("Core Offline.")
        elif choice == "3":
            manage_docker_interactive()
        elif choice == "4":
            manage_core_service_install()
        elif choice == "5":
            manage_core_service_actions()
        elif choice == "7":
            if input("Type 'ERASE' to confirm: ") == "ERASE":
                if os.path.exists(DB_PATH):
                    os.remove(DB_PATH)
                    print("[OK] Deleted.")
                else:
                    print("[!] Not found.")
        elif choice == "0":
            break
        input("\nPress Enter...")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument("--core-service-install", action="store_true")
    parser.add_argument("--core-service-name", default="nebula-core")
    parser.add_argument("--core-service-user", default="")
    parser.add_argument("--core-service-project-dir", default="")
    parser.add_argument("--core-service-env", default="production")
    parser.add_argument(
        "--core-service-action",
        choices=["start", "stop", "restart", "status", "logs", "enable", "disable"],
        default="",
    )
    parser.add_argument("--core-service-log-lines", type=int, default=100)
    args = parser.parse_args()

    if args.check:
        sys.exit(check_system())
    elif args.core_service_install:
        ok, msg = install_or_update_service(
            project_dir=args.core_service_project_dir or None,
            run_user=args.core_service_user or None,
            service_name=args.core_service_name,
            env_mode=args.core_service_env,
        )
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.core_service_action:
        ok, msg = service_action(args.core_service_name, args.core_service_action, lines=args.core_service_log_lines)
        print(msg)
        sys.exit(0 if ok else 1)
    else:
        run_interactive()

