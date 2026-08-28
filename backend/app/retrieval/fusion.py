"""Reciprocal rank fusion for combining BM25 and dense-vector rankings.

Financial questions often hinge on an exact line-item name where lexical
match wins, and sometimes on paraphrased concepts where semantic similarity
wins - so neither signal alone is trusted; results that rank well in
multiple retrievers are pushed to the top.
"""

from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = 60
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
