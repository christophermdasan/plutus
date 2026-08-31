"""End-to-end ingestion: parse -> chunk -> embed -> index.

Two properties matter here. First, it must finish well inside the ten-minute
budget: the LLM is never called on the hot path, only the local embedder,
and passages are embedded in one batched call rather than one request each.
Second, status must be observable throughout, because the UI polls it while
the user waits.

Enrichment (company/type/period, suggested questions) runs *after* the
filing is marked ready. It needs the LLM and is purely additive, so a slow
or failing enrichment never delays or breaks the thing the user is waiting
for - which is being able to ask a question.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings
from app.domain.enums import FilingStatus
from app.domain.models import Passage
from app.exceptions import IngestionError
from app.ingestion.chunker import chunk_pages
from app.ingestion.parser import parse_document
from app.ingestion.xbrl_facts import Fact as XbrlFact
from app.ingestion.xbrl_facts import extract_from_file
from app.qa import prompts

logger = logging.getLogger(__name__)

# Enough of the document for the model to identify it, without paying to
# send a whole 10-K.
_METADATA_PAGES = 2
_SUGGESTION_PAGES = 6


class IngestionPipeline:
    def __init__(self, filings_repo, vector_store, embedder, llm_client=None):
        self._filings = filings_repo
        self._vectors = vector_store
        self._embedder = embedder
        self._llm = llm_client

    # -- local index cache -------------------------------------------------

    @staticmethod
    def _index_dir(filing_id: str) -> Path:
        return settings.index_dir / filing_id

    def _write_index(self, filing_id: str, passages: list[Passage], pages: list[str]) -> None:
        """Persist passage text and page text next to the vectors.

        BM25 needs every passage's full text in memory to build its index,
        and the verifier needs to check a quote against the *page* it was
        attributed to. Round-tripping either through Qdrant payloads would be
        slower and no simpler.
        """
        directory = self._index_dir(filing_id)
        directory.mkdir(parents=True, exist_ok=True)

        (directory / "passages.json").write_text(
            json.dumps(
                [
                    {"id": p.id, "page": p.page, "text": p.text, "ordinal": p.ordinal}
                    for p in passages
                ]
            ),
            encoding="utf-8",
        )
        (directory / "pages.json").write_text(
            json.dumps({str(i + 1): text for i, text in enumerate(pages)}), encoding="utf-8"
        )

    def _write_xbrl(self, filing_id: str, source_path: Path) -> None:
        """Persist which pages carry which tagged US-GAAP concept.

        Extracted at ingest rather than per question: it is a scan of the
        source markup, so doing it once here keeps it off the path a user
        waits on. Absent for the filings that predate the SEC's inline-XBRL
        phase-in, where the fact index locates statements from their line
        items instead - so a missing or unreadable file is not an error.
        """
        pages: dict[str, list[int]] = {}
        try:
            for fact in extract_from_file(source_path):
                slot = pages.setdefault(fact.local_name, [])
                if fact.page not in slot:
                    slot.append(fact.page)
        except Exception:
            logger.warning("XBRL extraction failed for %s", filing_id, exc_info=True)
            return

        if pages:
            (self._index_dir(filing_id) / "xbrl.json").write_text(
                json.dumps(pages), encoding="utf-8"
            )

    def load_xbrl_pages(self, filing_id: str) -> dict[str, list[int]]:
        path = self._index_dir(filing_id) / "xbrl.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_facts(self, filing_id: str, source_path: Path) -> None:
        """Persist the tagged facts themselves, not just which page they sit on.

        `xbrl.json` records concept -> pages, which is all retrieval needs to
        nominate a statement page. The deterministic engine needs the
        *values*, their periods and their scale to compute anything, and
        re-parsing the source markup on every question would put a full
        document scan on the path a user waits on.
        """
        try:
            facts = extract_from_file(source_path)
        except Exception:
            logger.warning("XBRL fact extraction failed for %s", filing_id, exc_info=True)
            return
        if not facts:
            return
        (self._index_dir(filing_id) / "facts.json").write_text(
            json.dumps([
                {
                    "concept": f.concept,
                    "value": f.value,
                    "period_end": f.period_end,
                    "period_start": f.period_start,
                    "page": f.page,
                    "member": f.member,
                    # Without the axis a reloaded segment fact cannot be told
                    # from a geographical one, and "which segment had the
                    # highest net income" answers with a region.
                    "axis": f.axis,
                }
                for f in facts
            ]),
            encoding="utf-8",
        )

    def load_facts(self, filing_id: str) -> list[XbrlFact]:
        """The tagged facts for a filing, or an empty list.

        Empty is a normal outcome, not an error: 26 of the 78 filings in the
        practice corpus predate the SEC's inline-XBRL phase-in and carry no
        tags at all. `fact_store.build` falls back to parsing statement lines
        out of the page text for those.
        """
        path = self._index_dir(filing_id) / "facts.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [
            XbrlFact(
                concept=item["concept"],
                value=item["value"],
                period_end=item["period_end"],
                period_start=item["period_start"],
                page=item["page"],
                member=item.get("member", ""),
                axis=item.get("axis", ""),
            )
            for item in raw
        ]

    def load_index(self, filing_id: str) -> tuple[list[Passage], dict[int, str]]:
        directory = self._index_dir(filing_id)
        passages_raw = json.loads((directory / "passages.json").read_text(encoding="utf-8"))
        pages_raw = json.loads((directory / "pages.json").read_text(encoding="utf-8"))

        passages = [
            Passage(
                id=item["id"],
                filing_id=filing_id,
                page=item["page"],
                text=item["text"],
                ordinal=item.get("ordinal", 0),
            )
            for item in passages_raw
        ]
        return passages, {int(page): text for page, text in pages_raw.items()}

    # -- main path ---------------------------------------------------------

    def ingest(self, filing_id: str, source_path: Path) -> None:
        try:
            self._filings.update_status(filing_id, FilingStatus.PARSING)
            pages = parse_document(source_path)

            self._filings.update_status(filing_id, FilingStatus.CHUNKING)
            passages = chunk_pages(filing_id, pages)
            if not passages:
                raise IngestionError("No readable text was found in this filing.")

            self._filings.update_status(filing_id, FilingStatus.EMBEDDING)
            embeddings = self._embedder.embed_batch([p.text for p in passages])

            self._filings.update_status(filing_id, FilingStatus.INDEXING)
            # Clear first so re-ingesting replaces rather than layers.
            self._vectors.delete_filing(filing_id)
            self._vectors.add_passages(passages, embeddings)
            self._write_index(filing_id, passages, pages)
            self._write_xbrl(filing_id, source_path)
            self._write_facts(filing_id, source_path)

            self._filings.update_status(
                filing_id, FilingStatus.READY, num_pages=len(pages)
            )
            logger.info(
                "Ingested %s: %d pages, %d passages", filing_id, len(pages), len(passages)
            )
        except IngestionError as exc:
            # The detail is the half that tells someone what to do about it -
            # "printed to PDF in a way that turned the text into vector
            # outlines" is actionable where "no extractable text" alone is
            # just a dead end. It is the only place this reason is recorded,
            # so it has to survive into the row.
            reason = f"{exc.message} {exc.detail}".strip() if exc.detail else exc.message
            self._filings.update_status(filing_id, FilingStatus.FAILED, error=reason)
            raise
        except Exception as exc:
            logger.exception("Ingestion failed for %s", filing_id)
            self._filings.update_status(filing_id, FilingStatus.FAILED, error=str(exc))
            raise IngestionError(str(exc)) from exc

        self._enrich(filing_id, pages)

    # -- enrichment (best effort, post-ready) ------------------------------

    def _enrich(self, filing_id: str, pages: list[str]) -> None:
        if self._llm is None or not settings.llm_api_key or not settings.enrich_filings:
            return

        metadata = self._extract_metadata(pages)
        questions = self._suggest_questions(pages)

        if metadata or questions:
            self._filings.update_metadata(
                filing_id,
                company_name=metadata.get("company_name") or None,
                filing_type=metadata.get("filing_type") or None,
                fiscal_period=metadata.get("fiscal_period") or None,
                suggested_questions=questions or None,
            )

    def _extract_metadata(self, pages: list[str]) -> dict:
        try:
            excerpt = "\n\n".join(pages[:_METADATA_PAGES])[:6000]
            result = self._llm.complete_json(prompts.FILING_METADATA_PROMPT, excerpt)
            return {
                key: (result.get(key) or "").strip()
                for key in ("company_name", "filing_type", "fiscal_period")
            }
        except Exception:
            logger.warning("Metadata extraction failed", exc_info=True)
            return {}

    def _suggest_questions(self, pages: list[str]) -> list[str]:
        try:
            excerpt = "\n\n".join(pages[:_SUGGESTION_PAGES])[:12000]
            result = self._llm.complete_json(prompts.SUGGESTED_QUESTIONS_PROMPT, excerpt)
            questions = [str(q).strip() for q in result.get("questions", []) if str(q).strip()]
            return questions[:4]
        except Exception:
            logger.warning("Suggested-question generation failed", exc_info=True)
            return []
