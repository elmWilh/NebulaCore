"""Database helpers for Nebula Core."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = PROJECT_ROOT / "storage"
DATABASES_DIR = STORAGE_DIR / "databases"
CLIENTS_DIR = DATABASES_DIR / "clients"
SYSTEM_DB = str(DATABASES_DIR / "system.db")

_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\.db$")


def _ensure_base_dirs() -> None:
    DATABASES_DIR.mkdir(parents=True, exist_ok=True)
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)


def normalize_client_db_name(db_name: str) -> str:
    raw = str(db_name or "").strip()
    if not raw:
        raise ValueError("Database name is required")

    if "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError("Invalid database name")

    if not raw.endswith(".db"):
        raw = f"{raw}.db"

    if raw == "system.db":
        raise ValueError("system.db is reserved")

    if not _DB_NAME_RE.fullmatch(raw):
        raise ValueError("Invalid database name format")
    return raw


def list_client_databases() -> list[str]:
    _ensure_base_dirs()
    names = [p.name for p in CLIENTS_DIR.glob("*.db") if p.is_file()]
    names.sort(key=str.lower)
    return names


def resolve_client_db_path(db_name: str) -> tuple[str, str]:
    normalized = normalize_client_db_name(db_name)
    _ensure_base_dirs()
    path = (CLIENTS_DIR / normalized).resolve()
    if path.parent != CLIENTS_DIR.resolve():
        raise ValueError("Invalid database path")
    return str(path), normalized


@contextmanager
def get_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    _ensure_base_dirs()
    target = Path(str(db_path)).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_client_db(db_name: str, create_if_missing: bool = True) -> Iterator[sqlite3.Connection]:
    db_path, normalized = resolve_client_db_path(db_name)
    path_obj = Path(db_path)
    if not path_obj.exists() and not create_if_missing:
        raise ValueError(f"Database '{normalized}' not found")
    with get_connection(db_path) as conn:
        yield conn


__all__ = [
    "SYSTEM_DB",
    "CLIENTS_DIR",
    "DATABASES_DIR",
    "get_connection",
    "get_client_db",
    "list_client_databases",
    "normalize_client_db_name",
    "resolve_client_db_path",
]

