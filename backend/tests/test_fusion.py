from app.retrieval.fusion import reciprocal_rank_fusion


def test_single_ranking_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    ids = [doc_id for doc_id, _ in fused]
    assert ids == ["a", "b", "c"]


def test_agreement_across_rankings_reinforces_top_result():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]])
    ids = [doc_id for doc_id, _ in fused]
    assert ids[0] == "a"


def test_item_ranked_high_in_both_lists_beats_item_ranked_high_in_only_one():
    # "b" is #1 in list one but absent from list two; "c" is #2 in both -
    # cross-list agreement should let "c" outrank a single-list #1.
    fused = reciprocal_rank_fusion([["b", "c", "d"], ["c", "a", "e"]])
    scores = dict(fused)
    assert scores["c"] > scores["b"]


def test_doc_only_in_one_ranking_is_still_included():
    fused = reciprocal_rank_fusion([["a", "b"], ["c"]])
    ids = {doc_id for doc_id, _ in fused}
    assert ids == {"a", "b", "c"}


def test_empty_rankings_returns_empty_list():
    assert reciprocal_rank_fusion([]) == []


def test_empty_ranking_lists_returns_empty_list():
    assert reciprocal_rank_fusion([[], []]) == []
