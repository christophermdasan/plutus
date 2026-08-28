"""Filing persistence.

Nothing is ever hard-deleted. "Delete" sets `deleted_at` and "archive" sets
`archived_at`; the PDF on disk and every row stay exactly where they are.
Listing is scoped to a workspace (a user id, or NULL for the shared guest
workspace) so one person's filings never leak into another's.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg_pool import ConnectionPool

from app.domain.enums import FilingStatus
from app.domain.models import Filing

_COLUMNS = """
    id, user_id, original_name, custom_title, stored_path, content_hash, status,
    error, num_pages, size_bytes, company_name, filing_type, fiscal_period,
    suggested_questions, archived_at, deleted_at, created_at, updated_at
"""


def _to_filing(row: dict[str, Any]) -> Filing:
    suggested = row.get("suggested_questions")
    if isinstance(suggested, str):
        suggested = json.loads(suggested)

    return Filing(
        id=row["id"],
        user_id=row["user_id"],
        original_name=row["original_name"],
        custom_title=row.get("custom_title"),
        stored_path=row["stored_path"],
        content_hash=row["content_hash"],
        status=FilingStatus(row["status"]),
        error=row["error"],
        num_pages=row["num_pages"],
        size_bytes=row["size_bytes"],
        company_name=row["company_name"],
        filing_type=row["filing_type"],
        fiscal_period=row["fiscal_period"],
        suggested_questions=suggested or [],
        archived_at=row["archived_at"],
        deleted_at=row["deleted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class FilingRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def create(
        self,
        filing_id: str,
        original_name: str,
        user_id: int | None,
        stored_path: str,
        size_bytes: int,
        content_hash: str,
    ) -> Filing:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""INSERT INTO filings
                    (id, user_id, original_name, stored_path, size_bytes, content_hash, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'queued')
                    RETURNING {_COLUMNS}""",
                (filing_id, user_id, original_name, stored_path, size_bytes, content_hash),
            ).fetchone()
            conn.commit()
        return _to_filing(row)

    def get(self, filing_id: str, *, include_deleted: bool = False) -> Filing | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM filings WHERE id = %s{clause}", (filing_id,)
            ).fetchone()
        return _to_filing(row) if row else None

    def list_for_workspace(
        self, user_id: int | None, *, archived: bool = False
    ) -> list[Filing]:
        """Active (or archived) filings for one workspace.

        `user_id IS NOT DISTINCT FROM %s` matches NULL to NULL, which is what
        scopes the shared guest workspace correctly - plain `=` would never
        match a NULL owner.
        """
        archived_clause = (
            "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
        )
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT {_COLUMNS} FROM filings
                    WHERE user_id IS NOT DISTINCT FROM %s
                      AND deleted_at IS NULL
                      AND {archived_clause}
                    ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return [_to_filing(row) for row in rows]

    def find_by_hash(self, content_hash: str, user_id: int | None) -> Filing | None:
        """Detect a re-upload of the same file in the same workspace."""
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""SELECT {_COLUMNS} FROM filings
                    WHERE content_hash = %s
                      AND user_id IS NOT DISTINCT FROM %s
                      AND deleted_at IS NULL
                    LIMIT 1""",
                (content_hash, user_id),
            ).fetchone()
        return _to_filing(row) if row else None

    def update_status(
        self,
        filing_id: str,
        status: FilingStatus,
        *,
        num_pages: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """UPDATE filings
                   SET status = %s,
                       num_pages = COALESCE(%s, num_pages),
                       error = %s,
                       updated_at = now()
                   WHERE id = %s""",
                (str(status), num_pages, error, filing_id),
            )
            conn.commit()

    def update_metadata(
        self,
        filing_id: str,
        *,
        company_name: str | None = None,
        filing_type: str | None = None,
        fiscal_period: str | None = None,
        suggested_questions: list[str] | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """UPDATE filings
                   SET company_name = COALESCE(%s, company_name),
                       filing_type = COALESCE(%s, filing_type),
                       fiscal_period = COALESCE(%s, fiscal_period),
                       suggested_questions = COALESCE(%s, suggested_questions),
                       updated_at = now()
                   WHERE id = %s""",
                (
                    company_name,
                    filing_type,
                    fiscal_period,
                    json.dumps(suggested_questions) if suggested_questions else None,
                    filing_id,
                ),
            )
            conn.commit()

    def rename(self, filing_id: str, title: str) -> None:
        """Set the user's own label. The uploaded filename is left alone."""
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE filings SET custom_title = %s, updated_at = now() WHERE id = %s",
                (title, filing_id),
            )
            conn.commit()

    def fail_interrupted(self) -> int:
        """Mark work that was in flight when the process died as failed.

        Ingestion runs in a background task, so a filing left in an active
        status at startup cannot still be progressing - nothing resumes it,
        and the row would otherwise sit on "Embedding" forever, looking busy
        rather than broken. Returns how many were reconciled.
        """
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """UPDATE filings
                      SET status = %s,
                          error = 'Interrupted before indexing finished - upload it again.',
                          updated_at = now()
                    WHERE status = ANY(%s)""",
                (
                    str(FilingStatus.FAILED),
                    [str(s) for s in FilingStatus if not s.is_terminal],
                ),
            )
            conn.commit()
            return cursor.rowcount

    def set_archived(self, filing_id: str, archived: bool) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """UPDATE filings
                   SET archived_at = CASE WHEN %s THEN now() ELSE NULL END,
                       updated_at = now()
                   WHERE id = %s""",
                (archived, filing_id),
            )
            conn.commit()

    def soft_delete(self, filing_id: str) -> None:
        """Mark deleted. The PDF and every row are retained."""
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE filings SET deleted_at = now(), updated_at = now() WHERE id = %s",
                (filing_id,),
            )
            conn.commit()

    def restore(self, filing_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE filings SET deleted_at = NULL, updated_at = now() WHERE id = %s",
                (filing_id,),
            )
            conn.commit()
