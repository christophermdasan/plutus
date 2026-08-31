"""Versioned schema migrations.

Replaces the previous approach of running `CREATE TABLE IF NOT EXISTS` on
every boot, which cannot express a *change* to an existing table - only the
initial shape. Each migration runs exactly once, in order, recorded in
`schema_migrations`, so an existing database can be evolved instead of
recreated.

Migrations are plain SQL kept inline: the schema is small enough that a
dedicated tool (Alembic) would add more machinery than it removes, but the
version tracking that actually matters is here.
"""

from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

Migration = tuple[str, str]

MIGRATIONS: list[Migration] = [
    (
        "0001_initial",
        """
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            display_name  TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS filings (
            id             TEXT PRIMARY KEY,
            user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
            original_name  TEXT NOT NULL,
            stored_path    TEXT,
            content_hash   TEXT,
            status         TEXT NOT NULL DEFAULT 'queued',
            error          TEXT,
            num_pages      INTEGER,
            size_bytes     BIGINT,
            company_name   TEXT,
            filing_type    TEXT,
            fiscal_period  TEXT,
            archived_at    TIMESTAMPTZ,
            deleted_at     TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
            filing_id  TEXT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
            title      TEXT NOT NULL DEFAULT 'New chat',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role       TEXT NOT NULL,
            question   TEXT NOT NULL DEFAULT '',
            answer     TEXT NOT NULL DEFAULT '',
            found      BOOLEAN NOT NULL DEFAULT FALSE,
            page       INTEGER,
            quote      TEXT,
            reason     TEXT,
            latency_ms INTEGER,
            model      TEXT,
            feedback   SMALLINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        -- Listing filings for a workspace filters on deleted/archived state,
        -- and history loads by session; these are the only hot paths.
        CREATE INDEX IF NOT EXISTS idx_filings_user     ON filings(user_id);
        CREATE INDEX IF NOT EXISTS idx_filings_active   ON filings(deleted_at, archived_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_filing  ON chat_sessions(filing_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_user    ON chat_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);
        """,
    ),
    (
        "0002_suggested_questions",
        """
        ALTER TABLE filings ADD COLUMN IF NOT EXISTS suggested_questions JSONB;
        """,
    ),
    (
        # A rename used to write over original_name, which the sidebar label
        # only falls back to - so renaming anything the enricher had already
        # identified appeared to do nothing at all. A name the user typed is
        # held separately and always wins; original_name stays what was
        # uploaded, because that is a fact about the file.
        "0003_custom_title",
        """
        ALTER TABLE filings ADD COLUMN IF NOT EXISTS custom_title TEXT;
        """,
    ),
    (
        # A figure is usually printed in several places - the statement, the
        # MD&A discussion of it, a note - and each is a truthful citation.
        # The answer carries all of them so a reader can check the figure
        # wherever they trust most, but only the first was ever stored, so
        # reopening a conversation lost the alternates.
        #
        # JSONB rather than a child table: citations are always read as a
        # whole with their message, never queried across messages, and a
        # join buys nothing for a list of two or three rows. `page` and
        # `quote` stay as they are - they are the primary citation the
        # source drawer opens to, and keeping them avoids rewriting every
        # existing row.
        "0004_message_citations",
        """
        ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS citations JSONB;
        """,
    ),
]

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def run_migrations(pool: ConnectionPool) -> list[str]:
    """Apply any migrations this database hasn't seen. Returns those applied."""
    applied: list[str] = []

    with pool.connection() as conn:
        conn.execute(_TRACKING_TABLE)
        conn.commit()

        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        already = {row["version"] for row in rows}

        for version, sql in MIGRATIONS:
            if version in already:
                continue
            logger.info("Applying migration %s", version)
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            applied.append(version)

    return applied
