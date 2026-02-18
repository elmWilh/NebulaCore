# nebula_core/api/logs.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request
from typing import List, Dict
import time
import asyncio
from .security import INTERNAL_AUTH_KEY, is_staff_session

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
async def get_log_history(request: Request, limit: int = 200):
    session_cookie = request.cookies.get("nebula_session")
    token = request.headers.get("x-nebula-token")
    if not is_staff_session(session_cookie) and not (INTERNAL_AUTH_KEY and token == INTERNAL_AUTH_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")
    return get_log_history_snapshot(limit)


def get_log_history_snapshot(limit: int = 200):
    safe_limit = max(1, min(int(limit), MAX_LOGS))
    return LOG_BUFFER[-safe_limit:]

@router.websocket("/stream")
async def websocket_logs(websocket: WebSocket):
    session_cookie = websocket.cookies.get("nebula_session")
    token = websocket.headers.get("x-nebula-token")
    if not is_staff_session(session_cookie) and not (INTERNAL_AUTH_KEY and token == INTERNAL_AUTH_KEY):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    history_sent = False
    cursor = 0

    try:
        while True:
            if not history_sent:
                snapshot = LOG_BUFFER[-100:]
                await websocket.send_json({"type": "history", "data": snapshot})
                history_sent = True
                cursor = len(LOG_BUFFER)
            else:
                current_len = len(LOG_BUFFER)
                if current_len < cursor:
                    cursor = 0
                if current_len > cursor:
                    updates = LOG_BUFFER[cursor:current_len]
                    cursor = current_len
                    for item in updates:
                        await websocket.send_json(item)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
