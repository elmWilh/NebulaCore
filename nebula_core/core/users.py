# nebula_core/core/users.py
from pydantic import BaseModel
from typing import List, Optional

class User(BaseModel):
    username: str
    password_hash: str
    roles: List[str] = []
    is_active: bool = True
