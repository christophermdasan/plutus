import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table

from app.exceptions import IngestionError
from app.ingestion.chunker import TABLE_MARKER
from app.ingestion.parser import parse_pdf


@pytest.fixture
def fixture_pdf(tmp_path):
    path = tmp_path / "fixture.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    doc.build(
        [
            Paragraph("Total revenue for fiscal 2023 was $12.4 million.", styles["Normal"]),
            PageBreak(),
            Table([["Item", "2023", "2022"], ["Revenue", "$12.4M", "$10.1M"]]),
            PageBreak(),
            Paragraph("Third page has no table, just prose about risk factors.", styles["Normal"]),
        ]
    )
    return path


def test_returns_one_entry_per_page_in_order(fixture_pdf):
    pages = parse_pdf(fixture_pdf)
    assert len(pages) == 3
    assert "risk factors" in pages[2]


def test_extracts_paragraph_text(fixture_pdf):
    assert "Total revenue for fiscal 2023 was $12.4 million." in parse_pdf(fixture_pdf)[0]


def test_serializes_table_rows_keeping_labels_beside_their_values(fixture_pdf):
    table_page = parse_pdf(fixture_pdf)[1]
    assert "Revenue | $12.4M | $10.1M" in table_page


def test_marks_serialized_tables_so_the_chunker_can_keep_them_whole(fixture_pdf):
    assert TABLE_MARKER in parse_pdf(fixture_pdf)[1]
    assert TABLE_MARKER not in parse_pdf(fixture_pdf)[2]


def test_a_corrupt_file_raises_a_domain_error_not_a_library_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_text("this is definitely not a pdf")

    with pytest.raises(IngestionError):
        parse_pdf(bad)


def test_a_pdf_with_no_text_layer_is_rejected_before_the_expensive_pass(tmp_path):
    """Fail fast, not after doing all the work.

    A print-to-PDF turns glyphs into vector outlines: the page looks perfect
    and yields no characters. The check used to run after table detection, so
    a 243-page document of pure vector drawings spent quarter of an hour and
    six gigabytes proving it had nothing to say. Rejecting on the cheap text
    pass turns that into seconds.
    """
    import time

    from reportlab.pdfgen import canvas

    path = tmp_path / "vectors_only.pdf"
    pdf = canvas.Canvas(str(path))
    for _ in range(12):
        # Drawings, deliberately no drawString: this is what a printed page
        # looks like once its text has been outlined.
        for i in range(300):
            pdf.line(i, 0, i, 800)
        pdf.showPage()
    pdf.save()

    started = time.perf_counter()
    with pytest.raises(IngestionError) as raised:
        parse_pdf(path)
    elapsed = time.perf_counter() - started

    assert "no extractable text" in str(raised.value.message).lower()
    # Generous, but far below what the table pass on this many pages costs.
    assert elapsed < 10, f"rejection took {elapsed:.1f}s - the early exit is not working"


def test_blank_pages_keep_their_place_in_the_numbering(tmp_path):
    """Skipping table detection on an empty page must not skip the page."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    path = tmp_path / "with_blank.pdf"
    styles = getSampleStyleSheet()
    SimpleDocTemplate(str(path), pagesize=letter).build([
        Paragraph("Page one has revenue of $12.0 million.", styles["Normal"]),
        PageBreak(),
        Spacer(1, 1),
        PageBreak(),
        Paragraph("Page three has the maturity date.", styles["Normal"]),
    ])

    pages = parse_pdf(path)
    assert len(pages) == 3
    assert "revenue" in pages[0]
    assert not pages[1].strip()
    assert "maturity" in pages[2]
