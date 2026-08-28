"""Vector search backed by Qdrant.

One shared collection holds every filing's passages, distinguished by a
`filing_id` payload field and filtered at query time. That is Qdrant's own
recommendation at this scale - a collection per filing would multiply
overhead for no benefit with dozens of documents.

Point IDs must be integers or UUIDs, but passage ids are strings like
`f1:p4:0:ab12`. Each is mapped to a deterministic UUID5, so re-ingesting the
same filing upserts in place instead of accumulating duplicates.
"""

from __future__ import annotations

import uuid

import logging
import time

from qdrant_client import QdrantClient
from qdrant_client.models import (
    PayloadSchemaType,
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config import settings
from app.domain.models import Passage

_NAMESPACE = uuid.UUID("6f2c9b6e-9b3b-4b0a-9c1a-2f7e6a9d4b10")

# Upserting in batches keeps a large filing to a handful of round trips
# rather than one request per passage.
logger = logging.getLogger(__name__)

_UPSERT_BATCH = 128

# A vector-store blip must not cost a filing. Indexing is the last step of a
# pipeline that has already spent minutes parsing and embedding, so throwing
# that away over one slow response is the wrong trade - an 11-passage 8-K was
# observed failing this way while the machine was busy indexing a larger
# filing. Retried with backoff; only a persistent fault surfaces as an error.
_UPSERT_ATTEMPTS = 4
_UPSERT_BACKOFF_SECONDS = 2.0


def _point_id(passage_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, passage_id))


class VectorStore:
    def __init__(
        self,
        client: QdrantClient | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
    ):
        self._client = client or QdrantClient(
            url=settings.qdrant_url, timeout=settings.qdrant_timeout_seconds
        )
        self._collection = collection_name or settings.qdrant_collection
        self._vector_size = vector_size or settings.embedding_dim
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._vector_size, distance=Distance.COSINE
                ),
            )

        # Every operation this store performs is scoped to one filing, and an
        # unindexed payload field makes each of those a full scan of the
        # collection. That is what made re-indexing time out: the delete that
        # clears a filing's old vectors had to walk every point in the store.
        # Run unconditionally - collections created before this existed need
        # it too, and Qdrant treats a repeat call as a no-op.
        try:
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name="filing_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:  # already indexed, or a store that predates it
            logger.debug("Payload index on filing_id not created: %s", exc)

    def add_passages(self, passages: list[Passage], embeddings: list[list[float]]) -> None:
        if not passages:
            return
        if len(passages) != len(embeddings):
            raise ValueError("passages and embeddings must be the same length")

        points = [
            PointStruct(
                id=_point_id(passage.id),
                vector=embedding,
                payload={
                    "passage_id": passage.id,
                    "filing_id": passage.filing_id,
                    "page": passage.page,
                },
            )
            for passage, embedding in zip(passages, embeddings)
        ]

        for start in range(0, len(points), _UPSERT_BATCH):
            self._upsert_with_retry(points[start : start + _UPSERT_BATCH])

    def _upsert_with_retry(self, batch: list[PointStruct]) -> None:
        self._with_retry(
            "upsert",
            lambda: self._client.upsert(collection_name=self._collection, points=batch),
        )

    def _with_retry(self, what: str, call) -> None:
        for attempt in range(_UPSERT_ATTEMPTS):
            try:
                call()
                return
            except Exception as exc:
                if attempt == _UPSERT_ATTEMPTS - 1:
                    raise
                delay = _UPSERT_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    "Vector %s failed (%s); retrying in %.0fs [%d/%d]",
                    what, exc, delay, attempt + 2, _UPSERT_ATTEMPTS,
                )
                time.sleep(delay)

    def search(
        self, query_embedding: list[float], top_k: int, filing_id: str
    ) -> list[tuple[str, float]]:
        results = self._client.query_points(
            collection_name=self._collection,
            query=query_embedding,
            limit=top_k,
            query_filter=Filter(
                must=[FieldCondition(key="filing_id", match=MatchValue(value=filing_id))]
            ),
        ).points
        return [(point.payload["passage_id"], point.score) for point in results]

    def delete_filing(self, filing_id: str) -> None:
        """Drop a filing's vectors.

        Not used by the normal delete path - that is a soft delete which
        keeps everything - but needed when re-indexing a filing from
        scratch.
        """
        self._with_retry(
            "delete",
            lambda: self._client.delete(
                collection_name=self._collection,
                points_selector=Filter(
                    must=[FieldCondition(key="filing_id", match=MatchValue(value=filing_id))]
                ),
            ),
        )
