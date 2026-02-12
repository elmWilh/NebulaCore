# nebula_core/api/roles.py
from fastapi import APIRouter, HTTPException, Query, Depends
from ..db import get_client_db
from .security import verify_staff_or_internal

router = APIRouter(prefix="/roles", tags=["Roles"])

@router.post("/create")
def create_role(name: str, db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    with get_client_db(db_name) as conn:
        try:
            conn.execute("INSERT INTO roles (name) VALUES (?)", (name,))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"role": name, "database": db_name}

@router.post("/assign")
def assign_role(username: str, role_name: str, db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    with get_client_db(db_name) as conn:
        user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        role = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
        
        if not user or not role:
            raise HTTPException(status_code=404, detail="User or Role not found")
            
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user["id"], role["id"])
        )
    return {"username": username, "role": role_name, "status": "assigned"}
