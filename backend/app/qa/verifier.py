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

# A number with its optional enclosing parens, which real filings use for
# negatives instead of a leading minus sign - "(1,577)" means -1,577.
_SIGNED_NUMBER_RE = re.compile(r"\(?\$?-?\d+(?:,\d{3})*(?:\.\d+)?%?\)?")
_RECONSTRUCT_TOLERANCE_REL = 0.003
_RECONSTRUCT_TOLERANCE_ABS = 0.015


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


_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
# "December 31, 2018" / "31 December 2018" - the day only, and only when a
# month name is against it.
_DATE_DAY_RE = re.compile(
    rf"(?:(?:{_MONTHS})\.?\s+(\d{{1,2}})\b)|(?:\b(\d{{1,2}})\s+(?:{_MONTHS}))", re.I
)


def _date_days(text: str) -> set[str]:
    """Day-of-month numbers that are part of a written date.

    A date is a label on a figure, not a figure itself, so its day is
    excused the way a year already is. Kept deliberately narrow - the number
    must be adjacent to a month name - because "31 distribution centres" is
    a real claim and has to stay checkable.
    """
    return {
        day
        for match in _DATE_DAY_RE.finditer(text or "")
        for day in match.groups()
        if day
    }


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


def _to_float(token: str) -> float | None:
    """Parse a filing-style number token, parens meaning negative."""
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").rstrip("%")
    cleaned = _strip_currency(cleaned).replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _numeric_pool(text: str) -> list[float]:
    """Figures usable as arithmetic inputs when reconstructing a metric.

    Percentages and bare years are excluded even though they're valid
    numbers: averaging a dollar amount with an unrelated percentage on the
    same page is a category error, not a real formula, and doing it anyway
    is exactly how an unrelated pair of figures can coincidentally land near
    a fabricated target. A ratio's inputs are the underlying quantities, not
    another already-computed percentage sitting nearby.
    """
    pool = []
    for tok in _SIGNED_NUMBER_RE.findall(text or ""):
        if tok.endswith("%") or _is_year(tok.strip("()")):
            continue
        value = _to_float(tok)
        if value is not None:
            pool.append(value)
    return pool


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_RECONSTRUCT_TOLERANCE_ABS, abs(b) * _RECONSTRUCT_TOLERANCE_REL)


# The pool a computed figure may be derived from is deliberately tiny: only
# the figures the answer *itself* states and which were independently found
# in the evidence. Deriving from everything retrieved instead was measured
# to be no check at all - n figures yield ~n^2 pairs and ~n^3 triples, so a
# 20-passage context densely covers the number line and almost any value
# "reconstructs". That is exactly how a wrong fixed-asset-turnover of 24.67
# passed the gate where the true figure was 24.26. Anchoring to the answer's
# own stated inputs keeps the pool at a handful of numbers, which makes a
# coincidental match vanishingly unlikely and turns this into a real audit
# of the model's arithmetic rather than a lottery.
_MAX_RECONSTRUCT_POOL = 12


def _reconstructible(target: float, pool: list[float]) -> bool:
    """True iff `target` is a simple combination of `pool`.

    `pool` must be the figures the answer states *and* that have already
    been verified against the evidence - not the whole retrieved context.
    A model computing a metric almost always shows the inputs it used
    ("revenue was X and average PP&E was Y, a ratio of Z"), so checking
    Z against X and Y re-derives the arithmetic rather than trusting it.
    An answer that asserts a bare computed figure with no inputs has
    nothing to check and is therefore refused, which is the correct
    outcome under a scoring policy where a wrong figure costs more than a
    gap.
    """
    seen: dict[float, None] = {}
    for v in pool:
        if v != 0:
            seen.setdefault(v, None)
    candidates = list(seen)[:_MAX_RECONSTRUCT_POOL]
    if len(candidates) < 2:
        return False

    for a in candidates:
        for b in candidates:
            if a == b:
                continue
            for value in (a + b, a - b, a / b, (a / b) * 100, (a - b) / b * 100, (a + b) / 2):
                if _close(value, target):
                    return True
    return False


def verify_numeric_consistency(
    answer: str,
    quote: str,
    question: str = "",
    page_text: str = "",
    context_text: str = "",
) -> bool:
    """True iff every figure the answer asserts is supported by the quote.

    Tokens are excused only when they are demonstrably not new claims:

    - present in the question (the answer is echoing the user's words),
    - a year that merely labels a figure which has itself been verified, or
    - a computed metric reconstructible from figures present in the wider
      retrieved context (see `_reconstructible`).
    """
    tokens = _numbers(answer)
    if not tokens:
        return True

    question_numbers = set(_numbers(question))
    # Days belonging to a written date in the answer ("December 31, 2018").
    date_days = _date_days(answer)

    substantive: list[str] = []
    years: list[str] = []
    for token in tokens:
        if token in question_numbers:
            continue  # echoed from the question - not an independent claim
        if token in date_days:
            continue  # part of a date label, not a figure being asserted
        (years if _is_year(token) else substantive).append(token)

    # Money and quantities are checked strictly against the quote, or - a
    # model that shows its work often restates a figure it read from a
    # *different* retrieved passage than the one it ends up citing - against
    # the wider context actually shown to it. `page_text` deliberately does
    # not count here: it is whatever else sits on the cited page, which was
    # never independently judged relevant the way a retrieved passage was,
    # so a figure merely nearby on that page stays unverified.
    unverified = [
        token
        for token in substantive
        if not (_contains_number(quote, token) or _contains_number(context_text, token))
    ]
    if unverified:
        # A token that looks like a computed ratio/margin (it carries a
        # decimal point or a percent sign - raw filing figures are almost
        # always whole dollar amounts) gets one more chance: can it be
        # re-derived from the *other figures this same answer states*, each
        # of which has already been found in the evidence? Whole-number
        # tokens get no such leniency, so a fabricated dollar amount is
        # rejected exactly as before.
        # The pool is the answer's own verified figures plus the figures in
        # the quote it cited. The quote is bounded (a row, a sentence) and
        # has already been confirmed to appear on the cited page, so
        # deriving from it is deriving from evidence. Intermediate steps a
        # model states without the filing printing them - "average PP&E was
        # $267.5 million", the mean of two quoted figures - are legitimate
        # and only reachable this way. `page_text` and `context_text` stay
        # out: those are the large pools that made this check vacuous.
        pool = [
            value
            for token in substantive
            if token not in unverified and (value := _to_float(token)) is not None
        ] + _numeric_pool(quote)

        # Follow the derivation the answer describes, rather than searching
        # for any combination that happens to land on the figure.
        #
        # A model showing its work states its intermediates: "revenue
        # $6,489M, average PP&E $267.5M, ratio 24.26". The average is not in
        # the filing, so it must be derived from the quoted 253 and 282
        # before the ratio can be derived from it. Each figure proved this
        # way joins the pool, so the chain resolves in the order the answer
        # lays it out - and every link is checked.
        #
        # Searching two figures deep instead, in one unconstrained pass, was
        # measured to accept a wrong answer: with five figures in play the
        # triples cover the number line densely enough that 24.77 matched
        # "mean of this year's and last year's revenue over last year's
        # PP&E" - not a formula anyone uses, just the nearest coincidence.
        pending = list(unverified)
        while pending:
            resolved = []
            for token in pending:
                value = _to_float(token)
                looks_computed = ("." in token or "%" in token) and value is not None
                if looks_computed and _reconstructible(value, pool):
                    resolved.append(token)
                    pool.append(value)
            if not resolved:
                return False
            pending = [t for t in pending if t not in resolved]

    # A year is only excused when there is a verified figure for it to
    # label. Otherwise the year *is* the answer ("when does the loan
    # mature?") and gets no special treatment.
    has_verified_figure = bool(substantive)
    for year in years:
        if _contains_number(quote, year):
            continue
        if has_verified_figure and (
            _contains_number(page_text, year) or _contains_number(context_text, year)
        ):
            continue
        return False

    return True


@dataclass
class VerificationResult:
    passed: bool
    reason: str


def verify_citations(
    citations: list[tuple[int, str]],
    page_text: dict[int, str],
    answer: str,
    question: str = "",
    context_text: str = "",
) -> VerificationResult:
    """Verify an answer resting on one or more (page, quote) citations.

    A quarter of real analyst questions cannot be answered from a single
    page - "what were current assets and revenue?" draws one figure from the
    balance sheet and the other from the income statement. Restricted to one
    citation the system could not name where the second figure came from, so
    the verifier rejected it and the question was refused: measured on the
    practice set, 1 of 32 such questions scored.

    Two rules, both stricter than they look:

    - **Every** citation must hold. An answer is one claim resting on all of
      its evidence, so a single quote that is not on the page it names
      invalidates the answer regardless of how good the others are.
    - Figures are checked against the *union* of the cited quotes, not
      against any one of them. That is the whole point of citing several
      pages - and it is not a loosening, because the union is still only
      text the model quoted and this function has confirmed.
    """
    if not citations:
        return VerificationResult(False, "the answer cited no evidence")

    for page, quote in citations:
        if page not in page_text:
            return VerificationResult(False, f"page {page} is outside this filing")
        if not verify_quote(page_text[page], quote):
            return VerificationResult(False, f"quote not found on page {page}")

    if not verify_numeric_consistency(
        answer,
        quote="\n".join(quote for _, quote in citations),
        question=question,
        page_text="\n".join(page_text[page] for page, _ in citations),
        context_text=context_text,
    ):
        return VerificationResult(False, "answer contains a number not present in the quotes")

    return VerificationResult(True, "verified")


def verify_answer(
    page_text: str, quote: str, answer: str, question: str = "", context_text: str = ""
) -> VerificationResult:
    if not verify_quote(page_text, quote):
        return VerificationResult(False, "quote not found on page")
    if not verify_numeric_consistency(
        answer, quote, question=question, page_text=page_text, context_text=context_text
    ):
        return VerificationResult(False, "answer contains a number not present in the quote")
    return VerificationResult(True, "verified")
