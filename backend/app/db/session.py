"""Connection pool lifecycle.

Postgres handles concurrent access correctly via MVCC, so - unlike the
SQLite arrangement this replaced - no application-level lock is needed
around queries. A pool gives real parallelism across the thread pool
FastAPI runs sync handlers in.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def create_pool(database_url: str | None = None) -> ConnectionPool:
    """Open a pool and wait for it to be usable.

    `open=True` on the constructor only *starts* opening in a background
    thread and returns immediately, which pushes several seconds of latency
    onto whichever request happens to be first. Opening synchronously makes
    that cost explicit and paid once, at startup.
    """
    pool = ConnectionPool(
        database_url or settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    pool.open(wait=True, timeout=15)
    return pool


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = create_pool()
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
