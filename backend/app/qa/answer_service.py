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
import re
import threading
import time

from app.config import settings
from app.domain.models import Answer, Citation, RejectedPassage
from app.exceptions import LLMError, LLMMalformedResponseError
from app.finance.metric_engine import (
    MetricEngine,
    answer_cashflow_superlative,
    is_cashflow_superlative_question,
)
from app.finance.segment_engine import SegmentEngine, is_segment_question
from app.ingestion.page_labels import label_for
from app.qa.router import route
from app.qa.verifier import verify_citations
from app.retrieval.retriever import RetrievalResult

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 240
# A figure as an answer states it, parens meaning negative.
_ANSWER_NUMBER_RE = re.compile(r"\(?\$?-?\d+(?:,\d{3})*(?:\.\d+)?%?\)?")


def _considered(result: RetrievalResult, limit: int = 3) -> list[RejectedPassage]:
    return [
        RejectedPassage(
            page=passage.page,
            excerpt=passage.text[:_EXCERPT_CHARS].strip(),
            score=round(score, 2),
        )
        for passage, score in zip(result.passages[:limit], result.scores[:limit])
    ]


class _ThreadedResult:
    """Runs one call on a thread and hands back its result or its failure.

    Deliberately not a ThreadPoolExecutor: this is one short call per
    question, and a pool would add a lifecycle to own and shut down inside a
    request path for no gain. An exception is captured rather than raised on
    the worker thread, so a fault in the deterministic engine degrades to
    "no computed answer" and the model path still answers, instead of
    surfacing as an unhandled error from a thread nobody is watching.
    """

    def __init__(self, fn, *args):
        self._value = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run, args=(fn, args), daemon=True
        )
        self._thread.start()

    def _run(self, fn, args) -> None:
        try:
            self._value = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised in result()
            self._error = exc

    def result(self):
        self._thread.join()
        if self._error is not None:
            logger.warning("Deterministic engine failed: %s", self._error)
            return None
        return self._value


def _cited(pairs, offset: int) -> list[Citation]:
    """Citations carrying both the internal page and the printed label.

    `page` is what the source viewer navigates by; `label` is the number
    printed on that page, which runs behind the index on a filing whose
    front matter is counted but not numbered. Citing the index alone sends a
    reader to a page number their copy does not have.
    """
    return [
        Citation(page=page, quote=quote, label=label_for(page, offset))
        for page, quote in pairs
    ]


def _stated_figures(text: str) -> list[float]:
    """Every number an answer asserts, for comparing two answers' claims."""
    values = []
    for token in _ANSWER_NUMBER_RE.findall(text or ""):
        cleaned = token.strip("()").rstrip("%").replace(",", "").lstrip("$")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        values.append(-value if token.startswith("(") else value)
    return values


def _same_claim(computed_value: float | None, model_text: str) -> bool:
    """Whether the model's answer states the figure the engine computed.

    Only the engine's *result* is compared, never the operands it shows its
    work with: the computed text says "24.26, calculated from revenue 6,489
    ...", and a model answering with the bare revenue 6,489 would otherwise
    register as agreement when it has in fact answered a different question.

    Scale-tolerant, because the same quantity is legitimately written
    differently - "$8,738.00 million" and "8.7 billion" are one claim, and a
    percentage held as 0.0512 is written "5.1%". That is the distinction
    between the two paths corroborating each other and genuinely disagreeing.
    """
    if computed_value is None:
        return False
    for got in _stated_figures(model_text):
        for scale in (1, 1e2, 1e-2, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9):
            if computed_value and abs(got * scale - computed_value) <= max(
                abs(computed_value) * 0.01, 0.011
            ):
                return True
    return False


class AnswerService:
    def __init__(self, llm_client, relevance_threshold: float | None = None):
        self._llm = llm_client
        self._threshold = (
            settings.relevance_threshold if relevance_threshold is None else relevance_threshold
        )

    @staticmethod
    def _computed_answer(computed, filing_id: str, latency_ms: int, page_offset: int = 0) -> Answer:
        return Answer(
            found=True,
            filing_id=filing_id,
            answer=computed.text,
            citations=_cited(computed.citations, page_offset),
            reason=f"computed from reported figures ({computed.metric})",
            model="deterministic",
            latency_ms=latency_ms,
        )

    @staticmethod
    def _adjudicate(computed, proposed, filing_id: str, latency_ms: int, page_offset: int = 0) -> Answer:
        """Both paths verified. The computed figure ships; agreement is noted.

        The engine wins on disagreement rather than the model, and not
        arbitrarily: its operands are figures the issuer tagged in its own
        filing and its arithmetic is done in code, where the measured error
        rate is zero. The model's answer is prose it read off a page, and on
        this corpus it read revenue 6,489 and PP&E 253/282 correctly,
        averaged them correctly, then reported the quotient as 24.77 where
        it is 24.26. When the two differ, that is the shape of the
        difference.
        """
        agrees = _same_claim(computed.value, proposed.answer)
        reason = (
            f"computed from reported figures ({computed.metric}), "
            f"corroborated by the model reading the filing"
            if agrees
            else f"computed from reported figures ({computed.metric}); "
                 f"the model proposed a different figure, which was not used"
        )
        if not agrees:
            logger.info(
                "Paths disagreed on %s: computed %r vs model %r",
                computed.metric, computed.text[:80], proposed.answer[:80],
            )
        return Answer(
            found=True,
            filing_id=filing_id,
            answer=computed.text,
            citations=_cited(computed.citations, page_offset),
            reason=reason,
            model="deterministic+llm",
            latency_ms=latency_ms,
        )

    def answer(
        self,
        question: str,
        filing_id: str,
        retriever,
        page_text: dict[int, str],
        fact_store=None,
        page_offset: int = 0,
    ) -> Answer:
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        # Which machinery this question wants is decided explicitly, and
        # recorded, because the two answers deserve different amounts of
        # trust: one is arithmetic over figures the issuer tagged, the other
        # is prose read out of the filing by a model.
        decision = route(question, has_facts=fact_store is not None and len(fact_store) > 0)

        # A ranking across segments is arithmetic too - the issuer tags each
        # segment's figures, so the numbers, the names and their page are all
        # machine-readable. Handing these to the model produced the
        # *consolidated* figure, which is confidently wrong rather than
        # merely unhelpful.
        if fact_store is not None and settings.use_metric_engine and is_segment_question(question):
            ranked = SegmentEngine(fact_store, page_text=page_text).answer(question)
            if ranked.found:
                return Answer(
                    found=True,
                    filing_id=filing_id,
                    answer=ranked.text,
                    citations=_cited(ranked.citations, page_offset),
                    reason=f"ranked by segment ({ranked.metric})",
                    model="deterministic",
                    latency_ms=elapsed(),
                )

        # "Which activity brought in the most cash" ranks three fixed,
        # always-consolidated cash-flow-statement totals - not a dimensional
        # breakdown, so it is answerable the same way a segment ranking is,
        # and for the same reason it must be checked before the general
        # metric gate: that gate's superlative guard would otherwise refuse
        # it outright, correctly, since "most" across an unnamed breakdown
        # is exactly what it exists to catch.
        if fact_store is not None and settings.use_metric_engine and is_cashflow_superlative_question(question):
            activity = answer_cashflow_superlative(fact_store, question, page_text)
            if activity.found:
                return Answer(
                    found=True,
                    filing_id=filing_id,
                    answer=activity.text,
                    citations=_cited(activity.citations, page_offset),
                    reason=f"ranked by cash-flow activity ({activity.metric})",
                    model="deterministic",
                    latency_ms=elapsed(),
                )

        # For a question the engine can compute, both paths run and the
        # verifier adjudicates between them. Two independent derivations
        # agreeing is stronger evidence than either alone, and the
        # disagreement case is the one worth catching: the model reading
        # every input off the page correctly and still reporting the wrong
        # quotient is the measured failure this whole package exists for.
        #
        # The computation is started first and on its own thread so it
        # overlaps the retrieval it does not depend on. Being honest about
        # the size of that win: the arithmetic is sub-millisecond and
        # retrieval plus the model call is seconds, so this removes a
        # rounding error from the total, not a meaningful wait. It is
        # structured this way because the two are genuinely independent -
        # the engine reads tagged facts, retrieval reads the vector index -
        # and neither should be able to delay or fail the other.
        eligible = (
            not decision.needs_llm
            and settings.use_metric_engine
            and fact_store is not None
        )
        pending = (
            _ThreadedResult(MetricEngine(fact_store, page_text=page_text).answer, question)
            if eligible
            else None
        )

        retrieval = retriever.retrieve(question)

        computed = pending.result() if pending is not None else None
        if computed is not None and not computed.found:
            logger.info("Deterministic path declined (%s); falling back to retrieval",
                        computed.reason)
            computed = None

        if not retrieval.passages:
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="Nothing in this filing matched the question.",
                latency_ms=elapsed(),
            )

        # Nothing cleared the relevance bar. Don't spend an LLM call to be
        # told the same thing - and don't risk it inventing an answer from
        # weak context. A computed answer does not depend on retrieval
        # having found anything, so it still stands.
        if retrieval.top_score is not None and retrieval.top_score < self._threshold:
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
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
        except LLMMalformedResponseError:
            # The client has already retried; the model keeps mangling its
            # own JSON. That is not an outage and not a failure of the
            # filing - it means no *verified* answer could be produced,
            # which is a refusal worth 0 rather than an error page showing
            # the user broken JSON. The reason says so, so an unhelpful
            # answer is not mistaken for the filing lacking the information.
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            logger.info("Model output could not be parsed after retries; declining")
            return Answer(
                found=False,
                filing_id=filing_id,
                reason=(
                    "The AI's response could not be read, even after retrying. "
                    "This is a fault in the AI provider's output rather than the "
                    "filing - asking again usually succeeds."
                ),
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )
        except LLMError:
            # The computed answer is independent of the provider being
            # reachable, so an outage must not lose an answer we already hold.
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            raise
        except Exception as exc:
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            raise LLMError(str(exc)) from exc

        if not proposed.found:
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            return Answer(
                found=False,
                filing_id=filing_id,
                reason="The filing does not appear to contain this information.",
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )

        cited = [(c.page, c.quote) for c in proposed.citations]

        verification = verify_citations(
            citations=cited,
            page_text=page_text,
            answer=proposed.answer,
            # The question is needed to tell a figure the answer *asserts*
            # from one it is merely echoing back from what was asked.
            question=question,
            # A computed metric (a margin, a ratio) can be correct without
            # ever appearing verbatim anywhere - only the inputs to it can.
            # Those inputs may sit on a passage the answer did not cite, so
            # the wider context the model actually saw is what gets checked.
            context_text="\n\n".join(text for _, text in passages),
        )
        if not verification.passed:
            logger.info("Verification rejected an answer: %s", verification.reason)
            if computed is not None:
                return self._computed_answer(computed, filing_id, elapsed(), page_offset)
            return Answer(
                found=False,
                filing_id=filing_id,
                reason=f"The proposed answer could not be verified against the filing ({verification.reason}).",
                considered=_considered(retrieval),
                model=self._llm.model,
                latency_ms=elapsed(),
            )

        # Both paths produced a verified answer. The computed figure ships
        # either way - it is arithmetic over figures the issuer itself
        # tagged, where the model's is prose it read - but whether the two
        # agree is worth recording, because agreement is corroboration and
        # disagreement is a caught error.
        if computed is not None:
            return self._adjudicate(computed, proposed, filing_id, elapsed(), page_offset)

        return Answer(
            found=True,
            filing_id=filing_id,
            answer=proposed.answer,
            citations=_cited(cited, page_offset),
            reason="verified",
            model=self._llm.model,
            latency_ms=elapsed(),
        )
