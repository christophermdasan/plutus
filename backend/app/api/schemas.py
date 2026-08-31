"""Request/response DTOs.

Deliberately separate from the domain models: what the API accepts and
returns is a contract with the frontend and should be able to change
independently of internal representation. It also makes it structurally
impossible to leak a field like `password_hash` by serialising a domain
object directly.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.domain.models import Answer, ChatMessage, ChatSession, Filing, User

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8


# --- auth / account -------------------------------------------------------


class SignupRequest(BaseModel):
    email: str
    display_name: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v.lower().strip()

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name is required.")
        return v.strip()

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD:
            raise ValueError(f"Password must be at least {_MIN_PASSWORD} characters.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name is required.")
        return v.strip()


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD:
            raise ValueError(f"Password must be at least {_MIN_PASSWORD} characters.")
        return v


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, email=user.email, display_name=user.display_name)


class AuthResponse(BaseModel):
    token: str
    user: UserOut


# --- filings --------------------------------------------------------------


class FilingOut(BaseModel):
    id: str
    original_name: str
    display_title: str
    status: str
    status_label: str
    num_pages: int | None
    size_bytes: int | None
    company_name: str | None
    filing_type: str | None
    fiscal_period: str | None
    suggested_questions: list[str]
    error: str | None
    is_archived: bool
    created_at: datetime | None
    # Which viewer can display this filing. Derived rather than stored: it is
    # a property of the file on disk, not a separate fact to keep in sync.
    media_kind: str

    @classmethod
    def of(cls, filing: Filing) -> "FilingOut":
        return cls(
            id=filing.id,
            original_name=filing.original_name,
            display_title=filing.display_title,
            status=str(filing.status),
            status_label=filing.status.label,
            num_pages=filing.num_pages,
            size_bytes=filing.size_bytes,
            company_name=filing.company_name,
            filing_type=filing.filing_type,
            fiscal_period=filing.fiscal_period,
            suggested_questions=filing.suggested_questions,
            error=filing.error,
            media_kind="html"
            if (filing.stored_path or filing.original_name or "").lower().endswith(
                (".htm", ".html", ".xhtml")
            )
            else "pdf",
            is_archived=filing.is_archived,
            created_at=filing.created_at,
        )


class RenameFilingRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name is required.")
        return v.strip()


# --- chat -----------------------------------------------------------------


class AskRequest(BaseModel):
    filing_id: str
    question: str
    session_id: int | None = None

    @field_validator("question")
    @classmethod
    def _question(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Ask a question first.")
        return v.strip()


class ConsideredOut(BaseModel):
    page: int
    excerpt: str
    score: float


class CitationOut(BaseModel):
    page: int
    quote: str
    # The number printed on that page. `page` is what the source
    # viewer navigates by; this is what the reader is shown.
    label: int | None = None


class AnswerOut(BaseModel):
    message_id: int
    session_id: int
    question: str
    found: bool
    answer: str
    # `page`/`quote` remain the primary citation - the one the source drawer
    # opens to, and what stored history carries. `citations` is every place
    # the figure is reported, so the reader can check it against the
    # statement, the MD&A or a note as they prefer.
    page: int | None
    quote: str
    citations: list[CitationOut]
    reason: str
    considered: list[ConsideredOut]
    latency_ms: int
    model: str | None

    @classmethod
    def of(
        cls, answer: Answer, *, question: str, session_id: int, message_id: int
    ) -> "AnswerOut":
        return cls(
            message_id=message_id,
            session_id=session_id,
            question=question,
            found=answer.found,
            answer=answer.answer,
            page=answer.citation.page if answer.citation else None,
            quote=answer.citation.quote if answer.citation else "",
            citations=[CitationOut(page=c.page, quote=c.quote, label=c.label) for c in answer.citations],
            reason=answer.reason,
            considered=[
                ConsideredOut(page=c.page, excerpt=c.excerpt, score=c.score)
                for c in answer.considered
            ],
            latency_ms=answer.latency_ms,
            model=answer.model,
        )


class MessageOut(BaseModel):
    id: int
    session_id: int
    question: str
    answer: str
    found: bool
    page: int | None
    quote: str
    # Reloaded history offers the same alternate locations the live answer
    # did, rather than only the page it happened to open to.
    citations: list[CitationOut]
    reason: str
    latency_ms: int
    feedback: int | None
    created_at: datetime | None

    @classmethod
    def of(cls, message: ChatMessage) -> "MessageOut":
        return cls(
            id=message.id,
            session_id=message.session_id,
            question=message.question,
            answer=message.answer,
            found=message.found,
            page=message.page,
            quote=message.quote,
            citations=[CitationOut(page=c.page, quote=c.quote, label=c.label) for c in message.citations],
            reason=message.reason,
            latency_ms=message.latency_ms,
            feedback=message.feedback,
            created_at=message.created_at,
        )


class SessionOut(BaseModel):
    id: int
    filing_id: str
    title: str
    message_count: int
    updated_at: datetime | None

    @classmethod
    def of(cls, session: ChatSession) -> "SessionOut":
        return cls(
            id=session.id,
            filing_id=session.filing_id,
            title=session.title,
            message_count=session.message_count,
            updated_at=session.updated_at,
        )


class RenameSessionRequest(BaseModel):
    title: str


class FeedbackRequest(BaseModel):
    feedback: int | None

    @field_validator("feedback")
    @classmethod
    def _feedback(cls, v: int | None) -> int | None:
        if v not in (None, 1, -1):
            raise ValueError("Feedback must be 1, -1, or null.")
        return v


# --- settings -------------------------------------------------------------


class LLMStatusOut(BaseModel):
    configured: bool
    model: str
    base_url: str
    ok: bool | None = None
    message: str | None = None
    latency_ms: int | None = None
