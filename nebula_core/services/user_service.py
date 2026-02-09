# nebula_core/services/user_service.py
import bcrypt
from typing import List
from ..db import get_connection
from ..models.user import User, UserCreate

class UserService:
    def create_user(self, data: UserCreate) -> User:
        password_hash = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt())
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (data.username, password_hash)
            )
            user_id = cursor.lastrowid
        return User(id=user_id, username=data.username, roles=[])

    def authenticate(self, username: str, password: str) -> User | None:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not bcrypt.checkpw(password.encode(), row["password_hash"]):
                return None
            roles = self.get_user_roles(row["id"])
            return User(id=row["id"], username=row["username"], roles=roles)

    def get_user_roles(self, user_id: int) -> List[str]:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT r.name
                FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = ?
            """, (user_id,)).fetchall()
            return [r["name"] for r in rows]

    def check_permission(self, user_id: int, permission: str) -> bool:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT 1
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                JOIN roles r ON r.id = rp.role_id
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id=? AND p.name=?
            """, (user_id, permission)).fetchall()
            return len(rows) > 0
