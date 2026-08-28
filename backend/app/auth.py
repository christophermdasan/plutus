"""Password hashing and JWT session tokens.

Auth is optional everywhere in this app (see routers/auth.py and the
`current_user` dependency) - this module only concerns itself with the two
primitives: turning a password into something safe to store, and turning a
user id into a bearer token and back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int, secret: str, algorithm: str, expires_minutes: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str) -> int | None:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        return int(payload["sub"])
    except jwt.PyJWTError:
        return None
