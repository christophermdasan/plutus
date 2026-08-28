import pytest

from app.domain.models import Passage
from app.exceptions import LLMError
from app.qa.answer_service import AnswerService
from app.qa.llm_client import LLMAnswer
from app.retrieval.retriever import RetrievalResult

PAGE_TEXT = {
    4: "Total revenue for fiscal year 2023 was $12.4 million, up from $10.1 million.",
}
PASSAGE = Passage(id="p1", filing_id="f1", page=4, text=PAGE_TEXT[4])


class FakeRetriever:
    def __init__(self, result: RetrievalResult):
        self.result = result

    def retrieve(self, question, top_k=None):
        return self.result


class FakeLLM:
    model = "test-model"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def answer(self, question, passages):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _relevant() -> RetrievalResult:
    return RetrievalResult(passages=[PASSAGE], scores=[8.0])


def _service(llm):
    return AnswerService(llm, relevance_threshold=0.0)


def test_returns_a_cited_answer_when_everything_agrees():
    llm = FakeLLM(
        LLMAnswer(
            found=True,
            answer="$12.4 million",
            page=4,
            quote="Total revenue for fiscal year 2023 was $12.4 million",
        )
    )

    result = _service(llm).answer("What was revenue?", "f1", FakeRetriever(_relevant()), PAGE_TEXT)

    assert result.found is True
    assert result.citation.page == 4
    assert result.answer == "$12.4 million"
    assert result.model == "test-model"


def test_declines_without_calling_the_model_when_nothing_is_relevant():
    # spending an LLM call on irrelevant context invites invention
    llm = FakeLLM()
    retrieval = RetrievalResult(passages=[PASSAGE], scores=[-9.0])

    result = _service(llm).answer("Unrelated?", "f1", FakeRetriever(retrieval), PAGE_TEXT)

    assert result.found is False
    assert llm.calls == 0


def test_declines_when_retrieval_found_nothing_at_all():
    llm = FakeLLM()
    result = _service(llm).answer("q", "f1", FakeRetriever(RetrievalResult()), PAGE_TEXT)

    assert result.found is False
    assert llm.calls == 0


def test_declines_when_the_model_says_it_found_nothing():
    llm = FakeLLM(LLMAnswer(found=False))
    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)
    assert result.found is False


def test_declines_when_the_quote_is_not_actually_on_the_page():
    # the model sounded confident; the text does not exist. This is the
    # -1 case the whole design exists to prevent.
    llm = FakeLLM(
        LLMAnswer(found=True, answer="$99.9 million", page=4, quote="Net income was $99.9 million")
    )

    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)

    assert result.found is False
    assert "could not be verified" in result.reason


def test_declines_when_the_answer_states_a_number_absent_from_its_own_quote():
    llm = FakeLLM(
        LLMAnswer(
            found=True,
            answer="$6.7 million",
            page=4,
            quote="Total revenue for fiscal year 2023 was $12.4 million",
        )
    )

    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)
    assert result.found is False


def test_declines_when_the_cited_page_is_outside_the_filing():
    llm = FakeLLM(
        LLMAnswer(found=True, answer="$12.4 million", page=99, quote="Total revenue")
    )

    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)

    assert result.found is False
    assert "outside this filing" in result.reason


def test_a_refusal_reports_what_was_considered_so_it_can_be_explained():
    llm = FakeLLM(LLMAnswer(found=False))
    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)

    assert result.considered
    assert result.considered[0].page == 4
    assert result.considered[0].score == 8.0
    assert result.considered[0].excerpt


def test_provider_failures_propagate_rather_than_masquerading_as_a_refusal():
    # a broken API key must not look like "the filing doesn't say" - that
    # would quietly turn an outage into wrong answers
    llm = FakeLLM(error=LLMError("provider is down"))

    with pytest.raises(LLMError):
        _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)


def test_latency_is_measured_on_every_path():
    llm = FakeLLM(LLMAnswer(found=False))
    result = _service(llm).answer("q", "f1", FakeRetriever(_relevant()), PAGE_TEXT)
    assert result.latency_ms >= 0
