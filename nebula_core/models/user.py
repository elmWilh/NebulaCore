# nebula_core/models/user.py
from pydantic import BaseModel
from typing import List, Optional

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int
    is_active: bool = True
    roles: List[str] = []

class Role(BaseModel):
    name: str
    permissions: List[str] = []
