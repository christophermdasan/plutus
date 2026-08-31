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
    # The number printed on that page, when the document's own numbering
    # runs behind our sequential count (a cover page or contents leaf is
    # counted but not numbered). `page` stays the internal index the source
    # viewer navigates by; `label` is what the reader should be told, so a
    # citation names a page they can actually find. Equal on most filings.
    label: int | None = None

    @property
    def display_page(self) -> int:
        return self.label if self.label is not None else self.page


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
    # Every page the answer rests on. A quarter of real analyst questions
    # combine figures from two statements, and each figure has to be
    # checkable against the page it came from.
    citations: list[Citation] = field(default_factory=list)
    reason: str = ""
    considered: list[RejectedPassage] = field(default_factory=list)
    model: str | None = None
    latency_ms: int = 0

    @property
    def citation(self) -> Citation | None:
        """The first citation.

        The stored chat history, the API response and the source drawer are
        all built around a single page and quote. Keeping this as a property
        means multi-citation answers degrade gracefully everywhere that has
        not been taught about the list yet, rather than breaking.
        """
        return self.citations[0] if self.citations else None


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
    # Every place the figure is reported. `page`/`quote` above remain the
    # primary one the source drawer opens to; this is that plus the
    # alternates, so history reloads with the same evidence the live answer
    # offered.
    citations: list[Citation] = field(default_factory=list)
    reason: str = ""
    latency_ms: int = 0
    model: str | None = None
    feedback: int | None = None  # +1 / -1 / None
    created_at: datetime | None = None
