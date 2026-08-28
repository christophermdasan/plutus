"""Splits parsed pages into retrievable passages.

The previous version used one chunk per page. That kept citations trivially
page-accurate, but a 10-K page runs to several thousand characters and a
single embedding over all of it is badly diluted - the sentence that
actually answers the question gets averaged away with everything else on
the page.

This splits pages into smaller passages while keeping a page anchor on each
one, so retrieval gets a focused signal and citations still resolve to a
real page. Two constraints shape the splitting:

- Split on paragraph boundaries, never mid-sentence, so a passage always
  reads as a coherent unit to the embedder and the reranker.
- Never split a table *row*. Financial tables are precisely where the numbers
  analysts ask about live, and separating a figure from its label destroys
  the thing that makes them answerable.

A large table is divided between rows, with its header repeated on each part.
Keeping one whole was the original rule and it quietly did the opposite of
what it promised: the embedder truncates at 512 tokens, so on a 26,000
character table only the first tenth was ever represented, and every row
below that was unreachable by search while still being paid for at ingest.
Repeating the header keeps each row interpretable, which is the invariant
that actually mattered.
"""

from __future__ import annotations

import hashlib
import re

from app.domain.models import Passage

# The parser marks serialized tables with this so the chunker can keep them
# intact; it is also a useful cue to the model that rows are tabular data.
TABLE_MARKER = "[TABLE]"

TARGET_CHARS = 900
MAX_CHARS = 1600

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _passage_id(filing_id: str, page: int, ordinal: int, text: str) -> str:
    """Deterministic id, so re-ingesting the same filing upserts in place."""
    digest = hashlib.sha1(f"{filing_id}:{page}:{ordinal}:{text}".encode()).hexdigest()[:16]
    return f"{filing_id}:p{page}:{ordinal}:{digest}"


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]


def _split_blocks(page_text: str) -> list[str]:
    """Break a page into blocks, keeping each serialized table whole.

    Prose is split on blank lines; everything from a TABLE_MARKER to the end
    of that section stays in one block so a table is never divided.
    """
    if TABLE_MARKER not in page_text:
        return _paragraphs(page_text)

    prose, *table_sections = page_text.split(TABLE_MARKER)
    blocks = _paragraphs(prose)
    blocks.extend(f"{TABLE_MARKER}\n{section.strip()}" for section in table_sections)
    return blocks


# Roughly the 512 tokens the embedding and reranking models accept. Anything
# past it is tokenised and then discarded, so a passage larger than this is
# paid for in full and represented only in part.
_MODEL_WINDOW_CHARS = 2000


def _split_table(block: str) -> list[str]:
    """Divide an oversized table between rows, repeating its header.

    Each row keeps its own label and the header travels with it, so a figure
    is still interpretable wherever it lands.
    """
    marker, _, body = block.partition("\n")
    rows = [row for row in body.split("\n") if row.strip()]
    if len(rows) < 3:
        return [block]  # nothing meaningful to divide

    header, *data = rows
    parts: list[str] = []
    current: list[str] = []
    size = 0

    for row in data:
        if current and size + len(row) > TARGET_CHARS:
            parts.append("\n".join([marker, header, *current]))
            current, size = [], 0
        current.append(row)
        size += len(row) + 1

    if current:
        parts.append("\n".join([marker, header, *current]))
    return parts or [block]


def _split_prose(block: str) -> list[str]:
    """Divide a paragraph that is long enough to overflow the model window.

    Split between sentences; a run of text with no sentence break at all is
    cut on length rather than left oversized.
    """
    sentences = re.split(r"(?<=[.!?])\s+", block)
    parts: list[str] = []
    current = ""

    for sentence in sentences:
        while len(sentence) > MAX_CHARS:  # no break to split on
            parts.append(sentence[:MAX_CHARS])
            sentence = sentence[MAX_CHARS:]
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > TARGET_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = candidate

    if current:
        parts.append(current)
    return parts or [block]


def _divide(block: str) -> list[str]:
    """Bring a single oversized block within the model's window."""
    if len(block) <= _MODEL_WINDOW_CHARS:
        return [block]
    return _split_table(block) if block.startswith(TABLE_MARKER) else _split_prose(block)


def _pack(blocks: list[str]) -> list[str]:
    """Greedily combine blocks up to the target size.

    A block too large to be represented on its own is divided first, so no
    passage is emitted that the embedder would only read the beginning of.
    """
    packed: list[str] = []
    current = ""

    for block in blocks:
        if len(block) >= MAX_CHARS:
            if current:
                packed.append(current)
                current = ""
            packed.extend(_divide(block))
            continue

        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > TARGET_CHARS and current:
            packed.append(current)
            current = block
        else:
            current = candidate

    if current:
        packed.append(current)
    return packed


def chunk_pages(filing_id: str, page_texts: list[str]) -> list[Passage]:
    passages: list[Passage] = []

    for index, page_text in enumerate(page_texts):
        page_number = index + 1
        if not page_text or not page_text.strip():
            continue

        for ordinal, text in enumerate(_pack(_split_blocks(page_text))):
            passages.append(
                Passage(
                    id=_passage_id(filing_id, page_number, ordinal, text),
                    filing_id=filing_id,
                    page=page_number,
                    text=text,
                    ordinal=ordinal,
                )
            )

    return passages
