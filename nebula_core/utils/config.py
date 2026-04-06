# nebula_core/utils/config.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import yaml
from pathlib import Path
from pydantic_settings import BaseSettings

CONFIG_PATH = Path(__file__).parent.parent / "serviceconfig.yaml"


def load_yaml_config(config_path: str | Path | None = None):
    target = Path(config_path) if config_path else CONFIG_PATH
    if target.exists():
        with open(target, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _coerce_debug_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0

    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y", "on", "debug", "development", "dev"}:
        return True
    if token in {"0", "false", "no", "n", "off", "", "release", "prod", "production"}:
        return False
    return False


def _server_settings_payload(raw_config: dict | None) -> dict:
    config = raw_config if isinstance(raw_config, dict) else {}
    services = config.get("services")
    service_server = services.get("server") if isinstance(services, dict) else None
    legacy_server = config.get("server")
    source = service_server if isinstance(service_server, dict) else legacy_server if isinstance(legacy_server, dict) else {}

    payload = dict(source)
    mapped = {}
    if "host" in payload:
        mapped["SERVER_HOST"] = payload.pop("host")
    if "port" in payload:
        mapped["SERVER_PORT"] = payload.pop("port")
    if "debug" in payload:
        mapped["DEBUG"] = _coerce_debug_flag(payload.pop("debug"))
    elif "DEBUG" in payload:
        mapped["DEBUG"] = _coerce_debug_flag(payload.pop("DEBUG"))
    mapped.update(payload)
    return mapped

class Settings(BaseSettings):
    APP_NAME: str = "Nebula Core"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"

    SERVER_HOST: str = "127.0.0.1"
    SERVER_PORT: int = 5000
    DEBUG: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

yaml_config = load_yaml_config()
settings = Settings(**_server_settings_payload(yaml_config))
