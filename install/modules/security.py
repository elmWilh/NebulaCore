import secrets
import os
from dotenv import load_dotenv, set_key

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

def generate_installer_key():
    key = secrets.token_urlsafe(32)
    if not os.path.exists(ENV_PATH):
        open(ENV_PATH, 'w').close()
    set_key(ENV_PATH, "INSTALLER_MASTER_KEY", key)
    return key

def verify_master_key(input_key):
    load_dotenv(ENV_PATH)
    return input_key == os.getenv("INSTALLER_MASTER_KEY")

def get_core_token():
    load_dotenv(ENV_PATH)
    return os.getenv("NEBULA_INSTALLER_TOKEN", "LOCAL_DEV_KEY_2026")