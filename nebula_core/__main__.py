import uvicorn
from .utils.config import settings

if __name__ == "__main__":
    uvicorn.run("nebula_core.main:app",
                host="0.0.0.0",
                port=8000,
                reload=True,
                log_level=settings.LOG_LEVEL.lower())
