"""Finds the part of a filing a narrative question is asking about.

The numeric half of this system works because `fact_index` can say which
page holds a figure. The prose half had no equivalent, and it shows: of the
85 questions routed to the language model, 37 came back "the filing does
not appear to contain this information" - the model was handed eight
passages and none of them held the answer.

Those questions are not vague. They are asking about specific, named parts
of a document whose structure is prescribed by regulation:

    "Which debt securities are registered to trade..."  -> the cover page
    "Who are the primary customers of Boeing?"          -> Item 1, Business
    "Is the business subject to cyclicality?"           -> Item 1A, Risk Factors
    "Has CVS reported material legal battles?"          -> Item 3, Legal Proceedings
    "What drove operating margin change?"               -> Item 7, MD&A
    "What major acquisitions were made?"                -> Item 8, the notes

A 10-K always carries those headings, in that order. Locating them costs a
regex pass at ingest, and turns "search the whole document" into "search
the twenty pages where this kind of answer lives".

Anchoring, not filtering: the section's pages are *added* to what retrieval
proposed, never substituted for it. A misjudged section costs a context
slot; excluding the right page because the heading was missed would cost
the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "Item 1.", "Item 1A -", "ITEM 7." at the head of a line. Filings vary the
# punctuation and the case; none of them omit the word.
_ITEM_RE = re.compile(r"^\s*item\s+(1a|1b|7a|9a|[1-9]|1[0-6])\s*[.:—–\-]", re.I | re.M)

# A page naming this many distinct items is the table of contents, not a
# section - every item appears there, which would put every section on
# page 2.
_TOC_ITEM_COUNT = 4

# How far into a page to look. A heading introduces its section, so it sits
# near the top; matching further down catches cross-references in prose.
_HEADING_WINDOW = 3000


@dataclass
class SectionIndex:
    """Item number -> the page its section starts on."""

    starts: dict[str, int] = field(default_factory=dict)
    total_pages: int = 0

    @classmethod
    def from_pages(cls, pages: list[str]) -> "SectionIndex":
        starts: dict[str, int] = {}
        for number, text in enumerate(pages, start=1):
            found = {m.group(1).lower() for m in _ITEM_RE.finditer(text[:_HEADING_WINDOW])}
            if len(found) >= _TOC_ITEM_COUNT:
                continue  # contents page
            for item in found:
                # The first real occurrence wins; later ones are
                # cross-references ("see Item 1A") in the body.
                starts.setdefault(item, number)
        return cls(starts=starts, total_pages=len(pages))

    def span(self, item: str) -> tuple[int, int] | None:
        """The page range of a section, up to wherever the next one starts."""
        start = self.starts.get(item)
        if start is None:
            return None
        later = [p for p in self.starts.values() if p > start]
        return start, (min(later) - 1 if later else self.total_pages)

    def pages_for(self, question: str) -> list[int]:
        """Every page of the section that answers this kind of question.

        The whole span, not a capped window. Item 7 runs to fifty pages in a
        large filing and the answer is as likely to be at its end as its
        start - capping the front of it missed the ground truth on five of
        eleven questions tested. The caller treats these as a *preference*
        and lets the reranker choose within them, rather than forcing a
        fixed set of pages into the context.
        """
        item = classify(question)
        if item is None:
            return []
        if item == "cover":
            # Registered securities, trading symbols and the exchange are
            # printed on the cover, before Item 1.
            first_item = min(self.starts.values(), default=2)
            return list(range(1, min(first_item, 4)))
        span = self.span(item)
        if span is None:
            return []
        start, end = span
        return list(range(start, end + 1))


# Question wording -> the item that answers it. Ordered: the first pattern
# to match wins, so the more specific ones are listed first.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("cover", r"registered (?:to trade|pursuant|under)|trading symbol|securities registered"
              r"|national securities exchange|title of (?:each )?class"),
    ("3", r"legal (?:battle|proceeding|dispute|matter)|litigation|lawsuit|court|settlement"),
    ("1a", r"risk factor|cyclical|subject to (?:cyclic|seasonal)|exposure to risk"),
    ("7", r"what drove|driven by|drove (?:the )?(?:change|growth|increase|decrease)"
          r"|management'?s discussion|liquidity and capital|results of operations"),
    ("8", r"acquisition|acquired|discontinued operation|spin[- ]?off|divest|separation of"
          r"|subsequent event|restructuring (?:liability|charge|cost)|supplemental indenture"),
    ("1", r"primary customer|major customer|products and services|major products"
          r"|what industry|primarily operate|geograph|business segment|competitors?"
          r"|number of (?:stores|employees)|key agenda"),
)

_COMPILED = tuple((item, re.compile(pattern, re.I)) for item, pattern in _ROUTES)


def classify(question: str) -> str | None:
    """Which item of the filing answers this kind of question."""
    for item, pattern in _COMPILED:
        if pattern.search(question):
            return item
    return None
