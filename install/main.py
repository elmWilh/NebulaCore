#!/usr/bin/env python3
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import argparse
import getpass
import json
import os
import platform
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

from modules.core_service import (
    default_project_dir,
    detect_run_user,
    install_or_update_gui_service,
    install_or_update_service,
    service_action,
    systemd_available,
)

PROJECT_ROOT = default_project_dir()
CORE_API_URL = "http://127.0.0.1:8000/system/internal/core"
ENV_PATH = PROJECT_ROOT / ".env"
LEGACY_ENV_PATH = PROJECT_ROOT / "install" / ".env"
DB_PATH = PROJECT_ROOT / "storage" / "databases" / "system.db"
VENV_PATH = PROJECT_ROOT / ".venv"
PYTHON_BIN = VENV_PATH / "bin" / "python"
PIP_BIN = VENV_PATH / "bin" / "pip"
DEFAULT_CORE_SERVICE = "nebula-core"
DEFAULT_GUI_SERVICE = "nebula-gui"


def header():
    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 72)
    print("                 NEBULA PANEL INSTALLER v2026")
    print("=" * 72)


def print_step(message: str):
    print(f"\n[>] {message}")


def print_ok(message: str):
    print(f"[OK] {message}")


def print_warn(message: str):
    print(f"[WARN] {message}")


def print_error(message: str):
    print(f"[ERROR] {message}")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return values
    return values


def write_env_file(path: Path, values: dict[str, str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Nebula installer-managed environment",
        "# Adjust values if you need a custom host/proxy setup.",
    ]
    for key in sorted(values):
        lines.append(f'{key}="{values[key]}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_env_values() -> dict[str, str]:
    values = {}
    values.update(read_env_file(LEGACY_ENV_PATH))
    values.update(read_env_file(ENV_PATH))
    return values


def ensure_env_file() -> dict[str, str]:
    values = resolve_env_values()
    defaults = {
        "NEBULA_SESSION_SECRET": secrets.token_urlsafe(32),
        "NEBULA_INSTALLER_TOKEN": secrets.token_urlsafe(32),
        "NEBULA_GUI_SECRET_KEY": secrets.token_urlsafe(32),
        "NEBULA_COOKIE_SECURE": "false",
        "NEBULA_GUI_COOKIE_SECURE": "false",
        "NEBULA_CORE_HOST": "127.0.0.1",
        "NEBULA_CORE_PORT": "8000",
        "NEBULA_CORE_GRPC_HOST": "127.0.0.1",
        "NEBULA_CORE_GRPC_PORT": "50051",
        "NEBULA_CORS_ORIGINS": "http://127.0.0.1:5000,http://localhost:5000",
        "NEBULA_GUI_CORS_ORIGINS": "http://127.0.0.1:5000,http://localhost:5000",
    }
    for key, value in defaults.items():
        values.setdefault(key, value)

    write_env_file(ENV_PATH, values)

    # Keep the legacy path in sync for older helper code that still probes it.
    if LEGACY_ENV_PATH != ENV_PATH:
        write_env_file(LEGACY_ENV_PATH, values)
    return values


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def run_command(cmd: list[str], *, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, check=check)


def create_virtualenv_if_missing():
    if PYTHON_BIN.exists():
        print_ok(f"Virtualenv already exists at {VENV_PATH}")
        return
    print_step("Creating Python virtual environment")
    VENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    venv.create(VENV_PATH, with_pip=True)
    print_ok("Virtual environment created")


def install_python_dependencies():
    print_step("Installing Python dependencies")
    run_command([str(PYTHON_BIN), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    run_command(
        [
            str(PIP_BIN),
            "install",
            "-r",
            "requirements.txt",
            "-r",
            "nebula_gui_flask/requirements.txt",
        ],
        check=True,
    )
    print_ok("Python dependencies installed")


def check_system():
    if not ENV_PATH.exists():
        return 2
    if not DB_PATH.exists():
        return 2
    return 0


def verify_admin_exists() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_staff = 1")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except sqlite3.Error:
        return False


def is_docker_installed() -> bool:
    return shutil.which("docker") is not None


def detect_distro():
    try:
        os_release = Path("/etc/os-release")
        if os_release.exists():
            data = os_release.read_text(encoding="utf-8").lower()
            if "debian" in data or "ubuntu" in data:
                return "debian"
            if "rhel" in data or "fedora" in data or "centos" in data:
                return "rhel"
    except OSError:
        pass
    return platform.system().lower()


def install_docker() -> bool:
    distro = detect_distro()
    print(f"Detected platform: {distro}")
    if distro not in {"debian", "ubuntu", "linux", "rhel"}:
        print_warn("Automatic Docker installation is not available on this OS.")
        print("See: https://docs.docker.com/engine/install/")
        return False

    try:
        run_command(["/bin/sh", "-c", "curl -fsSL https://get.docker.com -o get-docker.sh"], check=True)
        run_command(["sudo", "sh", "get-docker.sh"], check=True)
        get_docker = PROJECT_ROOT / "get-docker.sh"
        if get_docker.exists():
            get_docker.unlink()
        print_ok("Docker installation command completed")
        return True
    except subprocess.CalledProcessError as exc:
        print_error(f"Docker installation failed: {exc}")
        return False


def start_docker_service() -> bool:
    try:
        run_command(["sudo", "systemctl", "start", "docker"], check=True)
        run_command(["sudo", "systemctl", "enable", "docker"], check=True)
        print_ok("Docker service started and enabled")
        return True
    except subprocess.CalledProcessError as exc:
        print_error(f"Failed to start Docker: {exc}")
        return False


def check_docker_status() -> tuple[bool, str]:
    if not is_docker_installed():
        return False, "docker binary not found"
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except OSError as exc:
        return False, str(exc)


def add_user_to_docker_group() -> bool:
    username = os.getenv("SUDO_USER") or os.getenv("USER")
    if not username:
        try:
            username = getpass.getuser()
        except Exception:
            username = ""

    if not username:
        print_warn("Could not determine current user.")
        print("Run manually: sudo usermod -aG docker <your-user>")
        return False

    try:
        run_command(["sudo", "usermod", "-aG", "docker", username], check=True)
        print_ok(
            f"User '{username}' added to docker group. Log out and back in if Docker access still fails."
        )
        return True
    except subprocess.CalledProcessError as exc:
        print_error(f"Failed to add user to docker group: {exc}")
        return False


def ensure_docker_ready():
    print_step("Checking Docker")
    installed = is_docker_installed()
    ok, info = check_docker_status() if installed else (False, "Docker is not installed")
    if installed and ok:
        print_ok("Docker is installed and responding")
        return

    if not installed:
        print_warn("Docker is not installed.")
        if ask_yes_no("Install Docker automatically now?", default=True):
            if not install_docker():
                return

    ok, info = check_docker_status()
    if not ok:
        print_warn("Docker daemon is not ready.")
        if "permission denied" in info.lower():
            if ask_yes_no("Add current user to the docker group?", default=True):
                add_user_to_docker_group()
            return
        if ask_yes_no("Start and enable Docker now?", default=True):
            start_docker_service()
            ok, info = check_docker_status()
            if ok:
                print_ok("Docker is ready")
                return
    if not ok:
        print_warn("Docker is still unavailable. Nebula can install, but container features will not work yet.")


def _socket_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(method: str, url: str, *, payload: dict | None = None, headers: dict | None = None, timeout: float = 5.0):
    body = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return response.status, data, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"detail": raw}
        return exc.code, data, raw


def wait_for_core(timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _socket_open("127.0.0.1", 8000):
            time.sleep(1)
            continue
        try:
            status, data, _ = _http_json("GET", "http://127.0.0.1:8000/system/status", timeout=2)
            if status == 200 and isinstance(data, dict) and data.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def create_admin_via_core(username: str, password: str, installer_token: str) -> tuple[bool, str]:
    status, data, raw = _http_json(
        "POST",
        f"{CORE_API_URL}/init-admin",
        payload={"username": username, "password": password},
        headers={"X-Nebula-Token": installer_token},
        timeout=8,
    )
    if status in {200, 201}:
        return True, "Administrator account created"
    if status == 409:
        return True, "Administrator already exists"
    detail = data.get("detail") if isinstance(data, dict) else raw
    return False, str(detail or "Core did not accept admin setup request")


def prompt_admin_credentials() -> tuple[str, str]:
    while True:
        username = input("Admin username [nebula_admin]: ").strip() or "nebula_admin"
        if len(username) < 5 or not username.replace("_", "").isalnum():
            print_warn("Username must be at least 5 characters and contain only letters, digits, and underscores.")
            continue
        break

    while True:
        password = getpass.getpass("Admin password: ").strip()
        confirm = getpass.getpass("Repeat password: ").strip()
        if len(password) < 12:
            print_warn("Password must be at least 12 characters.")
            continue
        if password != confirm:
            print_warn("Passwords do not match.")
            continue
        break

    return username, password


def setup_services(core_service_name: str, gui_service_name: str, env_mode: str) -> bool:
    if not systemd_available():
        print_warn("systemd is not available. Skipping service installation.")
        return False

    run_user = detect_run_user()
    print_step(f"Installing systemd services for user '{run_user}'")
    ok, msg = install_or_update_service(
        project_dir=str(PROJECT_ROOT),
        run_user=run_user,
        service_name=core_service_name,
        env_mode=env_mode,
    )
    print(msg)
    if not ok:
        return False

    ok, msg = install_or_update_gui_service(
        project_dir=str(PROJECT_ROOT),
        run_user=run_user,
        service_name=gui_service_name,
        env_mode=env_mode,
    )
    print(msg)
    return ok


def start_core_temporarily() -> subprocess.Popen | None:
    print_step("Starting Nebula Core temporarily for first setup")
    logs_dir = PROJECT_ROOT / "storage" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "core.bootstrap.stdout.log"
    stderr_path = logs_dir / "core.bootstrap.stderr.log"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("ENV", "production")
    env["NEBULA_CONFIG_PATH"] = str(PROJECT_ROOT / "nebula_core" / "serviceconfig.yaml")
    process = subprocess.Popen(
        [str(PYTHON_BIN), "-m", "nebula_core"],
        cwd=PROJECT_ROOT,
        stdout=stdout_path.open("a", encoding="utf-8"),
        stderr=stderr_path.open("a", encoding="utf-8"),
        env=env,
    )
    return process


def easy_install(core_service_name: str, gui_service_name: str, env_mode: str):
    header()
    print("This wizard prepares Python, config, Core, GUI, and the first admin account.")
    print("Recommended for a normal first install on one Linux host.\n")
    try:
        create_virtualenv_if_missing()
        install_python_dependencies()
        env_values = ensure_env_file()
        print_ok(f"Environment file prepared: {ENV_PATH}")
        ensure_docker_ready()

        use_services = ask_yes_no("Install Nebula as systemd services and auto-start them on boot?", default=True)
        bootstrap_core_process = None

        if use_services:
            ok = setup_services(core_service_name, gui_service_name, env_mode)
            if not ok:
                print_warn("Service installation failed. Falling back to temporary Core startup for setup.")
                use_services = False

        if use_services:
            ok, msg = service_action(core_service_name, "restart")
            if not ok:
                print_error(msg or "Failed to start Core service")
                sys.exit(1)
            print_ok(f"Core service started: {core_service_name}")
        else:
            bootstrap_core_process = start_core_temporarily()

        print_step("Waiting for Nebula Core to become ready")
        if not wait_for_core():
            print_error("Nebula Core did not become ready in time.")
            sys.exit(1)
        print_ok("Nebula Core is online")

        if verify_admin_exists():
            print_ok("Administrator account already exists. Skipping first-admin creation.")
        else:
            username, password = prompt_admin_credentials()
            ok, message = create_admin_via_core(username, password, env_values["NEBULA_INSTALLER_TOKEN"])
            if not ok:
                print_error(message)
                sys.exit(1)
            print_ok(message)

        if use_services:
            ok, msg = service_action(gui_service_name, "restart")
            if ok:
                print_ok(f"GUI service started: {gui_service_name}")
            else:
                print_error(msg or "Failed to start GUI service")
        else:
            print_warn("Services were not installed.")
            print("Run the panel manually later with:")
            print("  ./.venv/bin/python -m nebula_core")
            print("  cd nebula_gui_flask && ../.venv/bin/python app.py")

        print("\n" + "=" * 72)
        print("Nebula installation is complete.")
        print("Open: http://127.0.0.1:5000")
        if use_services:
            print("Manage services with:")
            print("  ./panelctl.sh status")
            print("  ./panelctl.sh restart")
            print("  ./panelctl.sh logs")
        if bootstrap_core_process and bootstrap_core_process.poll() is None:
            print_warn("Core is currently running in temporary bootstrap mode.")
            print("Stop it with Ctrl+C in that process or install services later via ./panelctl.sh install")
    except subprocess.CalledProcessError as exc:
        print_error(f"Command failed: {' '.join(exc.cmd)}")
        sys.exit(exc.returncode or 1)


def first_run_setup():
    header()
    print("[!] FIRST ADMIN SETUP")
    print("-" * 72)
    env_values = ensure_env_file()
    if not wait_for_core(timeout=10):
        print_error("Nebula Core is offline. Start the Core service first.")
        input("\nPress Enter to return to menu...")
        return

    if verify_admin_exists():
        print_ok("Administrator already exists.")
        input("\nPress Enter to return to menu...")
        return

    username, password = prompt_admin_credentials()
    ok, message = create_admin_via_core(username, password, env_values["NEBULA_INSTALLER_TOKEN"])
    print_ok(message) if ok else print_error(message)
    input("\nPress Enter to return to menu...")


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
    print(" [1] Install Docker")
    print(" [2] Start Docker service")
    print(" [3] Install + Start")
    print(" [4] Add current user to docker group")
    print(" [0] Back")
    choice = input("Select >> ").strip()
    if choice == "1":
        install_docker()
    elif choice == "2":
        start_docker_service()
    elif choice == "3":
        if not installed:
            install_docker()
        start_docker_service()
    elif choice == "4":
        add_user_to_docker_group()


def manage_core_service_install():
    print("\n=== Nebula systemd install ===")
    if not systemd_available():
        print("systemd/systemctl is not available on this host.")
        return

    project_dir_default = str(default_project_dir())
    user_default = detect_run_user()
    project_dir = input(f"Project dir [{project_dir_default}]: ").strip() or project_dir_default
    run_user = input(f"Run user [{user_default}]: ").strip() or user_default
    core_service_name = input(f"Core service name [{DEFAULT_CORE_SERVICE}]: ").strip() or DEFAULT_CORE_SERVICE
    gui_service_name = input(f"GUI service name [{DEFAULT_GUI_SERVICE}]: ").strip() or DEFAULT_GUI_SERVICE
    env_mode = input("ENV mode [production]: ").strip() or "production"

    ok, msg = install_or_update_service(
        project_dir=project_dir,
        run_user=run_user,
        service_name=core_service_name,
        env_mode=env_mode,
    )
    print("[OK]" if ok else "[ERROR]", msg)

    ok2, msg2 = install_or_update_gui_service(
        project_dir=project_dir,
        run_user=run_user,
        service_name=gui_service_name,
        env_mode=env_mode,
    )
    print("[OK]" if ok2 else "[ERROR]", msg2)


def manage_service_actions():
    print("\n=== Nebula service control ===")
    if not systemd_available():
        print("systemd/systemctl is not available on this host.")
        return

    target = input("Target [panel/core/gui]: ").strip().lower() or "panel"
    core_service_name = input(f"Core service name [{DEFAULT_CORE_SERVICE}]: ").strip() or DEFAULT_CORE_SERVICE
    gui_service_name = input(f"GUI service name [{DEFAULT_GUI_SERVICE}]: ").strip() or DEFAULT_GUI_SERVICE
    print(" [1] start")
    print(" [2] stop")
    print(" [3] restart")
    print(" [4] status")
    print(" [5] logs")
    print(" [0] back")
    action_choice = input("Select >> ").strip()
    mapping = {"1": "start", "2": "stop", "3": "restart", "4": "status", "5": "logs"}
    action = mapping.get(action_choice)
    if not action:
        return

    services = [core_service_name, gui_service_name] if target == "panel" else [core_service_name if target == "core" else gui_service_name]
    for service_name in services:
        ok, output = service_action(service_name, action, lines=100)
        print(f"\n--- {service_name} ---")
        print("[OK]" if ok else "[ERROR]")
        print(output or "(no output)")


def run_interactive():
    ensure_env_file()
    while True:
        header()
        db_status = "FOUND" if DB_PATH.exists() else "MISSING"
        admin_status = "YES" if verify_admin_exists() else "NO"
        print(f" DATABASE: {db_status} | ADMIN CONFIGURED: {admin_status}")
        print("-" * 72)
        print(" [1] Easy install (recommended)")
        print(" [2] Create first admin")
        print(" [3] View system status")
        print(" [4] Install / Start Docker")
        print(" [5] Install / Update Core + GUI services")
        print(" [6] Manage services (start/stop/restart/status/logs)")
        print(" [7] Hard reset (delete system database)")
        print(" [0] Exit")
        print("-" * 72)

        choice = input("SELECT >> ").strip()
        if choice == "1":
            easy_install(DEFAULT_CORE_SERVICE, DEFAULT_GUI_SERVICE, "production")
        elif choice == "2":
            first_run_setup()
        elif choice == "3":
            if wait_for_core(timeout=2):
                print_ok("Core is online")
            else:
                print_warn("Core is offline")
            input("\nPress Enter...")
        elif choice == "4":
            manage_docker_interactive()
            input("\nPress Enter...")
        elif choice == "5":
            manage_core_service_install()
            input("\nPress Enter...")
        elif choice == "6":
            manage_service_actions()
            input("\nPress Enter...")
        elif choice == "7":
            confirm = input("Type 'ERASE' to delete storage/databases/system.db: ").strip()
            if confirm == "ERASE":
                if DB_PATH.exists():
                    DB_PATH.unlink()
                    print_ok("Database deleted")
                else:
                    print_warn("Database file does not exist")
            input("\nPress Enter...")
        elif choice == "0":
            break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--easy-install", action="store_true")
    parser.add_argument("--core-service-install", action="store_true")
    parser.add_argument("--gui-service-install", action="store_true")
    parser.add_argument("--core-service-name", default=DEFAULT_CORE_SERVICE)
    parser.add_argument("--gui-service-name", default=DEFAULT_GUI_SERVICE)
    parser.add_argument("--core-service-user", default="")
    parser.add_argument("--gui-service-user", default="")
    parser.add_argument("--core-service-project-dir", default="")
    parser.add_argument("--gui-service-project-dir", default="")
    parser.add_argument("--core-service-env", default="production")
    parser.add_argument("--gui-service-env", default="production")
    parser.add_argument(
        "--core-service-action",
        choices=["start", "stop", "restart", "status", "logs", "enable", "disable"],
        default="",
    )
    parser.add_argument(
        "--gui-service-action",
        choices=["start", "stop", "restart", "status", "logs", "enable", "disable"],
        default="",
    )
    parser.add_argument("--core-service-log-lines", type=int, default=100)
    parser.add_argument("--gui-service-log-lines", type=int, default=100)
    args = parser.parse_args()

    if args.check:
        sys.exit(check_system())
    if args.easy_install:
        easy_install(args.core_service_name, args.gui_service_name, args.core_service_env)
        return
    if args.core_service_install:
        ok, msg = install_or_update_service(
            project_dir=args.core_service_project_dir or None,
            run_user=args.core_service_user or None,
            service_name=args.core_service_name,
            env_mode=args.core_service_env,
        )
        print(msg)
        sys.exit(0 if ok else 1)
    if args.gui_service_install:
        ok, msg = install_or_update_gui_service(
            project_dir=args.gui_service_project_dir or None,
            run_user=args.gui_service_user or None,
            service_name=args.gui_service_name,
            env_mode=args.gui_service_env,
        )
        print(msg)
        sys.exit(0 if ok else 1)
    if args.core_service_action:
        ok, msg = service_action(args.core_service_name, args.core_service_action, lines=args.core_service_log_lines)
        print(msg)
        sys.exit(0 if ok else 1)
    if args.gui_service_action:
        ok, msg = service_action(args.gui_service_name, args.gui_service_action, lines=args.gui_service_log_lines)
        print(msg)
        sys.exit(0 if ok else 1)

    run_interactive()


if __name__ == "__main__":
    main()
