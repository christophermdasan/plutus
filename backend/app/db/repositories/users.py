"""User persistence. Passwords arrive already hashed - this layer never
sees or handles plaintext."""

from __future__ import annotations

from typing import Any

from psycopg_pool import ConnectionPool

from app.domain.models import User


def _to_user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        created_at=row.get("created_at"),
    )


class UserRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def create(self, email: str, display_name: str, password_hash: str) -> User:
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO users (email, display_name, password_hash)
                   VALUES (%s, %s, %s) RETURNING *""",
                (email, display_name, password_hash),
            ).fetchone()
            conn.commit()
        return _to_user(row)

    def get(self, user_id: int) -> User | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        return _to_user(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        return _to_user(row) if row else None

    def get_password_hash(self, email: str) -> tuple[int, str] | None:
        """Returns (user_id, password_hash), kept off the User model so a
        hash can never be accidentally serialized into an API response."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE email = %s", (email,)
            ).fetchone()
        return (row["id"], row["password_hash"]) if row else None

    def update_display_name(self, user_id: int, display_name: str) -> User | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE users SET display_name = %s, updated_at = now()
                   WHERE id = %s RETURNING *""",
                (display_name, user_id),
            ).fetchone()
            conn.commit()
        return _to_user(row) if row else None

    def update_password(self, user_id: int, password_hash: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s",
                (password_hash, user_id),
            )
            conn.commit()
