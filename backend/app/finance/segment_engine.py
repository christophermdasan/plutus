"""Answers questions about a company's segments from its own tagging.

"Which of JPM's business segments had the highest net income?" was refused
by the deterministic path and handed to the model, which mostly failed on
it - and when it did answer, it answered with the *consolidated* figure,
which is confidently wrong rather than merely unhelpful.

The answer was in the filing all along. An issuer tags each segment's
figures against a business-segment axis, so the numbers, the segment names
and the page they sit on are all machine-readable:

    ConsumerCommunityBankingMember      2,895M   page 163
    CorporateInvestmentBankMember       4,385M   page 163
    CommercialBankingMember               850M   page 163
    AssetandWealthManagementMember      1,008M   page 163

Ranking those is arithmetic, not judgement. What this module adds over the
consolidated engine is only the grouping - every other property is the
same: figures come from the issuer, the comparison happens in code, and a
question whose operands are missing is refused rather than guessed at.

Only tagged filings can be served this way. The 26 without XBRL keep going
to the retrieval path, where a segment table is at least text a model can
read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.finance.fact_store import FactStore, StoredFact
from app.finance.metric_engine import find_year
from app.finance.ontology import METRICS, find_metric, normalise

# "which segment had the highest/lowest X"
_SUPERLATIVE_RE = re.compile(
    r"\b(highest|largest|biggest|most|greatest|best|top)\b|\b(lowest|smallest|least|worst)\b", re.I
)
_LOWEST_RE = re.compile(r"\b(lowest|smallest|least|worst)\b", re.I)
_SEGMENT_RE = re.compile(
    r"\b(segments?|divisions?|business units?|reporting units?"
    r"|geograph\w*|regions?|countr(?:y|ies)|markets?)\b", re.I
)
# Which breakdown the question means. An issuer reports the same concept
# against both axes, so answering "which segment" from the geographical axis
# names a region that was never a segment - confidently wrong.
_GEOGRAPHY_ASK_RE = re.compile(r"\bgeograph\w*|\bregions?\b|\bcountr(?:y|ies)\b", re.I)

# "jpm:CorporateInvestmentBankMember" -> "Corporate Investment Bank"
_MEMBER_SUFFIX_RE = re.compile(r"(?:Segment)?Member$", re.I)
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def readable(member: str) -> str:
    """The segment name as a person writes it."""
    local = member.split(":", 1)[-1]
    local = _MEMBER_SUFFIX_RE.sub("", local)
    return _CAMEL_RE.sub(" ", local).replace("  ", " ").strip()


def is_segment_question(question: str) -> bool:
    """A ranking across the company's segments, which needs their figures."""
    text = normalise(question)
    return bool(_SEGMENT_RE.search(text) and _SUPERLATIVE_RE.search(text))


@dataclass
class SegmentAnswer:
    found: bool
    text: str = ""
    citations: list[tuple[int, str]] = field(default_factory=list)
    metric: str = ""
    reason: str = ""


class SegmentEngine:
    def __init__(self, store: FactStore, page_text: dict[int, str] | None = None):
        self._store = store
        self._pages = page_text or {}

    def answer(self, question: str) -> SegmentAnswer:
        if not is_segment_question(question):
            return SegmentAnswer(False, reason="not a segment ranking question")

        key = find_metric(question)
        if key is None or key not in METRICS:
            return SegmentAnswer(False, reason="no defined metric named")

        metric = METRICS[key]
        if len(metric.inputs) != 1:
            # A ratio needs each segment to report every operand, which
            # issuers tag inconsistently. Only directly reported quantities
            # are ranked.
            return SegmentAnswer(False, reason="only reported quantities can be ranked by segment")

        concept = metric.inputs[0]
        year = find_year(question) or (self._store.years[0] if self._store.years else None)
        geography = bool(_GEOGRAPHY_ASK_RE.search(normalise(question)))
        values = self._store.segment_values(concept, year, geography=geography)
        if len(values) < 2:
            kind = "geography" if geography else "segment"
            return SegmentAnswer(False, reason=f"{concept} is not reported by {kind} for FY{year}")

        want_lowest = bool(_LOWEST_RE.search(normalise(question)))
        ordered = sorted(values, key=lambda f: f.value, reverse=not want_lowest)
        winner = ordered[0]

        ranking = ", ".join(f"{readable(f.member)} {self._fmt(f.value)}" for f in ordered)
        superlative = "lowest" if want_lowest else "highest"
        text = (
            f"{readable(winner.member)} had the {superlative} "
            f"{metric.key.replace('_', ' ')} in FY{winner.period} at {self._fmt(winner.value)}. "
            f"By segment: {ranking}."
        )
        return SegmentAnswer(
            found=True,
            text=text,
            citations=self._cite(ordered),
            metric=key,
        )

    def _cite(self, facts: list[StoredFact]) -> list[tuple[int, str]]:
        """One citation per page the segment figures were tagged on."""
        from app.finance.metric_engine import find_quote

        seen: list[tuple[int, str]] = []
        for fact in facts:
            if any(page == fact.page for page, _ in seen):
                continue
            quote = find_quote(self._pages.get(fact.page, ""), fact) if self._pages else None
            seen.append((fact.page, quote or f"{readable(fact.member)}: {self._fmt(fact.value)}"))
            if len(seen) >= 3:
                break
        return seen

    @staticmethod
    def _fmt(value: float) -> str:
        millions = value / 1_000_000 if abs(value) >= 1_000_000 else value
        return f"${millions:,.0f} million"
