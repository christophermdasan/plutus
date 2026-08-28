"""Application entry point.

Startup does three things: open the database pool and migrate it, create the
vector store, and warm the local models. Notably absent is any dependency on
an external model server being up first - embeddings and reranking run
in-process and generation is a hosted HTTP call, so `uvicorn app.main:app`
is genuinely all that's needed alongside `docker compose up`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import auth, chat, filings, settings as settings_routes
from app.config import settings
from app.db.migrations import run_migrations
from app.db.repositories.filings import FilingRepository
from app.db.session import create_pool
from app.hardware import detect as detect_hardware
from app.retrieval.qdrant_store import VectorStore

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _warm_models() -> None:
    """Load the ONNX models off the request path.

    Only a few seconds, and only on a cold cache, but doing it here means
    the first question of a session isn't the one that pays for it.
    """
    try:
        from app.ingestion.embedder import warm_up as warm_embedder
        from app.retrieval.reranker import warm_up as warm_reranker

        warm_embedder()
        warm_reranker()
        logger.info("Local models ready")
    except Exception:
        logger.warning("Model warm-up failed; will load on first use", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    settings.ensure_directories()

    app.state.pool = create_pool()
    applied = run_migrations(app.state.pool)
    if applied:
        logger.info("Applied migrations: %s", ", ".join(applied))

    # Ingestion runs in a background task, so anything left mid-flight when
    # the process last stopped is not still running - nothing resumes it. Left
    # alone the row sits on "Embedding" indefinitely, which reads as busy
    # rather than broken.
    interrupted = FilingRepository(app.state.pool).fail_interrupted()
    if interrupted:
        logger.warning(
            "Marked %d filing(s) as failed: ingestion was interrupted by a restart",
            interrupted,
        )

    detect_hardware()  # logs the profile the models will be sized to

    app.state.vector_store = VectorStore()
    app.state.retriever_cache = {}

    threading.Thread(target=_warm_models, daemon=True).start()

    if not settings.llm_api_key:
        logger.warning(
            "No LLM_API_KEY configured - questions will fail until one is set in .env"
        )

    yield

    app.state.pool.close()


app = FastAPI(
    title="The Analyst Copilot",
    description="Question answering over company filings, with verifiable citations.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(filings.router)
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(settings_routes.router)


@app.get("/health", tags=["ops"])
def health():
    """Liveness, plus what the local models were sized to.

    The hardware profile is reported because it is the single thing most
    likely to explain a difference in speed between two machines running the
    same build, and guessing at it from the outside is hard.
    """
    hardware = detect_hardware()
    return {
        "status": "ok",
        "version": app.version,
        "hardware": {
            "accelerator": hardware.accelerator or "cpu",
            "cpu_cores": hardware.cpu_count,
            "onnx_threads": hardware.onnx_threads,
            "ram_gb": round(hardware.ram_gb, 1),
            "embed_batch_size": hardware.embed_batch_size,
        },
    }
