"""End-to-end API tests against a real Postgres, Qdrant and LLM.

Deliberately not mocked: the things most likely to break in this system are
integration seams (schema drift, a changed provider contract, a filter that
silently matches nothing), and mocks would assert my assumptions about those
rather than their behaviour.
"""

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from tests.conftest import TEST_DATABASE_URL, requires_llm, requires_postgres, requires_qdrant

pytestmark = [requires_postgres, requires_qdrant]


def _pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    SimpleDocTemplate(buffer, pagesize=letter).build(
        [
            Paragraph("MERIDIAN ROBOTICS, INC. Form 10-K for fiscal year 2023.", styles["Normal"]),
            PageBreak(),
            Paragraph(
                "Total revenue for fiscal year 2023 was $184.6 million, compared to "
                "$162.3 million in fiscal year 2022, an increase of 13.7%.",
                styles["Normal"],
            ),
            PageBreak(),
            Paragraph(
                "During the third quarter we recorded a goodwill impairment charge of "
                "$6.4 million related to our Industrial Sensing reporting unit.",
                styles["Normal"],
            ),
        ]
    )
    return buffer.getvalue()


@pytest.fixture
def client(pool, monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.database_url", TEST_DATABASE_URL)
    monkeypatch.setattr("app.config.settings.storage_dir", tmp_path)
    monkeypatch.setattr("app.config.settings.qdrant_collection", "test_passages_384")
    # Enrichment costs two extra LLM calls per upload and these tests
    # upload constantly; leaving it on just burns the rate-limit budget
    # that the chat tests actually need. Covered separately.
    monkeypatch.setattr("app.config.settings.enrich_filings", False)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def ready_filing(client):
    """Upload a filing and wait for background ingestion to finish."""
    response = client.post(
        "/filings", files={"file": ("meridian-10k.pdf", _pdf_bytes(), "application/pdf")}
    )
    assert response.status_code == 201
    filing_id = response.json()["id"]

    # TestClient runs background tasks synchronously on response close, so
    # by here ingestion has already run.
    status = client.get(f"/filings/{filing_id}").json()
    assert status["status"] == "ready", status
    return filing_id


# --- health & settings ----------------------------------------------------


def test_health_reports_ok(client):
    assert client.get("/health").json()["status"] == "ok"


def test_llm_status_reports_configuration_without_calling_the_provider(client):
    body = client.get("/settings/llm").json()
    assert "configured" in body and "model" in body


# --- filings lifecycle ----------------------------------------------------


def test_uploading_a_filing_indexes_it_and_reports_pages(client, ready_filing):
    filing = client.get(f"/filings/{ready_filing}").json()
    assert filing["num_pages"] == 3
    assert filing["status_label"] == "Ready"


def test_a_non_pdf_upload_is_rejected(client):
    response = client.post("/filings", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 422


def test_re_uploading_the_same_file_returns_the_existing_filing(client):
    # Must be the *same bytes*: reportlab stamps a creation time into each
    # PDF it generates, so calling the builder twice produces genuinely
    # different files that should not (and do not) dedupe.
    data = _pdf_bytes()
    first = client.post("/filings", files={"file": ("a.pdf", data, "application/pdf")})
    second = client.post("/filings", files={"file": ("b.pdf", data, "application/pdf")})

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/filings").json()) == 1


def test_the_pdf_can_be_fetched_back_for_the_viewer(client, ready_filing):
    response = client.get(f"/filings/{ready_filing}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def test_archiving_moves_a_filing_between_the_two_lists(client, ready_filing):
    client.post(f"/filings/{ready_filing}/archive")

    assert client.get("/filings").json() == []
    archived = client.get("/filings", params={"archived": True}).json()
    assert [f["id"] for f in archived] == [ready_filing]

    client.post(f"/filings/{ready_filing}/unarchive")
    assert [f["id"] for f in client.get("/filings").json()] == [ready_filing]


def test_delete_is_soft_and_the_filing_can_be_restored(client, ready_filing):
    assert client.delete(f"/filings/{ready_filing}").status_code == 204
    assert client.get("/filings").json() == []
    assert client.get(f"/filings/{ready_filing}").status_code == 404

    assert client.post(f"/filings/{ready_filing}/restore").status_code == 200
    assert [f["id"] for f in client.get("/filings").json()] == [ready_filing]


def test_renaming_a_filing_changes_its_display_name(client, ready_filing):
    client.patch(f"/filings/{ready_filing}", json={"name": "Meridian FY2023"})
    body = client.get(f"/filings/{ready_filing}").json()

    assert body["display_title"] == "Meridian FY2023"
    # The uploaded filename is a fact about the file, not a label, so a
    # rename leaves it alone.
    assert body["original_name"] != "Meridian FY2023"


# --- accounts -------------------------------------------------------------


def test_the_app_is_usable_signed_out(client):
    assert client.get("/auth/me").json() is None
    assert client.get("/filings").status_code == 200


def test_signup_login_and_profile_update(client):
    signup = client.post(
        "/auth/signup",
        json={"email": "a@example.com", "display_name": "Ada", "password": "correcthorse"},
    )
    assert signup.status_code == 200
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=headers).json()["display_name"] == "Ada"

    renamed = client.patch("/auth/me", json={"display_name": "Ada L"}, headers=headers)
    assert renamed.json()["display_name"] == "Ada L"

    assert client.post("/auth/login", json={"email": "a@example.com", "password": "correcthorse"}).status_code == 200


def test_password_change_requires_the_current_password(client):
    signup = client.post(
        "/auth/signup",
        json={"email": "b@example.com", "display_name": "Bea", "password": "correcthorse"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['token']}"}

    wrong = client.post(
        "/auth/me/password",
        json={"current_password": "nope", "new_password": "newpassword123"},
        headers=headers,
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/auth/me/password",
        json={"current_password": "correcthorse", "new_password": "newpassword123"},
        headers=headers,
    )
    assert ok.status_code == 204
    assert client.post(
        "/auth/login", json={"email": "b@example.com", "password": "newpassword123"}
    ).status_code == 200


def test_a_signed_in_user_does_not_see_guest_filings(client, ready_filing):
    signup = client.post(
        "/auth/signup",
        json={"email": "c@example.com", "display_name": "Cy", "password": "correcthorse"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['token']}"}

    assert client.get("/filings", headers=headers).json() == []
    assert len(client.get("/filings").json()) == 1


def test_duplicate_signup_is_rejected(client):
    payload = {"email": "d@example.com", "display_name": "Dee", "password": "correcthorse"}
    client.post("/auth/signup", json=payload)
    assert client.post("/auth/signup", json=payload).status_code == 409


def test_a_short_password_is_rejected(client):
    response = client.post(
        "/auth/signup",
        json={"email": "e@example.com", "display_name": "Eve", "password": "short"},
    )
    assert response.status_code == 422


# --- chat -----------------------------------------------------------------


@requires_llm
def test_asking_a_grounded_question_returns_a_verified_citation(client, ready_filing):
    response = client.post(
        "/chat/ask",
        json={"filing_id": ready_filing, "question": "What was total revenue in fiscal 2023?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert "184.6" in body["answer"]
    assert body["page"] == 2
    assert "184.6" in body["quote"]
    assert body["session_id"] and body["message_id"]


@requires_llm
def test_an_unanswerable_question_is_declined_with_what_was_considered(client, ready_filing):
    response = client.post(
        "/chat/ask",
        json={"filing_id": ready_filing, "question": "Who is the chief executive officer?"},
    )

    body = response.json()
    assert body["found"] is False
    assert body["answer"] == ""
    assert body["reason"]


@requires_llm
def test_history_persists_and_carries_the_citation(client, ready_filing):
    ask = client.post(
        "/chat/ask",
        json={"filing_id": ready_filing, "question": "What was total revenue in fiscal 2023?"},
    ).json()

    messages = client.get(f"/chat/sessions/{ask['session_id']}/messages").json()
    assert len(messages) == 1
    # the stored citation is what lets clicking an old answer reopen the page
    assert messages[0]["page"] == 2
    assert "184.6" in messages[0]["quote"]


@requires_llm
def test_a_session_is_titled_after_its_first_question(client, ready_filing):
    ask = client.post(
        "/chat/ask",
        json={"filing_id": ready_filing, "question": "What was total revenue in fiscal 2023?"},
    ).json()

    sessions = client.get("/chat/sessions").json()
    session = next(s for s in sessions if s["id"] == ask["session_id"])
    assert session["title"].startswith("What was total revenue")


@requires_llm
def test_follow_up_questions_stay_in_the_same_session(client, ready_filing):
    first = client.post(
        "/chat/ask", json={"filing_id": ready_filing, "question": "What was total revenue?"}
    ).json()
    second = client.post(
        "/chat/ask",
        json={
            "filing_id": ready_filing,
            "question": "What about goodwill impairment?",
            "session_id": first["session_id"],
        },
    ).json()

    assert second["session_id"] == first["session_id"]
    assert len(client.get(f"/chat/sessions/{first['session_id']}/messages").json()) == 2


@requires_llm
def test_feedback_can_be_recorded_against_an_answer(client, ready_filing):
    ask = client.post(
        "/chat/ask", json={"filing_id": ready_filing, "question": "What was total revenue?"}
    ).json()

    assert client.post(
        f"/chat/messages/{ask['message_id']}/feedback", json={"feedback": 1}
    ).status_code == 204

    messages = client.get(f"/chat/sessions/{ask['session_id']}/messages").json()
    assert messages[0]["feedback"] == 1


@requires_llm
def test_history_is_searchable(client, ready_filing):
    client.post(
        "/chat/ask",
        json={"filing_id": ready_filing, "question": "What was the goodwill impairment charge?"},
    )

    hits = client.get("/chat/search", params={"q": "goodwill"}).json()
    assert len(hits) >= 1
    assert "goodwill" in hits[0]["question"].lower()


def test_asking_about_a_missing_filing_is_a_404(client):
    response = client.post(
        "/chat/ask", json={"filing_id": "does-not-exist", "question": "anything"}
    )
    assert response.status_code == 404


def test_an_empty_question_is_rejected(client, ready_filing):
    response = client.post("/chat/ask", json={"filing_id": ready_filing, "question": "   "})
    assert response.status_code == 422


def test_a_filing_still_resolves_after_the_checkout_is_moved(client, ready_filing, monkeypatch):
    """Renaming or moving the project directory must not orphan filings.

    stored_path is recorded absolute, so it stops resolving the moment the
    checkout moves. The layout underneath is deterministic, so the recorded
    path is a hint and the location is re-derived when it fails.
    """
    from app.api.deps import get_filing_repo  # noqa: F401  (documents the seam)
    from app.db.session import create_pool

    pool = create_pool(TEST_DATABASE_URL)
    try:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE filings SET stored_path = %s WHERE id = %s",
                (r"C:\somewhere\that\no\longer\exists\x.pdf", ready_filing),
            )
            conn.commit()
    finally:
        pool.close()

    # The bytes are still on disk where the layout says they should be.
    assert client.get(f"/filings/{ready_filing}/pdf").status_code == 200
