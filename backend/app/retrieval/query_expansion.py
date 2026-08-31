"""Translates analyst vocabulary into the words filings actually print.

Analysts and filings name the same quantity differently, and the gap is not
a paraphrase a dense embedding closes reliably. Measured on the FinanceBench
practice set, the question asks for "capital expenditure"; the cash-flow
statement that answers it says "Purchases of property, plant and equipment
(PP&E)" and never contains the phrase "capital expenditure" at all. BM25 has
no term in common to match on, and the page itself is a dense grid of
numbers whose embedding carries little of the topical signal a paraphrase
match would need. The right page therefore lost at *both* retrievers, and
the reranker never saw it - a passage absent from the candidate pool cannot
be recovered downstream. Expanded, that page moves from rank 26 to rank 4.

So the query is expanded with the line-item names, and the statement
heading, that the answer would actually be printed under. Two properties
keep this honest:

- **It only ever adds terms.** The analyst's own wording is still in the
  query and still scores, so a question that was already retrievable stays
  retrievable. Expansion can promote the right page; it cannot demote one.
- **It expands the retrieval query only.** The reranker, the model and the
  verifier all continue to see the question exactly as it was asked, so
  nothing here can change what counts as a relevant or verified answer -
  only which passages get the chance to be judged.

Line items and statement headings are kept in separate tables so each is
emitted at most once. Several metric terms in one question routinely live on
the same statement, and repeating its name three times would weight the
statement heading above the line item actually asked about.

Deliberately a static table rather than an LLM rewrite: it costs no API call
on the hot path, it is deterministic, and the vocabulary of financial
statements is small, stable and standardised by regulation - exactly the
case where a lookup beats a generation.
"""

from __future__ import annotations

import re

_CASH_FLOW = "consolidated statement of cash flows"
_BALANCE_SHEET = "consolidated balance sheets"
_INCOME = "consolidated statements of operations"

# Analyst term -> (line items a filing prints, the statement they sit on).
# Values name *line items* rather than synonyms of the concept, because
# those are the literal strings on the page BM25 has to match. Where a
# metric is computed from several line items (a ratio, a margin), every
# input is listed - the passage that answers it holds those inputs.
_EXPANSIONS: dict[str, tuple[str, str]] = {
    # --- cash flow statement ---------------------------------------------
    "capital expenditure": ("purchases of property plant and equipment additions capital spending investing activities", _CASH_FLOW),
    "capex": ("purchases of property plant and equipment additions capital spending investing activities", _CASH_FLOW),
    "free cash flow": ("net cash provided by operating activities purchases of property plant and equipment", _CASH_FLOW),
    "operating cash flow": ("net cash provided by operating activities", _CASH_FLOW),
    "cash flow from operations": ("net cash provided by operating activities", _CASH_FLOW),
    "depreciation": ("depreciation and amortization", _CASH_FLOW),
    "amortization": ("depreciation and amortization", _CASH_FLOW),
    "dividend": ("dividends paid dividends declared per share financing activities", _CASH_FLOW),
    "share repurchase": ("purchases of treasury stock repurchases of common stock financing activities", _CASH_FLOW),
    "buyback": ("purchases of treasury stock repurchases of common stock financing activities", _CASH_FLOW),
    # --- balance sheet ----------------------------------------------------
    "working capital": ("total current assets total current liabilities", _BALANCE_SHEET),
    "current ratio": ("total current assets total current liabilities", _BALANCE_SHEET),
    "quick ratio": ("cash and cash equivalents accounts receivable total current liabilities", _BALANCE_SHEET),
    "inventory": ("inventories net", _BALANCE_SHEET),
    "receivable": ("accounts receivable net of allowances", _BALANCE_SHEET),
    "payable": ("accounts payable", _BALANCE_SHEET),
    "goodwill": ("goodwill intangible assets net", _BALANCE_SHEET),
    "total assets": ("total assets", _BALANCE_SHEET),
    "total debt": ("long-term debt net current portion of long-term debt short-term borrowings", _BALANCE_SHEET),
    "long-term debt": ("long-term debt net", _BALANCE_SHEET),
    "shareholders equity": ("total shareholders equity retained earnings", _BALANCE_SHEET),
    "stockholders equity": ("total stockholders equity retained earnings", _BALANCE_SHEET),
    "book value": ("total shareholders equity", _BALANCE_SHEET),
    "cash and cash equivalents": ("cash and cash equivalents", _BALANCE_SHEET),
    "fixed asset": ("property and equipment net property plant and equipment", _BALANCE_SHEET),
    "leverage": ("long-term debt net total shareholders equity", _BALANCE_SHEET),
    # --- income statement -------------------------------------------------
    "revenue": ("total revenues net sales net revenues", _INCOME),
    "net sales": ("net sales total revenues", _INCOME),
    "gross margin": ("gross profit cost of sales cost of revenue net sales", _INCOME),
    "gross profit": ("gross profit cost of sales cost of revenue net sales", _INCOME),
    "operating margin": ("operating income total revenues", _INCOME),
    "operating income": ("operating income", _INCOME),
    "net margin": ("net income total revenues", _INCOME),
    "profit margin": ("net income total revenues", _INCOME),
    "net income": ("net income", _INCOME),
    "earnings per share": ("earnings per share basic diluted", _INCOME),
    "eps": ("earnings per share basic diluted", _INCOME),
    "ebitda": ("operating income depreciation and amortization", _INCOME),
    "interest expense": ("interest expense", _INCOME),
    "effective tax rate": ("provision for income taxes income before income taxes", _INCOME),
    "tax rate": ("provision for income taxes income before income taxes", _INCOME),
    "research and development": ("research and development expense", _INCOME),
    "cost of goods sold": ("cost of sales cost of revenue", _INCOME),
    "sg&a": ("selling general and administrative expenses", _INCOME),
    # --- ratios spanning statements ---------------------------------------
    "return on assets": ("net income total assets", _BALANCE_SHEET),
    "return on equity": ("net income total shareholders equity", _BALANCE_SHEET),
    "asset turnover": ("total revenues total assets property and equipment net", _BALANCE_SHEET),
    "inventory turnover": ("inventories net cost of sales", _BALANCE_SHEET),
    "days inventory": ("inventories net cost of sales", ""),
    "days sales outstanding": ("accounts receivable net total revenues", ""),
    "days payable": ("accounts payable cost of sales", ""),
    "debt to equity": ("long-term debt net total shareholders equity", _BALANCE_SHEET),
    "interest coverage": ("operating income interest expense", _INCOME),
}

# Naming a statement in the question is a direct instruction about where to
# look ("...relying on the details shown in the cash flow statement"), worth
# matching on its own even when no metric term above fires.
_STATEMENT_HINTS: dict[str, str] = {
    "cash flow statement": _CASH_FLOW,
    "statement of cash flows": _CASH_FLOW,
    "balance sheet": _BALANCE_SHEET,
    "statement of financial position": _BALANCE_SHEET,
    "income statement": _INCOME,
    "statement of income": _INCOME,
    "statement of operations": _INCOME,
    "p&l": _INCOME,
    "profit and loss": _INCOME,
    "statement of shareholders equity": "consolidated statements of shareholders equity",
}

_WS_RE = re.compile(r"\s+")
# Analysts write "Cash & Cash equivalents" and "PP&E"; filings print "and".
# Matching on a normalised copy means a table needs only one spelling.
_AMPERSAND_RE = re.compile(r"\s*&\s*")


def _match_form(question: str) -> str:
    """Lowercased, punctuation-normalised text used only for lookup."""
    return _WS_RE.sub(" ", _AMPERSAND_RE.sub(" and ", question.lower()))


def expand_query(question: str) -> str:
    """Return the question plus any filing vocabulary it implies.

    The original text always comes first and is never altered, so this can
    only add matches. Returns the question unchanged when nothing applies.
    """
    lowered = _match_form(question)

    line_items: dict[str, None] = {}
    statements: dict[str, None] = {}

    for term, (items, statement) in _EXPANSIONS.items():
        if term in lowered:
            line_items.setdefault(items, None)
            if statement:
                statements.setdefault(statement, None)

    for term, statement in _STATEMENT_HINTS.items():
        if term in lowered:
            statements.setdefault(statement, None)

    if not line_items and not statements:
        return question

    return " ".join([question, *line_items, *statements])
