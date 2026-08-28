"""Scores the evaluation questions against the real pipeline.

Scoring policy: correct answer + correct page = +1, an honest "not found" = 0,
correct answer + wrong page = 0, confidently wrong = -1. The asymmetry is
deliberate - for an analyst, an unsupported figure costs more than a gap.

Runs the same code path the app uses - real parsing, retrieval, reranking,
generation and verification - so the number it prints reflects the product,
not a test harness approximation.

    python scripts/eval.py
    python scripts/eval.py --questions ../data/sample/eval_questions_sample.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

from qdrant_client import QdrantClient
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.ingestion.chunker import chunk_pages  # noqa: E402
from app.ingestion.embedder import Embedder  # noqa: E402
from app.ingestion.parser import parse_pdf  # noqa: E402
from app.qa.answer_service import AnswerService  # noqa: E402
from app.exceptions import LLMRateLimitedError  # noqa: E402
from app.qa.llm_client import LLMClient  # noqa: E402
from app.retrieval.qdrant_store import VectorStore  # noqa: E402
from app.retrieval.reranker import Reranker  # noqa: E402
from app.retrieval.retriever import HybridRetriever  # noqa: E402

_NUMBER_RE = re.compile(r"\$?-?\d[\d,]*(?:\.\d+)?%?")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = ROOT / "data" / "sample" / "eval_questions_sample.jsonl"
DEFAULT_FILINGS = ROOT / "data" / "sample" / "filings"

EVAL_COLLECTION = "eval_passages_384"


def _answers_match(predicted: str, expected: str) -> bool:
    expected_numbers = set(_NUMBER_RE.findall(expected))
    if expected_numbers:
        return bool(expected_numbers & set(_NUMBER_RE.findall(predicted)))
    # Qualitative answers: fuzzy token-set similarity, tolerant of the model
    # paraphrasing the same substance.
    return fuzz.token_set_ratio(predicted.lower(), expected.lower()) >= 55


def score(question: dict, answer) -> tuple[int, str]:
    if not answer.found:
        return 0, "abstained"
    if not question["answerable"]:
        return -1, "answered an unanswerable question"

    # A figure stated in both MD&A and the notes is equally true in both
    # places, so a question may name every page that genuinely supports it.
    # Single-page questions are unaffected.
    acceptable = {question["page"], *question.get("also_pages", [])}
    page_ok = answer.citation is not None and answer.citation.page in acceptable
    answer_ok = _answers_match(answer.answer, question["answer"])

    if answer_ok and page_ok:
        return 1, "correct answer, correct page"
    if answer_ok:
        return 0, "correct answer, wrong page"
    return -1, "confidently wrong"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--filings-dir", type=Path, default=DEFAULT_FILINGS)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Pause between questions. Free tiers meter tokens per minute, and "
        "a real 10-K question spends several thousand, so an unpaced batch trips "
        "the burst limit within a few questions. The app itself never waits this "
        "long - it surfaces the limit to the user instead.",
    )
    parser.add_argument(
        "--local-vectors",
        action="store_true",
        help="Use Qdrant's embedded mode instead of a server, so the eval runs "
        "with no Docker dependency. Search becomes exact rather than HNSW-"
        "approximate, which at this collection size is equivalent or better.",
    )
    args = parser.parse_args()

    if not settings.llm_api_key:
        print("No LLM_API_KEY configured - set one in .env first.")
        raise SystemExit(1)

    questions = [json.loads(line) for line in args.questions.read_text(encoding="utf-8").splitlines() if line.strip()]
    docs = sorted({q["doc"] for q in questions})

    print(f"Model: {settings.llm_model}")
    print(f"Embedding: {settings.embedding_model} | Reranker: {settings.reranker_model}\n")

    embedder = Embedder()
    reranker = Reranker()
    llm = LLMClient()
    answer_service = AnswerService(llm)

    # An isolated collection so an eval run never disturbs real filings.
    client = QdrantClient(location=":memory:") if args.local_vectors else None
    vectors = VectorStore(client=client, collection_name=EVAL_COLLECTION)

    retrievers: dict[str, tuple[HybridRetriever, dict[int, str]]] = {}
    with tempfile.TemporaryDirectory():
        for doc in docs:
            print(f"Indexing {doc} …")
            pages = parse_pdf(args.filings_dir / doc)
            passages = chunk_pages(doc, pages)
            vectors.delete_filing(doc)
            vectors.add_passages(passages, embedder.embed_batch([p.text for p in passages]))
            retrievers[doc] = (
                HybridRetriever(passages, vectors, embedder, reranker, doc),
                {i + 1: text for i, text in enumerate(pages)},
            )

        print()
        results, total = [], 0
        for i, q in enumerate(questions, 1):
            retriever, page_text = retrievers[q["doc"]]
            if args.delay_seconds and i > 1:
                time.sleep(args.delay_seconds)
            try:
                answer = answer_service.answer(q["question"], q["doc"], retriever, page_text)
            except LLMRateLimitedError as exc:
                # A batch run can afford to wait out a burst limit; the
                # interactive app deliberately cannot.
                wait = (exc.retry_after or 30.0) + 5.0
                print(f"     rate limited - waiting {wait:.0f}s and retrying once")
                time.sleep(wait)
                answer = answer_service.answer(q["question"], q["doc"], retriever, page_text)
            points, label = score(q, answer)
            total += points

            page = answer.citation.page if answer.citation else None
            print(
                f"[{i:>2}/{len(questions)}] {q['id']} {points:+d}  {label:<34} "
                f"page={page} {answer.answer[:60]!r}"
            )
            results.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "answerable": q["answerable"],
                    "expected_page": q.get("page"),
                    "expected_answer": q.get("answer"),
                    "found": answer.found,
                    "predicted_page": page,
                    "predicted_answer": answer.answer,
                    "reason": answer.reason,
                    "latency_ms": answer.latency_ms,
                    "score": points,
                    "label": label,
                }
            )

    for doc in docs:
        vectors.delete_filing(doc)

    n = len(questions)
    print(f"\n{'=' * 52}")
    print(f"Total: {total:+d} / {n}   (max {n:+d})")
    for label in [
        "correct answer, correct page",
        "correct answer, wrong page",
        "abstained",
        "confidently wrong",
        "answered an unanswerable question",
    ]:
        count = sum(1 for r in results if r["label"] == label)
        if count:
            print(f"  {label:<36} {count}")

    latencies = [r["latency_ms"] for r in results if r["latency_ms"]]
    if latencies:
        print(f"  {'median latency':<36} {sorted(latencies)[len(latencies) // 2]}ms")

    report_path = args.report or Path(__file__).resolve().parent / "eval_report.json"
    report_path.write_text(
        json.dumps(
            {
                "model": settings.llm_model,
                "embedding_model": settings.embedding_model,
                "reranker_model": settings.reranker_model,
                "relevance_threshold": settings.relevance_threshold,
                "n_questions": n,
                "total_score": total,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
