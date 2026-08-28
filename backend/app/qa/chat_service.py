"""Orchestrates one chat turn end to end.

The seam between HTTP and business logic: routes validate and shape, this
does the work - resolve the session, build (or reuse) a retriever for the
filing, answer, persist. Routes never touch the retriever, embedder or LLM
directly.

Retrievers are cached per filing because constructing one means reading the
passage index off disk and building a BM25 index over it. Doing that on
every question would add avoidable latency to a path the user is waiting on.
"""

from __future__ import annotations

import logging
import threading

from app.domain.models import Answer, ChatSession
from app.exceptions import FilingNotReadyError, NotFoundError
from app.retrieval.retriever import HybridRetriever

logger = logging.getLogger(__name__)

# Guards the check-then-build step. Without it, two concurrent questions
# about the same uncached filing would both pay to build the index.
_cache_lock = threading.Lock()

# The first question in a session names it, so history is browsable
# without asking the model to summarise anything.
_TITLE_MAX_CHARS = 60


class ChatService:
    def __init__(
        self,
        filings_repo,
        chat_repo,
        pipeline,
        vector_store,
        embedder,
        reranker,
        answer_service,
        retriever_cache: dict | None = None,
    ):
        self._filings = filings_repo
        self._chats = chat_repo
        self._pipeline = pipeline
        self._vectors = vector_store
        self._embedder = embedder
        self._reranker = reranker
        self._answers = answer_service
        self._cache = retriever_cache if retriever_cache is not None else {}

    def _retriever_for(self, filing_id: str):
        if filing_id in self._cache:
            return self._cache[filing_id]

        with _cache_lock:
            if filing_id not in self._cache:
                passages, page_text = self._pipeline.load_index(filing_id)
                retriever = HybridRetriever(
                    passages=passages,
                    vector_store=self._vectors,
                    embedder=self._embedder,
                    reranker=self._reranker,
                    filing_id=filing_id,
                )
                self._cache[filing_id] = (retriever, page_text)
            return self._cache[filing_id]

    def invalidate(self, filing_id: str) -> None:
        self._cache.pop(filing_id, None)

    def ensure_session(
        self, filing_id: str, user_id: int | None, session_id: int | None
    ) -> ChatSession:
        if session_id is not None:
            session = self._chats.get_session(session_id)
            if session is None:
                raise NotFoundError("That conversation no longer exists.")
            return session
        return self._chats.create_session(filing_id, user_id)

    def ask(
        self,
        filing_id: str,
        question: str,
        user_id: int | None = None,
        session_id: int | None = None,
    ) -> tuple[Answer, ChatSession, int]:
        filing = self._filings.get(filing_id)
        if filing is None:
            raise NotFoundError("Filing not found.")
        if not filing.is_ready:
            raise FilingNotReadyError(
                f"This filing is still being processed ({filing.status.label})."
            )

        session = self.ensure_session(filing_id, user_id, session_id)
        retriever, page_text = self._retriever_for(filing_id)

        answer = self._answers.answer(question, filing_id, retriever, page_text)

        message = self._chats.add_message(
            session.id,
            question=question,
            answer=answer.answer,
            found=answer.found,
            page=answer.citation.page if answer.citation else None,
            quote=answer.citation.quote if answer.citation else "",
            reason=answer.reason,
            latency_ms=answer.latency_ms,
            model=answer.model,
        )

        # Name the conversation after the question that started it.
        if session.title == "New chat":
            title = question.strip()[:_TITLE_MAX_CHARS]
            self._chats.rename_session(session.id, title or "New chat")
            session.title = title

        return answer, session, message.id
