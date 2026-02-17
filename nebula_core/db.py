# nebula_core/db.py
import sqlite3
import os
import re
from contextlib import contextmanager

BASE_DIR = "storage/databases"
CLIENTS_DIR = os.path.join(BASE_DIR, "clients")
SYSTEM_DB = os.path.join(BASE_DIR, "system.db")
CLIENT_DB_RE = re.compile(r"^[A-Za-z0-9._-]+\.db$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    is_staff BOOLEAN DEFAULT 0,
    two_factor_secret TEXT,
    two_factor_enabled BOOLEAN DEFAULT 0
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
    db_name TEXT DEFAULT 'system.db',
    role_tag TEXT DEFAULT 'user',
    PRIMARY KEY(container_id, username)
);
CREATE TABLE IF NOT EXISTS container_settings (
    container_id TEXT PRIMARY KEY,
    startup_command TEXT,
    allowed_ports TEXT,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS container_storage (
    container_id TEXT PRIMARY KEY,
    workspace_path TEXT,
    workspace_mount TEXT,
    disk_quota_mb INTEGER,
    explorer_root TEXT,
    console_cwd TEXT,
    profile_name TEXT,
    managed_workspace BOOLEAN DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS identity_roles (
    name TEXT PRIMARY KEY,
    description TEXT,
    is_staff BOOLEAN DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS user_identity_tags (
    db_name TEXT NOT NULL,
    username TEXT NOT NULL,
    role_tag TEXT NOT NULL DEFAULT 'user',
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (db_name, username)
);
CREATE TABLE IF NOT EXISTS container_role_permissions (
    container_id TEXT NOT NULL,
    role_tag TEXT NOT NULL,
    allow_explorer BOOLEAN DEFAULT 1,
    allow_root_explorer BOOLEAN DEFAULT 0,
    allow_console BOOLEAN DEFAULT 1,
    allow_shell BOOLEAN DEFAULT 0,
    allow_settings BOOLEAN DEFAULT 0,
    allow_edit_files BOOLEAN DEFAULT 0,
    allow_edit_startup BOOLEAN DEFAULT 0,
    allow_edit_ports BOOLEAN DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (container_id, role_tag)
);
"""

def ensure_dirs():
    if not os.path.exists(CLIENTS_DIR):
        os.makedirs(CLIENTS_DIR, exist_ok=True)


def ensure_user_security_columns(conn):
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    except Exception:
        return
    if not cols:
        return

    if "two_factor_secret" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN two_factor_secret TEXT")
    if "two_factor_enabled" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT 0")
    if "email" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")


def ensure_container_settings_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_settings (
            container_id TEXT PRIMARY KEY,
            startup_command TEXT,
            allowed_ports TEXT,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

def ensure_container_storage_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_storage (
            container_id TEXT PRIMARY KEY,
            workspace_path TEXT,
            workspace_mount TEXT,
            disk_quota_mb INTEGER,
            explorer_root TEXT,
            console_cwd TEXT,
            profile_name TEXT,
            managed_workspace BOOLEAN DEFAULT 0,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(container_storage)").fetchall()}
    except Exception:
        cols = set()
    if cols:
        if "explorer_root" not in cols:
            conn.execute("ALTER TABLE container_storage ADD COLUMN explorer_root TEXT")
        if "console_cwd" not in cols:
            conn.execute("ALTER TABLE container_storage ADD COLUMN console_cwd TEXT")
        if "profile_name" not in cols:
            conn.execute("ALTER TABLE container_storage ADD COLUMN profile_name TEXT")


def ensure_container_permission_columns(conn):
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(container_permissions)").fetchall()}
    except Exception:
        return
    if not cols:
        return
    if "db_name" not in cols:
        conn.execute("ALTER TABLE container_permissions ADD COLUMN db_name TEXT DEFAULT 'system.db'")
    if "role_tag" not in cols:
        conn.execute("ALTER TABLE container_permissions ADD COLUMN role_tag TEXT DEFAULT 'user'")


def ensure_user_identity_tags_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_identity_tags (
            db_name TEXT NOT NULL,
            username TEXT NOT NULL,
            role_tag TEXT NOT NULL DEFAULT 'user',
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (db_name, username)
        )
    """)


def ensure_container_role_permissions_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_role_permissions (
            container_id TEXT NOT NULL,
            role_tag TEXT NOT NULL,
            allow_explorer BOOLEAN DEFAULT 1,
            allow_root_explorer BOOLEAN DEFAULT 0,
            allow_console BOOLEAN DEFAULT 1,
            allow_shell BOOLEAN DEFAULT 0,
            allow_settings BOOLEAN DEFAULT 0,
            allow_edit_files BOOLEAN DEFAULT 0,
            allow_edit_startup BOOLEAN DEFAULT 0,
            allow_edit_ports BOOLEAN DEFAULT 0,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (container_id, role_tag)
        )
    """)


def ensure_identity_roles_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS identity_roles (
            name TEXT PRIMARY KEY,
            description TEXT,
            is_staff BOOLEAN DEFAULT 0,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO identity_roles (name, description, is_staff, updated_by, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("user", "Default end-user role", 0, "system"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO identity_roles (name, description, is_staff, updated_by, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("developer", "Developer role with shell/settings capabilities", 0, "system"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO identity_roles (name, description, is_staff, updated_by, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("moderator", "Moderation role", 0, "system"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO identity_roles (name, description, is_staff, updated_by, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("tester", "QA/testing role", 0, "system"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO identity_roles (name, description, is_staff, updated_by, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("admin", "Administrative role with elevated permissions", 1, "system"),
    )

@contextmanager
def get_connection(db_path: str = SYSTEM_DB):
    ensure_dirs()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_user_security_columns(conn)
    ensure_container_settings_table(conn)
    ensure_container_storage_table(conn)
    ensure_container_permission_columns(conn)
    ensure_user_identity_tags_table(conn)
    ensure_container_role_permissions_table(conn)
    ensure_identity_roles_table(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def normalize_client_db_name(db_name: str) -> str:
    if not isinstance(db_name, str):
        raise ValueError("Database name must be a string")
    candidate = db_name.strip()
    if not candidate:
        raise ValueError("Database name is empty")

    if "/" in candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError("Invalid database name")
    if ".." in candidate:
        raise ValueError("Invalid database name")
    if os.path.basename(candidate) != candidate:
        raise ValueError("Invalid database name")

    if not candidate.endswith(".db"):
        candidate += ".db"
    if not CLIENT_DB_RE.fullmatch(candidate):
        raise ValueError("Invalid database name format")
    return candidate
 
@contextmanager
def get_client_db(db_name: str, create_if_missing: bool = True):
    ensure_dirs()
    db_name = normalize_client_db_name(db_name)
    db_path = os.path.join(CLIENTS_DIR, db_name)
    
    is_new = not os.path.exists(db_path)
    if is_new and not create_if_missing:
        raise FileNotFoundError(f"Database not found: {db_name}")
    
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
