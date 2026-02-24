# nebula_core/api/system.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os
import sqlite3
from fastapi import APIRouter, Query, Depends
from ..core.system_info import get_system_info
from ..db import SYSTEM_DB, CLIENTS_DIR, get_connection
from .security import verify_staff_or_internal

router = APIRouter(prefix="/system", tags=["System"])

@router.get("/status")
async def system_status():
    return {"status": "ok", "system": get_system_info()}

@router.get("/lookup")
async def resolve_user_location(
    username: str = Query(...),
    _=Depends(verify_staff_or_internal),
):
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
            system_user = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if system_user:
                return {
                    "status": "found",
                    "db_name": "system.db",
                    "type": "user",
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
