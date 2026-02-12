# nebula_core/db.py
import sqlite3
import os
from contextlib import contextmanager

BASE_DIR = "storage/databases"
CLIENTS_DIR = os.path.join(BASE_DIR, "clients")
SYSTEM_DB = os.path.join(BASE_DIR, "system.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    is_staff BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER,
    PRIMARY KEY(user_id, role_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(role_id) REFERENCES roles(id)
);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS role_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER,
    permission_id INTEGER,
    UNIQUE(role_id, permission_id),
    FOREIGN KEY(role_id) REFERENCES roles(id),
    FOREIGN KEY(permission_id) REFERENCES permissions(id)
);
CREATE TABLE IF NOT EXISTS container_permissions (
    container_id TEXT NOT NULL,
    username TEXT NOT NULL,
    PRIMARY KEY(container_id, username)
);
"""

def ensure_dirs():
    if not os.path.exists(CLIENTS_DIR):
        os.makedirs(CLIENTS_DIR, exist_ok=True)

@contextmanager
def get_connection(db_path: str = SYSTEM_DB):
    ensure_dirs()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

@contextmanager
def get_client_db(db_name: str):
    ensure_dirs()
    if not db_name.endswith(".db"):
        db_name += ".db"
    db_path = os.path.join(CLIENTS_DIR, db_name)
    
    is_new = not os.path.exists(db_path)
    
    with get_connection(db_path) as conn:
        if is_new:
            conn.executescript(SCHEMA)
        yield conn

def init_secure_system():
    ensure_dirs()
    with get_connection(SYSTEM_DB) as conn:
        conn.executescript(SCHEMA)
        conn.execute("CREATE TABLE IF NOT EXISTS sys_metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR IGNORE INTO sys_metadata VALUES ('version', '2026.1')")
        conn.execute("INSERT OR IGNORE INTO sys_metadata VALUES ('init_date', datetime('now'))")
        
        conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('SUPERUSER')")
        conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('OPERATOR')")

def init_system_db():
    with get_connection(SYSTEM_DB) as conn:
        conn.executescript(SCHEMA)

def list_client_databases():
    ensure_dirs()
    return [f for f in os.listdir(CLIENTS_DIR) if f.endswith(".db")]