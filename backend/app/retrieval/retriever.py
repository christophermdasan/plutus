"""Adaptive hybrid retrieval: BM25 + vectors, fused, reranked, escalated.

Three layers, each doing what it is actually good at:

1. **Recall (hybrid).** BM25 catches exact financial vocabulary - "goodwill
   impairment", "SOFR", a specific line-item name - where lexical overlap is
   the whole signal. Dense vectors catch paraphrase, where the analyst's
   wording never appears in the filing. Neither alone is sufficient, so both
   run and Reciprocal Rank Fusion merges them: a passage both retrievers
   like outranks one only a single retriever likes.

2. **Precision (rerank).** RRF gives an ordering but not a *meaning* - its
   scores are arbitrary (~0.016 either way). A cross-encoder reads query and
   passage together and returns a calibrated relevance score, which both
   reorders the shortlist properly and tells the caller how much to trust it.

3. **Adaptation (escalate).** Most queries come back decisive: the top
   passage is clearly relevant or clearly not. The costly mistakes live in
   the ambiguous middle, where answering risks a wrong citation and
   abstaining throws away a real answer. Only in that band does the
   retriever widen the candidate pool and rerank again - so the extra
   compute is spent exclusively on the queries where it changes the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.domain.models import Passage
from app.retrieval.bm25_index import BM25Index
from app.retrieval.fusion import reciprocal_rank_fusion

# The escalation band lives in settings because it is expressed on the
# reranker's score scale, and the reranker is swappable - a constant here
# would silently mean something different the moment that model changed.


@dataclass
class RetrievalResult:
    passages: list[Passage] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    escalated: bool = False

    @property
    def top_score(self) -> float | None:
        return self.scores[0] if self.scores else None

    def pages(self) -> list[int]:
        """Distinct pages represented, best-scoring first."""
        seen: list[int] = []
        for passage in self.passages:
            if passage.page not in seen:
                seen.append(passage.page)
        return seen


class HybridRetriever:
    def __init__(
        self,
        passages: list[Passage],
        vector_store,
        embedder,
        reranker,
        filing_id: str,
    ):
        self._passages_by_id = {p.id: p for p in passages}
        self._bm25 = BM25Index(passages)
        self._vector_store = vector_store
        self._embedder = embedder
        self._reranker = reranker
        self._filing_id = filing_id

        self.initial_pool_size = settings.candidate_pool
        self.last_pool_size = 0

    def _candidates(self, query: str, query_embedding: list[float], pool: int) -> list[Passage]:
        lexical = [pid for pid, _ in self._bm25.search(query, top_k=pool)]
        semantic = [
            pid
            for pid, _ in self._vector_store.search(
                query_embedding, top_k=pool, filing_id=self._filing_id
            )
        ]
        fused = reciprocal_rank_fusion([lexical, semantic])
        return [
            self._passages_by_id[pid]
            for pid, _ in fused
            if pid in self._passages_by_id
        ]

    def _rerank(self, query: str, candidates: list[Passage], top_k: int) -> tuple[list[Passage], list[float]]:
        ranked = self._reranker.rank(query, [p.text for p in candidates], top_k=top_k)
        return [candidates[i] for i, _ in ranked], [score for _, score in ranked]

    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        top_k = top_k or settings.context_passages
        if not self._passages_by_id:
            self.last_pool_size = 0
            return RetrievalResult()

        query_embedding = self._embedder.embed_query(query)

        pool = self.initial_pool_size
        self.last_pool_size = pool
        candidates = self._candidates(query, query_embedding, pool)[: settings.rerank_candidates]
        if not candidates:
            return RetrievalResult()

        passages, scores = self._rerank(query, candidates, top_k)
        best = scores[0] if scores else None

        # Ambiguous: neither clearly answerable nor clearly absent. Widen
        # once and look again before committing to answer or abstain.
        if best is not None and settings.escalate_above < best < settings.escalate_below:
            wider_pool = pool * settings.escalation_factor
            wider = self._candidates(query, query_embedding, wider_pool)[
                : settings.rerank_candidates * settings.escalation_factor
            ]
            if len(wider) > len(candidates):
                self.last_pool_size = wider_pool
                passages, scores = self._rerank(query, wider, top_k)
                return RetrievalResult(passages=passages, scores=scores, escalated=True)

        return RetrievalResult(passages=passages, scores=scores)
