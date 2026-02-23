# nebula_core/utils/logger.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_LIFECYCLE_LOCK = threading.Lock()
_LIFECYCLE_STATE_FILE = Path("logs/.lifecycle_state.json")


class DailyFileHandler(logging.Handler):
    """Write logs to files split by date: <logger-name>-YYYY-MM-DD.log."""

    def __init__(self, log_dir: Path, logger_name: str, encoding: str = "utf-8"):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger_name = logger_name
        self.encoding = encoding
        self._stream = None
        self._opened_for = None

    def _target_path(self, day) -> Path:
        return self.log_dir / f"{self.logger_name}-{day:%Y-%m-%d}.log"

    def _ensure_stream(self):
        current_day = datetime.now().date()
        if self._stream and self._opened_for == current_day:
            return
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        target = self._target_path(current_day)
        self._stream = open(target, "a", encoding=self.encoding)
        self._opened_for = current_day

    def emit(self, record):
        try:
            msg = self.format(record)
            self._ensure_stream()
            self._stream.write(msg + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            if self._stream:
                self._stream.close()
        finally:
            self._stream = None
            self._opened_for = None
            super().close()


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logger(name: str, *, with_console: bool = True, level: int = logging.INFO) -> logging.Logger:
    """Create a configured logger using daily log files."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = _build_formatter()

    if with_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    file_handler = DailyFileHandler(Path("logs"), logger_name=name)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _read_lifecycle_state() -> dict:
    if not _LIFECYCLE_STATE_FILE.exists():
        return {}
    try:
        with _LIFECYCLE_STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_lifecycle_state(payload: dict):
    _LIFECYCLE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _LIFECYCLE_STATE_FILE.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2)
    os.replace(temp_path, _LIFECYCLE_STATE_FILE)


def register_lifecycle_start(service_name: str) -> dict:
    """
    Register process start event and classify as startup or restart.
    First ever run for service_name -> startup, otherwise restart.
    """
    with _LIFECYCLE_LOCK:
        state = _read_lifecycle_state()
        service_state = state.get(service_name, {}) if isinstance(state, dict) else {}
        starts = int(service_state.get("starts", 0)) + 1
        event = "startup" if starts == 1 else "restart"
        now = datetime.now(timezone.utc).isoformat()
        pid = os.getpid()
        service_state.update(
            {
                "starts": starts,
                "last_start_at_utc": now,
                "last_pid": pid,
                "last_event": event,
            }
        )
        state[service_name] = service_state
        _write_lifecycle_state(state)
        return {
            "event": event,
            "starts": starts,
            "pid": pid,
            "at_utc": now,
        }


def register_lifecycle_shutdown(service_name: str) -> dict:
    """Register graceful shutdown marker for current process."""
    with _LIFECYCLE_LOCK:
        state = _read_lifecycle_state()
        service_state = state.get(service_name, {}) if isinstance(state, dict) else {}
        now = datetime.now(timezone.utc).isoformat()
        pid = os.getpid()
        service_state.update(
            {
                "last_shutdown_at_utc": now,
                "last_shutdown_pid": pid,
            }
        )
        state[service_name] = service_state
        _write_lifecycle_state(state)
        return {"event": "shutdown", "pid": pid, "at_utc": now}


def get_logger(name: str) -> logging.Logger:
    """Return configured logger with daily file output."""
    return setup_logger(name)
