import httpx
import pytest

from app.config import settings

TEST_DATABASE_URL = "postgresql://analyst:analyst@localhost:5433/analyst_copilot_test"
TEST_QDRANT_URL = "http://localhost:6333"


def postgres_is_up() -> bool:
    try:
        import psycopg

        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


def qdrant_is_up() -> bool:
    try:
        httpx.get(f"{TEST_QDRANT_URL}/collections", timeout=2)
        return True
    except httpx.HTTPError:
        return False


requires_postgres = pytest.mark.skipif(
    not postgres_is_up(), reason="Postgres test DB is not reachable"
)
requires_qdrant = pytest.mark.skipif(not qdrant_is_up(), reason="Qdrant is not reachable")
requires_llm = pytest.mark.skipif(
    not settings.llm_api_key, reason="No LLM API key configured"
)


@pytest.fixture
def pool():
    if not postgres_is_up():
        pytest.skip("Postgres test DB is not reachable")

    from app.db.migrations import run_migrations
    from app.db.session import create_pool

    connection_pool = create_pool(TEST_DATABASE_URL)
    run_migrations(connection_pool)
    with connection_pool.connection() as conn:
        conn.execute(
            "TRUNCATE chat_messages, chat_sessions, filings, users RESTART IDENTITY CASCADE"
        )
        conn.commit()

    yield connection_pool
    connection_pool.close()


@pytest.fixture
def filings(pool):
    from app.db.repositories.filings import FilingRepository

    return FilingRepository(pool)


@pytest.fixture
def users(pool):
    from app.db.repositories.users import UserRepository

    return UserRepository(pool)


@pytest.fixture
def chats(pool):
    from app.db.repositories.chat import ChatRepository

    return ChatRepository(pool)
