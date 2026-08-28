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
    database_url: str = "postgresql://analyst:analyst@localhost:5433/analyst_copilot"
    qdrant_url: str = "http://localhost:6333"
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
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
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
