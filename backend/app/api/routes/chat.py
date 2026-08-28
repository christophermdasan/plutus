from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Chat, Chats, WorkspaceId
from app.api.schemas import (
    AnswerOut,
    AskRequest,
    FeedbackRequest,
    MessageOut,
    RenameSessionRequest,
    SessionOut,
)
from app.exceptions import NotFoundError

router = APIRouter(prefix="/chat", tags=["chat"])


def _owned_session(chats, session_id: int, workspace: int | None):
    """A session, but only the caller's own.

    Session ids are sequential integers, so an id is not a secret and cannot
    stand in for authorisation: without this, anyone could read, rename or
    delete another account's conversations by counting upwards. Reported as
    missing rather than forbidden, so the API does not confirm the id exists.
    """
    session = chats.get_session(session_id)
    if session is None or session.user_id != workspace:
        raise NotFoundError("That conversation no longer exists.")
    return session


@router.post("/ask", response_model=AnswerOut)
def ask(payload: AskRequest, chat: Chat, workspace: WorkspaceId):
    answer, session, message_id = chat.ask(
        filing_id=payload.filing_id,
        question=payload.question,
        user_id=workspace,
        session_id=payload.session_id,
    )
    return AnswerOut.of(
        answer, question=payload.question, session_id=session.id, message_id=message_id
    )


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(chats: Chats, workspace: WorkspaceId, filing_id: str | None = None):
    return [SessionOut.of(s) for s in chats.list_sessions(workspace, filing_id)]


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: int, chats: Chats, workspace: WorkspaceId):
    _owned_session(chats, session_id, workspace)
    return [MessageOut.of(m) for m in chats.list_messages(session_id)]


@router.patch("/sessions/{session_id}", response_model=SessionOut)
def rename_session(
    session_id: int, payload: RenameSessionRequest, chats: Chats, workspace: WorkspaceId
):
    _owned_session(chats, session_id, workspace)
    chats.rename_session(session_id, payload.title)
    return SessionOut.of(chats.get_session(session_id))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, chats: Chats, workspace: WorkspaceId):
    _owned_session(chats, session_id, workspace)
    chats.soft_delete_session(session_id)


@router.post("/messages/{message_id}/feedback", status_code=204)
def set_feedback(
    message_id: int, payload: FeedbackRequest, chats: Chats, workspace: WorkspaceId
):
    # Rating had no ownership check at all - a message id was enough to
    # write into anyone else's history.
    session = chats.session_for_message(message_id)
    if session is None or session.user_id != workspace:
        raise NotFoundError("That answer no longer exists.")
    chats.set_feedback(message_id, payload.feedback)


@router.get("/search", response_model=list[MessageOut])
def search(q: str, chats: Chats, workspace: WorkspaceId):
    if not q.strip():
        return []
    return [MessageOut.of(m) for m in chats.search_messages(workspace, q.strip())]
