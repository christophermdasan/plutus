"""Local embeddings via fastembed (ONNX, in-process).

No server to start before the app, no multi-minute cold model load, and no
client-timeout-aborts-the-load failure mode. The model downloads once (~67MB)
into a local cache and is memory-mapped thereafter, so only the very first run
on a machine pays for it.

Where it runs and how much of the machine it takes are decided from the
hardware rather than fixed here - see `app.hardware`. The same build is meant
to run well on a laptop, a many-core server and a GPU box, and one set of
constants cannot suit all three.
"""

from __future__ import annotations

import threading

from fastembed import TextEmbedding

from app.config import settings
from app.hardware import detect

# Loading an ONNX model takes a few seconds and is not thread-safe to do
# twice concurrently, so instances are created once and shared. Embedding
# itself is thread-safe.
_model_lock = threading.Lock()
_models: dict[str, TextEmbedding] = {}


def onnx_threads() -> int | None:
    """Cores the ONNX sessions may use, or None for the library default."""
    if settings.onnx_threads is not None:
        return settings.onnx_threads
    return detect().onnx_threads


def onnx_providers() -> list[str] | None:
    """Execution providers, best first, or None to let fastembed choose."""
    if settings.force_cpu:
        return ["CPUExecutionProvider"]
    return detect().providers


def embed_batch_size() -> int:
    return settings.embed_batch_size or detect().embed_batch_size


def _get_model(model_name: str) -> TextEmbedding:
    if model_name not in _models:
        with _model_lock:
            if model_name not in _models:
                _models[model_name] = TextEmbedding(
                    model_name=model_name,
                    threads=onnx_threads(),
                    providers=onnx_providers(),
                )
    return _models[model_name]


class Embedder:
    def __init__(self, model_name: str | None = None, dimension: int | None = None):
        self.model_name = model_name or settings.embedding_model
        self.dimension = dimension or settings.embedding_dim

    @property
    def _model(self) -> TextEmbedding:
        return _get_model(self.model_name)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts in one pass.

        fastembed batches internally, so this is dramatically faster than
        looping over `embed` - which is why ingestion calls it with every
        passage at once.
        """
        if not texts:
            return []
        return [
            vector.tolist()
            for vector in self._model.embed(texts, batch_size=embed_batch_size())
        ]

    def embed_query(self, query: str) -> list[float]:
        """Embed a question.

        Kept as a separate entry point because retrieval quality for some
        models depends on queries and passages being encoded differently.
        bge-small-en-v1.5 is trained so that plain encoding already works
        symmetrically, so today this is a passthrough - but callers use the
        right name, so swapping in an asymmetric model later is a one-line
        change here instead of a hunt through the retrieval code.
        """
        return self.embed(query)


def warm_up() -> None:
    """Load models into memory ahead of first use.

    Cheap enough (a few seconds, and only on a cold cache) to just do at
    startup rather than making the first user request pay for it.
    """
    Embedder().embed("warm up")
