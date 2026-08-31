"""Every reported figure in a filing, with the page it was printed on.

Two extractors feeding one store, so everything downstream is unaware of
which kind of filing it is reading:

1. **Inline XBRL.** Since the SEC's 2019-2021 phase-in, issuers tag each
   figure with a US-GAAP concept. This is authoritative - the company is
   stating what the number means - and it carries the period and scale.

2. **Statement line parsing.** The rest (pre-2019 10-Ks, most 8-Ks) carry
   no tags, so the statements are located by how *complete* a page looks
   against the line items that statement is made of, and the figures are
   read off the matching rows. Heuristic, and known to be, but it is the
   only route into those filings.

The store is the input to `metric_engine`, which computes ratios from it in
code. That division matters: a model reading 6,489 and 267.5 off a page
correctly and then reporting their quotient as 24.77 (it is 24.26) is the
measured failure this whole path exists to remove. Numbers come from here;
arithmetic happens in code; the model is left doing what it is reliable at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from app.finance.ontology import CONCEPTS, Concept
from app.ingestion.xbrl_facts import _GEOGRAPHY_AXIS_RE
from app.ingestion.xbrl_facts import Fact as XbrlFact

# A figure in a serialized statement row: 1,234 / (1,234) / $ 1,234 / 1234.5
_CELL_NUMBER_RE = re.compile(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True)
class StoredFact:
    concept: str        # ontology key, e.g. "revenue"
    value: float
    period: str         # fiscal year as "2019", or "" when unknown
    page: int
    source: str         # "xbrl" | "statement"
    instant: bool = False
    member: str = ""    # segment this figure is scoped to, "" if consolidated
    # The dimension `member` hangs from, which distinguishes a business
    # segment from a geography - different questions, different pools.
    axis: str = ""


def _fiscal_year(period_end: str) -> str:
    """The fiscal year a period belongs to.

    A filing's fiscal year is the year its period *ends* in for almost every
    US registrant, including the January year-ends common in retail, where
    "FY2023" ends January 2024. Retailers are the exception this gets wrong,
    and the caller tolerates a one-year miss by trying the neighbouring year.
    """
    match = _YEAR_RE.search(period_end or "")
    return match.group(0) if match else ""


class FactStore:
    """Concept + fiscal year -> value, for one filing."""

    def __init__(self, facts: list[StoredFact]):
        self._facts = facts
        self._by_concept: dict[str, list[StoredFact]] = {}
        self._segments: dict[str, list[StoredFact]] = {}
        for fact in facts:
            # Segment figures are held apart, never mixed into the
            # consolidated index. Answering "what was revenue" with one
            # division's revenue is the failure this separation prevents.
            target = self._segments if fact.member else self._by_concept
            target.setdefault(fact.concept, []).append(fact)

    def segment_values(
        self, concept: str, year: str | None = None, geography: bool = False
    ) -> list[StoredFact]:
        """Every segment's figure for a concept, one entry per segment.

        A filing tags the same segment on several pages (the segment note,
        the MD&A discussion); the first sighting of each is enough.

        `geography` selects which breakdown is wanted. An issuer reports the
        same concept against several axes, and mixing them ranks a region
        against a business unit: JPMorgan's FY2022 net income by geography
        peaks at North America $29.2bn, well above its largest *segment*,
        the Corporate Investment Bank at $15.0bn. Asked "which segment", the
        honest pool is the segment axis alone.
        """
        candidates = [
            f for f in self._segments.get(concept, [])
            if (year is None or f.period == year)
            and bool(_GEOGRAPHY_AXIS_RE.search(f.axis)) == geography
        ]
        seen: dict[str, StoredFact] = {}
        for fact in candidates:
            seen.setdefault(fact.member, fact)
        return list(seen.values())

    def __len__(self) -> int:
        return len(self._facts)

    @property
    def concepts(self) -> set[str]:
        return set(self._by_concept)

    @property
    def years(self) -> list[str]:
        return sorted({f.period for f in self._facts if f.period}, reverse=True)

    def get(self, concept: str, year: str | None = None) -> StoredFact | None:
        """The figure for a concept, preferring the requested fiscal year.

        Falls back to the filing's most recent year when the requested one
        is absent, because a 10-K answers "FY2018" questions with its own
        FY2018 column whether or not the question named the year the same
        way the filing does.
        """
        candidates = self._by_concept.get(concept, [])
        if not candidates:
            return None
        if year:
            exact = [f for f in candidates if f.period == year]
            if exact:
                # Several tags can carry the same concept; prefer the
                # issuer's own tagging over a parsed line.
                return sorted(exact, key=lambda f: 0 if f.source == "xbrl" else 1)[0]
        by_recency = sorted(candidates, key=lambda f: (f.period, 0 if f.source == "xbrl" else 1), reverse=True)
        return by_recency[0] if not year else None

    def locations(self, concept: str, year: str | None = None) -> list[StoredFact]:
        """Every place the filing reports this figure, best source first.

        A figure is routinely printed more than once - the statement, the
        MD&A discussion of it, and a note - and each is a truthful place to
        cite. Returning all of them lets the answer name the one it used and
        still offer the others for the reader to check, rather than picking
        one and discarding evidence that is equally valid.
        """
        candidates = [
            f for f in self._by_concept.get(concept, [])
            if year is None or f.period == year
        ]
        seen: dict[int, StoredFact] = {}
        for fact in sorted(candidates, key=lambda f: 0 if f.source == "xbrl" else 1):
            # One entry per page; the same figure tagged twice on a page is
            # one location as far as a reader is concerned.
            seen.setdefault(fact.page, fact)
        return list(seen.values())

    def prior(self, concept: str, year: str) -> StoredFact | None:
        """The same concept one fiscal year earlier - the other half of an average."""
        try:
            return self.get(concept, str(int(year) - 1))
        except (TypeError, ValueError):
            return None


# --- XBRL -------------------------------------------------------------------

_XBRL_TO_CONCEPT: dict[str, str] = {
    tag: concept.key for concept in CONCEPTS.values() for tag in concept.xbrl
}


def from_xbrl(facts: list[XbrlFact]) -> list[StoredFact]:
    stored: list[StoredFact] = []
    for fact in facts:
        concept = _XBRL_TO_CONCEPT.get(fact.local_name)
        if concept is None:
            continue
        stored.append(
            StoredFact(
                concept=concept,
                value=fact.value,
                period=_fiscal_year(fact.period_end),
                page=fact.page,
                source="xbrl",
                instant=fact.is_instant,
                member=fact.member,
                axis=fact.axis,
            )
        )
    return stored


# --- untagged filings -------------------------------------------------------

# A page is treated as a statement when it carries most of the line items
# that statement is composed of. Heading alone is not enough: it appears in
# contents pages, cross-references and MD&A discussion, none of which hold
# the figures.
#
# The items are deliberately the *full* phrases a statement prints, not
# fragments. Loose fragments ("operating activities", "net cash") match the
# MD&A page discussing cash flow just as well as the statement itself: on
# 3M's FY2018 10-K that scored the MD&A page above the real statement and
# the capex figure was cited to page 46 instead of page 60.
_STATEMENT_ITEMS: dict[str, tuple[str, ...]] = {
    "cash_flow": (
        "cash flows from operating activities",
        "cash flows from investing activities",
        "cash flows from financing activities",
        "net cash provided by",
        "depreciation and amortization",
        "purchases of property",
        "dividends paid",
        "cash and cash equivalents at end",
    ),
    "balance_sheet": (
        "total current assets", "total current liabilities", "total assets",
        "accounts payable", "retained earnings", "total liabilities",
        "cash and cash equivalents", "goodwill",
    ),
    "income": (
        "cost of", "operating income", "net income", "per share",
        "income tax", "gross", "total revenues",
    ),
}
_STATEMENT_THRESHOLD = 0.6


def _cell_values(line: str) -> list[float]:
    """Figures on a statement row, left to right.

    Columns run most-recent-first in US filings, so the first value is the
    reporting year and the second the comparative.
    """
    values: list[float] = []
    for token in _CELL_NUMBER_RE.findall(line):
        negative = "(" in token
        cleaned = token.strip("()$ ").replace(",", "").replace("$", "").strip()
        if not cleaned or cleaned in {"-", "."}:
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        values.append(-value if negative else value)
    return values


def _statement_pages(pages: list[str]) -> dict[str, int]:
    """The page of each primary statement.

    Neither the best-scoring page nor the first one is reliable on its own,
    and the two filings that break each rule break it in opposite
    directions:

    - AMD FY2015: the real income statement scores 0.71 on page 56; the
      quarterly supplement scores 0.86 on page 94. Taking the maximum read
      revenue as $958M, one quarter's worth.
    - Walmart FY2019: the MD&A discussion clears the bar on page 34 while
      the statement itself is on page 48. Taking the first read revenue as
      $14.1M against operating income of $21,957M - a margin of 729,892%.

    What holds in both, and generally, is that the primary statements are
    printed together: Walmart's sit on 48/50/52, AMD's on 56/58/60. So the
    candidates are scored as a *set*, and the tightest cluster wins.
    """
    candidates: dict[str, list[int]] = {}
    for index, text in enumerate(pages, start=1):
        lowered = text.lower()
        for statement, items in _STATEMENT_ITEMS.items():
            score = sum(1 for item in items if item in lowered) / len(items)
            if score >= _STATEMENT_THRESHOLD:
                candidates.setdefault(statement, []).append(index)

    if not candidates:
        return {}

    names = sorted(candidates)
    best: tuple[int, dict[str, int]] | None = None
    for combination in product(*(candidates[name] for name in names)):
        spread = max(combination) - min(combination)
        if best is None or spread < best[0]:
            best = (spread, dict(zip(names, combination)))
    return best[1] if best else {}


_UNIT_RE = re.compile(r"\(?\s*(?:amounts?\s+)?in\s+(thousands|millions|billions)", re.I)
_UNIT_SCALE = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}


def _unit_scale(text: str) -> float:
    """The multiplier a statement's own header declares.

    Statements print "(in millions)" or "(in thousands)" once, at the top,
    and every figure below is in those units. XBRL carries this per fact;
    parsed tables do not, and reading the numbers at face value gets the
    magnitude wrong by three orders: Netflix's FY2017 current liabilities
    came out as "$5.47 million" where the filing reports $5,466 million.
    Absent a declaration the figures are taken as written.
    """
    match = _UNIT_RE.search(text[:2000])
    return _UNIT_SCALE[match.group(1).lower()] if match else 1.0


def _years_on_page(text: str) -> list[str]:
    """Column years, in the order the header presents them."""
    seen: list[str] = []
    for match in _YEAR_RE.finditer(text[:1500]):
        year = match.group(0)
        if year not in seen and 1990 < int(year) < 2100:
            seen.append(year)
    return seen


def from_statements(pages: list[str]) -> list[StoredFact]:
    """Read figures off the statement pages of an untagged filing."""
    statement_pages = _statement_pages(pages)
    if not statement_pages:
        return []

    stored: list[StoredFact] = []
    for statement, page_no in statement_pages.items():
        text = pages[page_no - 1]
        years = _years_on_page(text)
        scale = _unit_scale(text)
        concepts = [c for c in CONCEPTS.values() if c.statement == statement]

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            label = stripped.split("|")[0].lower() if "|" in stripped else stripped.lower()
            for concept in concepts:
                if not any(re.search(pattern, label) for pattern in concept.labels):
                    continue
                values = _cell_values(stripped)
                if not values:
                    continue
                # Pair each column with its year. A row with more figures
                # than the header has years (a stray footnote reference,
                # say) is truncated rather than guessed at.
                for offset, value in enumerate(values[: max(len(years), 1)]):
                    period = years[offset] if offset < len(years) else ""
                    stored.append(
                        StoredFact(
                            concept=concept.key,
                            value=value * scale,
                            period=period,
                            page=page_no,
                            source="statement",
                            instant=concept.instant,
                        )
                    )
                break
    return stored


def build(pages: list[str], xbrl_facts: list[XbrlFact] | None = None) -> FactStore:
    """Facts from the issuer's tags where present, parsed statements otherwise.

    Both are kept when both exist: a tagged filing can still leave a line
    untagged, and a parsed row is better than nothing. Tags win on lookup.
    """
    facts = from_xbrl(xbrl_facts or [])
    tagged_concepts = {f.concept for f in facts if not f.member}
    for fact in from_statements(pages):
        if fact.concept not in tagged_concepts:
            facts.append(fact)
    return FactStore(facts)
