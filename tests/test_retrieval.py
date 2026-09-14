"""Tests for indexing and hybrid retrieval (spec Modules 6, 19).

Runs against a real Qdrant service. Skipped - not silently passed - when it is
unavailable, so a green run without Qdrant never reads as verified retrieval.

These tests cover retrieval *mechanics*: filters isolate, fusion combines both
legs, ids are stable. They do **not** establish retrieval *quality*; that needs
Recall@K and MRR against gold evidence spans, which is still to be built.
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.financial_value import Scale
from backend.documents.extraction import Provenance
from backend.rag.chunking import Chunk, ChunkKind
from backend.rag.indexing import QdrantIndex, _point_id
from backend.retrieval.hybrid import RRF_K, HybridRetriever, tokenize

QDRANT_URL = "http://localhost:6333"


def _qdrant_up() -> bool:
    try:
        return httpx.get(f"{QDRANT_URL}/collections", timeout=5).status_code == 200
    except Exception:
        return False


requires_qdrant = pytest.mark.skipif(
    not _qdrant_up(), reason="Qdrant unavailable - retrieval cannot be verified"
)

DIM = 8


def chunk(cid: str, text: str, *, company="Infosys Limited", year="2023-24", page=1,
          kind=ChunkKind.TABLE, document_id="doc1", section="Balance Sheet") -> Chunk:
    return Chunk(
        chunk_id=cid,
        kind=kind,
        text=text,
        provenance=Provenance(document_id, page, section, 0),
        document_id=document_id,
        page=page,
        section=section,
        table_index=0,
        context_scale=Scale.CRORE,
        context_currency="INR",
        company=company,
        fiscal_year=year,
    )


def vec(seed: float) -> list[float]:
    """Deterministic unit-ish vector; exact direction does not matter here."""
    raw = [(seed + i) % 3 + 0.1 for i in range(DIM)]
    norm = sum(x * x for x in raw) ** 0.5
    return [x / norm for x in raw]


class TestTokenizer:
    def test_numbers_are_preserved(self):
        """'2,071' is a highly selective term when checking a specific figure."""
        assert "2,071" in tokenize("Equity share capital 2,071")

    def test_lowercased(self):
        assert tokenize("Trade Payables") == ["trade", "payables"]

    def test_punctuation_does_not_create_empty_tokens(self):
        assert all(t for t in tokenize("revenue: 1,234.56 (net) -- total"))


class TestPointIds:
    def test_stable_across_calls(self):
        """Re-indexing must replace a chunk, not duplicate it."""
        assert _point_id("doc1:p12:t0:0") == _point_id("doc1:p12:t0:0")

    def test_distinct_for_distinct_chunks(self):
        assert _point_id("doc1:p12:t0:0") != _point_id("doc1:p12:t0:1")


class TestFilterConstruction:
    def test_no_constraints_yields_no_filter(self):
        assert QdrantIndex.build_filter() is None

    def test_company_filter_built(self):
        assert QdrantIndex.build_filter(company="Infosys Limited") is not None

    def test_page_range_filter_built(self):
        assert QdrantIndex.build_filter(pages=(10, 20)) is not None


@requires_qdrant
class TestQdrantIndex:
    @pytest.fixture
    def index(self):
        idx = QdrantIndex(collection="finverify_test", dimension=DIM)
        idx.ensure_collection(recreate=True)
        yield idx
        idx._client.delete_collection("finverify_test")

    def test_upsert_and_count(self, index):
        chunks = [chunk(f"c{i}", f"row {i}") for i in range(5)]
        assert index.upsert(chunks, [vec(i) for i in range(5)]) == 5
        assert index.count() == 5

    def test_reindexing_replaces_rather_than_duplicates(self, index):
        chunks = [chunk("c1", "original")]
        index.upsert(chunks, [vec(1)])
        index.upsert([chunk("c1", "revised")], [vec(1)])
        assert index.count() == 1

    def test_mismatched_vector_count_is_rejected(self, index):
        with pytest.raises(ValueError, match="mismatch"):
            index.upsert([chunk("c1", "x")], [vec(1), vec(2)])

    def test_company_filter_isolates(self, index):
        """A question about one company must not retrieve another's figures."""
        index.upsert(
            [chunk("a", "infosys equity", company="Infosys Limited"),
             chunk("b", "hdfc equity", company="HDFC Bank Limited")],
            [vec(1), vec(2)],
        )
        hits = index.search(
            vec(1), limit=10, query_filter=index.build_filter(company="Infosys Limited")
        )
        assert len(hits) == 1
        assert hits[0].payload["company"] == "Infosys Limited"

    def test_fiscal_year_filter_isolates(self, index):
        index.upsert(
            [chunk("a", "fy24", year="2023-24"), chunk("b", "fy23", year="2022-23")],
            [vec(1), vec(2)],
        )
        hits = index.search(
            vec(1), limit=10, query_filter=index.build_filter(fiscal_year="2022-23")
        )
        assert [h.payload["fiscal_year"] for h in hits] == ["2022-23"]

    def test_kind_filter_isolates_tables(self, index):
        index.upsert(
            [chunk("a", "a table", kind=ChunkKind.TABLE),
             chunk("b", "some prose", kind=ChunkKind.TEXT)],
            [vec(1), vec(2)],
        )
        hits = index.search(vec(1), limit=10, query_filter=index.build_filter(kind="table"))
        assert [h.payload["kind"] for h in hits] == ["table"]

    def test_section_filter_isolates(self, index):
        """Spec section 27 names section as a required filter dimension.

        The payload field was indexed and `build_filter` accepted it, so this
        half always worked - it was unreachable from the pipeline, which is the
        defect the retriever-level test below covers.
        """
        index.upsert(
            [
                chunk("bs", "total equity 48382", section="Balance Sheet"),
                chunk("pl", "total equity 48382", section="Profit and Loss"),
            ],
            [vec(1), vec(2)],
        )
        hits = index.search(
            vec(1), limit=5, query_filter=QdrantIndex.build_filter(section="Balance Sheet")
        )
        assert [h.chunk_id for h in hits] == ["bs"]

    def test_page_range_filter(self, index):
        index.upsert(
            [chunk(f"c{p}", f"page {p}", page=p) for p in (5, 12, 30)],
            [vec(p) for p in (5, 12, 30)],
        )
        hits = index.search(vec(1), limit=10, query_filter=index.build_filter(pages=(10, 20)))
        assert [h.payload["page"] for h in hits] == [12]

    def test_citation_survives_the_round_trip(self, index):
        index.upsert([chunk("a", "x", page=12)], [vec(1)])
        assert "p.12" in index.search(vec(1), limit=1)[0].citation

    def test_scale_metadata_survives_the_round_trip(self, index):
        """Units must not be lost between chunking and retrieval."""
        index.upsert([chunk("a", "x")], [vec(1)])
        payload = index.search(vec(1), limit=1)[0].payload
        assert payload["scale"] == "crore"
        assert payload["currency"] == "INR"

    def test_delete_document_removes_only_that_document(self, index):
        index.upsert(
            [chunk("a", "x", document_id="doc1"), chunk("b", "y", document_id="doc2")],
            [vec(1), vec(2)],
        )
        index.delete_document("doc1")
        assert index.count() == 1

    def test_scroll_all_returns_every_payload(self, index):
        index.upsert([chunk(f"c{i}", f"t{i}") for i in range(7)], [vec(i) for i in range(7)])
        assert len(index.scroll_all()) == 7


@requires_qdrant
class TestHybridFusion:
    """BM25 is not decoration: semantic search on this corpus put a table
    containing the literal query phrase outside the top hits, with every score
    bunched between 0.53 and 0.63."""

    @pytest.fixture
    def retriever(self):
        idx = QdrantIndex(collection="finverify_test_hybrid", dimension=DIM)
        idx.ensure_collection(recreate=True)

        chunks = [
            chunk("bs", "Trade payables 2.14 3,956 3,865 balance sheet liabilities"),
            chunk("pl", "Revenue from operations 1,53,670 profit and loss statement"),
            chunk("notes", "Employee benefit expenses and other operating costs detail"),
        ]
        idx.upsert(chunks, [vec(1), vec(2), vec(3)])

        class StubEmbedder:
            """Fixed query vector: isolates fusion behaviour from embedding
            quality, which is a separate question measured separately."""

            def embed_queries(self, texts):
                from backend.rag.embedding import EmbeddingResult

                return EmbeddingResult([vec(3)], "stub", DIM)

        yield HybridRetriever(index=idx, embedder=StubEmbedder())
        idx._client.delete_collection("finverify_test_hybrid")

    def test_keyword_leg_finds_a_literal_rare_term(self, retriever):
        results = retriever.retrieve("trade payables", top_k=3)
        top = results[0]
        assert top.chunk_id == "bs"
        assert top.keyword_rank == 1

    def test_found_by_reports_which_leg_contributed(self, retriever):
        results = retriever.retrieve("trade payables", top_k=3)
        assert {r.found_by for r in results} <= {"both", "semantic", "keyword"}
        assert any(r.keyword_rank is not None for r in results)

    def test_rrf_score_matches_the_formula(self, retriever):
        """Fusion combines ranks, not scores - cosine (~0.5-0.6, bunched) and
        BM25 (unbounded) are not on comparable scales."""
        result = retriever.retrieve("trade payables", top_k=3)[0]
        expected = 0.0
        if result.semantic_rank:
            expected += 1.0 / (RRF_K + result.semantic_rank)
        if result.keyword_rank:
            expected += 1.0 / (RRF_K + result.keyword_rank)
        assert result.fused_score == pytest.approx(expected)

    def test_a_term_in_no_document_still_returns_semantic_hits(self, retriever):
        assert retriever.retrieve("xyzzy nonexistent term", top_k=3)

    def test_filter_applies_to_both_legs(self, retriever):
        assert retriever.retrieve("trade payables", top_k=5, company="HDFC Bank Limited") == []

    def test_bm25_cache_is_keyed_by_filter(self, retriever):
        retriever.retrieve("trade payables", top_k=3, company="Infosys Limited")
        retriever.retrieve("trade payables", top_k=3, company="HDFC Bank Limited")
        assert len(retriever._bm25_cache) == 2

    def test_section_filter_is_reachable_from_the_retriever(self, retriever):
        """The spec's section filter, through the entry point the pipeline uses.

        `QdrantIndex.build_filter` took `section` all along and `retrieve()` did
        not pass it, so the pipeline could not apply a filter the spec requires
        and the index already supported. An index-layer capability no caller can
        reach is not a capability.
        """
        assert retriever.retrieve("trade payables", top_k=3, section="Balance Sheet")
        assert retriever.retrieve("trade payables", top_k=3, section="Cash Flow") == []

    def test_bm25_cache_is_keyed_by_section_too(self, retriever):
        """Every filter dimension must appear in the cache key.

        The keyword leg caches its corpus per filter. A dimension missing from
        the key means two different filters share one cached candidate set and
        the second query silently receives the first's documents - a wrong answer
        with no error. Adding `section` to the signature without adding it to the
        key would have done exactly that.
        """
        retriever.retrieve("trade payables", top_k=3, section="Balance Sheet")
        retriever.retrieve("trade payables", top_k=3, section="Cash Flow")
        assert len(retriever._bm25_cache) == 2
