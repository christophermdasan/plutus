"""The verification gate: the last thing between a model's claim and a user.

Constrained generation guarantees the *shape* of a citation. This checks the
*substance* - that the quoted text genuinely exists on the page it names, and
that the figures the answer states are genuinely in that quote. The model's
own confidence is never sufficient.

Two failure modes have to be balanced:

- **Too lenient** and a fabricated figure reaches the user, which is the
  single most expensive outcome an analyst tool can produce.
- **Too strict** and correct answers get thrown away as unverifiable, which
  quietly costs just as much - a refusal earns nothing.

Both relaxations below exist because real filings broke a naive strict
check, and both are deliberately narrow. See the individual docstrings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WHITESPACE_RE = re.compile(r"\s+")
# A comma only counts as a thousands separator when three digits follow it.
# Written as `[\d,]*` it also swallowed the comma that ends a clause, so
# "December 31, 2022, with..." yielded the token "2022," while the same date
# ending a sentence yielded "2022" - and a correct answer quoting its source
# verbatim was rejected because one had a comma and the other a full stop.
_NUMBER_RE = re.compile(r"\$?-?\d+(?:,\d{3})*(?:\.\d+)?%?")
# A bare four-digit year, not attached to a currency symbol or decimal.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

_CURRENCY = "$£€"
_CURRENCY_GAP_RE = re.compile("([" + re.escape(_CURRENCY) + r"])\s+")
_TRAILING_SYMBOL_RE = re.compile(r"\s+([%)])")
_OPEN_PAREN_RE = re.compile(r"\(\s+")


def _normalize(text: str) -> str:
    # Models emit narrow/non-breaking spaces around figures
    # ("$139.0<nbsp>million"), which would defeat a literal comparison.
    cleaned = text.replace(" ", " ").replace("\xa0", " ")
    return _WHITESPACE_RE.sub(" ", cleaned).strip().lower()


def _collapse_table_cells(text: str) -> str:
    """Normalise a serialized table row for comparison.

    Real filings put the currency symbol in its own table column, so a row
    serialises as `Total net sales | $ | 416,161`. A model quoting that
    writes the natural `$416,161` - which is not a literal substring, so a
    strict check discards a correct, genuinely-supported answer. Observed
    on Apple's FY2025 10-K.

    Dropping the cell delimiters and closing the gaps they created does not
    loosen the check in any way that matters: a figure that is not in the
    row is still not in the row after collapsing.
    """
    collapsed = _normalize(text.replace("|", " "))
    collapsed = _CURRENCY_GAP_RE.sub(r"\1", collapsed)     # "$ 416,161" -> "$416,161"
    collapsed = _TRAILING_SYMBOL_RE.sub(r"\1", collapsed)  # "6 %"       -> "6%"
    collapsed = _OPEN_PAREN_RE.sub("(", collapsed)         # "( 4)"      -> "(4)"
    return collapsed


def _strip_currency(text: str) -> str:
    """Drop currency symbols.

    Financial tables routinely hoist the symbol into a column header, so a
    cell holds a bare `64,377` while the model writes the natural `$64,377`
    (and sometimes the reverse). That is a formatting difference, not a
    different claim - the digits are what is being asserted.
    """
    return text.translate({ord(c): None for c in _CURRENCY})


def verify_quote(page_text: str, quote: str) -> bool:
    """True iff `quote` appears on the page.

    Comparison is progressively normalised, but only ever for *formatting*.
    The invariant that must not be broken: the characters the answer
    actually claims - above all the digits - still have to be present, in
    order, on the cited page. Fabricated figures remain rejected.

    1. whitespace and case
    2. table cell delimiters (`$ | 416,161` -> `$416,161`)
    3. currency symbols (`64,377` <-> `$64,377`)
    """
    if not quote.strip():
        return False
    if _normalize(quote) in _normalize(page_text):
        return True

    collapsed_page = _collapse_table_cells(page_text)
    collapsed_quote = _collapse_table_cells(quote)
    if collapsed_quote in collapsed_page:
        return True

    return _strip_currency(collapsed_quote) in _strip_currency(collapsed_page)


def _numbers(text: str) -> list[str]:
    return _NUMBER_RE.findall(text or "")


def _is_year(token: str) -> bool:
    return bool(_YEAR_RE.match(token))


def _contains_number(haystack: str, token: str) -> bool:
    """Number lookup that tolerates cell boundaries and currency symbols.

    Same invariant as verify_quote: the digits must be present; only
    formatting is normalised away.
    """
    haystack = haystack or ""
    if token in haystack:
        return True
    collapsed = _collapse_table_cells(haystack)
    if token.lower() in collapsed:
        return True
    return _strip_currency(token.lower()) in _strip_currency(collapsed)


def verify_numeric_consistency(
    answer: str,
    quote: str,
    question: str = "",
    page_text: str = "",
) -> bool:
    """True iff every figure the answer asserts is supported by the quote.

    Tokens are excused only when they are demonstrably not new claims:

    - present in the question (the answer is echoing the user's words), or
    - a year that merely labels a figure which has itself been verified.
    """
    tokens = _numbers(answer)
    if not tokens:
        return True

    question_numbers = set(_numbers(question))

    substantive: list[str] = []
    years: list[str] = []
    for token in tokens:
        if token in question_numbers:
            continue  # echoed from the question - not an independent claim
        (years if _is_year(token) else substantive).append(token)

    # Money and quantities are checked strictly against the quote.
    if any(not _contains_number(quote, token) for token in substantive):
        return False

    # A year is only excused when there is a verified figure for it to
    # label. Otherwise the year *is* the answer ("when does the loan
    # mature?") and gets no special treatment.
    has_verified_figure = bool(substantive)
    for year in years:
        if _contains_number(quote, year):
            continue
        if has_verified_figure and _contains_number(page_text, year):
            continue
        return False

    return True


@dataclass
class VerificationResult:
    passed: bool
    reason: str


def verify_answer(
    page_text: str, quote: str, answer: str, question: str = ""
) -> VerificationResult:
    if not verify_quote(page_text, quote):
        return VerificationResult(False, "quote not found on page")
    if not verify_numeric_consistency(answer, quote, question=question, page_text=page_text):
        return VerificationResult(False, "answer contains a number not present in the quote")
    return VerificationResult(True, "verified")
