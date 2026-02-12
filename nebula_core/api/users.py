# nebula_core/api/users.py
from fastapi import APIRouter, HTTPException, Query, Form, Response, Depends
from ..services.user_service import UserService
from ..models.user import UserCreate
from ..db import get_client_db, list_client_databases
from .security import verify_staff_or_internal
import bcrypt

router = APIRouter(prefix="/users", tags=["Users"])
user_service = UserService()

@router.get("/databases")
def get_available_databases(_=Depends(verify_staff_or_internal)):
    return {"databases": list_client_databases()}

@router.get("/list")
def list_users(db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    with get_client_db(db_name) as conn:
        rows = conn.execute("SELECT id, username, is_staff FROM users").fetchall()
        return [dict(row) for row in rows]

@router.post("/login")
def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db_name: str = Query("system.db")
):
    with get_client_db(db_name) as conn:
        user = user_service.authenticate(conn, username, password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid Identity or Security Key")

        response.set_cookie(
            key="nebula_session", 
            value=f"{username}:{db_name}", 
            httponly=True,
            max_age=3600
        )
        
        return {"status": "authorized", "redirect": "/dashboard"}

@router.post("/create")
def register_user(data: UserCreate, db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    with get_client_db(db_name) as conn:
        try:
            user = user_service.create_user(conn, data)
            return user
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Registration failed: {str(e)}")

@router.post("/update")
def update_user(data: dict, _=Depends(verify_staff_or_internal)):
    source_db = data.get("source_db")
    target_db = data.get("target_db")
    old_name = data.get("old_username")
    new_name = data.get("new_username")
    new_password = data.get("new_password")
    role = data.get("role") 
    
    is_staff = 1 if role in ['staff', 'moderator'] else 0

    with get_client_db(source_db) as conn_src:
        user = conn_src.execute("SELECT * FROM users WHERE username=?", (old_name,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found in source sector")

        if new_password and len(new_password.strip()) > 0:
            p_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        else:
            p_hash = user["password_hash"]

        if source_db == target_db:
            conn_src.execute("""
                UPDATE users 
                SET username=?, password_hash=?, is_staff=?
                WHERE username=?
            """, (new_name, p_hash, is_staff, old_name))
            conn_src.commit()
            return {"status": "updated", "location": "local"}

        else:
            with get_client_db(target_db) as conn_dst:
                exists = conn_dst.execute("SELECT id FROM users WHERE username=?", (new_name,)).fetchone()
                if exists:
                    raise HTTPException(status_code=400, detail="Identity collision in target sector")

                try:
                    conn_dst.execute("""
                        INSERT INTO users (username, password_hash, is_staff) 
                        VALUES (?, ?, ?)
                    """, (new_name, p_hash, is_staff))
                    conn_dst.commit()
                    
                    conn_src.execute("DELETE FROM users WHERE username=?", (old_name,))
                    conn_src.commit()
                    return {"status": "moved", "location": target_db}
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Migration fatal error: {str(e)}")

@router.delete("/terminate")
def delete_user(username: str = Query(...), db_name: str = Query(...), _=Depends(verify_staff_or_internal)):
    with get_client_db(db_name) as conn:
        exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Target not found")
        
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
        return {"status": "terminated", "target": username}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("nebula_session")
    return {"status": "logged_out"}
