"""Cross-encoder reranking - the precision layer over cheap recall.

BM25 and vector search are *bi-encoders*: query and passage are embedded
independently, so relevance is approximated by vector distance. A
cross-encoder reads the query and passage together and scores them jointly,
which is far more accurate but too slow to run over a whole corpus. The
standard pattern - and what this does - is to let the cheap retrievers
propose a shortlist, then rerank only that shortlist.

Beyond ordering, this gives the system something it previously lacked: a
*calibrated* relevance score. Fused RRF scores are arbitrary (~0.016 either
way), so the old abstention threshold was a magic number. Cross-encoder
logits separate cleanly - relevant passages score around +8, irrelevant ones
around -11 - which makes "is there actually an answer in here?" a real
measurement.
"""

from __future__ import annotations

import threading

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import settings
from app.hardware import detect
from app.ingestion.embedder import onnx_providers, onnx_threads

_model_lock = threading.Lock()
_models: dict[str, TextCrossEncoder] = {}


def _get_model(model_name: str) -> TextCrossEncoder:
    if model_name not in _models:
        with _model_lock:
            if model_name not in _models:
                # Sized from the machine like the embedder. Reranking runs
                # on every question, so left unbounded it competes with the
                # rest of the box each time somebody asks one.
                _models[model_name] = TextCrossEncoder(
                    model_name=model_name,
                    threads=onnx_threads(),
                    providers=onnx_providers(),
                )
    return _models[model_name]


class Reranker:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.reranker_model

    @property
    def _model(self) -> TextCrossEncoder:
        return _get_model(self.model_name)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance logits for each document, in the order given."""
        if not documents:
            return []
        batch = settings.rerank_batch_size or detect().rerank_batch_size
        return [
            float(score) for score in self._model.rerank(query, documents, batch_size=batch)
        ]

    def rank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """(original_index, score) pairs ordered most relevant first."""
        scores = self.score(query, documents)
        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        return ranked[:top_k] if top_k is not None else ranked


def warm_up() -> None:
    Reranker().score("warm up", ["warm up"])
