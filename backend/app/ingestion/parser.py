"""Extracts per-page text from a filing.

`parse_document` is the entry point: it picks a reader by file type so that
chunking, retrieval, citation and verification stay format-agnostic. PDF is
handled here; HTML lives in `html_parser` because pagination there is a
different problem entirely.

Prose comes from PyMuPDF (fast, reliable reading order). Tables are pulled
separately with pdfplumber and serialized row-by-row, because a table's
visual grid has no dependable linear reading order - extracted as flat text,
a line item and its value end up separated by whole columns of other
numbers. Serializing each row explicitly keeps "Total revenue" next to
"$184.6".

Serialized tables are prefixed with TABLE_MARKER so the chunker knows not to
split them and the model knows the rows are tabular.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pymupdf

from app.exceptions import IngestionError
from app.ingestion.chunker import TABLE_MARKER
from app.ingestion.html_parser import parse_html


# Bordered tables are found by their ruling lines. Plenty of real filings
# align columns with whitespace instead and have no lines at all, so a text
# based pass runs as a fallback - guarded, because that strategy will
# happily read ordinary prose as a one-column "table".
_LINE_STRATEGY = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
_TEXT_STRATEGY = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    # pdfplumber defaults to requiring 3 aligned words before it will call
    # something a column, which silently misses short tables - exactly the
    # summary tables ("Item | 2023 | 2022") that carry headline figures.
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
}

_MIN_TABLE_ROWS = 2
_MIN_TABLE_COLUMNS = 2


def _serialize_table(rows: list[list[str | None]]) -> str:
    lines = []
    for row in rows:
        cells = [(cell or "").strip() for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _looks_like_a_table(rows: list[list[str | None]]) -> bool:
    """Reject single-column or single-row 'tables' from the text strategy."""
    populated = [row for row in rows if any((cell or "").strip() for cell in row)]
    if len(populated) < _MIN_TABLE_ROWS:
        return False
    return max(
        sum(1 for cell in row if (cell or "").strip()) for row in populated
    ) >= _MIN_TABLE_COLUMNS


def _extract_tables(plumber_page) -> list[list[list[str | None]]]:
    if tables := plumber_page.extract_tables(_LINE_STRATEGY):
        return tables
    return [t for t in plumber_page.extract_tables(_TEXT_STRATEGY) if _looks_like_a_table(t)]


def parse_pdf(path: str | Path) -> list[str]:
    """Return one text blob per page, tables appended and marked."""
    path = Path(path)

    # Text first, and cheaply. PyMuPDF reads a 243-page document in about
    # eight seconds, which is what makes it affordable to establish "is there
    # any text here at all?" before doing anything expensive.
    try:
        with pymupdf.open(path) as doc:
            page_texts = [doc[i].get_text() for i in range(len(doc))]
    except Exception as exc:
        raise IngestionError(f"Could not read this PDF: {exc}") from exc

    if not page_texts:
        raise IngestionError("No pages could be extracted from this PDF.")

    # A document with no text layer can never be answered from, so stop before
    # the table pass rather than after it. This check used to sit at the end,
    # and a 73MB print-to-PDF - 243 pages, 605,000 vector drawings, zero
    # extractable characters - spent over fifteen minutes and six gigabytes
    # hunting tables in a document that was never going to yield an answer.
    if not any(text.strip() for text in page_texts):
        raise IngestionError(
            "This PDF contains no extractable text.",
            detail="It may be scanned, or printed to PDF in a way that turned "
            "the text into vector outlines. OCR is not supported yet.",
        )

    pages: list[str] = []
    try:
        with pdfplumber.open(path) as plumber_doc:
            for page_index, text in enumerate(page_texts):
                # Nothing readable on this page means nothing to tabulate, and
                # table detection is the expensive half of parsing.
                if not text.strip():
                    pages.append(text)
                    continue

                page = plumber_doc.pages[page_index]
                try:
                    serialized = [
                        f"{TABLE_MARKER}\n{table_text}"
                        for table in _extract_tables(page)
                        if (table_text := _serialize_table(table))
                    ]
                finally:
                    # pdfplumber caches every object it parses for a page and
                    # holds it for the lifetime of the document. Across a few
                    # hundred dense pages that is gigabytes, and the machine
                    # starts swapping long before the parse finishes.
                    page.close()

                pages.append("\n\n".join([text, *serialized]) if serialized else text)
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(f"Could not read this PDF: {exc}") from exc

    return pages


_HTML_SUFFIXES = {".htm", ".html", ".xhtml"}


def parse_document(path: str | Path) -> list[str]:
    """Return one text blob per page, whatever format the filing arrived in.

    Analysts keep filings in whichever form they downloaded them - EDGAR
    serves HTML, most people archive PDFs - so both are first class rather
    than one being converted into the other. Converting HTML to PDF would
    re-paginate it against whatever the renderer decided, moving every
    citation off the page the source actually used.
    """
    path = Path(path)
    if path.suffix.lower() in _HTML_SUFFIXES:
        return parse_html(path)
    return parse_pdf(path)
