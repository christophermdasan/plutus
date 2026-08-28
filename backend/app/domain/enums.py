from __future__ import annotations

from enum import StrEnum


class FilingStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (FilingStatus.READY, FilingStatus.FAILED)

    @property
    def label(self) -> str:
        return {
            FilingStatus.QUEUED: "Queued",
            FilingStatus.PARSING: "Reading filing",
            FilingStatus.CHUNKING: "Splitting into passages",
            FilingStatus.EMBEDDING: "Embedding text",
            FilingStatus.INDEXING: "Building index",
            FilingStatus.READY: "Ready",
            FilingStatus.FAILED: "Failed",
        }[self]


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
