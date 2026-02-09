# nebula_core/api/roles.py
from fastapi import APIRouter, HTTPException
from ..db import get_connection

router = APIRouter(prefix="/roles", tags=["Roles"])

@router.post("/create")
def create_role(name: str):
    with get_connection() as conn:
        try:
            conn.execute("INSERT INTO roles (name) VALUES (?)", (name,))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"role": name, "status": "created"}

@router.post("/{role_name}/add_permission")
def add_permission_to_role(role_name: str, permission: str):
    with get_connection() as conn:
        role = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        perm = conn.execute("INSERT OR IGNORE INTO permissions (name) VALUES (?)", (permission,))
        perm_id = conn.execute("SELECT id FROM permissions WHERE name=?", (permission,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role["id"], perm_id)
        )
    return {"role": role_name, "permission": permission, "status": "assigned"}

@router.post("/assign_role")
def assign_role(username: str, role_name: str):
    with get_connection() as conn:
        user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        role = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
        if not user or not role:
            raise HTTPException(status_code=404, detail="User or Role not found")
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user["id"], role["id"])
        )
    return {"username": username, "role": role_name, "status": "assigned"}
