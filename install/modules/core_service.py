# install/modules/core_service.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None


def default_project_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def detect_run_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "root"


def build_unit_content(
    project_dir: Path,
    run_user: str,
    service_name: str = "nebula-core",
    env_mode: str = "production",
) -> str:
    python_bin = project_dir / ".venv" / "bin" / "python"
    config_path = project_dir / "nebula_core" / "serviceconfig.yaml"
    env_path = project_dir / ".env"
    logs_dir = project_dir / "storage" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    return f"""[Unit]
Description=Nebula Core Service ({service_name})
After=network.target

[Service]
Type=simple
User={run_user}
Group={run_user}
WorkingDirectory={project_dir}
Environment=PYTHONUNBUFFERED=1
Environment=ENV={env_mode}
Environment=NEBULA_CONFIG_PATH={config_path}
EnvironmentFile=-{env_path}
ExecStart={python_bin} -m nebula_core
Restart=on-failure
RestartSec=2
Delegate=yes
NoNewPrivileges=yes
TimeoutStopSec=15
LimitNOFILE=65535
StandardOutput=append:{logs_dir / "core.stdout.log"}
StandardError=append:{logs_dir / "core.stderr.log"}

[Install]
WantedBy=multi-user.target
"""


def install_or_update_service(
    project_dir: Optional[str] = None,
    run_user: Optional[str] = None,
    service_name: str = "nebula-core",
    env_mode: str = "production",
) -> tuple[bool, str]:
    if not systemd_available():
        return False, "systemctl is not available on this host"

    base = Path(project_dir).resolve() if project_dir else default_project_dir()
    user = (run_user or detect_run_user()).strip() or "root"
    python_bin = base / ".venv" / "bin" / "python"
    if not python_bin.exists():
        return False, f"Python virtualenv not found: {python_bin}"

    unit_content = build_unit_content(base, user, service_name=service_name, env_mode=env_mode)
    tmp_unit = Path("/tmp") / f"{service_name}.service"
    target_unit = Path("/etc/systemd/system") / f"{service_name}.service"
    tmp_unit.write_text(unit_content, encoding="utf-8")

    commands = [
        ["sudo", "cp", str(tmp_unit), str(target_unit)],
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "enable", service_name],
    ]
    for cmd in commands:
        result = _run(cmd)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "failed").strip()

    return True, f"Installed/updated {service_name} at {target_unit}"


def service_action(service_name: str, action: str, lines: int = 100) -> tuple[bool, str]:
    if not systemd_available():
        return False, "systemctl is not available on this host"

    normalized = str(action or "").strip().lower()
    if normalized in {"start", "stop", "restart", "status", "enable", "disable"}:
        result = _run(["sudo", "systemctl", normalized, service_name])
        ok = result.returncode == 0
        out = (result.stdout + result.stderr).strip()
        if normalized == "status":
            status = _run(["sudo", "systemctl", "status", service_name, "--no-pager", "-n", "40"])
            out = (status.stdout + status.stderr).strip()
            ok = status.returncode == 0
        return ok, out

    if normalized == "logs":
        tail = max(10, int(lines or 100))
        result = _run(["sudo", "journalctl", "-u", service_name, "-n", str(tail), "--no-pager"])
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    return False, f"Unsupported action: {action}"
