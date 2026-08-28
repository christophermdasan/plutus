"""Builds the bundled regression dataset (filings/ + eval-questions.jsonl).

This exists so the pipeline can be developed and regression-tested without
depending on a licensed or proprietary filing corpus, and so anyone cloning
the repository can reproduce the published accuracy numbers immediately.

The two filings are clearly-fictional synthetic 10-K/10-Q style documents
(not scraped real company filings) - built with reportlab so every fact and
its exact page/quote is known with certainty, which is what makes the
questions' ground truth trustworthy. Pointing the evaluation at a real
corpus requires no code changes: eval.py just needs `--questions` and
`--filings-dir` aimed at the real paths.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"
FILINGS_DIR = OUT_DIR / "filings"

styles = getSampleStyleSheet()
BODY = styles["Normal"]
HEADING = ParagraphStyle("Heading", parent=styles["Heading2"], spaceAfter=12)
TITLE = ParagraphStyle("Title", parent=styles["Title"], spaceAfter=24)

TABLE_STYLE = TableStyle(
    [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]
)


def _page(*flowables):
    return list(flowables) + [PageBreak()]


def build_meridian_robotics_10k(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    story = []

    story += _page(
        Paragraph("MERIDIAN ROBOTICS, INC.", TITLE),
        Paragraph("Form 10-K", BODY),
        Paragraph("Annual Report for the Fiscal Year Ended December 31, 2023", BODY),
        Spacer(1, 24),
        Paragraph("Commission File Number: 001-99999 (fictional, for development/testing only)", BODY),
    )

    story += _page(
        Paragraph("Item 7. Management's Discussion and Analysis", HEADING),
        Paragraph(
            "Total revenue for fiscal year 2023 was $184.6 million, compared to "
            "$162.3 million in fiscal year 2022, an increase of 13.7%. The increase "
            "was primarily driven by higher unit shipments of our warehouse "
            "automation platform.",
            BODY,
        ),
        Spacer(1, 12),
        Paragraph(
            "Research and development expense increased to $28.9 million in fiscal "
            "year 2023, compared to $24.1 million in fiscal year 2022, reflecting "
            "continued investment in our next-generation robotics platform.",
            BODY,
        ),
    )

    story += _page(
        Paragraph("Item 7. Management's Discussion and Analysis (continued)", HEADING),
        Paragraph(
            "During the third quarter of fiscal year 2023, we recorded a goodwill "
            "impairment charge of $6.4 million related to our Industrial Sensing "
            "reporting unit, following a decline in projected cash flows for that unit.",
            BODY,
        ),
        Spacer(1, 12),
        Paragraph(
            "We did not declare or pay any cash dividends on our common stock during "
            "fiscal year 2023 or fiscal year 2022, and we do not anticipate paying "
            "cash dividends in the foreseeable future.",
            BODY,
        ),
    )

    story += _page(
        Paragraph("Item 8. Consolidated Statements of Operations", HEADING),
        Table(
            [
                ["(in millions, except per share data)", "FY2023", "FY2022"],
                ["Total revenue", "$184.6", "$162.3"],
                ["Cost of revenue", "$97.2", "$88.0"],
                ["Gross profit", "$87.4", "$74.3"],
                ["Research and development", "$28.9", "$24.1"],
                ["Selling, general and administrative", "$31.5", "$27.8"],
                ["Goodwill impairment", "$6.4", "$0.0"],
                ["Operating income", "$20.6", "$22.4"],
                ["Net income", "$16.1", "$17.9"],
                ["Diluted earnings per share", "$0.62", "$0.70"],
            ],
            style=TABLE_STYLE,
        ),
    )

    story += _page(
        Paragraph("Item 8. Consolidated Balance Sheets", HEADING),
        Table(
            [
                ["(in millions)", "Dec 31, 2023", "Dec 31, 2022"],
                ["Cash and cash equivalents", "$52.3", "$41.8"],
                ["Total current assets", "$121.7", "$104.2"],
                ["Goodwill", "$38.1", "$44.5"],
                ["Total assets", "$243.9", "$228.6"],
                ["Total current liabilities", "$58.4", "$52.1"],
                ["Long-term debt", "$45.0", "$50.0"],
                ["Total liabilities", "$121.3", "$118.9"],
                ["Total stockholders' equity", "$122.6", "$109.7"],
            ],
            style=TABLE_STYLE,
        ),
    )

    story += _page(
        Paragraph("Notes to Consolidated Financial Statements - Note 6: Goodwill and Intangible Assets", HEADING),
        Paragraph(
            "The Company's Industrial Sensing reporting unit experienced a decline "
            "in projected future cash flows during the third quarter of fiscal 2023, "
            "which triggered an interim goodwill impairment test. As a result, the "
            "Company recorded a goodwill impairment charge of $6.4 million, reducing "
            "the carrying value of goodwill for that reporting unit to zero.",
            BODY,
        ),
    )

    story += _page(
        Paragraph("Notes to Consolidated Financial Statements - Note 9: Debt", HEADING),
        Paragraph(
            "As of December 31, 2023, the Company's long-term debt consisted of "
            "$45.0 million outstanding under its term loan facility, which matures "
            "on June 30, 2027 and bears interest at a rate of SOFR plus 2.25%.",
            BODY,
        ),
    )

    story += _page(
        Paragraph("Item 1A. Risk Factors (excerpt)", HEADING),
        Paragraph(
            "We depend on a limited number of suppliers for certain critical "
            "components used in our robotics platforms. A significant disruption "
            "to any of these suppliers, including due to geopolitical events or "
            "natural disasters, could materially and adversely affect our ability "
            "to manufacture and deliver our products on schedule.",
            BODY,
        ),
    )

    story.append(
        Paragraph("Note 11: Segment Information", HEADING)
    )
    story.append(
        Table(
            [
                ["(in millions)", "FY2023 Revenue", "FY2022 Revenue"],
                ["Warehouse Automation", "$139.0", "$118.7"],
                ["Industrial Sensing", "$45.6", "$43.6"],
            ],
            style=TABLE_STYLE,
        )
    )

    doc.build(story)


def build_havenbrook_foods_10q(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    story = []

    story += _page(
        Paragraph("HAVENBROOK FOODS CORPORATION", TITLE),
        Paragraph("Form 10-Q", BODY),
        Paragraph("Quarterly Report for the Quarter Ended September 30, 2023", BODY),
        Spacer(1, 24),
        Paragraph("Commission File Number: 001-88888 (fictional, for development/testing only)", BODY),
    )

    story += _page(
        Paragraph("Item 2. Management's Discussion and Analysis", HEADING),
        Paragraph(
            "Net sales for the three months ended September 30, 2023 were $58.2 "
            "million, compared to $54.9 million for the same period in the prior "
            "year, an increase of 6.0%, driven primarily by pricing actions in our "
            "snack foods segment.",
            BODY,
        ),
        Spacer(1, 12),
        Paragraph(
            "Net income for the quarter was $4.1 million, compared to $3.6 million "
            "in the prior-year quarter.",
            BODY,
        ),
    )

    story += _page(
        Paragraph("Condensed Consolidated Statements of Operations (unaudited)", HEADING),
        Table(
            [
                ["(in millions)", "Q3 2023", "Q3 2022"],
                ["Net sales", "$58.2", "$54.9"],
                ["Cost of goods sold", "$36.9", "$35.4"],
                ["Gross profit", "$21.3", "$19.5"],
                ["Operating expenses", "$15.8", "$14.7"],
                ["Net income", "$4.1", "$3.6"],
            ],
            style=TABLE_STYLE,
        ),
    )

    story += _page(
        Paragraph("Note 4: Inventory", HEADING),
        Paragraph(
            "Inventories are stated at the lower of cost (first-in, first-out) or "
            "net realizable value. As of September 30, 2023, total inventory was "
            "$19.8 million, consisting of $8.1 million of raw materials, $3.2 "
            "million of work-in-process, and $8.5 million of finished goods.",
            BODY,
        ),
    )

    story.append(
        Paragraph("Note 8: Subsequent Events", HEADING)
    )
    story.append(
        Paragraph(
            "On October 12, 2023, the Company completed the acquisition of a "
            "regional snack foods brand for approximately $14.5 million in cash, "
            "funded from existing cash on hand.",
            BODY,
        )
    )

    doc.build(story)


QUESTIONS = [
    dict(
        id="q01", doc="meridian-robotics-10k.pdf", question="What was Meridian Robotics' total revenue for fiscal year 2023?",
        answerable=True, page=2, answer="$184.6 million",
        quote="Total revenue for fiscal year 2023 was $184.6 million",
    ),
    dict(
        id="q02", doc="meridian-robotics-10k.pdf", question="How much did Meridian Robotics spend on research and development in fiscal 2022?",
        answerable=True, page=2, answer="$24.1 million",
        quote="compared to $24.1 million in fiscal year 2022",
    ),
    dict(
        id="q03", doc="meridian-robotics-10k.pdf", question="What goodwill impairment charge did Meridian Robotics record in fiscal 2023, and for which reporting unit?",
        answerable=True, page=3, answer="$6.4 million, Industrial Sensing reporting unit",
        quote="we recorded a goodwill impairment charge of $6.4 million related to our Industrial Sensing",
    ),
    dict(
        id="q04", doc="meridian-robotics-10k.pdf", question="Did Meridian Robotics pay any cash dividends in fiscal year 2023?",
        answerable=True, page=3, answer="No",
        quote="We did not declare or pay any cash dividends on our common stock during",
    ),
    dict(
        id="q05", doc="meridian-robotics-10k.pdf", question="What was Meridian Robotics' diluted earnings per share for fiscal year 2023?",
        answerable=True, page=4, answer="$0.62",
        quote="Diluted earnings per share",
    ),
    dict(
        id="q06", doc="meridian-robotics-10k.pdf", question="What was Meridian Robotics' total stockholders' equity as of December 31, 2023?",
        answerable=True, page=5, answer="$122.6 million",
        quote="Total stockholders' equity",
    ),
    dict(
        id="q07", doc="meridian-robotics-10k.pdf", question="What is the maturity date of Meridian Robotics' term loan facility?",
        answerable=True, page=7, answer="June 30, 2027",
        quote="which matures on June 30, 2027",
    ),
    dict(
        id="q08", doc="meridian-robotics-10k.pdf", question="What interest rate does Meridian Robotics' term loan bear?",
        answerable=True, page=7, answer="SOFR plus 2.25%",
        quote="bears interest at a rate of SOFR plus 2.25%",
    ),
    dict(
        id="q09", doc="meridian-robotics-10k.pdf", question="What risk does Meridian Robotics identify related to its suppliers?",
        answerable=True, page=8, answer="Dependence on a limited number of suppliers for critical components, vulnerable to disruption from geopolitical events or natural disasters",
        quote="We depend on a limited number of suppliers for certain critical",
    ),
    dict(
        id="q10", doc="meridian-robotics-10k.pdf", question="What was Meridian Robotics' fiscal 2023 revenue from its Warehouse Automation segment?",
        answerable=True, page=9, answer="$139.0 million",
        quote="Warehouse Automation",
    ),
    dict(
        id="q11", doc="meridian-robotics-10k.pdf", question="What was Meridian Robotics' quarterly dividend per share in fiscal 2023?",
        answerable=False, page=None, answer=None, quote=None,
    ),
    dict(
        id="q12", doc="meridian-robotics-10k.pdf", question="Who is Meridian Robotics' Chief Executive Officer?",
        answerable=False, page=None, answer=None, quote=None,
    ),
    dict(
        id="q13", doc="havenbrook-foods-10q.pdf", question="What were Havenbrook Foods' net sales for the third quarter of 2023?",
        answerable=True, page=2, answer="$58.2 million",
        quote="Net sales for the three months ended September 30, 2023 were $58.2",
    ),
    dict(
        id="q14", doc="havenbrook-foods-10q.pdf", question="What was Havenbrook Foods' net income for Q3 2023 compared to Q3 2022?",
        answerable=True, page=2, answer="$4.1 million in Q3 2023, compared to $3.6 million in Q3 2022",
        quote="Net income for the quarter was $4.1 million, compared to $3.6 million",
    ),
    dict(
        id="q15", doc="havenbrook-foods-10q.pdf", question="What was Havenbrook Foods' gross profit for Q3 2023?",
        answerable=True, page=3, answer="$21.3 million",
        quote="Gross profit",
    ),
    dict(
        id="q16", doc="havenbrook-foods-10q.pdf", question="What was the total value of Havenbrook Foods' inventory as of September 30, 2023?",
        answerable=True, page=4, answer="$19.8 million",
        quote="total inventory was $19.8 million",
    ),
    dict(
        id="q17", doc="havenbrook-foods-10q.pdf", question="What acquisition did Havenbrook Foods complete after the quarter ended, and for how much?",
        answerable=True, page=5, answer="A regional snack foods brand for approximately $14.5 million in cash",
        quote="the Company completed the acquisition of a regional snack foods brand for approximately $14.5 million",
    ),
    dict(
        id="q18", doc="havenbrook-foods-10q.pdf", question="How many employees does Havenbrook Foods have?",
        answerable=False, page=None, answer=None, quote=None,
    ),
    dict(
        id="q19", doc="havenbrook-foods-10q.pdf", question="What is Havenbrook Foods' effective tax rate for Q3 2023?",
        answerable=False, page=None, answer=None, quote=None,
    ),
]


def write_questions_jsonl(path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for q in QUESTIONS:
            f.write(json.dumps(q) + "\n")


def main() -> None:
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    build_meridian_robotics_10k(FILINGS_DIR / "meridian-robotics-10k.pdf")
    build_havenbrook_foods_10q(FILINGS_DIR / "havenbrook-foods-10q.pdf")
    write_questions_jsonl(OUT_DIR / "eval_questions_sample.jsonl")

    zip_path = OUT_DIR.parent / "analyst-copilot-data.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(OUT_DIR / "eval_questions_sample.jsonl", "eval-questions.jsonl")
        for pdf in FILINGS_DIR.glob("*.pdf"):
            zf.write(pdf, f"filings/{pdf.name}")

    print(f"Wrote {len(QUESTIONS)} evaluation questions and 2 synthetic filings to {OUT_DIR}")
    print(f"Packaged dataset zip at {zip_path}")


if __name__ == "__main__":
    main()
