# nebula_core/models/user.py
from pydantic import BaseModel
from typing import List, Optional

class UserBase(BaseModel):
    username: str
    email: Optional[str] = None

class UserCreate(UserBase):
    password: str
    is_staff: bool = False

class User(UserBase):
    id: int
    is_active: bool = True
    is_staff: bool = False
    roles: List[str] = []

    class Config:
        from_attributes = True