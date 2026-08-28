"""Extracts per-page text from a filing served as HTML.

EDGAR publishes most filings as HTML, and analysts hold on to those files
rather than re-exporting them, so the pipeline has to read them directly.

The hard part is pages. A citation is only useful if it names a page the
reader can turn to, and HTML has no intrinsic pagination. Filing HTML does
carry the pagination its author intended, as elements styled
`page-break-after: always` - 73 of the 78 documents in the reference corpus
mark every page that way, and the remainder fall back to `<hr>` rules, which
the same generators emit at the same boundaries.

Splitting on those markers is deliberately done on the raw markup rather than
a parsed tree. The break element is usually a sibling several levels down
from `<body>`, so an element-level split would have to reason about which
ancestors to close and reopen; slicing the source and letting the HTML parser
repair each fragment gets the same pages without that bookkeeping.

Pages are never dropped, even when blank. Page numbers are the product, so an
index that skips an empty page would silently shift every citation after it.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from app.exceptions import IngestionError
from app.ingestion.chunker import TABLE_MARKER

# An opening tag whose style declares a page break. Matched on the raw source,
# so it catches the break wherever it sits in the tree.
_PAGE_BREAK_RE = re.compile(r"<[^>]*page-break-(?:after|before)\s*:\s*always[^>]*>", re.I)
_HR_RE = re.compile(r"<hr[^>]*>", re.I)

# The SGML envelope EDGAR wraps around the document itself. Its header tags
# are unclosed, with the value trailing on the same line - "<TYPE>10-K",
# "<FILENAME>mmm-20181231x10k.htm" - so removing just the tags would leave
# "10-K" and the filename loose at the top of page 1, where they would be
# indexed and could be quoted back as filing text. The whole header block is
# dropped instead.
_SGML_HEADER_RE = re.compile(r"(?is)<DOCUMENT>.*?<TEXT>")
_SGML_FOOTER_RE = re.compile(r"(?is)</TEXT>\s*</DOCUMENT>")

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote",
}


def _decode(path: Path) -> str:
    """EDGAR HTML predates consistent charset declarations."""
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def split_html_pages(raw: str) -> list[str]:
    """Split filing markup into one HTML fragment per page.

    Returned fragments are source slices, so a fragment may open tags it never
    closes. Every consumer parses them, and both BeautifulSoup and browsers
    repair that, which is why the pages are not re-serialised here.
    """
    body = _SGML_FOOTER_RE.sub("", _SGML_HEADER_RE.sub("", raw))

    pages = _PAGE_BREAK_RE.split(body)
    if len(pages) == 1:
        # No explicit breaks: the same generators rule a horizontal line at
        # each page foot, so it stands in as the boundary.
        pages = _HR_RE.split(body)

    return pages


def _serialize_table(table) -> str:
    """One row per line, cells kept adjacent to their labels.

    A financial table read as flat text separates a line item from its figure
    by whole columns of unrelated numbers. Emitting each row explicitly keeps
    "Total revenue" next to "$184.6" - the same reason the PDF path serialises
    tables rather than trusting reading order.
    """
    lines = []
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _page_text(fragment: str) -> str:
    # html.parser, not lxml. Fragments are source slices, so one routinely
    # opens with a stray closing tag; lxml discards the entire fragment when
    # it does, silently emptying most pages of a filing. The stdlib parser
    # recovers the content, and costs no third-party dependency.
    soup = BeautifulSoup(fragment, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    # Serialised before the prose pass, which would otherwise flatten them.
    tables = [text for table in soup.find_all("table") if (text := _serialize_table(table))]

    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")

    prose = re.sub(r"[ \t\xa0]+", " ", soup.get_text())
    prose = re.sub(r"\n\s*\n\s*\n+", "\n\n", prose).strip()

    serialized = [f"{TABLE_MARKER}\n{t}" for t in tables]
    return "\n\n".join([prose, *serialized]) if serialized else prose


def parse_html(path: str | Path) -> list[str]:
    """Return one text blob per page, tables appended and marked.

    Mirrors `parse_pdf`, so everything downstream - chunking, retrieval,
    citation, verification - is unaware of which format it came from.
    """
    path = Path(path)

    try:
        pages = [_page_text(fragment) for fragment in split_html_pages(_decode(path))]
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(f"Could not read this HTML filing: {exc}") from exc

    if not pages:
        raise IngestionError("No pages could be extracted from this document.")
    if not any(page.strip() for page in pages):
        raise IngestionError("This document contains no extractable text.")

    return pages


# Anything that can execute, navigate, or reach the network. Filing HTML is
# uploaded content: it is displayed to the analyst, so it is treated as
# untrusted no matter how reputable its source looks.
_UNSAFE_TAGS = ["script", "iframe", "object", "embed", "applet", "form", "link", "meta", "base"]
_URL_ATTRS = ("href", "src", "action", "background", "poster")


def sanitize_page_html(fragment: str) -> str:
    """Strip anything executable from a page before it is rendered.

    The viewer additionally renders this inside a sandboxed iframe, so this is
    the inner of two layers rather than the only one. Formatting is left
    alone - the point of showing the source page is that it looks like the
    document the analyst knows.
    """
    soup = BeautifulSoup(fragment, "html.parser")

    for tag in soup(_UNSAFE_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            value = tag.attrs[attr]
            # Inline event handlers, and javascript:/data: URLs, are script
            # in every position they can appear.
            if attr.lower().startswith("on"):
                del tag.attrs[attr]
            elif attr.lower() in _URL_ATTRS:
                url = (value if isinstance(value, str) else " ".join(value)).strip().lower()
                if url.startswith(("javascript:", "data:", "vbscript:")):
                    del tag.attrs[attr]

    return str(soup)
