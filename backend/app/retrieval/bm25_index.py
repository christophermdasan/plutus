"""Lexical (keyword) retrieval over passages, via BM25.

Carries the half of retrieval that embeddings are worst at: exact financial
vocabulary. An analyst asking about "goodwill impairment" or "SOFR" wants
the passage containing those literal tokens, and lexical overlap is a
stronger signal for that than semantic similarity.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from app.domain.models import Passage

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A term appearing in more than half a corpus gets negative raw IDF, which
# would penalise a passage for containing a query term - so a document
# holding every term could rank below one holding none. Flooring keeps the
# ordering sane on small filings, where this is common.
_MIN_IDF = 1e-4


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Index:
    def __init__(self, passages: list[Passage]):
        self._ids = [p.id for p in passages]
        tokenized = [_tokenize(p.text) for p in passages]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

        if self._bm25 is not None:
            for term, idf in self._bm25.idf.items():
                if idf < _MIN_IDF:
                    self._bm25.idf[term] = _MIN_IDF

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self._ids, scores), key=lambda pair: pair[1], reverse=True)
        return ranked[:top_k]
