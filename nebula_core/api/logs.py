# nebula_core/api/logs.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict
import time
import asyncio

router = APIRouter(prefix="/logs", tags=["Logs"])

LOG_BUFFER: List[Dict] = []
MAX_LOGS = 500

def add_log_entry(level: str, message: str, logger_name: str = "nebula_core"):
    entry = {
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "logger": logger_name,
        "message": message.strip()
    }
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > MAX_LOGS:
        LOG_BUFFER.pop(0)

import logging
class LogInterceptor(logging.Handler):
    def emit(self, record):
        add_log_entry(record.levelname, record.getMessage(), record.name)

logging.getLogger().addHandler(LogInterceptor())

@router.get("/history")
async def get_log_history(limit: int = 200):
    return LOG_BUFFER[-limit:]

@router.websocket("/stream")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    history_sent = False

    try:
        while True:
            if not history_sent:
                await websocket.send_json(LOG_BUFFER[-100:])
                history_sent = True
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass