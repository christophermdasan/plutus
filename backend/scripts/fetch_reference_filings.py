"""Fetch real 10-K filings from SEC EDGAR as PDFs with a genuine text layer.

Why this exists: PDFs produced by printing a filing from a browser
("Microsoft: Print To PDF" and friends) convert every glyph into a vector
outline. The result looks perfect and contains *zero* extractable text, so
no text-based pipeline can read it - ours, LlamaParse or anything else.
Every reference PDF originally supplied to this project had that problem.

EDGAR serves the authoritative filing as HTML. This downloads that, then
renders it to PDF through headless Chromium, which preserves real text.

    python scripts/fetch_reference_filings.py
    python scripts/fetch_reference_filings.py --companies Apple NVIDIA

Requires `playwright` for the PDF step:
    pip install playwright && playwright install chromium

Without it, the HTML is still downloaded and can be converted with any
tool that preserves text.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import httpx

# SEC asks for a descriptive User-Agent identifying the requester.
HEADERS = {"User-Agent": "AnalystCopilot research contact@example.com"}

CIKS = {
    "Apple": "0000320193",
    "Microsoft": "0000789019",
    "NVIDIA": "0001045810",
    "Tesla": "0001318605",
    "Ford": "0000037996",
    "McDonalds": "0000063908",
}

OUT_DIR = Path(__file__).resolve().parents[2] / "reference" / "edgar"


def latest_10k_url(cik: str) -> tuple[str, str]:
    """(document URL, report date) for a company's most recent 10-K."""
    r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=HEADERS, timeout=45)
    r.raise_for_status()
    recent = r.json()["filings"]["recent"]

    index = next(i for i, form in enumerate(recent["form"]) if form == "10-K")
    accession = recent["accessionNumber"][index].replace("-", "")
    document = recent["primaryDocument"][index]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
    return url, recent["reportDate"][index]


_HEAD_RE = re.compile(rb"<head[^>]*>", re.IGNORECASE)


def _with_base_href(html: bytes, base_url: str) -> bytes:
    """Anchor relative URLs to EDGAR instead of to the local directory.

    A filing's exhibit links are relative. Rendered from a `file://` origin
    they resolve against wherever the HTML happens to sit, and Chromium bakes
    the resulting absolute paths into the PDF as link annotations - leaking the
    operator's directory structure to anyone who opens the document, and
    producing links that are dead for every other reader. A <base> tag makes
    them resolve to the public EDGAR URLs they were always meant to point at.
    """
    tag = f'<base href="{base_url}">'.encode()
    match = _HEAD_RE.search(html)
    return html[: match.end()] + tag + html[match.end() :] if match else tag + html


def download_html(name: str, cik: str) -> Path | None:
    try:
        url, report_date = latest_10k_url(cik)
        response = httpx.get(url, headers=HEADERS, timeout=120)
        response.raise_for_status()
        path = OUT_DIR / f"{name}.htm"
        base_url = url.rsplit("/", 1)[0] + "/"
        path.write_bytes(_with_base_href(response.content, base_url))
        print(f"  {name:<12} {report_date}  {len(response.content) / 1e6:5.1f} MB  {path.name}")
        return path
    except Exception as exc:
        print(f"  {name:<12} FAILED: {type(exc).__name__}: {exc}")
        return None


_LOCAL_URI_RE = re.compile(r"\(file:///[^)]*?/([^/)]+)\)")


def strip_local_uris(pdf: Path) -> int:
    """Belt-and-braces: rewrite any surviving file:/// link to a bare name.

    The <base> tag above should prevent these entirely. This runs anyway,
    because a PDF that leaks an absolute local path is not something to
    discover after it has been shared. Returns the number rewritten.
    """
    try:
        import pymupdf
    except ImportError:
        return 0

    doc = pymupdf.open(pdf)
    changed = 0
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if "file:///" not in obj:
            continue
        rewritten = _LOCAL_URI_RE.sub(r"()", obj)
        if rewritten != obj:
            doc.update_object(xref, rewritten)
            changed += 1

    if changed:
        tmp = pdf.with_suffix(".pdf.tmp")
        doc.save(str(tmp))
        doc.close()
        tmp.replace(pdf)
    else:
        doc.close()
    return changed


def render_pdfs(html_paths: list[Path]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "\nplaywright not installed - HTML downloaded but not converted.\n"
            "  pip install playwright && playwright install chromium"
        )
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for html in html_paths:
            pdf = html.with_suffix(".pdf")
            try:
                page.goto(html.resolve().as_uri(), wait_until="load", timeout=180_000)
                page.pdf(
                    path=str(pdf),
                    format="Letter",
                    margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                )
                stripped = strip_local_uris(pdf)
                note = f"  (stripped {stripped} local link(s))" if stripped else ""
                print(f"  {html.stem:<12} -> {pdf.name}{note}")
            except Exception as exc:
                print(f"  {html.stem:<12} PDF FAILED: {exc}")
        browser.close()


def verify(pdf_dir: Path) -> None:
    """Confirm the rendered PDFs actually carry a text layer."""
    try:
        import pymupdf
    except ImportError:
        return

    print("\nVerifying text layers:")
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        with pymupdf.open(pdf) as doc:
            pages_with_text = sum(1 for i in range(len(doc)) if doc[i].get_text().strip())
            verdict = "OK" if pages_with_text else "NO TEXT - unusable"
            print(f"  {pdf.stem:<12} {len(doc):>4} pages, {pages_with_text:>4} with text  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", nargs="*", default=list(CIKS), choices=list(CIKS))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading latest 10-K from SEC EDGAR:")
    downloaded = [p for name in args.companies if (p := download_html(name, CIKS[name]))]

    if downloaded:
        print("\nRendering to PDF (preserving text):")
        render_pdfs(downloaded)
        verify(OUT_DIR)


if __name__ == "__main__":
    main()
