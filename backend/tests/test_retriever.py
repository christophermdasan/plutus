from app.config import settings
from app.domain.models import Passage
from app.retrieval.retriever import HybridRetriever, RetrievalResult

PASSAGES = [
    Passage(id="p1", filing_id="f1", page=1, text="Total revenue increased eight percent year over year"),
    Passage(id="p2", filing_id="f1", page=2, text="Goodwill impairment charge recorded in the third quarter"),
    Passage(id="p3", filing_id="f1", page=3, text="The board of directors approved a share repurchase program"),
    Passage(id="p4", filing_id="f1", page=4, text="Research and development expense rose to $28.9 million"),
]


class FakeVectorStore:
    """Returns passages in a fixed order, ignoring the query vector."""

    def __init__(self, order: list[str] | None = None):
        self.order = order or ["p2", "p1", "p3", "p4"]
        self.last_limit: int | None = None

    def search(self, query_embedding, top_k, filing_id):
        self.last_limit = top_k
        return [(pid, 1.0 - i * 0.1) for i, pid in enumerate(self.order[:top_k])]


class FakeEmbedder:
    def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class FakeReranker:
    """Scores passages from a lookup table so tests control relevance."""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.call_count = 0
        self.last_batch_size = 0
        self.batch_sizes: list[int] = []

    def rank(self, query, documents, top_k=None):
        self.call_count += 1
        self.last_batch_size = len(documents)
        self.batch_sizes.append(len(documents))
        ranked = sorted(
            ((i, self.scores.get(doc, -10.0)) for i, doc in enumerate(documents)),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:top_k] if top_k else ranked


def _retriever(reranker, vector_store=None):
    return HybridRetriever(
        passages=PASSAGES,
        vector_store=vector_store or FakeVectorStore(),
        embedder=FakeEmbedder(),
        reranker=reranker,
        filing_id="f1",
    )


def test_returns_passages_ordered_by_reranker_score():
    reranker = FakeReranker({PASSAGES[1].text: 9.0, PASSAGES[0].text: 2.0})
    result = _retriever(reranker).retrieve("goodwill impairment", top_k=2)

    assert isinstance(result, RetrievalResult)
    assert result.passages[0].page == 2
    assert result.scores[0] == 9.0


def test_top_score_reflects_the_best_reranked_candidate():
    reranker = FakeReranker({PASSAGES[1].text: 7.5})
    assert _retriever(reranker).retrieve("goodwill impairment").top_score == 7.5


def test_respects_top_k():
    reranker = FakeReranker({p.text: float(i) for i, p in enumerate(PASSAGES)})
    assert len(_retriever(reranker).retrieve("anything", top_k=2).passages) == 2


def test_no_passages_yields_an_empty_result_without_calling_the_reranker():
    reranker = FakeReranker({})
    retriever = HybridRetriever([], FakeVectorStore(), FakeEmbedder(), reranker, "f1")

    result = retriever.retrieve("anything")

    assert result.passages == []
    assert result.top_score is None
    assert reranker.call_count == 0


def test_a_decisive_first_pass_does_not_trigger_escalation():
    # clearly relevant top hit: no reason to spend more compute widening
    reranker = FakeReranker({PASSAGES[1].text: 9.0})
    _retriever(reranker).retrieve("goodwill impairment")

    assert reranker.call_count == 1


def test_an_ambiguous_first_pass_escalates_to_a_wider_candidate_pool():
    # every candidate lands in the uncertain middle, so the retriever should
    # widen and rerank again rather than decide on a weak signal - this is
    # exactly the band where a wrong call costs +1 or -1 on the scoring policy.
    # Needs a filing large enough that widening actually surfaces passages
    # the first pass didn't see - so comfortably more than rerank_candidates,
    # which this was previously sized under.
    many = [
        Passage(id=f"p{i}", filing_id="f1", page=i, text=f"Passage number {i} discussing results")
        for i in range(settings.rerank_candidates * 4)
    ]
    # Mid-band for the configured reranker: the escalation window is on that
    # model's score scale, so the fake has to speak the same units.
    ambiguous = (settings.escalate_above + settings.escalate_below) / 2
    reranker = FakeReranker({p.text: ambiguous for p in many})
    retriever = HybridRetriever(
        passages=many,
        vector_store=FakeVectorStore([p.id for p in many]),
        embedder=FakeEmbedder(),
        reranker=reranker,
        filing_id="f1",
    )

    retriever.retrieve("something ambiguous")

    assert reranker.call_count == 2
    assert retriever.last_pool_size > retriever.initial_pool_size
    # The second pass must actually consider more than the first - that is the
    # whole point of escalating. Compared against the first pass rather than
    # against rerank_candidates: how many distinct candidates fusion can
    # produce depends on how much the two retrievers overlap, so the cap is
    # not the right yardstick.
    assert reranker.batch_sizes[1] > reranker.batch_sizes[0]


def test_a_clearly_irrelevant_first_pass_does_not_escalate():
    # nothing is close; widening will not conjure an answer, so don't pay
    # for a second pass
    reranker = FakeReranker({p.text: -9.0 for p in PASSAGES})
    _retriever(reranker).retrieve("something unrelated")

    assert reranker.call_count == 1
