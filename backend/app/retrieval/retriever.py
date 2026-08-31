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
from app.retrieval.fact_index import FactIndex
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.section_index import SectionIndex
from app.retrieval.query_expansion import expand_query

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
        xbrl_pages: dict[str, list[int]] | None = None,
        page_text: list[str] | None = None,
    ):
        self._passages_by_id = {p.id: p for p in passages}
        self._bm25 = BM25Index(passages)
        self._vector_store = vector_store
        self._embedder = embedder
        self._reranker = reranker
        self._filing_id = filing_id
        self._passages_by_page: dict[int, list[Passage]] = {}
        for passage in passages:
            self._passages_by_page.setdefault(passage.page, []).append(passage)
        self._facts = FactIndex.from_passages(passages, xbrl_pages)
        self._sections = SectionIndex.from_pages(page_text or [])

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

    def _with_anchored(
        self,
        query: str,
        candidates: list[Passage],
        top_k: int,
    ) -> tuple[list[Passage], list[float]]:
        """Rerank, then guarantee the statement pages a place in the result.

        Reranking alone is not enough for these questions. On the 3M FY2018
        10-K the consolidated cash-flow statement - the page holding the
        capital-expenditure figure, and the page the answer key names - was
        present in the candidate pool and the cross-encoder ranked it 26th
        of 50, so a top-8 window never showed it to the model. The
        cross-encoder is comparing a natural-language question against a
        grid of numbers and labels; that is the comparison it is weakest at,
        and no threshold tuning fixes it.

        So a small number of slots are reserved for passages from pages the
        fact index nominated, and the rest of the window is filled by rerank
        order as before. The reserved passages are still ordered among
        themselves by the reranker, and the page still has to survive the
        model choosing to cite it and the verifier confirming the quote - so
        a wrong nomination costs a context slot, not a wrong answer.
        """
        anchor_pages = self._facts.pages_for(query)
        ranked_passages, scores = self._rerank(query, candidates, top_k=len(candidates))

        # Narrative questions have no fact anchor, and returning here on
        # that basis skipped the section preference below - which is the
        # only mechanism that helps them.
        anchors = [
            p for p in self._anchor_passages(anchor_pages) if p not in ranked_passages
        ] if anchor_pages else []
        # Score the anchors that retrieval never proposed, so every passage
        # in the result carries a real relevance score - the abstain
        # threshold reads the top one and must not see a placeholder.
        if anchors:
            extra = self._reranker.rank(query, [p.text for p in anchors], top_k=len(anchors))
            ranked_passages = ranked_passages + [anchors[i] for i, _ in extra]
            scores = scores + [s for _, s in extra]

        order = sorted(range(len(ranked_passages)), key=lambda i: -scores[i])

        # A narrative question names a part of the filing whose location is
        # prescribed - registered securities on the cover, customers in
        # Item 1, what drove a margin change in Item 7. Passages from that
        # section are preferred within the window, which is what turns
        # "search 300 pages" into "search the twenty where this lives".
        # A preference, not a filter: if the heading was missed or the
        # question was misjudged, retrieval's own ordering still stands.
        section = set(self._sections.pages_for(query))
        if section:
            in_section = [i for i in order if ranked_passages[i].page in section]
            rest = [i for i in order if ranked_passages[i].page not in section]
            reserved = min(settings.section_slots, top_k, len(in_section))
            order = in_section[:reserved] + [i for i in (in_section[reserved:] + rest)]
            order.sort(key=lambda i: (0 if i in set(in_section[:reserved]) else 1, -scores[i]))

        chosen = order[:top_k]
        present = {ranked_passages[i].page for i in chosen}

        # Added *after* the reranked window rather than carved out of it.
        # Reserving slots instead was measured to lose answers: a
        # fixed-asset-turnover question needs PP&E from the balance sheet
        # and revenue from the income statement, on different pages, and
        # evicting three reranked passages to seat the balance sheet threw
        # out the revenue the ratio needed - so the question that had been
        # answered correctly became an abstention. Anchoring is meant to add
        # a page retrieval missed, never to displace one it found.
        for i in order:
            if len(chosen) >= top_k + settings.anchor_slots:
                break
            page = ranked_passages[i].page
            if page in anchor_pages and page not in present:
                chosen.append(i)
                present.add(page)

        chosen.sort(key=lambda i: -scores[i])
        return [ranked_passages[i] for i in chosen], [scores[i] for i in chosen]

    def _anchor_passages(self, pages: list[int]) -> list[Passage]:
        found: list[Passage] = []
        for page in pages:
            found.extend(self._passages_by_page.get(page, []))
        return found

    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        top_k = top_k or settings.context_passages
        if not self._passages_by_id:
            self.last_pool_size = 0
            return RetrievalResult()

        # Optional, and off by default - see settings.expand_queries. It
        # widens recall for questions whose vocabulary misses, but measured
        # end to end it also perturbed questions that were already working,
        # for a net loss. The same failure is addressed more precisely by
        # the reserved anchor slots in _with_anchored, which add pages
        # without altering the query every other question depends on.
        retrieval_query = expand_query(query) if settings.expand_queries else query
        query_embedding = self._embedder.embed_query(retrieval_query)

        pool = self.initial_pool_size
        self.last_pool_size = pool
        candidates = self._candidates(retrieval_query, query_embedding, pool)[
            : settings.rerank_candidates
        ]
        if not candidates:
            return RetrievalResult()

        passages, scores = self._with_anchored(query, candidates, top_k)
        best = scores[0] if scores else None

        # Ambiguous: neither clearly answerable nor clearly absent. Widen
        # once and look again before committing to answer or abstain.
        if best is not None and settings.escalate_above < best < settings.escalate_below:
            wider_pool = pool * settings.escalation_factor
            wider = self._candidates(retrieval_query, query_embedding, wider_pool)[
                : settings.rerank_candidates * settings.escalation_factor
            ]
            if len(wider) > len(candidates):
                self.last_pool_size = wider_pool
                passages, scores = self._with_anchored(query, wider, top_k)
                return RetrievalResult(passages=passages, scores=scores, escalated=True)

        return RetrievalResult(passages=passages, scores=scores)
