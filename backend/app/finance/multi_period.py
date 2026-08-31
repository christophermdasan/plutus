"""Questions spanning more than one reporting period.

A large share of analyst questions are not "what was X in FY2022" but "did
X grow", "how does X compare with last year", "what was the three-year
average". Measured on the practice set, 23 of the 77 questions the
single-period engine refused are of this shape - and refusing them is
right only until the capability exists, because each one is as computable
as a single-period figure once the periods are pinned down.

Three intents, all deterministic:

- **change** - "year-over-year change from FY2015 to FY2016", "by how much
  did X increase". Reports the difference and the percentage.
- **compare** - "has X increased its debt between FY2023 and FY2022?",
  "how does the tax rate compare to FY2021?". Reports the direction and
  both figures, because the direction alone is not checkable.
- **average** - "three year average operating margin FY2019-FY2021".
  Computes the metric for each year and means them, which is not the same
  as computing the metric from mean inputs and is what analysts intend.

The arithmetic is done here for the same reason as everywhere else in this
package: the model gets it wrong often enough to matter, and code does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.finance.ontology import METRICS, Metric, normalise

# "from FY2015 to FY2016", "between FY2023 and FY2022", "FY2019 - FY2021"
_RANGE_RE = re.compile(
    r"(?:from|between)?\s*(?:fy|fiscal(?:\s+year)?)?\s*(\d{4})\s*(?:-|–|to|through|and)\s*"
    r"(?:fy|fiscal(?:\s+year)?)?\s*(\d{4})",
    re.I,
)
_N_YEAR_RE = re.compile(r"\b(\d+|two|three|four|five)[- ]year\b", re.I)
_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5}

_CAGR_RE = re.compile(
    r"\bcagr\b|\bcompound(?:ed)? annual growth\b", re.I
)
_CHANGE_RE = re.compile(
    r"\bchanges?\b|\bchanged\b|\byear[- ]over[- ]year\b|\byoy\b|\bby how much\b|\bgrowth\b", re.I
)
_COMPARE_RE = re.compile(
    r"\b(has|did|was|were|is|does)\b[^?]*\b(increas|decreas|grow|grew|ris|rose|fall|fell|drop|improv|declin|worsen|expand|shrink)\w*\b"
    r"|\bcompare[ds]?\s+(?:to|with)\b|\bversus\b|\bvs\.?\b|\bany (?:drop|increase|change)\b",
    re.I,
)
_AVERAGE_RE = re.compile(r"\baverage\b", re.I)


@dataclass(frozen=True)
class Intent:
    kind: str                 # "change" | "compare" | "average" | "cagr" | "single"
    years: tuple[str, ...] = ()


_DEFINITION_RE = re.compile(r"\b(?:is )?defined as\b|\bcalculated as\b|\bdefine[ds]?\b", re.I)


def _ask_only(text: str) -> str:
    """The question without the formula it spells out for you.

    FinanceBench questions frequently carry their own definition - "fixed
    asset turnover ratio is defined as: FY2019 revenue / (average PP&E
    between FY2018 and FY2019)". Read whole, that says "average" and names
    two years, so it looked like a request for a two-year average and the
    single-year ratio was never computed.
    """
    return _DEFINITION_RE.split(text, maxsplit=1)[0]


def detect(question: str, averaged_metric: bool = False) -> Intent:
    """What kind of multi-period question this is, and over which years.

    Order matters: "average" is checked before "change" because a question
    asking for an average *over* a range contains range wording too, and
    averaging is the stronger signal of intent.
    """
    text = _ask_only(normalise(question))
    span = _RANGE_RE.search(text)
    years: tuple[str, ...] = ()
    if span:
        start, end = sorted((span.group(1), span.group(2)))
        years = tuple(str(y) for y in range(int(start), int(end) + 1))

    # A turnover or return ratio averages opening and closing balances by
    # definition, so "average" in its question is about the formula.
    if _AVERAGE_RE.search(text) and not averaged_metric:
        if not years and (n := _N_YEAR_RE.search(text)):
            # "three year average" with no range given: the count is known
            # but the endpoints are not, so the caller supplies the latest
            # year and counts back.
            token = n.group(1).lower()
            count = _WORD_NUMBERS.get(token, int(token) if token.isdigit() else 0)
            return Intent("average", ()) if count == 0 else Intent("average", ("?",) * count)
        if years:
            return Intent("average", years)

    # Checked before "change": a CAGR question routinely also says "growth",
    # and simple percent change is the wrong arithmetic for a span of more
    # than one year - FY2020-FY2022 revenue doubling is a 100% change but a
    # 41.4% CAGR, and reporting one as the other is confidently wrong.
    if _CAGR_RE.search(text) and len(years) >= 2:
        return Intent("cagr", (years[0], years[-1]))

    if _CHANGE_RE.search(text) and len(years) >= 2:
        return Intent("change", (years[0], years[-1]))

    if _COMPARE_RE.search(text):
        return Intent("compare", (years[0], years[-1]) if len(years) >= 2 else ())

    return Intent("single", years)


def _direction(earlier: float, later: float) -> str:
    if later > earlier:
        return "increased"
    if later < earlier:
        return "decreased"
    return "was unchanged"


@dataclass
class PeriodResult:
    year: str
    value: float
    citations: list[tuple[int, str]]
    # The operands, so a single-period answer can state the arithmetic it
    # performed rather than only its result.
    values: dict[str, float] = field(default_factory=dict)


def describe_change(metric: Metric, first: PeriodResult, last: PeriodResult) -> str:
    delta = last.value - first.value
    label = metric.description or metric.key.replace("_", " ")
    pct = f" ({delta / abs(first.value) * 100:+.1f}%)" if first.value else ""
    return (
        f"{label} {_direction(first.value, last.value)} by {_fmt(abs(delta), metric.unit)}"
        f"{pct}, from {_fmt(first.value, metric.unit)} in FY{first.year} to "
        f"{_fmt(last.value, metric.unit)} in FY{last.year}."
    )


def describe_compare(metric: Metric, first: PeriodResult, last: PeriodResult) -> str:
    """The direction, both endpoints, and the size of the change.

    The magnitude is stated because subtracting the two printed endpoints
    does not reliably reproduce it: they are rounded for display, and the
    difference of two rounded figures is not the rounded difference. AMCOR's
    gross margin ran 19.389% to 18.545% - a decline of 0.844pp, which rounds
    to 0.8 - but the endpoints print as 19.4 and 18.5, from which a reader
    would compute 0.9. Reporting the change from the unrounded values removes
    that trap rather than asking the reader to avoid it.
    """
    label = metric.description or metric.key.replace("_", " ")
    verdict = "Yes" if last.value > first.value else ("No" if last.value < first.value else "No")
    delta = abs(last.value - first.value)
    # A percentage moves in points, not in percent of itself.
    change = f"{delta * 100:.1f} percentage points" if metric.unit == "percent" else _fmt(delta, metric.unit)
    return (
        f"{verdict} - {label} {_direction(first.value, last.value)} by {change}, from "
        f"{_fmt(first.value, metric.unit)} in FY{first.year} to "
        f"{_fmt(last.value, metric.unit)} in FY{last.year}."
    )


def cagr_rate(first: PeriodResult, last: PeriodResult) -> float:
    """Compounded, not simple, growth - the span in years is the compounding period."""
    periods = int(last.year) - int(first.year)
    return (last.value / first.value) ** (1 / periods) - 1


def describe_cagr(metric: Metric, first: PeriodResult, last: PeriodResult) -> str:
    periods = int(last.year) - int(first.year)
    label = metric.description or metric.key.replace("_", " ")
    rate = cagr_rate(first, last)
    return (
        f"The {periods}-year CAGR of {label} from FY{first.year} to FY{last.year} "
        f"was {rate * 100:.1f}%, from {_fmt(first.value, metric.unit)} to "
        f"{_fmt(last.value, metric.unit)}."
    )


def describe_average(metric: Metric, results: list[PeriodResult]) -> str:
    label = metric.description or metric.key.replace("_", " ")
    mean = sum(r.value for r in results) / len(results)
    span = f"FY{results[0].year} - FY{results[-1].year}"
    parts = ", ".join(f"FY{r.year} {_fmt(r.value, metric.unit)}" for r in results)
    return f"The {len(results)}-year average {label} over {span} was {_fmt(mean, metric.unit)} ({parts})."


def _fmt(value: float, unit: str) -> str:
    if unit == "percent":
        return f"{value * 100:.1f}%"
    if unit == "currency":
        millions = value / 1_000_000 if abs(value) >= 1_000_000 else value
        return f"${millions:,.2f} million"
    return f"{value:.2f}"


def mean_of(results: list[PeriodResult]) -> float:
    return sum(r.value for r in results) / len(results)
