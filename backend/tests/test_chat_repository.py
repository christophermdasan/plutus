import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres


@pytest.fixture
def filing(filings):
    return filings.create(
        filing_id="f1",
        original_name="apple-10k.pdf",
        user_id=None,
        stored_path="/storage/f1.pdf",
        size_bytes=100,
        content_hash="h1",
    )


def _answer(chats, session_id, question="What was revenue?", **overrides):
    payload = dict(
        question=question,
        answer="$184.6 million",
        found=True,
        page=4,
        quote="Total revenue was $184.6 million",
        reason="verified",
        latency_ms=900,
        model="openai/gpt-oss-120b",
    )
    payload.update(overrides)
    return chats.add_message(session_id, **payload)


def test_a_session_starts_empty_and_is_retrievable(chats, filing):
    session = chats.create_session(filing.id, user_id=None, title="Revenue questions")

    fetched = chats.get_session(session.id)
    assert fetched.title == "Revenue questions"
    assert chats.list_messages(session.id) == []


def test_messages_store_the_citation_needed_to_reopen_the_source(chats, filing):
    session = chats.create_session(filing.id, None)
    _answer(chats, session.id)

    message = chats.list_messages(session.id)[0]
    assert message.found is True
    assert message.page == 4
    assert "184.6" in message.quote
    assert message.model == "openai/gpt-oss-120b"


def test_an_abstention_is_recorded_just_like_an_answer(chats, filing):
    session = chats.create_session(filing.id, None)
    _answer(
        chats,
        session.id,
        question="What was the dividend?",
        answer="",
        found=False,
        page=None,
        quote="",
        reason="no confident match",
    )

    message = chats.list_messages(session.id)[0]
    assert message.found is False
    assert message.page is None


def test_messages_come_back_in_the_order_they_were_asked(chats, filing):
    session = chats.create_session(filing.id, None)
    for i in range(3):
        _answer(chats, session.id, question=f"question {i}")

    assert [m.question for m in chats.list_messages(session.id)] == [
        "question 0",
        "question 1",
        "question 2",
    ]


def test_sessions_are_scoped_to_a_workspace(chats, filings, users):
    alice = users.create("a@example.com", "Alice", "hash")
    filings.create(
        filing_id="f2", original_name="x.pdf", user_id=alice.id,
        stored_path="/s/f2.pdf", size_bytes=1, content_hash="h2",
    )
    filings.create(
        filing_id="f3", original_name="y.pdf", user_id=None,
        stored_path="/s/f3.pdf", size_bytes=1, content_hash="h3",
    )
    chats.create_session("f2", alice.id, "Alice chat")
    chats.create_session("f3", None, "Guest chat")

    assert [s.title for s in chats.list_sessions(alice.id)] == ["Alice chat"]
    assert [s.title for s in chats.list_sessions(None)] == ["Guest chat"]


def test_listing_can_be_filtered_to_one_filing(chats, filings, filing):
    filings.create(
        filing_id="f2", original_name="x.pdf", user_id=None,
        stored_path="/s/f2.pdf", size_bytes=1, content_hash="h2",
    )
    chats.create_session("f1", None, "About f1")
    chats.create_session("f2", None, "About f2")

    titles = [s.title for s in chats.list_sessions(None, filing_id="f1")]
    assert titles == ["About f1"]


def test_sessions_report_their_message_count(chats, filing):
    session = chats.create_session(filing.id, None)
    _answer(chats, session.id)
    _answer(chats, session.id)

    assert chats.list_sessions(None)[0].message_count == 2


def test_a_soft_deleted_session_disappears_from_listings(chats, filing):
    session = chats.create_session(filing.id, None)
    chats.soft_delete_session(session.id)

    assert chats.list_sessions(None) == []
    assert chats.get_session(session.id) is None


def test_renaming_a_session_sticks(chats, filing):
    session = chats.create_session(filing.id, None)
    chats.rename_session(session.id, "Impairment deep dive")
    assert chats.get_session(session.id).title == "Impairment deep dive"


def test_feedback_can_be_set_and_cleared(chats, filing):
    session = chats.create_session(filing.id, None)
    message = _answer(chats, session.id)

    chats.set_feedback(message.id, 1)
    assert chats.list_messages(session.id)[0].feedback == 1

    chats.set_feedback(message.id, None)
    assert chats.list_messages(session.id)[0].feedback is None


def test_search_finds_past_questions_and_answers_within_the_workspace(chats, filing):
    session = chats.create_session(filing.id, None)
    _answer(chats, session.id, question="What was goodwill impairment?")
    _answer(chats, session.id, question="What was the maturity date?")

    hits = chats.search_messages(None, "goodwill")
    assert len(hits) == 1
    assert "goodwill" in hits[0].question.lower()


def test_search_does_not_cross_workspaces(chats, filings, users):
    alice = users.create("a@example.com", "Alice", "hash")
    filings.create(
        filing_id="f2", original_name="x.pdf", user_id=alice.id,
        stored_path="/s/f2.pdf", size_bytes=1, content_hash="h2",
    )
    alice_session = chats.create_session("f2", alice.id)
    _answer(chats, alice_session.id, question="Alice private question about revenue")

    assert chats.search_messages(None, "private") == []
    assert len(chats.search_messages(alice.id, "private")) == 1
