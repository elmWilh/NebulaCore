# nebula_core/core/users.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from pydantic import BaseModel
from typing import List, Optional

class User(BaseModel):
    username: str
    password_hash: str
    roles: List[str] = []
    is_active: bool = True
