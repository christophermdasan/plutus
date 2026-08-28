"""Pure domain models - no I/O, no framework types, no persistence concerns.

Repositories return these; services operate on them; the API layer converts
them into response DTOs. Keeping them free of SQL/HTTP details is what lets
the storage and transport layers change without touching business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import FilingStatus, MessageRole


@dataclass(frozen=True)
class User:
    id: int
    email: str
    display_name: str
    created_at: datetime | None = None


@dataclass
class Filing:
    id: str
    original_name: str
    status: FilingStatus
    user_id: int | None = None
    stored_path: str | None = None
    content_hash: str | None = None
    error: str | None = None
    num_pages: int | None = None
    size_bytes: int | None = None
    # Extracted at ingest so the UI can show "Meridian Robotics - 10-K - FY2023"
    # instead of a filename.
    custom_title: str | None = None
    company_name: str | None = None
    filing_type: str | None = None
    fiscal_period: str | None = None
    suggested_questions: list[str] = field(default_factory=list)
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_ready(self) -> bool:
        return self.status is FilingStatus.READY

    @property
    def display_title(self) -> str:
        """Human label for the sidebar.

        A name the user set wins outright: renaming is an explicit override,
        and a label derived from the document is only a stand-in for not
        having been told what to call it.
        """
        if self.custom_title:
            return self.custom_title
        if self.company_name:
            parts = [self.company_name]
            if self.filing_type:
                parts.append(self.filing_type)
            if self.fiscal_period:
                parts.append(self.fiscal_period)
            return " · ".join(parts)
        return self.original_name


@dataclass
class Passage:
    """A retrievable unit of a filing, anchored to the page it came from.

    Passages are smaller than a page (a full 10-K page dilutes an embedding),
    but every one carries the page it belongs to, so citations stay
    page-accurate.
    """

    id: str
    filing_id: str
    page: int
    text: str
    ordinal: int = 0


@dataclass
class Citation:
    page: int
    quote: str


@dataclass
class RejectedPassage:
    """A candidate the system saw but did not answer from.

    Surfaced in the UI when the system declines, so "not found" is
    explainable rather than opaque - the user can see what was considered.
    """

    page: int
    excerpt: str
    score: float


@dataclass
class Answer:
    found: bool
    filing_id: str
    answer: str = ""
    citation: Citation | None = None
    reason: str = ""
    considered: list[RejectedPassage] = field(default_factory=list)
    model: str | None = None
    latency_ms: int = 0


@dataclass
class ChatSession:
    id: int
    filing_id: str
    title: str
    user_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0


@dataclass
class ChatMessage:
    id: int
    session_id: int
    role: MessageRole
    question: str = ""
    answer: str = ""
    found: bool = False
    page: int | None = None
    quote: str = ""
    reason: str = ""
    latency_ms: int = 0
    model: str | None = None
    feedback: int | None = None  # +1 / -1 / None
    created_at: datetime | None = None
