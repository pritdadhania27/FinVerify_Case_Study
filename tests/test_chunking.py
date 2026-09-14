"""Tests for chunking (spec Module 6)."""

from pathlib import Path

import pytest

from backend.core.financial_value import Scale
from backend.documents.extraction import ExtractedTable, Provenance, extract_document
from backend.rag.chunking import (
    ChunkKind,
    chunk_document,
    deserialise_chunk,
    is_table_spillover,
    serialise_chunk,
    serialise_table,
)

REAL_PDF = Path("documents/raw/infosys_ar_2023-24_consolidated.pdf")
requires_real_pdf = pytest.mark.skipif(not REAL_PDF.exists(), reason="report not downloaded")


def make_table(rows, *, page=12, scale=Scale.CRORE, currency="INR", source_page=12, section="BS"):
    return ExtractedTable(
        page=page,
        table_index=0,
        rows=tuple(tuple(r) for r in rows),
        header=tuple(rows[0]),
        caption="caption",
        context_scale=scale,
        context_currency=currency,
        section=section,
        provenance=Provenance("doc1", page, section, 0),
        parsing_accuracy=99.0,
        scale_source_page=source_page,
    )


def _one_table_document(table):
    """Minimal ExtractedDocument wrapping a single table, for chunker tests."""
    from backend.documents.extraction import ExtractedDocument, ExtractionQuality

    return ExtractedDocument(
        document_id="doc1",
        pages=(),
        tables=(table,),
        sections=(),
        quality=ExtractionQuality(1, 1, 1, 1, 0, 0),
    )


class TestTableSerialisation:
    def test_column_structure_survives(self):
        """Whitespace joining loses the boundary between a note ref and a year."""
        out = serialise_table(
            [("Particulars", "Note", "2024"), ("Equity", "2.12", "2,071")],
            ("Particulars", "Note", "2024"),
        )
        assert "| Equity | 2.12 | 2,071 |" in out

    def test_ragged_rows_are_padded(self):
        out = serialise_table([("a", "b", "c"), ("x",)], ("a", "b", "c"))
        assert "| x |  |  |" in out

    def test_newlines_inside_cells_do_not_break_rows(self):
        out = serialise_table([("multi\nline", "2")], None)
        assert out.count("\n") == 0


class TestUnitBanner:
    """Decision D13: a continuation page's units live on a page that will not be
    retrieved with it, so they must be written into the chunk itself."""

    def test_units_are_rendered_into_the_chunk_text(self):
        from backend.rag.chunking import _chunk_table

        chunk = _chunk_table(make_table([("a", "b"), ("1", "2")]), "doc1", None, None, 1800)[0]
        assert "INR crore" in chunk.text

    def test_inherited_units_say_which_page_declared_them(self):
        from backend.rag.chunking import _chunk_table

        table = make_table([("a", "b"), ("1", "2")], page=12, source_page=11)
        chunk = _chunk_table(table, "doc1", None, None, 1800)[0]
        assert "declared on page 11" in chunk.text
        assert "not on this page" in chunk.text

    def test_undeclared_units_are_stated_as_undeclared(self):
        """Silence about units is worse than an explicit 'not declared'."""
        from backend.rag.chunking import _chunk_table

        table = make_table([("a", "b"), ("1", "2")], scale=None, currency=None, source_page=None)
        chunk = _chunk_table(table, "doc1", None, None, 1800)[0]
        assert "not declared" in chunk.text


class TestTableSplitting:
    def test_small_table_is_one_chunk(self):
        from backend.rag.chunking import _chunk_table

        chunks = _chunk_table(make_table([("a", "b"), ("1", "2")]), "d", None, None, 1800)
        assert len(chunks) == 1
        assert chunks[0].total_parts == 1

    def test_large_table_splits_but_repeats_header_and_units_in_every_part(self):
        """A part without its header is a grid of digits with no meaning."""
        from backend.rag.chunking import _chunk_table

        rows = [("Particulars", "Note", "2024", "2023")]
        rows += [
            (f"Line item number {i} with a long label", "2.1", "1,234", "5,678")
            for i in range(40)
        ]
        chunks = _chunk_table(make_table(rows), "d", None, None, 600)

        assert len(chunks) > 1
        for chunk in chunks:
            assert "Particulars" in chunk.text, "header missing from a part"
            assert "INR crore" in chunk.text, "units missing from a part"
            assert chunk.total_parts == len(chunks)

    def test_parts_are_numbered(self):
        from backend.rag.chunking import _chunk_table

        rows = [("h1", "h2")] + [(f"row {i} label text here", "1,234") for i in range(40)]
        chunks = _chunk_table(make_table(rows), "d", None, None, 400)
        assert [c.part for c in chunks] == list(range(len(chunks)))

    def test_chunk_ids_are_unique(self):
        from backend.rag.chunking import _chunk_table

        rows = [("h1", "h2")] + [(f"row {i} label", "1,234") for i in range(40)]
        chunks = _chunk_table(make_table(rows), "d", None, None, 400)
        assert len({c.chunk_id for c in chunks}) == len(chunks)


class TestPayload:
    def test_payload_supports_every_filter_the_spec_requires(self):
        """Spec Module 19: company, year, document, page, section, table."""
        from backend.rag.chunking import _chunk_table

        chunk = _chunk_table(
            make_table([("a", "b"), ("1", "2")]), "doc1", "Infosys Limited", "2023-24", 1800
        )[0]
        payload = chunk.payload()
        for key in ("company", "fiscal_year", "document_id", "page", "section", "table_index"):
            assert key in payload
        assert payload["company"] == "Infosys Limited"
        assert payload["fiscal_year"] == "2023-24"

    def test_payload_carries_a_citation(self):
        from backend.rag.chunking import _chunk_table

        payload = _chunk_table(
            make_table([("a", "b"), ("1", "2")]), "doc1", None, None, 1800
        )[0].payload()
        assert "p.12" in payload["citation"]

    def test_payload_records_scale_provenance(self):
        from backend.rag.chunking import _chunk_table

        payload = _chunk_table(
            make_table([("a", "b"), ("1", "2")], page=12, source_page=11), "d", None, None, 1800
        )[0].payload()
        assert payload["scale"] == "crore"
        assert payload["scale_inherited"] is True
        assert payload["scale_source_page"] == 11


@pytest.fixture(scope="module")
def chunks():
    """Extraction is slow; chunk once for the whole module."""
    doc = extract_document(str(REAL_PDF), "infosys2324", max_pages=25)
    return chunk_document(doc, company="Infosys Limited", fiscal_year="2023-24")


@requires_real_pdf
class TestAgainstRealDocument:

    def test_produces_both_kinds(self, chunks):
        kinds = {c.kind for c in chunks}
        assert ChunkKind.TABLE in kinds and ChunkKind.TEXT in kinds

    def test_balance_sheet_chunk_is_self_describing(self, chunks):
        """Retrieved alone, it must still say what its numbers mean."""
        bs = [c for c in chunks if c.kind is ChunkKind.TABLE and c.page == 12][0]
        assert "INR crore" in bs.text
        assert "Equity share capital" in bs.text
        assert "2,071" in bs.text
        assert "declared on page 11" in bs.text

    def test_every_chunk_can_cite_its_source(self, chunks):
        assert all(c.provenance.cite() for c in chunks)

    def test_every_chunk_carries_company_and_year(self, chunks):
        assert all(c.company == "Infosys Limited" for c in chunks)
        assert all(c.fiscal_year == "2023-24" for c in chunks)

    def test_chunk_ids_unique_across_the_document(self, chunks):
        assert len({c.chunk_id for c in chunks}) == len(chunks)

    def test_no_empty_chunks(self, chunks):
        assert all(c.text.strip() for c in chunks)


class TestTableSpillover:
    """PyMuPDF's page text repeats table cells as loose runs. Indexed as text
    they beat the structured table chunk on numeric queries and return a figure
    with no label, no units and no year."""

    NUMERALS = frozenset({"2.17", "8,390", "9,287", "1,350", "73"})

    def test_a_flattened_table_run_is_spillover(self):
        paragraph = (
            "Current tax\n2.17\n 8,390\n 9,287\n"
            "Deferred tax\n2.17\n 1,350\n (73)\nProfit for the year"
        )
        assert is_table_spillover(paragraph, self.NUMERALS)

    def test_narrative_quoting_figures_is_kept(self):
        """Real prose has sentences. Dropping it would lose the only statement of
        facts that appear nowhere in a table."""
        paragraph = (
            "The Group contributed 8,390 crore and 9,287 crore to the trust "
            "during the year ended March 31, 2024."
        )
        assert not is_table_spillover(paragraph, self.NUMERALS)

    def test_too_few_figures_is_not_spillover(self):
        assert not is_table_spillover("Deferred tax 2.17", self.NUMERALS)

    def test_figures_not_in_any_table_are_kept(self):
        """A run of numbers the tables do not contain is not a duplicate of
        anything, so removing it would delete evidence."""
        paragraph = "Segment A\n 111\n 222\nSegment B\n 333\n 444"
        assert not is_table_spillover(paragraph, self.NUMERALS)


class TestChunkRoundTrip:
    def test_cache_round_trip_preserves_units(self, ):
        """The chunk cache exists so swapping the embedding model does not re-run
        extraction. A round trip that dropped the scale would silently reindex
        every figure without its magnitude."""
        table = make_table([("Particulars", "2024"), ("Trade payables", "3,956")])
        chunk = chunk_document(
            _one_table_document(table), company="Infosys Limited", fiscal_year="2023-24"
        )[0]
        restored = deserialise_chunk(serialise_chunk(chunk))
        assert restored == chunk
        assert restored.context_scale is Scale.CRORE

    def test_unknown_scale_label_raises_rather_than_defaulting(self):
        table = make_table([("Particulars", "2024"), ("Trade payables", "3,956")])
        record = serialise_chunk(
            chunk_document(_one_table_document(table))[0]
        )
        record["context_scale"] = "gazillion"
        with pytest.raises(ValueError, match="unknown scale label"):
            deserialise_chunk(record)
