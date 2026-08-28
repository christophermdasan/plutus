from app.domain.models import Passage
from app.retrieval.bm25_index import BM25Index


def _passages():
    return [
        Passage(id="p1", filing_id="d1", page=1, text="Total revenue increased eight percent year over year"),
        Passage(id="p2", filing_id="d1", page=2, text="Goodwill impairment charge recorded in the third quarter"),
        Passage(id="p3", filing_id="d1", page=3, text="The board of directors approved a new share repurchase program"),
    ]


def test_search_returns_the_lexically_best_matching_passage_first():
    results = BM25Index(_passages()).search("goodwill impairment", top_k=3)
    assert results[0][0] == "p2"


def test_search_respects_top_k():
    assert len(BM25Index(_passages()).search("revenue", top_k=1)) == 1


def test_search_on_an_empty_index_returns_nothing():
    assert BM25Index([]).search("anything", top_k=5) == []


def test_a_matching_passage_never_scores_below_a_non_matching_one():
    # regression: raw BM25 IDF goes negative when a query term appears in
    # more than half of a small corpus, which would rank a passage WITHOUT
    # the term above one that contains it. Small filings hit this easily.
    passages = [
        Passage(id="p1", filing_id="d1", page=1, text="Total revenue for fiscal 2023 was $12.4 million."),
        Passage(id="p2", filing_id="d1", page=2, text="Goodwill impairment of $1.1 million was recorded in Q3."),
        Passage(
            id="p3",
            filing_id="d1",
            page=3,
            text="Total revenue was $12.4 million. Goodwill impairment of $1.1 million was recorded.",
        ),
    ]
    scores = dict(BM25Index(passages).search("goodwill impairment", top_k=3))

    assert scores["p2"] > scores["p1"]
