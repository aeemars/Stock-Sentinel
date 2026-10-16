from typing import Optional, Dict, Any, Tuple
import bcrypt
from psycopg2.extras import RealDictCursor

class User:
    """User representation representing an authenticated user."""
    def __init__(self, user_dict: Dict[str, Any]):
        self.id = user_dict['id']
        self.username = user_dict['username']
        self.email = user_dict['email']
        self.created_at = user_dict.get('created_at')

    @property
    def is_authenticated(self) -> bool:
        return True

class AnonymousUser:
    """Anonymous placeholder when unauthenticated."""
    id = None
    username = ""
    email = ""
    created_at = None

    @property
    def is_authenticated(self) -> bool:
        return False

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False

def get_user_by_id(user_id: int, conn) -> Optional[User]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, username, email, created_at FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        if row:
            return User(row)
    return None

def get_user_with_hash(identifier: str, conn) -> Optional[Tuple[User, str]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, username, email, password_hash, created_at FROM users WHERE username = %s OR email = %s",
            (identifier, identifier.lower())
        )
        row = cur.fetchone()
        if row:
            pw_hash = row.pop('password_hash')
            return User(row), pw_hash
    return None
