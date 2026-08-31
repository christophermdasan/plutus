"""Reads the figures an issuer already machine-tagged in its own filing.

Since the SEC's inline-XBRL phase-in (2019-2021), a filing's HTML carries
every reported figure twice: once as the text a person reads, and once as an
`<ix:nonFraction>` element naming a standardised US-GAAP concept. 52 of the
78 filings in the practice corpus are tagged this way, covering 82% of the
questions asked about them.

That matters because the measured failure in this pipeline is vocabulary,
not reasoning. An analyst asks for "capital expenditure"; the cash-flow
statement says "Purchases of property, plant and equipment (PP&E)" and never
contains the phrase asked for, so BM25 has no term to match and the page -
a dense grid of numbers - embeds too weakly for a paraphrase match to
rescue. The issuer, however, tagged that exact line
`us-gaap:PaymentsToAcquirePropertyPlantAndEquipment`. The concept name is
the bridge between the two vocabularies, and it is authoritative rather
than inferred.

Deliberately parsed here rather than via a library. `edgartools` reads this
well, but it is built for fetching filings from EDGAR and brings pandas and
pyarrow with it; what is needed is a few hundred numbers per document, and
the tags are regular enough that reading them directly costs less than the
dependency. Nothing here reaches the network.

Two properties keep this safe to rely on:

- **Facts carry the page they were tagged on.** Offsets are measured
  against the same raw markup `html_parser` splits into pages, so a fact
  resolves to the page a citation would name - not an approximation of it.
- **Dimensional facts are dropped.** A context carrying an
  `xbrldi:explicitMember` scopes its figure to a segment, a geography or a
  single class of stock. Consolidated questions must not be answered from
  one, so only undimensioned contexts are kept.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

_FACT_RE = re.compile(r"<ix:nonfraction([^>]*)>([^<]*)<", re.I)
_CONTEXT_RE = re.compile(r"<xbrli:context id=\"([^\"]+)\"(.*?)</xbrli:context>", re.S | re.I)
# EDGAR filers vary the namespace prefix; some emit <context> unprefixed.
_CONTEXT_RE_BARE = re.compile(r"<context id=\"([^\"]+)\"(.*?)</context>", re.S | re.I)
_INSTANT_RE = re.compile(r"<(?:xbrli:)?instant>([^<]+)", re.I)
_END_DATE_RE = re.compile(r"<(?:xbrli:)?enddate>([^<]+)", re.I)
_START_DATE_RE = re.compile(r"<(?:xbrli:)?startdate>([^<]+)", re.I)
_DIMENSION_RE = re.compile(r"explicitmember|typedmember", re.I)

_NON_NUMERIC_RE = re.compile(r"[^\d.\-]")


def _attr(tag_body: str, name: str) -> str | None:
    match = re.search(name + r"=\"([^\"]*)\"", tag_body, re.I)
    return match.group(1) if match else None


@dataclass(frozen=True)
class Fact:
    concept: str          # "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    value: float          # already scaled and signed
    period_end: str       # "2019-12-31"
    period_start: str     # "" for instantaneous (balance sheet) facts
    page: int             # 1-based, matching html_parser's pagination
    # The segment, geography or share class this figure is scoped to, as the
    # issuer named it ("jpm:CorporateInvestmentBankMember"), or "" for the
    # consolidated figure. Kept rather than discarded: a fifth of the
    # questions in the practice set ask which segment led on some measure,
    # and the answer is tagged right here. Consolidated lookups filter these
    # out, so keeping them cannot contaminate a whole-company answer.
    member: str = ""
    # The axis the member hangs from, as the issuer named it. Kept because
    # "which segment" and "which region" are different questions answered
    # from different axes, and the member name alone does not say which it
    # is: read together, JPMorgan's largest *geography* outranks its largest
    # *business segment* and answers a question nobody asked.
    axis: str = ""

    @property
    def is_segment(self) -> bool:
        return bool(self.member)

    @property
    def local_name(self) -> str:
        return self.concept.split(":", 1)[-1]

    @property
    def is_instant(self) -> bool:
        return not self.period_start


# A segment or business-unit axis. Other axes - fair-value hierarchy, share
# class, financial-instrument type - also carry members, but they are not
# what "which segment" questions mean.
_SEGMENT_AXIS_RE = re.compile(r"(?:StatementBusinessSegments|OperatingSegments|ProductOrService|StatementGeographical)Axis", re.I)
# Which kind of breakdown an axis describes. A business-segment axis carries
# CIB and Consumer Banking; a geographical axis carries North America and
# EMEA. Both are legitimate breakdowns of the same concept, and answering
# one with the other is confidently wrong rather than merely unhelpful.
_GEOGRAPHY_AXIS_RE = re.compile(r"(?:StatementGeographical|Geographic)", re.I)
_MEMBER_RE = re.compile(r"<xbrldi:explicitMember dimension=\"([^\"]+)\">([^<]+)<", re.I)


def _parse_contexts(body: str) -> dict[str, tuple[str, str, str, str]]:
    """context id -> (period_end, period_start, member, axis).

    `member` is the segment the figure is scoped to, or "" when the context
    carries no dimension at all - the consolidated figure. `axis` is the
    dimension that member hangs from, which is what tells a business segment
    apart from a geography.
    """
    contexts: dict[str, tuple[str, str, str, str]] = {}
    for pattern in (_CONTEXT_RE, _CONTEXT_RE_BARE):
        for match in pattern.finditer(body):
            ctx_id, block = match.group(1), match.group(2)

            members = _MEMBER_RE.findall(block)
            if members and not any(_SEGMENT_AXIS_RE.search(axis) for axis, _ in members):
                # Dimensional, but on an axis no question in this domain
                # means - fair-value hierarchy, instrument type, share
                # class. Keeping these would let one answer a consolidated
                # question with a slice of it.
                continue
            axis_name, member = next(
                ((axis, m) for axis, m in members if _SEGMENT_AXIS_RE.search(axis)), ("", "")
            )

            instant = _INSTANT_RE.search(block)
            if instant:
                contexts[ctx_id] = (instant.group(1).strip(), "", member, axis_name)
                continue
            end = _END_DATE_RE.search(block)
            start = _START_DATE_RE.search(block)
            if end:
                contexts[ctx_id] = (
                    end.group(1).strip(), start.group(1).strip() if start else "",
                    member, axis_name,
                )
        if contexts:
            break
    return contexts


def _to_value(raw: str, scale: str | None, sign: str | None, fmt: str | None) -> float | None:
    text = _NON_NUMERIC_RE.sub("", (raw or "").replace(",", ""))
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if scale:
        try:
            value *= 10 ** int(scale)
        except ValueError:
            pass
    # A tagged negative carries sign="-" rather than a minus in the text,
    # because the text renders as "(473)".
    if sign == "-":
        value = -value
    if fmt and "zerodash" in fmt.lower() and value == 0:
        return 0.0
    return value


def extract_facts(body: str, page_offsets: list[int]) -> list[Fact]:
    """Facts tagged in `body`, each resolved to a page.

    `page_offsets` are the character offsets of the page breaks in the same
    string, so a fact's position places it on a page directly.
    """
    contexts = _parse_contexts(body)
    if not contexts:
        return []

    facts: list[Fact] = []
    for match in _FACT_RE.finditer(body):
        attrs, raw = match.group(1), match.group(2)
        concept = _attr(attrs, "name")
        ctx_ref = _attr(attrs, "contextref")
        if not concept or ctx_ref not in contexts:
            continue

        value = _to_value(raw, _attr(attrs, "scale"), _attr(attrs, "sign"), _attr(attrs, "format"))
        if value is None:
            continue

        period_end, period_start, member, axis_name = contexts[ctx_ref]
        facts.append(
            Fact(
                concept=concept,
                value=value,
                period_end=period_end,
                period_start=period_start,
                page=bisect.bisect_right(page_offsets, match.start()) + 1,
                member=member,
                axis=axis_name,
            )
        )
    return facts


def has_xbrl(body: str) -> bool:
    return bool(_FACT_RE.search(body))


def extract_from_file(path: str | Path) -> list[Fact]:
    """Convenience wrapper that re-derives pagination the same way parsing does."""
    from app.ingestion.html_parser import (
        _PAGE_BREAK_RE,
        _SGML_FOOTER_RE,
        _SGML_HEADER_RE,
        _decode,
    )

    raw = _decode(Path(path))
    body = _SGML_FOOTER_RE.sub("", _SGML_HEADER_RE.sub("", raw))
    offsets = [m.start() for m in _PAGE_BREAK_RE.finditer(body)]
    return extract_facts(body, offsets)
