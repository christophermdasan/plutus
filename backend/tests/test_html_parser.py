"""HTML filings are read for their pages as much as for their text.

A citation names a page, so the tests that matter most here are the ones
about page *boundaries* and page *count* - getting the text right on the
wrong page is worth nothing under the scoring policy.
"""

from __future__ import annotations

import pytest

from app.exceptions import IngestionError
from app.ingestion.chunker import TABLE_MARKER
from app.ingestion.html_parser import (
    parse_html,
    sanitize_page_html,
    split_html_pages,
)
from app.ingestion.parser import parse_document


def _write(tmp_path, name, markup, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(markup.encode(encoding))
    return path


# --- pagination -----------------------------------------------------------


def test_pages_split_on_the_page_break_markers_filings_actually_use():
    markup = (
        "<html><body><p>one</p>"
        '<hr style="page-break-after:always">'
        "<p>two</p>"
        '<div style="page-break-after: always"></div>'
        "<p>three</p></body></html>"
    )
    pages = split_html_pages(markup)
    assert len(pages) == 3
    assert "one" in pages[0] and "two" in pages[1] and "three" in pages[2]


def test_a_horizontal_rule_stands_in_when_no_page_break_style_is_declared():
    markup = "<html><body><p>one</p><hr><p>two</p></body></html>"
    assert len(split_html_pages(markup)) == 2


def test_a_document_with_no_breaks_at_all_is_a_single_page():
    assert len(split_html_pages("<html><body><p>only</p></body></html>")) == 1


def test_blank_pages_are_kept_so_later_citations_do_not_shift(tmp_path):
    # A filing's blank separator page still occupies a page number. Dropping
    # it would move every subsequent page up by one and silently invalidate
    # every citation after it.
    markup = (
        "<body><p>first</p>"
        '<hr style="page-break-after:always">'
        '<hr style="page-break-after:always">'
        "<p>third</p></body>"
    )
    pages = parse_html(_write(tmp_path, "f.htm", markup))
    assert len(pages) == 3
    assert not pages[1].strip()
    assert "third" in pages[2]


def test_the_sgml_envelope_edgar_wraps_filings_in_is_not_treated_as_content(tmp_path):
    markup = (
        "<DOCUMENT>\n<TYPE>10-K\n<FILENAME>x.htm\n<TEXT>\n"
        "<html><body><p>revenue</p></body></html>\n</TEXT>\n</DOCUMENT>"
    )
    pages = parse_html(_write(tmp_path, "f.htm", markup))
    assert "revenue" in pages[0]
    assert "10-K" not in pages[0]


# --- text and tables ------------------------------------------------------


def test_table_rows_keep_each_label_beside_its_figure(tmp_path):
    markup = (
        "<body><table>"
        "<tr><td>Total revenue</td><td>$184.6</td><td>$164.2</td></tr>"
        "<tr><td>Net income</td><td>$21.4</td><td>$18.9</td></tr>"
        "</table></body>"
    )
    page = parse_html(_write(tmp_path, "f.htm", markup))[0]
    assert TABLE_MARKER in page
    # The failure this guards against is a figure drifting away from its
    # label, leaving the model to pair them by guesswork.
    assert "Total revenue | $184.6 | $164.2" in page
    assert "Net income | $21.4 | $18.9" in page


def test_script_and_style_bodies_are_not_read_as_filing_text(tmp_path):
    markup = "<body><style>.x{color:red}</style><script>var y=1</script><p>real</p></body>"
    page = parse_html(_write(tmp_path, "f.htm", markup))[0]
    assert "real" in page
    assert "color:red" not in page and "var y" not in page


def test_a_fragment_opening_with_a_stray_closing_tag_still_yields_its_text(tmp_path):
    # Pages are raw source slices, so one routinely begins mid-tree. lxml
    # discards such a fragment outright, which emptied most pages of a real
    # filing; this is the regression test for that.
    markup = (
        "<body><div><p>first</p>"
        '<hr style="page-break-after:always">'
        "</div><p>second page text</p></body>"
    )
    pages = parse_html(_write(tmp_path, "f.htm", markup))
    assert "second page text" in pages[1]


def test_filings_that_are_not_utf8_are_still_read(tmp_path):
    path = _write(tmp_path, "f.htm", "<body><p>Nestlé revenue</p></body>", encoding="cp1252")
    assert "Nestl" in parse_html(path)[0]


def test_a_document_with_no_extractable_text_is_rejected(tmp_path):
    with pytest.raises(IngestionError):
        parse_html(_write(tmp_path, "f.htm", "<body><div></div></body>"))


# --- dispatch -------------------------------------------------------------


@pytest.mark.parametrize("name", ["f.htm", "f.html", "f.HTML", "f.xhtml"])
def test_parse_document_reads_html_by_extension(tmp_path, name):
    path = _write(tmp_path, name, "<body><p>total revenue</p></body>")
    assert "total revenue" in parse_document(path)[0]


# --- sanitising -----------------------------------------------------------
#
# Filing HTML is uploaded content rendered back to the analyst, so it is
# untrusted regardless of how reputable its source appears.


@pytest.mark.parametrize(
    "markup, must_not_contain",
    [
        ("<script>alert(1)</script><p>keep</p>", "alert"),
        ('<div onclick="steal()">x</div>', "onclick"),
        ('<div OnMouseOver="steal()">x</div>', "onmouseover"),
        ('<a href="javascript:alert(1)">x</a>', "javascript:"),
        ('<img src="data:text/html,<script>alert(1)</script>">', "data:"),
        ('<iframe src="//evil.example"></iframe><p>keep</p>', "iframe"),
        ('<object data="//evil.example"></object><p>keep</p>', "object"),
        ('<form action="//evil.example"><input></form>', "form"),
        ('<base href="//evil.example">', "base"),
    ],
)
def test_executable_content_is_stripped_before_rendering(markup, must_not_contain):
    assert must_not_contain not in sanitize_page_html(markup).lower()


def test_sanitising_leaves_the_page_looking_like_the_document(tmp_path):
    # The point of showing the source is recognition: an analyst should see
    # the table they know. Formatting therefore survives sanitising.
    markup = '<table><tr><td style="font-weight:bold">Revenue</td><td>$184.6</td></tr></table>'
    out = sanitize_page_html(markup)
    assert "font-weight:bold" in out
    assert "Revenue" in out and "$184.6" in out
