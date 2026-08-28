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
