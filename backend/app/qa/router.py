"""Decides whether a question needs the language model at all.

Two kinds of question arrive at this system and they want opposite
machinery:

- *"What is the FY2019 fixed asset turnover ratio?"* is a definition applied
  to figures the issuer reported. Code does that exactly, every time, in
  under a millisecond. The model does it wrong often enough to matter -
  measured on this corpus it read revenue 6,489 and PP&E 253/282 correctly
  off two pages, averaged to 267.5 correctly, then reported the quotient as
  24.77 where it is 24.26.

- *"What was the key agenda of AMCOR's 8-K?"* is prose. No table of formulas
  reaches it; the answer is a summary of a legal event. Only a language
  model can produce that.

Routing between them explicitly - rather than letting the engine try and
silently fall through - makes the decision inspectable and testable, and
lets the answer say which path produced it. A reader is entitled to know
whether a figure was computed from tagged data or read out of prose by a
model, because those two answers deserve different amounts of trust.

The rule is deliberately conservative: the deterministic path is taken only
when the question names a metric the ontology defines, asks for a quantity,
and the filing reports every operand. Anything else goes to the model,
because a wrong figure costs more than a slow one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.finance.metric_engine import is_supported
from app.finance.ontology import METRICS, find_metric

DETERMINISTIC = "deterministic"
LANGUAGE_MODEL = "llm"


@dataclass(frozen=True)
class Route:
    kind: str          # DETERMINISTIC | LANGUAGE_MODEL
    reason: str
    metric: str = ""

    @property
    def needs_llm(self) -> bool:
        return self.kind == LANGUAGE_MODEL


def route(question: str, has_facts: bool = True) -> Route:
    """Which engine should answer this question, and why."""
    if not has_facts:
        return Route(LANGUAGE_MODEL, "no structured figures extracted from this filing")

    if not is_supported(question):
        # Segment breakdowns, superlatives across a dimension, or a question
        # that names a metric only in passing ("which debt securities...").
        return Route(LANGUAGE_MODEL, "not a consolidated quantity this system computes")

    metric = find_metric(question)
    if metric is None or metric not in METRICS:
        return Route(LANGUAGE_MODEL, "no defined metric matches this question")

    return Route(DETERMINISTIC, f"computable from reported figures ({metric})", metric=metric)
