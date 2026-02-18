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
settings = Settings(**yaml_config.get("server", {}))
