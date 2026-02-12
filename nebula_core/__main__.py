import uvicorn
import os
from .utils.config import settings

if __name__ == "__main__":
    host = os.getenv("NEBULA_CORE_HOST", "127.0.0.1")
    port = int(os.getenv("NEBULA_CORE_PORT", "8000"))
    reload_enabled = os.getenv("NEBULA_CORE_RELOAD", "false").lower() == "true"
    uvicorn.run("nebula_core.main:app",
                host=host,
                port=port,
                reload=reload_enabled,
                log_level=settings.LOG_LEVEL.lower())
