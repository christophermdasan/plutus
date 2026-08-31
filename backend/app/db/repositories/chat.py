"""Chat session and message persistence.

History is organised as sessions (named conversations) rather than a flat
per-filing log, so a user can keep separate threads against one filing and
find them again later. Every message stores its citation alongside the
answer, which is what lets clicking an old answer reopen the exact page it
came from.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from app.domain.enums import MessageRole
from app.domain.models import ChatMessage, ChatSession, Citation


def _to_session(row: dict[str, Any]) -> ChatSession:
    return ChatSession(
        id=row["id"],
        user_id=row["user_id"],
        filing_id=row["filing_id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0) or 0,
    )


def _citations_from_row(row: dict[str, Any]) -> list[Citation]:
    """The stored citations, or the primary one promoted to a list.

    Rows written before the citations column existed have NULL there. Their
    page and quote are still a real citation, so they are returned as a
    one-entry list rather than as "this answer cited nothing" - which would
    strip the evidence link from every message already in the database.
    """
    stored = row.get("citations")
    if stored:
        return [
            Citation(page=item["page"], quote=item.get("quote", ""), label=item.get("label"))
            for item in stored
            if item.get("page") is not None
        ]
    if row.get("page") is not None:
        return [Citation(page=row["page"], quote=row.get("quote") or "")]
    return []


def _to_message(row: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        session_id=row["session_id"],
        role=MessageRole(row["role"]),
        question=row["question"] or "",
        answer=row["answer"] or "",
        found=row["found"],
        page=row["page"],
        quote=row["quote"] or "",
        citations=_citations_from_row(row),
        reason=row["reason"] or "",
        latency_ms=row["latency_ms"] or 0,
        model=row["model"],
        feedback=row["feedback"],
        created_at=row["created_at"],
    )


class ChatRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    # -- sessions ----------------------------------------------------------

    def create_session(
        self, filing_id: str, user_id: int | None, title: str = "New chat"
    ) -> ChatSession:
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO chat_sessions (filing_id, user_id, title)
                   VALUES (%s, %s, %s) RETURNING *""",
                (filing_id, user_id, title),
            ).fetchone()
            conn.commit()
        return _to_session(row)

    def get_session(self, session_id: int) -> ChatSession | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = %s AND deleted_at IS NULL",
                (session_id,),
            ).fetchone()
        return _to_session(row) if row else None

    def list_sessions(
        self, user_id: int | None, filing_id: str | None = None
    ) -> list[ChatSession]:
        filing_clause = "AND s.filing_id = %s" if filing_id else ""
        params: list[Any] = [user_id]
        if filing_id:
            params.append(filing_id)

        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT s.*, COUNT(m.id) AS message_count
                    FROM chat_sessions s
                    LEFT JOIN chat_messages m ON m.session_id = s.id
                    WHERE s.user_id IS NOT DISTINCT FROM %s
                      AND s.deleted_at IS NULL
                      {filing_clause}
                    GROUP BY s.id
                    ORDER BY s.updated_at DESC""",
                tuple(params),
            ).fetchall()
        return [_to_session(row) for row in rows]

    def rename_session(self, session_id: int, title: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET title = %s, updated_at = now() WHERE id = %s",
                (title, session_id),
            )
            conn.commit()

    def soft_delete_session(self, session_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET deleted_at = now() WHERE id = %s", (session_id,)
            )
            conn.commit()

    # -- messages ----------------------------------------------------------

    def add_message(
        self,
        session_id: int,
        *,
        question: str,
        answer: str,
        found: bool,
        page: int | None,
        quote: str,
        reason: str,
        latency_ms: int,
        model: str | None,
        citations: list[tuple] | None = None,
    ) -> ChatMessage:
        # (page, quote) and (page, quote, label) are both accepted: the
        # printed label is optional information about a citation, not part
        # of what makes one, and requiring it would force every caller to
        # know about a filing's front matter.
        payload = (
            Json([
                {"page": c[0], "quote": c[1], "label": c[2] if len(c) > 2 else None}
                for c in citations
            ])
            if citations
            else None
        )
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO chat_messages
                     (session_id, role, question, answer, found, page, quote,
                      reason, latency_ms, model, citations)
                   VALUES (%s, 'assistant', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (session_id, question, answer, found, page, quote, reason, latency_ms,
                 model, payload),
            ).fetchone()
            # Touch the session so recency ordering reflects real activity.
            conn.execute(
                "UPDATE chat_sessions SET updated_at = now() WHERE id = %s", (session_id,)
            )
            conn.commit()
        return _to_message(row)

    def list_messages(self, session_id: int) -> list[ChatMessage]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = %s ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [_to_message(row) for row in rows]

    def session_for_message(self, message_id: int) -> ChatSession | None:
        """The session a message belongs to, for authorising message actions."""
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT s.* FROM chat_sessions s
                   JOIN chat_messages m ON m.session_id = s.id
                   WHERE m.id = %s AND s.deleted_at IS NULL""",
                (message_id,),
            ).fetchone()
        return _to_session(row) if row else None

    def set_feedback(self, message_id: int, feedback: int | None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE chat_messages SET feedback = %s WHERE id = %s",
                (feedback, message_id),
            )
            conn.commit()

    def search_messages(self, user_id: int | None, query: str, limit: int = 30) -> list[ChatMessage]:
        """Find past questions/answers by substring.

        ILIKE is sufficient at this scale; a filing corpus per user is small
        and this avoids maintaining a separate full-text index for a
        convenience feature.
        """
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT m.* FROM chat_messages m
                   JOIN chat_sessions s ON s.id = m.session_id
                   WHERE s.user_id IS NOT DISTINCT FROM %s
                     AND s.deleted_at IS NULL
                     AND (m.question ILIKE %s OR m.answer ILIKE %s)
                   ORDER BY m.created_at DESC
                   LIMIT %s""",
                (user_id, f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [_to_message(row) for row in rows]
