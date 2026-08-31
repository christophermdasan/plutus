"""Application configuration, loaded from environment / .env.

Everything the app needs to run is here and has a sensible default except the
LLM API key, which must be supplied. There is deliberately no local-model
configuration: embeddings and reranking run in-process (fastembed/ONNX) and
generation goes to a hosted OpenAI-compatible endpoint, so there is no
background service to start before the app.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), extra="ignore", env_file_encoding="utf-8"
    )

    # --- LLM (OpenAI-compatible; Groq by default) -------------------------
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_api_key: str = ""
    # A thinking model reading eight passages of a real 10-K takes far longer
    # than one answering from a short context: gemini-3.6-flash was measured at
    # 45s on a 134-page filing, which left a 60s ceiling close enough to trip,
    # surfacing as a 502 rather than an answer. Sized against the slow case.
    llm_timeout_seconds: float = 180.0
    # Hosted reasoning models spend tokens thinking before emitting JSON, so
    # this is generous relative to the size of the answer itself.
    #
    # 1500 was tuned against one model and did not generalise: gemini-3.6-flash
    # spends several hundred tokens reasoning, and a quote lifted from a real
    # 10-K table can run long, so replies were truncated mid-string - producing
    # `Invalid JSON: EOF while parsing a string` rather than an answer. The cap
    # exists to bound a runaway generation, not to be a budget the answer has
    # to fit inside, so it is set well clear of the largest legitimate reply.
    llm_max_tokens: int = 4000

    # --- Local models (in-process ONNX, no server) ------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Left as None, these are derived from the machine at startup - see
    # app/hardware.py. Set any of them to pin a value instead, which is what
    # you want when benchmarking, or when the detected default is wrong for
    # a particular box.
    onnx_threads: int | None = None        # cores the models may use
    embed_batch_size: int | None = None    # passages per forward pass
    rerank_batch_size: int | None = None
    force_cpu: bool = False                # ignore any GPU that is present
    # Chosen by measurement on the 136 supplied questions, scoring each
    # against the evidence that answers it and against prose that does not:
    #
    #   model                      AUC   r@95%  r@90%  r@85%  r@80%
    #   ms-marco-MiniLM-L-12-v2    89%     23%    60%    68%    72%
    #   ms-marco-MiniLM-L-6-v2     91%     50%    65%    72%    77%
    #   jina-reranker-v1-turbo-en  94%     62%    74%    82%    85%
    #
    # jina wins at every operating point, runs faster (94ms vs 132ms per
    # pair) and is 0.15GB. An earlier benchmark in docs/BENCHMARKS.md
    # rejected it - that was measured on synthetic questions which reuse the
    # filing's own wording, where lexical overlap flatters the ms-marco
    # models. Real analyst questions are paraphrase, and the ranking
    # reverses.
    reranker_model: str = "jinaai/jina-reranker-v1-turbo-en"

    # --- Infrastructure ---------------------------------------------------
    database_url: str = "postgresql://analyst:analyst@localhost:7592/analyst_copilot"
    qdrant_url: str = "http://localhost:7593"
    # Collection name carries the embedding dimension: changing embedding
    # models invalidates every stored vector, and a distinct name makes that
    # a clean cutover instead of a silent dimension mismatch at query time.
    qdrant_collection: str = "passages_384"
    # Generous rather than tight: a slow response here is almost always the
    # store being busy, and failing an ingestion is far more expensive than
    # waiting for it.
    qdrant_timeout_seconds: float = 60.0

    # --- Auth -------------------------------------------------------------
    jwt_secret: str = "dev-only-insecure-secret-override-with-JWT_SECRET-env-var"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7

    # Enrichment (company/type/period + suggested questions) costs two extra
    # LLM calls per upload. Worth it for the UI, but it competes with real
    # questions for a rate-limited token budget, so it can be turned off.
    enrich_filings: bool = True

    # --- Retrieval tuning -------------------------------------------------
    candidate_pool: int = 25          # per retriever, before fusion
    # Expand the retrieval query with the line-item vocabulary a filing
    # actually prints (see app/retrieval/query_expansion.py).
    #
    # Off by default, on measurement rather than principle. It does what it
    # was built to do - the evidence page for a capex question moves from
    # rank 26 to rank 4 - but scored end to end it *cost* a point: the extra
    # terms shift the query embedding enough to change which passages are
    # retrieved for questions that were already working, and a
    # fixed-asset-turnover question that scored +1 without it fell to an
    # abstention with it. Widening recall for the questions that fail is not
    # worth narrowing it for the ones that do not. The targeted mechanism in
    # app/retrieval/fact_index.py addresses the same failure without
    # touching the query the other questions depend on.
    expand_queries: bool = False

    # Slots in the context window reserved for passages from pages the fact
    # index nominated as holding the figure asked about (see
    # app/retrieval/fact_index.py). Three rather than one because a
    # statement page splits into several passages and the line item wanted
    # may not be in the first; small relative to context_passages so the
    # reranker still decides most of the window.
    anchor_slots: int = 3

    # Slots preferred for passages from the filing section a narrative
    # question is about (see app/retrieval/section_index.py). Half the
    # window: enough to make the right section dominate, while leaving room
    # for retrieval to be right when the section was misjudged.
    section_slots: int = 4

    # Answer metric questions by computing from the figures the filing
    # reports, instead of asking the model to read them and do the
    # arithmetic. Off makes the system fall back to retrieval for
    # everything, which is how it behaved before and is worth keeping
    # switchable for comparison.
    use_metric_engine: bool = True
    # Fused candidates sent to the reranker. Was 12, which discarded the
    # answer before it could be judged: measured on the AMD 10-K, BM25 found
    # the right page for 5 of 7 questions and vectors for 4 of 7, yet only 1
    # of 7 survived the cut to 12 - the passage was in the fused list, just
    # ranked below the line. At 50 it is 6 of 7. Reranking is the cheap step
    # here, so admitting more candidates costs far less than the recall it
    # buys.
    rerank_candidates: int = 50
    # Passages handed to the LLM. Raised from 5 after testing on real 10-Ks:
    # a large filing repeats similar tables in several sections (MD&A, the
    # statements, the notes), so the *specific* table a question needs can
    # rank just outside a narrow window. On Apple's FY2025 filing the
    # segment table holding the answer ranked 6th, one place outside a
    # 5-passage cut, and the question was wrongly refused.
    #
    # Tried at 20 against the FinanceBench practice set on the theory that a
    # cut at 8 discards answers retrieval already found - the evidence page
    # ranked 10th to 26th on several refused questions. Measured, it made
    # the score *worse*, and the mechanism is worth recording because it is
    # not obvious: with 8 passages the model answered a fixed-asset-turnover
    # question by showing its inputs ("revenue $6,489M, average PP&E
    # $267.5M, ratio 24.26"); with 20 it answered a bare, wrong "24.67".
    # More context did not add evidence so much as dilute it, and a diluted
    # context produces answers that assert a figure without deriving it -
    # which is precisely the shape the verifier is weakest against. Left at
    # 8; the passages ranked below it are better recovered by making the
    # right one rank higher (see query_expansion) than by widening the net.
    context_passages: int = 8
    # Reranker score below which we decline to answer at all.
    #
    # Scale is a property of the reranker, so this moves whenever that does.
    # For jina-reranker-v1-turbo-en, measured over the 136 questions:
    #
    #   -3.0  94% of true answers admitted, 64% precision
    #   -2.0  86%                           76%
    #   -1.31 74%                           90%
    #    0.0  29%                          100%
    #
    # -2.0 rather than a precision-maximising value because a false positive
    # here is cheap and a false negative is not: an irrelevant passage that
    # reaches the model is almost always refused downstream by the
    # verification gate, scoring 0, whereas a true answer stopped here can
    # never be recovered. The verifier is the guard, not this number.
    relevance_threshold: float = -2.0

    # The band in which the retriever widens its pool and looks again -
    # scores too uncertain to answer or refuse on. Also on the reranker's
    # scale, and deliberately narrower than the answer/decline boundary:
    # sitting the lower bound exactly on relevance_threshold sent nearly
    # every answerable question through a second, three-times-larger rerank
    # and took median latency from 6s to 24s.
    escalate_above: float = -3.0
    escalate_below: float = -1.0
    escalation_factor: int = 2

    # --- Storage ----------------------------------------------------------
    storage_dir: Path = REPO_ROOT / "storage"

    # --- HTTP -------------------------------------------------------------
    # The dev server's pinned origin, and only it. This used to list
    # 5173-5176 because Vite silently steps to the next free port when its
    # default is taken, so the UI could come up on any of them - and if it
    # landed past the end of the range every request was blocked by CORS
    # while the page itself loaded fine, which reads as "no filings" rather
    # than as an error. The dev server now pins its port and refuses to
    # start if it is taken (see frontend/vite.config.ts), so there is one
    # origin to allow and a conflict is reported instead of hidden.
    #
    # Both spellings of the loopback host are listed because a browser
    # treats them as different origins.
    cors_origins: list[str] = [
        "http://localhost:7591",
        "http://127.0.0.1:7591",
    ]

    log_level: str = "INFO"

    @property
    def filings_dir(self) -> Path:
        return self.storage_dir / "filings"

    @property
    def index_dir(self) -> Path:
        return self.storage_dir / "index"

    def ensure_directories(self) -> None:
        self.filings_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
