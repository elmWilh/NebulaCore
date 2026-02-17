# nebula_core/services/user_service.py
import bcrypt
from typing import List, Optional
from ..models.user import User, UserCreate

class UserService:
    def create_user(self, conn, data: UserCreate) -> User:
        password_hash = self.hash_password(data.password)
        is_staff = 1 if getattr(data, 'is_staff', False) else 0
        email = str(getattr(data, 'email', '') or '').strip() or None
        
        cursor = conn.execute(
            "INSERT INTO users (username, email, password_hash, is_staff) VALUES (?, ?, ?, ?)",
            (data.username, email, password_hash, is_staff)
        )
        user_id = cursor.lastrowid
        return User(id=user_id, username=data.username, email=email, roles=[], is_staff=bool(is_staff))

    def hash_password(self, password: str) -> bytes:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    def verify_password(self, plain_password: str, stored_hash) -> bool:
        # stored_hash may be bytes or str depending on DB driver; normalize to bytes
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')
        return bcrypt.checkpw(plain_password.encode(), stored_hash)

    def authenticate(self, conn, username: str, password: str) -> Optional[User]:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row or not bcrypt.checkpw(password.encode(), row["password_hash"]):
            return None
        
        roles = self.get_user_roles(conn, row["id"])
        return User(
            id=row["id"], 
            username=row["username"], 
            email=row["email"] if "email" in row.keys() else None,
            roles=roles, 
            is_staff=bool(row["is_staff"])
        )

    def get_user_roles(self, conn, user_id: int) -> List[str]:
        rows = conn.execute("""
            SELECT r.name 
            FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
        """, (user_id,)).fetchall()
        return [r["name"] for r in rows]

    def check_permission(self, conn, user_id: int, permission: str) -> bool:
        row = conn.execute("""
            SELECT 1 
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = ? AND p.name = ?
        """, (user_id, permission)).fetchone()
        return row is not None
