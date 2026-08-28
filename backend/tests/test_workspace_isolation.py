"""One account must not reach another's filings or conversations.

These are regression tests for a real defect: every by-id route resolved its
target with an unscoped lookup, so possessing an id was treated as
authorisation. Filing ids are short random hex, but chat session ids are
sequential integers - a signed-out visitor could read any account's history
by counting upwards from 1.

Storage was already partitioned per workspace on disk; only the API
disagreed. Each test below drives the HTTP layer, because that is where the
check has to hold.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from tests.conftest import TEST_DATABASE_URL, requires_postgres, requires_qdrant

pytestmark = [requires_postgres, requires_qdrant]


def _pdf_bytes(marker: str) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    SimpleDocTemplate(buffer, pagesize=letter).build(
        [
            Paragraph(f"{marker} confidential filing cover page.", styles["Normal"]),
            PageBreak(),
            Paragraph(f"{marker} total revenue was $99.9 million.", styles["Normal"]),
        ]
    )
    return buffer.getvalue()


@pytest.fixture
def client(pool, monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.database_url", TEST_DATABASE_URL)
    monkeypatch.setattr("app.config.settings.storage_dir", tmp_path)
    monkeypatch.setattr("app.config.settings.qdrant_collection", "test_isolation_384")
    monkeypatch.setattr("app.config.settings.enrich_filings", False)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _account(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/signup",
        json={"email": email, "display_name": email.split("@")[0], "password": "correcthorse"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _upload(client: TestClient, headers: dict[str, str], marker: str) -> str:
    response = client.post(
        "/filings",
        files={"file": (f"{marker}.pdf", _pdf_bytes(marker), "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


# --- filings --------------------------------------------------------------


def test_a_filing_is_invisible_to_another_account(client):
    owner = _account(client, "owner-list@example.com")
    other = _account(client, "other-list@example.com")
    _upload(client, owner, "OWNED")

    assert client.get("/filings", headers=owner).json() != []
    assert client.get("/filings", headers=other).json() == []


@pytest.mark.parametrize(
    "method, path_suffix",
    [
        ("get", ""),
        ("get", "/pdf"),
        ("patch", ""),
        ("post", "/archive"),
        ("post", "/unarchive"),
        ("delete", ""),
        ("post", "/restore"),
    ],
)
def test_another_account_cannot_reach_a_filing_by_id(client, method, path_suffix):
    owner = _account(client, f"owner{method}{path_suffix.strip('/')}@example.com")
    other = _account(client, f"other{method}{path_suffix.strip('/')}@example.com")
    filing_id = _upload(client, owner, "OWNED")

    kwargs = {"headers": other}
    if method == "patch":
        kwargs["json"] = {"name": "hijacked"}

    response = getattr(client, method)(f"/filings/{filing_id}{path_suffix}", **kwargs)
    # Missing rather than forbidden: a 403 would confirm the id is real.
    assert response.status_code == 404


def test_a_signed_out_visitor_cannot_reach_a_signed_in_users_filing(client):
    owner = _account(client, "owner-guest@example.com")
    filing_id = _upload(client, owner, "OWNED")

    assert client.get(f"/filings/{filing_id}").status_code == 404
    assert client.get(f"/filings/{filing_id}/pdf").status_code == 404


def test_the_owner_is_unaffected_by_the_check(client):
    owner = _account(client, "owner-ok@example.com")
    filing_id = _upload(client, owner, "OWNED")

    assert client.get(f"/filings/{filing_id}", headers=owner).status_code == 200
    assert client.get(f"/filings/{filing_id}/pdf", headers=owner).status_code == 200
    assert (
        client.patch(f"/filings/{filing_id}", json={"name": "Renamed"}, headers=owner).json()[
            "display_title"
        ]
        == "Renamed"
    )


def test_guests_share_one_workspace_and_still_see_their_own_uploads(client):
    """Signing out is a workspace, not a wall - guests share it by design."""
    filing_id = _upload(client, {}, "GUEST")
    assert client.get(f"/filings/{filing_id}").status_code == 200


# --- chat -----------------------------------------------------------------


def _session_id(client: TestClient, headers: dict[str, str], filing_id: str) -> int:
    from app.db.repositories.chat import ChatRepository
    from app.db.session import create_pool

    # Created directly: asking a question would spend a real LLM call, and the
    # thing under test is the authorisation check, not the answer.
    pool = create_pool()
    try:
        user = client.get("/auth/me", headers=headers).json() if headers else None
        session = ChatRepository(pool).create_session(
            filing_id=filing_id, user_id=user["id"] if user else None
        )
        return session.id
    finally:
        pool.close()


def test_another_account_cannot_read_or_change_a_conversation(client):
    owner = _account(client, "owner-chat@example.com")
    other = _account(client, "other-chat@example.com")
    filing_id = _upload(client, owner, "OWNED")
    session_id = _session_id(client, owner, filing_id)

    assert client.get(f"/chat/sessions/{session_id}/messages", headers=other).status_code == 404
    assert (
        client.patch(
            f"/chat/sessions/{session_id}", json={"title": "hijacked"}, headers=other
        ).status_code
        == 404
    )
    assert client.delete(f"/chat/sessions/{session_id}", headers=other).status_code == 404

    # And the owner is still able to use it.
    assert client.get(f"/chat/sessions/{session_id}/messages", headers=owner).status_code == 200


def test_sequential_session_ids_cannot_be_walked_by_a_signed_out_visitor(client):
    """The ids are `SERIAL`, so enumeration is trivial if ids authorise."""
    owner = _account(client, "owner-walk@example.com")
    filing_id = _upload(client, owner, "OWNED")
    session_id = _session_id(client, owner, filing_id)

    for candidate in range(1, session_id + 1):
        assert client.get(f"/chat/sessions/{candidate}/messages").status_code == 404


def test_feedback_cannot_be_written_into_another_accounts_history(client):
    owner = _account(client, "owner-fb@example.com")
    other = _account(client, "other-fb@example.com")
    filing_id = _upload(client, owner, "OWNED")
    session_id = _session_id(client, owner, filing_id)

    from app.db.repositories.chat import ChatRepository
    from app.db.session import create_pool

    pool = create_pool()
    try:
        message_id = ChatRepository(pool).add_message(
            session_id,
            question="q",
            answer="a",
            found=True,
            page=1,
            quote="q",
            reason="verified",
            latency_ms=1,
            model="test",
        ).id
    finally:
        pool.close()

    assert (
        client.post(
            f"/chat/messages/{message_id}/feedback", json={"feedback": 1}, headers=other
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/chat/messages/{message_id}/feedback", json={"feedback": 1}, headers=owner
        ).status_code
        == 204
    )
