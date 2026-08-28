from app.config import settings
from app.retrieval.reranker import Reranker

QUERY = "What was the goodwill impairment charge?"

RELEVANT = "During the third quarter we recorded a goodwill impairment charge of $6.4 million."
IRRELEVANT_A = "The board of directors approved a new share repurchase program."
IRRELEVANT_B = "Research and development expense increased to $28.9 million."


def test_rerank_returns_one_score_per_document_in_input_order():
    scores = Reranker().score(QUERY, [RELEVANT, IRRELEVANT_A])
    assert len(scores) == 2
    assert scores[0] > scores[1]


def test_rerank_on_empty_documents_returns_empty_list():
    assert Reranker().score(QUERY, []) == []


def test_the_relevant_passage_lands_on_the_answering_side_of_the_threshold():
    """Separation has to be meaningful *against the configured threshold*.

    This previously asserted a fixed gap of 5 logits, which quietly encoded
    one model's score range: swapping the reranker for a better-discriminating
    one with a narrower range failed the test while improving the system. What
    matters operationally is that a relevant passage is answered from and an
    irrelevant one is refused, so that is what is asserted.
    """
    irrelevant_a, relevant, irrelevant_b = Reranker().score(
        QUERY, [IRRELEVANT_A, RELEVANT, IRRELEVANT_B]
    )

    assert relevant > settings.relevance_threshold, "a true answer would be refused"
    assert max(irrelevant_a, irrelevant_b) < settings.relevance_threshold, (
        "an irrelevant passage would be answered from"
    )


def test_rank_returns_indices_ordered_best_first():
    ranked = Reranker().rank(QUERY, [IRRELEVANT_A, RELEVANT, IRRELEVANT_B])

    assert [index for index, _ in ranked][0] == 1  # RELEVANT was at index 1
    assert len(ranked) == 3
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_respects_top_k():
    ranked = Reranker().rank(QUERY, [IRRELEVANT_A, RELEVANT, IRRELEVANT_B], top_k=1)
    assert len(ranked) == 1
    assert ranked[0][0] == 1


def test_a_question_with_no_supporting_passage_scores_everything_low():
    # the abstention path depends on this: when nothing in the filing answers
    # the question, every candidate must fall below the relevance threshold
    scores = Reranker().score(
        "What was the CEO's total compensation?", [IRRELEVANT_A, IRRELEVANT_B]
    )
    assert max(scores) < 0
