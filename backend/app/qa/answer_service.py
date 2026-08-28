"""Turns a question into a verified, cited answer - or a refusal.

This is where the scoring asymmetry is enforced. Under the scoring policy a
correct cited answer earns +1, an honest "not found" earns 0, and a confident
wrong answer costs -1. So every exit path defaults to refusing, and an answer
is only returned when three independent things agree:

1. Retrieval found something the reranker scores as genuinely relevant.
2. The model claims it found an answer.
3. The claimed quote actually exists on the claimed page, and any number in
   the answer actually appears in that quote.

The model's own confidence is never sufficient on its own - that is the
whole point of step 3.

When it does refuse, it returns the passages it considered and their scores,
so the UI can show *why* rather than an opaque shrug.
"""

from __future__ import annotations

import logging
import time

from app.config import settings
from app.domain.models import Answer, Citation, RejectedPassage
from app.exceptions import LLMError
from app.qa.verifier import verify_answer
from app.retrieval.retriever import RetrievalResult

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 240


def _considered(result: RetrievalResult, limit: int = 3) -> list[RejectedPassage]:
    return [
        RejectedPassage(
            page=passage.page,
            excerpt=passage.text[:_EXCERPT_CHARS].strip(),
            score=round(score, 2),
        )
        for passage, score in zip(result.passages[:limit], result.scores[:limit])
    ]


class AnswerService:
    def __init__(self, llm_client, relevance_threshold: float | None = None):
        self._llm = llm_client
        self._threshold = (
            settings.relevance_threshold if relevance_threshold is None else relevance_threshold
        )

    def answer(
        self, question: str, filing_id: str, retriever, page_text: dict[int, str]
    ) -> Answer:
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        retrieval = retriever.retrieve(question)

        if not retrieval.passages:
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="Nothing in this filing matched the question.",
                latency_ms=elapsed(),
            )

        # Nothing cleared the relevance bar. Don't spend an LLM call to be
        # told the same thing - and don't risk it inventing an answer from
        # weak context.
        if retrieval.top_score is not None and retrieval.top_score < self._threshold:
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="No passage in this filing was relevant enough to answer from.",
                considered=_considered(retrieval),
                latency_ms=elapsed(),
            )

        passages = [(p.page, p.text) for p in retrieval.passages]

        try:
            proposed = self._llm.answer(question, passages)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        if not proposed.found:
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="The filing does not appear to contain this information.",
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )

        if proposed.page not in page_text:
            logger.warning("Model cited page %s, outside the filing", proposed.page)
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="The proposed citation pointed outside this filing.",
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )

        verification = verify_answer(
            page_text=page_text[proposed.page],
            quote=proposed.quote,
            answer=proposed.answer,
            # The question is needed to tell a figure the answer *asserts*
            # from one it is merely echoing back from what was asked.
            question=question,
        )
        if not verification.passed:
            logger.info("Verification rejected an answer: %s", verification.reason)
            return Answer(
                found=False,
                filing_id=filing_id,
                reason=f"The proposed answer could not be verified against the filing ({verification.reason}).",
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )

        return Answer(
            found=True,
            filing_id=filing_id,
            answer=proposed.answer,
            citation=Citation(page=proposed.page, quote=proposed.quote),
            reason="verified",
            model=self._llm.model,
            latency_ms=elapsed(),
        )
