# nebula_core/main.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import uvicorn
import os
from .utils.config import settings

if __name__ == "__main__":
    host = os.getenv("NEBULA_CORE_HOST", "127.0.0.1")
    port = int(os.getenv("NEBULA_CORE_PORT", "8000"))
    reload_enabled = os.getenv("NEBULA_CORE_RELOAD", "false").lower() == "true"
    workers = int(os.getenv("NEBULA_CORE_WORKERS", "1"))
    if reload_enabled and workers > 1:
        # Uvicorn does not allow reload with multiple workers.
        workers = 1
    uvicorn.run("nebula_core.main:app",
                host=host,
                port=port,
                reload=reload_enabled,
                workers=max(1, workers),
                log_level=settings.LOG_LEVEL.lower())
