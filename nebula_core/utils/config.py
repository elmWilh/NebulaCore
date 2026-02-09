# nebula_core/core/config.py
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings

CONFIG_PATH = Path(__file__).parent.parent / "serviceconfig.yaml"

def load_yaml_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
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
