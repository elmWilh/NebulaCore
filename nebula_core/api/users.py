# nebula_core/api/users.py
from fastapi import APIRouter, Depends, HTTPException
from ..services.user_service import UserService
from ..models.user import UserCreate

router = APIRouter(prefix="/users", tags=["Users"])
user_service = UserService()

@router.post("/register")
def register_user(data: UserCreate):
    user = user_service.create_user(data)
    return {"id": user.id, "username": user.username}

@router.post("/login")
def login(username: str, password: str):
    user = user_service.authenticate(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"id": user.id, "username": user.username, "roles": user.roles}
