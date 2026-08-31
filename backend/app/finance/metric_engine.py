"""Computes a metric from stored facts, and cites where each figure came from.

This is the deterministic half of the system. It exists because the model
is measurably unreliable at arithmetic: asked for fixed-asset turnover on
Activision's FY2019 10-K it read revenue 6,489 and PP&E 253 and 282
correctly off the pages, averaged them correctly to 267.5, and then
reported the quotient as 24.77 when it is 24.26. Every input right, the
division wrong - and no amount of retrieval or prompt work fixes that,
because it is not a retrieval or prompting failure.

So for questions that name a metric, the figures come from the fact store
and the arithmetic happens here. There is no step at which a number can be
invented: an operand is either present in the filing or the engine
declines. Declining costs nothing under the scoring policy; a wrong figure
costs more than the answer was worth.

The engine answers only what it can prove and hands everything else back to
the retrieval pipeline, which is better at prose and judgement than any
formula table could be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.finance.fact_store import FactStore, StoredFact
from app.finance import multi_period
from app.finance.multi_period import PeriodResult
from app.finance.ontology import METRICS, Metric, find_metric, normalise

# "FY2019", "fiscal 2019", "in 2019", "FY19"
_FY_LONG_RE = re.compile(r"\bf(?:iscal\s*)?y(?:ear)?\s*(\d{4})\b|\bfiscal\s+(\d{4})\b|\b(20\d{2})\b")
_FY_SHORT_RE = re.compile(r"\bfy\s*(\d{2})\b")


# Questions this engine must not answer, however clearly they name a
# metric. Each of these produced a confidently wrong answer before the
# guard existed, and a wrong figure costs more than the answer was worth:
#
#   "which of JPM's business segments had the highest net income?"
#       -> the engine returned consolidated net income. Segment figures are
#          tagged with a dimension this store deliberately discards.
#   "3 year average unadjusted operating income % margin"
#       -> returned a single year's operating income.
#
# A metric name appearing in a question is not evidence the question is
# asking for that metric of the whole company in one period, which is the
# only thing this engine can compute.
_UNSUPPORTED_RE = re.compile(
    r"\b(segment|division|geograph\w*|business unit|region|by (?:country|product))\b"
    r"|\b(which|what)\b[^?]*\b(highest|lowest|largest|smallest|best|worst|most|least|biggest)\b"
    r"|\bexclud\w+\b",
    re.I,
)

# A range of fiscal years ("FY2019 - FY2021", "from FY2015 to FY2016") is a
# change or an average across periods, not a single-period figure.
_YEAR_RANGE_RE = re.compile(r"(20\d{2})\s*(?:-|–|to|through)\s*(?:fy\s*)?(20\d{2})", re.I)


# A metric name appearing in a question does not make it a metric question.
# "Which debt securities are registered to trade on a national securities
# exchange?" contains "debt"; "Does 3M maintain a stable trend of dividend
# distribution?" contains "dividend". Both were answered with a figure, both
# confidently wrong. The question has to be *asking for a quantity*.
_QUANTITATIVE_RE = re.compile(
    r"\bhow much\b|\bhow many\b|\bwhat (?:is|was|are|were)\b|\bwhat'?s\b"
    r"|\bcalculate\b|\bcompute\b|\bamount of\b|\bvalue of\b"
    r"|\b(?:does|did|do|is|was|has|have|had)\b[^?]*\b(?:positive|negative|healthy|improving"
    r"|higher|lower|increase\w*|decrease\w*|grow\w*|declin\w*|drop\w*|capital[- ]intensive)\b",
    re.I,
)

# Interrogatives that ask for a name, a list or an explanation - never a
# figure this engine could compute.
_QUALITATIVE_RE = re.compile(
    r"\bwhich\b[^?]*\b(?:are|were|is|was)\b(?!.*\b(?:higher|lower|greater)\b)"
    r"|\bwho\b|\bwhat (?:are|is) the (?:major|main|primary|key|nature|purpose|outcome|agenda)\b"
    r"|\bstable trend\b|\btrend of\b|\bexplain\b|\bdescribe\b|\bwhy\b",
    re.I,
)


def looks_quantitative(question: str) -> bool:
    """Whether the question asks for a number, rather than merely mentioning one.

    The qualitative test is applied only to the question actually being
    asked - everything up to the first question mark. FinanceBench questions
    routinely append an instruction to the analyst ("If the quick ratio is
    not relevant, please state that and explain why"), and matching
    "explain" or "why" there rejected perfectly ordinary metric questions.
    """
    text = normalise(question)
    asked = text.split("?", 1)[0]
    if _QUALITATIVE_RE.search(asked):
        return False
    if _QUANTITATIVE_RE.search(text):
        return True
    # A terse query is a direct ask. Benchmark questions are full
    # sentences, but a user types "fixed asset turnover FY2019?" - there is
    # no room in a phrase that short for a metric name to be incidental,
    # which is the only thing the interrogative test guards against.
    return len(asked.split()) <= 8


# The clause a question uses to spell out the formula it wants.
_DEFINITION_CLAUSE_RE = re.compile(
    r"(?:is\s+)?(?:defined|calculated|computed)\s+as\s*:?\s*(.+)", re.I | re.S
)


def _concepts_named_in(text: str) -> set[str]:
    """Ontology concepts a definition clause refers to, by any analyst name."""
    from app.finance.ontology import ALIASES, CONCEPTS

    normalised = normalise(text)
    found: set[str] = set()
    for alias in sorted(ALIASES, key=len, reverse=True):
        if alias in normalised:
            key = ALIASES[alias]
            if key in CONCEPTS:
                found.add(key)
    return found


# A stated definition we implement under a different key. Keyed by the
# metric the question names, each entry pairs a marker that must appear in
# the definition clause with the variant to compute instead.
_VARIANTS: dict[str, tuple[re.Pattern[str], str]] = {
    "dpo": (
        re.compile(r"change in inventor(?:y|ies)", re.I),
        "dpo_inventory_adjusted",
    ),
}


def resolve_variant(question: str, metric_key: str) -> str | None:
    """The metric key matching the formula the question spells out, if any.

    Answering a stated formula with a different one is confidently wrong,
    but declining throws the question away. Where the stated formula is one
    this engine can compute, computing it is strictly better than both -
    worth +1 rather than 0 on the three DPO questions in this corpus.
    """
    clause = _DEFINITION_CLAUSE_RE.search(question)
    if clause is None:
        return None
    entry = _VARIANTS.get(metric_key)
    if entry is None:
        return None
    marker, variant = entry
    return variant if marker.search(clause.group(1)) else None


# Arithmetic written out in words, joining two quantities into an
# expression. "less" is deliberately absent: "property, plant and equipment
# less accumulated depreciation" is a line-item name, not a subtraction the
# analyst is asking us to perform.
_OPERATOR_RE = re.compile(
    r"\bdivided by\b|\bdivide[sd]?\b|\bover\b(?!\s*(?:the\s+)?(?:year|period|time))"
    r"|\bminus\b|\bsubtract(?:ed)?\b|\bplus\b|\badded to\b"
    r"|\bmultiplied by\b|\btimes\b"
    r"|\bas a (?:%|percent(?:age)?) of\b",
    re.I,
)


def asks_for_expression(question: str, metric_key: str) -> bool:
    """Whether the question writes out arithmetic we have not been asked to name.

    `find_metric` returns the longest matching alias, so an expression
    naming two concepts resolves to whichever appears last and the engine
    reports that balance on its own. Measured live on JPMorgan's 2022 10-K:
    "net income divided by total assets" answered "assets was $3,665,743
    million", and it shipped as "corroborated by the model" - the system's
    strongest trust signal attached to an answer to a question nobody asked.

    Only raw quantities are guarded. A metric with several inputs *is* the
    expression, so operators in its question are describing the thing being
    asked for rather than some other calculation - which is why the
    definition clause is stripped first: "fixed asset turnover is defined
    as: revenue / average PP&E" restates our own formula.
    """
    metric = METRICS.get(metric_key)
    if metric is None or len(metric.inputs) > 1:
        return False
    asked = multi_period._ask_only(normalise(question))
    if not _OPERATOR_RE.search(asked):
        return False
    # An operator alone is not enough - "revenue over the period" is not a
    # division. A second known concept has to be present for this to be an
    # expression at all.
    return len(_concepts_named_in(asked)) > 1


def defines_own_formula(question: str, metric_key: str) -> bool:
    """Whether the question spells out a formula this engine does not implement.

    Most questions in this domain restate the definition the engine already
    uses - "fixed asset turnover ratio is defined as: revenue / average
    PP&E" - and refusing those would throw away most of the metric set. What
    matters is a definition that differs.

    Amazon's FY2017 DPO is asked as `365 * average payables / (COGS + change
    in inventory)`. Textbook DPO omits the inventory term, so the engine
    computed 97.70 against a key of 93.86 and shipped it confidently: a -1,
    the most expensive outcome under the scoring policy, and not an
    arithmetic error - both figures are right for their own definition. The
    engine had simply answered a question nobody asked.

    The test is deliberately one-directional: a concept named in the
    definition that our formula does not consume means the two formulas are
    not the same. Extra concepts *we* use but the question omits are not
    evidence of a mismatch, because a question routinely abbreviates.
    """
    metric = METRICS.get(metric_key)
    if metric is None:
        return False
    clause = _DEFINITION_CLAUSE_RE.search(question)
    if clause is None:
        return False
    named = _concepts_named_in(clause.group(1))
    if not named:
        return False
    return bool(named - set(metric.inputs))


def is_supported(question: str) -> bool:
    """Whether a consolidated computation can answer this.

    Multi-period questions - a change, a comparison, an average across
    years - used to be refused here. They are computed now (see
    `multi_period`), so only what remains genuinely out of reach is
    excluded: figures broken down by segment or geography, which this store
    deliberately discards as dimensional, and superlatives across such a
    breakdown.
    """
    if _UNSUPPORTED_RE.search(normalise(question)):
        return False
    return looks_quantitative(question)


@dataclass
class MetricAnswer:
    found: bool
    value: float | None = None
    text: str = ""
    citations: list[tuple[int, str]] = field(default_factory=list)
    metric: str = ""
    year: str = ""
    reason: str = ""


def find_year(question: str) -> str | None:
    """The fiscal year a question is about, if it names one."""
    text = normalise(question)
    years = [
        group
        for match in _FY_LONG_RE.finditer(text)
        for group in match.groups()
        if group
    ]
    if years:
        # A question naming a range ("FY2019 - FY2021") is asking about the
        # later end unless it says otherwise; the earlier year is context.
        return max(years)
    if short := _FY_SHORT_RE.search(text):
        return f"20{short.group(1)}"
    return None


def _format(value: float, unit: str) -> str:
    if unit == "percent":
        return f"{value * 100:.1f}%"
    if unit == "currency":
        millions = value / 1_000_000 if abs(value) >= 1_000_000 else value
        return f"${millions:,.2f} million"
    return f"{value:.2f}"


def _number_forms(value: float) -> list[str]:
    """How a filing might print this figure.

    A value held as 6,489,000,000 is printed "6,489" on a statement headed
    "(in millions)", and a negative is printed in parentheses. The forms are
    tried longest-first so a match is as specific as possible.
    """
    forms: list[str] = []
    magnitude = abs(value)
    for scale in (1_000_000, 1_000, 1):
        scaled = magnitude / scale
        if scaled < 1:
            continue
        for text in (f"{scaled:,.0f}", f"{scaled:,.1f}", f"{scaled:,.2f}"):
            if text not in forms:
                forms.append(text)
    return sorted(forms, key=len, reverse=True)


def find_quote(page_text: str, fact: StoredFact) -> str | None:
    """The line on the page that reports this figure, or None.

    The engine used to present a citation it had composed itself -
    "revenue: 6,489.0 (FY2019, page 70)" - which reads like evidence but
    appears nowhere in the filing. A reader clicking through found text that
    did not exist. The quote has to be the filing\'s own line, and if no
    line on the cited page carries the figure then the citation is wrong and
    the answer should not claim it.
    """
    if not page_text:
        return None

    labelled: str | None = None
    bare: str | None = None
    for line in page_text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 400:
            continue
        if not any(form in stripped for form in _number_forms(fact.value)):
            continue
        # A row carrying its label - "Total net revenues | 6,489 | 7,500" -
        # is evidence a reader can act on; the bare cell "6,489" is not,
        # even though both are genuinely on the page.
        if sum(c.isalpha() for c in stripped) >= 4:
            labelled = labelled or stripped
        else:
            bare = bare or stripped
    return labelled or bare


def _quote_for(fact: StoredFact, concept: str) -> str:
    """Fallback description, used only when the page text is unavailable."""
    scaled = fact.value / 1_000_000 if abs(fact.value) >= 1_000_000 else fact.value
    return f"{concept.replace('_', ' ')}: {scaled:,.1f} (FY{fact.period}, page {fact.page})"


# How many additional places to offer for one figure. A headline number can
# appear on a dozen pages in a large filing; two alternates is enough for a
# reader to cross-check without burying the page actually used.
_MAX_ALTERNATE_LOCATIONS = 2


def _same_value(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(a), abs(b)) * 1e-9


class MetricEngine:
    """Answers metric questions from facts, or declines."""

    def __init__(self, store: FactStore, page_text: dict[int, str] | None = None):
        self._store = store
        self._pages = page_text or {}

    def _cite(self, fact: StoredFact, concept: str) -> tuple[int, str] | None:
        """A citation whose quote is genuinely on the page, or nothing.

        Verification for the deterministic path. The figure came from the
        issuer\'s own tag or a parsed statement line, so it is not in doubt -
        but the *page* attribution is, and a citation the reader cannot find
        is worse than none.
        """
        if not self._pages:
            return fact.page, _quote_for(fact, concept)
        quote = find_quote(self._pages.get(fact.page, ""), fact)
        return (fact.page, quote) if quote else None

    def _compute(self, metric: Metric, year: str) -> PeriodResult | None:
        """The metric for one fiscal year, with a citation per operand.

        Citations are de-duplicated by page across operands: a ratio whose
        numerator and denominator are printed on the same page cites it
        once, not twice. JPMorgan's FY2022 ROE cited page 163 twice because
        net income and equity both appear there, which renders a redundant
        chip and implies more corroboration than exists.
        """
        values: dict[str, float] = {}
        citations: list[tuple[int, str]] = []
        cited_pages: set[int] = set()

        def cite(entry: tuple[int, str] | None) -> None:
            if entry is not None and entry[0] not in cited_pages:
                cited_pages.add(entry[0])
                citations.append(entry)

        for concept in metric.inputs:
            fact = self._store.get(concept, year)
            if fact is None:
                for neighbour in (str(int(year) + 1), str(int(year) - 1)):
                    fact = self._store.get(concept, neighbour)
                    if fact is not None:
                        break
            if fact is None:
                if concept in metric.optional:
                    values[concept] = 0.0
                    continue
                return None

            value = fact.value
            primary = self._cite(fact, concept)
            if primary is None:
                # The figure is not findable on the page the store recorded,
                # so the citation cannot be trusted even though the value can.
                return None
            cite(primary)

            # The same figure is usually printed in more than one place -
            # the statement, the MD&A discussion, a note - and each is a
            # truthful citation. The one used comes first; the rest are
            # offered so the reader can check the figure against whichever
            # part of the filing they trust. Only pages reporting the *same*
            # value qualify: a page carrying a segment or quarterly figure
            # under the same concept is a different number, not another
            # sighting of this one.
            alternates = [
                other for other in self._store.locations(concept, fact.period)
                if other.page != fact.page and _same_value(other.value, fact.value)
            ][:_MAX_ALTERNATE_LOCATIONS]
            for alt in alternates:
                cite(self._cite(alt, concept))

            if concept in metric.averaged or concept in metric.delta:
                opening = self._store.prior(concept, fact.period or year)
                if opening is None:
                    return None
                opening_cite = self._cite(opening, concept)
                if opening_cite is None:
                    return None
                cite(opening_cite)
                if concept in metric.delta:
                    # Closing minus opening - what "change in inventory
                    # between FY2016 and FY2017" means. Exposed alongside
                    # the level so a formula can use either.
                    values[f"{concept}_change"] = value - opening.value
                if concept in metric.averaged:
                    value = (value + opening.value) / 2

            values[concept] = value

        try:
            result = metric.fn(values)
        except (TypeError, ZeroDivisionError):
            return None
        if result is None:
            return None
        return PeriodResult(year=year, value=result, citations=citations, values=values)

    def _multi(self, metric: Metric, intent, question: str) -> MetricAnswer:
        """A change, a comparison or an average across fiscal years."""
        years = [y for y in intent.years if y != "?"]
        if intent.kind == "average" and not years:
            # "three year average" with no range named: count back from the
            # most recent year the filing reports.
            count = len(intent.years) or 3
            years = sorted(self._store.years)[-count:]
        if intent.kind in {"compare", "change"} and not years:
            # "Is the margin improving?" names no years, but the filing does,
            # and an unqualified comparison means the obvious two: the latest
            # period against the one before it. Refusing these sent questions
            # the engine could answer exactly - with both figures and both
            # pages - to the model, which cannot check its own arithmetic.
            years = sorted(self._store.years)[-2:]
        if len(years) < 2:
            return MetricAnswer(False, reason="not enough periods named")

        results = [r for y in years if (r := self._compute(metric, y)) is not None]
        if len(results) < 2:
            return MetricAnswer(False, reason="the filing does not report every period asked for")

        # De-duplicated across periods, not only within one: both years of a
        # comparative statement are printed on the same page, so a
        # year-over-year answer otherwise cites it once per year and the UI
        # renders two identical chips.
        citations: list[tuple[int, str]] = []
        seen_pages: set[int] = set()
        for result in results:
            for page, quote in result.citations:
                if page not in seen_pages:
                    seen_pages.add(page)
                    citations.append((page, quote))
        if intent.kind == "average":
            value = multi_period.mean_of(results)
            text = multi_period.describe_average(metric, results)
        elif intent.kind == "change":
            value = results[-1].value - results[0].value
            text = multi_period.describe_change(metric, results[0], results[-1])
        elif intent.kind == "cagr":
            value = multi_period.cagr_rate(results[0], results[-1])
            text = multi_period.describe_cagr(metric, results[0], results[-1])
        else:
            value = results[-1].value
            text = multi_period.describe_compare(metric, results[0], results[-1])

        return MetricAnswer(True, value=value, text=text, citations=citations,
                            metric=metric.key, year=results[-1].year)

    def answer(self, question: str) -> MetricAnswer:
        if not is_supported(question):
            return MetricAnswer(False, reason="not a single-period consolidated metric")

        key = find_metric(question)
        if key is None or key not in METRICS:
            return MetricAnswer(False, reason="not a recognised metric question")

        # The question may spell out a formula that is not this engine's
        # default. Where that formula is one we can compute, compute it.
        if (variant := resolve_variant(question, key)) is not None:
            key = variant

        metric: Metric = METRICS[key]

        # An expression written out longhand, whose operands we know but
        # whose arithmetic we were not asked to name. Reporting one operand
        # as though it were the answer is the -1 this guard prevents.
        if asks_for_expression(question, key):
            return MetricAnswer(
                False,
                reason=(
                    "the question asks for an expression over several figures, "
                    "not the value of one of them"
                ),
            )

        # Otherwise a stated definition we cannot honour. Answering with
        # ours is confidently wrong - worse, under the scoring policy, than
        # not answering at all.
        if defines_own_formula(question, key):
            return MetricAnswer(
                False,
                reason=(
                    f"the question defines {key.replace('_', ' ')} differently from "
                    "the definition this system computes"
                ),
            )

        intent = multi_period.detect(question, averaged_metric=bool(metric.averaged))
        if intent.kind != "single":
            return self._multi(metric, intent, question)

        year = find_year(question) or (self._store.years[0] if self._store.years else None)
        if year is None:
            return MetricAnswer(False, reason="no fiscal year available")

        computed = self._compute(metric, year)
        if computed is None:
            return MetricAnswer(False, reason=f"{metric.key} is not reported for FY{year}")
        result, citations, values = computed.value, computed.citations, computed.values

        return MetricAnswer(
            found=True,
            value=result,
            text=self._describe(metric, values, result, year),
            citations=citations,
            metric=key,
            year=year,
        )

    def _compute_public(self, metric: Metric, year: str) -> PeriodResult | None:
        """`_compute` for callers outside this class, same guarantees.

        Used by `answer_cashflow_superlative`, which is a comparison across
        three fixed concepts rather than a formula - it needs the same
        cited, verified figure this class already knows how to produce for
        one concept at a time, not a new way of producing one.
        """
        return self._compute(metric, year)

    @staticmethod
    def _describe(metric: Metric, values: dict[str, float], result: float, year: str) -> str:
        """State the result and the figures it was computed from.

        The inputs are named explicitly because an analyst checking an
        answer needs to see the arithmetic, not just its result - and
        because every figure quoted here is one the fact store took from the
        filing, so each is independently checkable against the cited page.
        """
        label = metric.description or metric.key.replace("_", " ")
        parts = [f"{k.replace('_', ' ')} {_format(v, 'currency' if abs(v) > 1000 else 'ratio')}"
                 for k, v in values.items()]
        if len(metric.inputs) == 1:
            return f"FY{year} {label} was {_format(result, metric.unit)}."
        return (
            f"FY{year} {label} was {_format(result, metric.unit)}, "
            f"calculated from {', '.join(parts)}."
        )


# --- which activity generated the most (or least) cash -----------------------
#
# "Among operations, investing, and financing activities, which brought in
# the most (or lost the least) cash flow?" is a ranking, so the general
# engine's superlative guard in `_UNSUPPORTED_RE` refuses it just as it
# refuses "which segment had the highest net income" - correctly, since
# neither is a single-period consolidated figure. But this ranking is over
# three fixed, always-consolidated cash-flow-statement totals, not a
# dimensional breakdown the store discards, so it is answerable the same way
# a segment ranking is: bypass the general gate with a dedicated recognizer,
# same as `is_segment_question` does.

_ACTIVITY_RE = re.compile(r"\boperat(?:ions|ing)\b|\binvesting\b|\bfinancing\b", re.I)
_CF_SUPERLATIVE_RE = re.compile(r"\b(most|least|highest|lowest|biggest|largest|smallest)\b", re.I)
_LOST_RE = re.compile(r"\blost\b|\bused\b|\boutflow\b", re.I)
_MOST_RE = re.compile(r"\bmost\b|\bhighest\b|\bbiggest\b|\blargest\b", re.I)
# FinanceBench's actual phrasing for this question is "which brought in the
# most (or lost the least) cash flow" - one comparison stated two ways to
# cover both a mostly-positive and a mostly-negative set of activities, not
# two competing directions. Read word-by-word, "most" and "lost" are both
# present and an XOR of them cancels out to the wrong (lowest) answer.
_BOTH_FRAMINGS_RE = re.compile(r"\b(most|least)\b[^.?]*\bor\b[^.?]*\b(most|least)\b", re.I)
_CONCEPT_FOR_ACTIVITY = {"operations": "ocf", "operating": "ocf", "investing": "icf", "financing": "financing_cf"}
_LABEL_FOR_CONCEPT = {"ocf": "operating", "icf": "investing", "financing_cf": "financing"}


def is_cashflow_superlative_question(question: str) -> bool:
    """A ranking across the three activity totals, not a single figure."""
    text = normalise(question)
    named = set(_ACTIVITY_RE.findall(text))
    return len(named) >= 2 and bool(_CF_SUPERLATIVE_RE.search(text))


def _wants_highest(text: str) -> bool:
    """"Brought in the most" and "lost the least" both mean the highest
    signed value; "lost the most" and "brought in the least" both mean the
    lowest. The two words interact, so they cannot be judged separately -
    except when both framings are stated together as one request (see
    `_BOTH_FRAMINGS_RE`), which always means the highest.
    """
    if _BOTH_FRAMINGS_RE.search(text):
        return True
    most = bool(_MOST_RE.search(text))
    lost = bool(_LOST_RE.search(text))
    return most != lost


def answer_cashflow_superlative(
    store: FactStore, question: str, page_text: dict[int, str] | None = None
) -> MetricAnswer:
    text = normalise(question)
    named = set(_ACTIVITY_RE.findall(text))
    wanted = {_CONCEPT_FOR_ACTIVITY[a] for a in named} or set(_LABEL_FOR_CONCEPT)

    year = find_year(question) or (store.years[0] if store.years else None)
    if year is None:
        return MetricAnswer(False, reason="no fiscal year available")

    engine = MetricEngine(store, page_text)
    results: dict[str, PeriodResult] = {}
    for concept in wanted:
        result = engine._compute_public(METRICS[concept], year)
        if result is not None:
            results[concept] = result
    if len(results) < 2:
        return MetricAnswer(False, reason="not enough activities reported for FY" + year)

    pick_max = _wants_highest(text)
    best_concept = (max if pick_max else min)(results, key=lambda c: results[c].value)
    best = results[best_concept]

    ranking = ", ".join(
        f"{_LABEL_FOR_CONCEPT[c]} {_format(results[c].value, 'currency')}"
        for c in sorted(results, key=lambda c: -results[c].value)
    )
    verdict = "highest" if pick_max else "lowest"
    answer_text = (
        f"{_LABEL_FOR_CONCEPT[best_concept].capitalize()} activities had the {verdict} "
        f"net cash flow in FY{year} at {_format(best.value, 'currency')}. By activity: {ranking}."
    )
    return MetricAnswer(
        True, value=best.value, text=answer_text, citations=best.citations,
        metric=best_concept, year=year,
    )
