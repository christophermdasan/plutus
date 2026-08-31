"""Finds the pages a financial figure is actually printed on.

The measured failure this exists for: asked for "capital expenditure", the
system never showed the model the page holding it. The words do not match -
3M's cash-flow statement says "Purchases of property, plant and equipment
(PP&E)" - so BM25 finds nothing, and the page is a dense grid of numbers
whose embedding carries little topical signal, so vectors rank it far down.
On the 3M FY2018 10-K the correct page ranked 26th of 50.

The fix does not try to make the general retrievers cleverer. It answers a
narrower question directly: *which page is the consolidated cash-flow
statement, and does it contain a capital-expenditure line?* That is
answerable from the document's own structure, and the answer is a page
number the retriever can then guarantee the model sees.

Two sources, in order of authority, both producing the same thing:

1. **Inline XBRL.** Since the SEC's 2019-2021 phase-in, filers tag every
   reported figure with a standardised US-GAAP concept -
   `us-gaap:PaymentsToAcquirePropertyPlantAndEquipment` *is* capital
   expenditure, stated by the issuer. 52 of the 78 practice filings carry
   these tags, covering 82% of the questions asked.

2. **Statement heading plus line-item label.** The remaining 26 filings -
   pre-2019 10-Ks and most 8-Ks, including the 3M one above - carry no tags
   at all, so structure has to be recovered from the text. A statement is
   located by scoring each page against the line items that statement is
   *made of*: the real consolidated cash-flow statement matches all eight
   canonical items, while the MD&A page discussing cash flow matches five.
   Measured on 3M FY2018: pages 46, 49 and 60 all quote the capex figure,
   and completeness picks 60 - the actual statement, and the page the
   answer key names.

Nothing here decides an answer. It only nominates pages, which the reranker
still scores and the verification gate still checks a quote against. A
mistaken nomination costs a wasted context slot, never a wrong citation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.models import Passage

# --- statements -------------------------------------------------------------
#
# Each statement is identified by the line items it is composed of, not by
# its heading alone: headings appear in tables of contents, cross-references
# and MD&A prose, whereas the full set of line items appears only on the
# statement itself.

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
        "total current assets",
        "total current liabilities",
        "total assets",
        "cash and cash equivalents",
        "accounts payable",
        "retained earnings",
        "goodwill",
        "total liabilities",
    ),
    "income": (
        "net sales",
        "total revenues",
        "cost of sales",
        "gross profit",
        "operating income",
        "net income",
        "earnings per share",
        "provision for income taxes",
    ),
}

# A page must look substantially like the statement before it is treated as
# one. Two thirds rather than everything: filers legitimately omit items
# (a company with no goodwill prints no goodwill line), and a quarterly
# statement is shorter than an annual one.
_STATEMENT_THRESHOLD = 0.6


# --- concepts ---------------------------------------------------------------
#
# `labels` are matched against page text for untagged filings; `xbrl` names
# the US-GAAP concepts that mean the same thing where tags exist. Keys are
# the analyst phrases a question actually uses, matched the same way
# query_expansion matches them.

@dataclass(frozen=True)
class Concept:
    labels: tuple[str, ...]
    statement: str
    xbrl: tuple[str, ...] = ()


_CONCEPTS: dict[str, Concept] = {
    "capital expenditure": Concept(
        (r"purchases? of property", r"additions to property", r"capital expenditures?"),
        "cash_flow",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "capex": Concept(
        (r"purchases? of property", r"additions to property", r"capital expenditures?"),
        "cash_flow",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "working capital": Concept(
        (r"total current assets", r"total current liabilities"),
        "balance_sheet",
        ("AssetsCurrent", "LiabilitiesCurrent"),
    ),
    "current ratio": Concept(
        (r"total current assets", r"total current liabilities"),
        "balance_sheet",
        ("AssetsCurrent", "LiabilitiesCurrent"),
    ),
    "quick ratio": Concept(
        (r"total current liabilities", r"accounts receivable"),
        "balance_sheet",
        ("AssetsCurrent", "LiabilitiesCurrent"),
    ),
    "fixed asset": Concept(
        (r"property and equipment,? net", r"property, plant and equipment,? net"),
        "balance_sheet",
        ("PropertyPlantAndEquipmentNet",),
    ),
    "asset turnover": Concept(
        (r"property and equipment,? net", r"total assets"),
        "balance_sheet",
        ("PropertyPlantAndEquipmentNet", "Assets"),
    ),
    "inventory turnover": Concept(
        (r"inventories,? net", r"cost of sales"),
        "balance_sheet",
        ("InventoryNet",),
    ),
    "gross margin": Concept(
        (r"gross profit", r"cost of sales", r"cost of revenue"),
        "income",
        ("GrossProfit", "CostOfRevenue", "CostOfGoodsAndServicesSold"),
    ),
    "operating margin": Concept(
        (r"operating income", r"total revenues", r"net sales"),
        "income",
        ("OperatingIncomeLoss",),
    ),
    "net margin": Concept(
        (r"net income", r"total revenues", r"net sales"),
        "income",
        ("NetIncomeLoss",),
    ),
    "effective tax rate": Concept(
        (r"provision for income taxes", r"income before income taxes"),
        "income",
        ("IncomeTaxExpenseBenefit",),
    ),
    "earnings per share": Concept(
        (r"earnings per share", r"per share"),
        "income",
        ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
    ),
    "dividend": Concept(
        (r"dividends paid", r"dividends declared"),
        "cash_flow",
        ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    ),
    "free cash flow": Concept(
        (r"net cash provided by operating", r"purchases? of property"),
        "cash_flow",
        ("NetCashProvidedByUsedInOperatingActivities",),
    ),
    "operating cash flow": Concept(
        (r"net cash provided by operating",),
        "cash_flow",
        ("NetCashProvidedByUsedInOperatingActivities",),
    ),
    "total debt": Concept(
        (r"long-term debt", r"short-term borrowings"),
        "balance_sheet",
        ("LongTermDebtNoncurrent", "LongTermDebt"),
    ),
    "long-term debt": Concept(
        (r"long-term debt",), "balance_sheet", ("LongTermDebtNoncurrent", "LongTermDebt")
    ),
    "inventory": Concept((r"inventories,? net",), "balance_sheet", ("InventoryNet",)),
    "goodwill": Concept((r"goodwill",), "balance_sheet", ("Goodwill",)),
    "cash and cash equivalents": Concept(
        (r"cash and cash equivalents",),
        "balance_sheet",
        ("CashAndCashEquivalentsAtCarryingValue",),
    ),
    "revenue": Concept(
        (r"total revenues", r"net sales", r"net revenues"),
        "income",
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ),
    "net income": Concept((r"net income",), "income", ("NetIncomeLoss",)),
}

_WS_RE = re.compile(r"\s+")
_AMPERSAND_RE = re.compile(r"\s*&\s*")

# How many pages a single concept may nominate. The figure an analyst asks
# about is legitimately printed in two or three places (the statement, the
# MD&A discussion, a note), and all of them support a true answer, but
# nominating more than a few would crowd out genuinely reranked passages.
_MAX_PAGES_PER_CONCEPT = 3


def _match_form(text: str) -> str:
    return _WS_RE.sub(" ", _AMPERSAND_RE.sub(" and ", text.lower()))


@dataclass
class FactIndex:
    """Concept -> the pages that print it, best page first."""

    pages_by_concept: dict[str, list[int]] = field(default_factory=dict)
    statement_pages: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_passages(
        cls, passages: list[Passage], xbrl_pages: dict[str, list[int]] | None = None
    ) -> "FactIndex":
        text_by_page: dict[int, str] = defaultdict(str)
        for passage in passages:
            text_by_page[passage.page] += " " + passage.text.lower()

        statement_pages = cls._locate_statements(text_by_page)
        pages_by_concept: dict[str, list[int]] = {}

        for term, concept in _CONCEPTS.items():
            # Tagged figures are the issuer's own statement of where this
            # concept is reported, so they take precedence over inference.
            tagged = []
            for name in concept.xbrl:
                tagged.extend((xbrl_pages or {}).get(name, []))

            matched = [
                page
                for page, text in text_by_page.items()
                if any(re.search(label, text) for label in concept.labels)
            ]

            # Rank: the issuer's own tags first, then the page that *is* the
            # relevant statement, then anywhere else the label appears.
            #
            # The statement page is nominated whether or not the label
            # matched on it. Filers word line items differently - Best Buy's
            # balance sheet says "Merchandise inventories", not
            # "Inventories, net" - and a label table can never cover every
            # variant, but the statement a figure belongs to is structural
            # and does not vary. Missing the label there is exactly the case
            # where nominating the statement is most valuable.
            statement_page = statement_pages.get(concept.statement)
            ordered: list[int] = []
            for page in [*tagged, statement_page, *sorted(matched)]:
                if page is not None and page not in ordered:
                    ordered.append(page)

            if ordered:
                pages_by_concept[term] = ordered[:_MAX_PAGES_PER_CONCEPT]

        return cls(pages_by_concept=pages_by_concept, statement_pages=statement_pages)

    @staticmethod
    def _locate_statements(text_by_page: dict[int, str]) -> dict[str, int]:
        """The single best page for each statement, by how complete it looks."""
        best: dict[str, int] = {}
        for statement, items in _STATEMENT_ITEMS.items():
            scored = [
                (sum(1 for item in items if item in text) / len(items), page)
                for page, text in text_by_page.items()
            ]
            score, page = max(scored, default=(0.0, 0))
            if score >= _STATEMENT_THRESHOLD:
                best[statement] = page
        return best

    def pages_for(self, question: str) -> list[int]:
        """Pages worth guaranteeing the model sees for this question."""
        lowered = _match_form(question)
        pages: list[int] = []
        for term, concept_pages in self.pages_by_concept.items():
            if term in lowered:
                for page in concept_pages:
                    if page not in pages:
                        pages.append(page)
        return pages
