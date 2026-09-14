"""Qdrant vector index (spec Module 19, decision D5).

Stores chunk vectors with the metadata the spec requires filtering on: company,
year, document, page, section, table. Runs against a real Qdrant service rather
than an embedded stub, because filter semantics are the part most likely to
differ between the two and filtering is what makes a multi-company corpus usable
- a question about Infosys must not retrieve HDFC Bank's balance sheet.

Indexing is idempotent: chunk ids are stable and derived from document, page and
table position, so re-indexing a document replaces its points instead of
duplicating them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from typing import TYPE_CHECKING

from qdrant_client import QdrantClient, models

if TYPE_CHECKING:  # pragma: no cover - annotation only
    # Type-checking only. `Chunk` appears here purely as an annotation on
    # upsert(), but importing it at runtime dragged in chunking -> extraction ->
    # pymupdf and camelot, so a service that only counts vectors could not start
    # without a PDF parser. `from __future__ import annotations` is already on,
    # so the annotation is a string at runtime and needs nothing imported.
    from backend.rag.chunking import Chunk

__all__ = ["SearchHit", "QdrantIndex"]

# Fields worth an index: the ones filtered on, per spec Module 19.
_INDEXED_PAYLOAD_FIELDS = {
    "document_id": models.PayloadSchemaType.KEYWORD,
    "company": models.PayloadSchemaType.KEYWORD,
    "fiscal_year": models.PayloadSchemaType.KEYWORD,
    "section": models.PayloadSchemaType.KEYWORD,
    "kind": models.PayloadSchemaType.KEYWORD,
    "page": models.PayloadSchemaType.INTEGER,
    "table_index": models.PayloadSchemaType.INTEGER,
}


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    text: str
    payload: dict

    @property
    def citation(self) -> str:
        return self.payload.get("citation", "")


def _point_id(chunk_id: str) -> str:
    """Qdrant needs a UUID or unsigned int; chunk ids are readable strings.

    A deterministic UUID5 keeps re-indexing idempotent - the same chunk always
    lands on the same point - while the readable id stays in the payload.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class QdrantIndex:
    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        collection: str = "finverify_chunks",
        dimension: int = 768,
        client: QdrantClient | None = None,
    ) -> None:
        self.collection = collection
        self.dimension = dimension
        self._client = client or QdrantClient(url=url, timeout=60)

    def ensure_collection(self, *, recreate: bool = False) -> bool:
        """Create the collection and its payload indexes. Returns True if created."""
        exists = self._client.collection_exists(self.collection)
        if exists and not recreate:
            return False
        if exists:
            self._client.delete_collection(self.collection)

        self._client.create_collection(
            collection_name=self.collection,
            # COSINE pairs with the normalised vectors the embedder produces.
            vectors_config=models.VectorParams(
                size=self.dimension, distance=models.Distance.COSINE
            ),
        )
        for field, schema in _INDEXED_PAYLOAD_FIELDS.items():
            self._client.create_payload_index(
                collection_name=self.collection, field_name=field, field_schema=schema
            )
        return True

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]], *, batch: int = 128) -> int:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunk/vector count mismatch: {len(chunks)} chunks, {len(vectors)} vectors"
            )
        points = [
            models.PointStruct(id=_point_id(c.chunk_id), vector=v, payload=c.payload())
            for c, v in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), batch):
            self._client.upsert(
                collection_name=self.collection, points=points[start : start + batch], wait=True
            )
        return len(points)

    @staticmethod
    def build_filter(
        *,
        company: str | None = None,
        fiscal_year: str | None = None,
        document_id: str | None = None,
        section: str | None = None,
        kind: str | None = None,
        page: int | None = None,
        pages: tuple[int, int] | None = None,
    ) -> models.Filter | None:
        """Spec Module 19 filters. Returns None when nothing is constrained."""
        must: list[models.Condition] = []
        for field, value in (
            ("company", company),
            ("fiscal_year", fiscal_year),
            ("document_id", document_id),
            ("section", section),
            ("kind", kind),
        ):
            if value is None:
                continue
            if isinstance(value, (list, tuple, set, frozenset)):
                # Several acceptable values, not several required ones. A report
                # states its own year AND the prior year as comparatives, so a
                # question about FY2023 must be allowed to match the FY2023-24
                # filing - see `acceptable_document_years` in retrieval.planned.
                must.append(
                    models.FieldCondition(
                        key=field, match=models.MatchAny(any=sorted(value))
                    )
                )
            else:
                must.append(
                    models.FieldCondition(key=field, match=models.MatchValue(value=value))
                )
        if page is not None:
            must.append(models.FieldCondition(key="page", match=models.MatchValue(value=page)))
        if pages is not None:
            must.append(
                models.FieldCondition(
                    key="page", range=models.Range(gte=pages[0], lte=pages[1])
                )
            )
        return models.Filter(must=must) if must else None

    def search(
        self,
        vector: list[float],
        *,
        limit: int = 10,
        query_filter: models.Filter | None = None,
    ) -> list[SearchHit]:
        response = self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            SearchHit(
                chunk_id=(p.payload or {}).get("chunk_id", ""),
                score=float(p.score),
                text=(p.payload or {}).get("text", ""),
                payload=p.payload or {},
            )
            for p in response.points
        ]

    def scroll_all(self, *, query_filter: models.Filter | None = None) -> list[dict]:
        """Every payload matching the filter.

        Used to build the BM25 index over exactly the same candidate set the
        vector search draws from, so the two legs of hybrid retrieval cannot
        silently diverge.
        """
        payloads: list[dict] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            payloads.extend(p.payload or {} for p in points)
            if offset is None:
                break
        return payloads

    def count(self, *, query_filter: models.Filter | None = None) -> int:
        return int(
            self._client.count(
                self.collection, count_filter=query_filter, exact=True
            ).count
        )

    def indexed_values(self, field: str, candidates: list[str]) -> tuple[str, ...]:
        """Which of `candidates` actually have points in this collection.

        Asks per candidate with a filtered exact count rather than scrolling
        every payload: both fields used here are indexed keyword fields, so each
        call is a cheap index lookup and 22,930 payloads never cross the wire
        just to answer "is HDFC Bank in here?".

        Answers only what was asked. A manifest that listed every *registered*
        document as indexed would be asserting something it never checked - and
        that is the same species of unverified claim as the defect this whole
        mechanism exists to catch.
        """
        return tuple(
            value
            for value in candidates
            if self.count(query_filter=self.build_filter(**{field: value})) > 0
        )

    def indexed_companies(self, candidates: list[str]) -> tuple[str, ...]:
        return self.indexed_values("company", candidates)

    def indexed_documents(self, candidates: list[str]) -> tuple[str, ...]:
        return self.indexed_values("document_id", candidates)

    def delete_document(self, document_id: str) -> None:
        self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=document_id)
                        )
                    ]
                )
            ),
        )
