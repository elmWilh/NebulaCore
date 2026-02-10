# nebula_core/api/system.py
import os
import sqlite3
from fastapi import APIRouter, Query
from ..core.system_info import get_system_info
from ..db import SYSTEM_DB, CLIENTS_DIR, get_connection

router = APIRouter(prefix="/system", tags=["System"])

@router.get("/status")
async def system_status():
    return {"status": "ok", "system": get_system_info()}

@router.get("/lookup")
async def resolve_user_location(username: str = Query(...)):
    try:
        with get_connection(SYSTEM_DB) as conn:
            admin = conn.execute(
                "SELECT 1 FROM users WHERE username = ? AND is_staff = 1", 
                (username,)
            ).fetchone()
            
            if admin:
                return {
                    "status": "found", 
                    "db_name": "system.db", 
                    "type": "staff"
                }
    except Exception as e:
        print(f"[Lookup Error] System DB access failed: {e}")

    if os.path.exists(CLIENTS_DIR):
        for db_file in os.listdir(CLIENTS_DIR):
            if db_file.endswith(".db"):
                db_path = os.path.join(CLIENTS_DIR, db_file)
                
                try:
                    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    cursor = conn.cursor()
                    
                    user = cursor.execute(
                        "SELECT 1 FROM users WHERE username = ?", 
                        (username,)
                    ).fetchone()
                    conn.close()
                    
                    if user:
                        return {
                            "status": "found", 
                            "db_name": db_file, 
                            "type": "user"
                        }
                except Exception:
                    continue

    return {
        "status": "not_found", 
        "db_name": None, 
        "type": None
    }