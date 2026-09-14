"""Tests for document intelligence (spec Module 3)."""

from pathlib import Path

import pytest

from backend.core.financial_value import Scale, parse_financial_value
from backend.documents.extraction import (
    Provenance,
    _looks_like_heading,
    _merge_header_rows,
    _page_scale_contexts,
    detect_scale_context,
    extract_document,
)

REAL_PDF = Path("documents/raw/infosys_ar_2023-24_consolidated.pdf")
requires_real_pdf = pytest.mark.skipif(
    not REAL_PDF.exists(), reason="real annual report not downloaded"
)


class TestScaleDeclarationDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("(In ₹ crore)", Scale.CRORE),
            ("Rs. in lakhs", Scale.LAKH),
            ("in USD millions", Scale.MILLION),
            ("Amounts in ₹ billion", Scale.BILLION),
            ("in thousands", Scale.THOUSAND),
        ],
    )
    def test_declaration_forms(self, text, expected):
        assert detect_scale_context(text, strict=True)[0] is expected

    def test_currency_detected_alongside(self):
        assert detect_scale_context("(In ₹ crore)")[1] == "INR"

    def test_strict_mode_ignores_narrative_mentions(self):
        """A sentence mentioning crore is not a units declaration for a table."""
        prose = "The board approved a buyback and the company earned crore sums."
        assert detect_scale_context(prose, strict=True)[0] is None

    def test_loose_mode_accepts_a_bare_unit_in_a_caption(self):
        """Within a short caption window, a bare unit word is the label."""
        assert detect_scale_context("Particulars crore 2024", strict=False)[0] is Scale.CRORE


class TestScaleCarryForward:
    """Multi-page statements declare units once, on the first page."""

    def test_declaration_carries_to_continuation_pages(self):
        pages = [
            "Consolidated Balance Sheet (In ₹ crore)\nEquity 2,071",
            "Consolidated Balance Sheet (contd.)\nLiabilities 10,559",
            "Consolidated Balance Sheet (contd.)\nTotal 88,461",
        ]
        contexts = _page_scale_contexts(pages)
        assert [c[0] for c in contexts] == [Scale.CRORE] * 3
        assert [c[2] for c in contexts] == [1, 1, 1], "source page must be recorded"

    def test_a_new_declaration_replaces_the_inherited_one(self):
        pages = ["Statement (In ₹ crore)\nx", "contd.\ny", "Other note (In ₹ million)\nz", "contd."]
        contexts = _page_scale_contexts(pages)
        assert [c[0] for c in contexts] == [
            Scale.CRORE, Scale.CRORE, Scale.MILLION, Scale.MILLION
        ]
        assert contexts[3][2] == 3

    def test_pages_before_any_declaration_have_no_scale(self):
        contexts = _page_scale_contexts(["Cover page", "Index", "Report (In ₹ crore)"])
        assert contexts[0][0] is None
        assert contexts[1][0] is None
        assert contexts[2][0] is Scale.CRORE


class TestProvenance:
    def test_citation_format(self):
        p = Provenance("doc1", 12, "Balance Sheet", 0, 3, 2)
        cite = p.cite()
        assert "p.12" in cite and "Balance Sheet" in cite and "r4c3" in cite

    def test_citation_without_table(self):
        assert Provenance("doc1", 5, "Notes").cite() == "p.5, Notes"


@pytest.fixture(scope="module")
def real_doc():
    """Extraction is slow; run it once for the whole module."""
    return extract_document(str(REAL_PDF), "infosys2324", max_pages=25)


@requires_real_pdf
class TestAgainstRealAnnualReport:
    """Infosys FY2023-24 consolidated financial statements."""

    @pytest.fixture(autouse=True)
    def _bind(self, real_doc):
        self.doc = real_doc

    def test_pages_extracted(self):
        doc = self.doc
        assert doc.quality.pages_total == 25
        assert doc.quality.pages_with_text == 25

    def test_tables_found_with_high_parsing_accuracy(self):
        doc = self.doc
        assert doc.quality.tables_found > 20
        assert doc.quality.mean_table_accuracy > 90

    def test_running_headers_are_removed(self):
        """The report title repeats on every page and is not content."""
        doc = self.doc
        assert any("Infosys Integrated Annual Report" in h for h in doc.headers_footers)
        assert doc.quality.repeated_lines_removed > 0

    def test_no_character_decoding_loss(self):
        """U+FFFD inside a numeral would silently corrupt a value."""
        doc = self.doc
        assert doc.quality.replacement_chars == 0

    def test_balance_sheet_table_parses_into_columns(self):
        """The regression that reversed decision D10.

        pdfplumber returned these tables as a single column with every numeric
        value lost; Camelot stream returns label, note, and both fiscal years.
        """
        doc = self.doc
        table = doc.tables_on_page(12)[0]
        rows, cols = table.shape
        assert cols >= 4, "numeric columns must survive extraction"
        labels = [r[0] for r in table.rows]
        assert any("Equity share capital" in x for x in labels)

    def test_continuation_page_inherits_its_scale(self):
        """Page 12 declares no units; page 11 does. Without carry-forward these
        figures would be read as rupees rather than crore - a 10,000,000x error."""
        doc = self.doc
        table = doc.tables_on_page(12)[0]
        assert table.context_scale is Scale.CRORE
        assert table.context_currency == "INR"
        assert table.scale_source_page == 11
        assert table.scale_was_inherited

    def test_real_cells_parse_to_correct_canonical_values(self):
        doc = self.doc
        table = doc.tables_on_page(12)[0]
        row = next(r for r in table.rows if r[0].startswith("Equity share capital"))
        value = parse_financial_value(
            row[2], context_scale=table.context_scale, context_currency=table.context_currency
        )
        assert value.amount.compare(2071) == 0
        assert value.canonical().compare(20_710_000_000) == 0
        assert value.currency == "INR"

    def test_the_documents_own_arithmetic_checks_out(self):
        """Internal consistency: the parts must sum to the stated total.

        This validates the whole extraction chain at once - if scale, sign or
        column alignment were wrong anywhere, this identity would break.
        """
        doc = self.doc
        table = doc.tables_on_page(12)[0]

        def cell(prefix: str):
            row = next(r for r in table.rows if r[0].startswith(prefix))
            return parse_financial_value(row[2], context_scale=table.context_scale)

        share_capital = cell("Equity share capital").canonical()
        other_equity = cell("Other equity").canonical()
        attributable = cell("Total equity attributabl").canonical()
        assert share_capital + other_equity == attributable

    def test_every_table_cell_can_cite_its_source(self):
        doc = self.doc
        table = doc.tables_on_page(12)[0]
        cite = table.cell_provenance(3, 2).cite()
        assert "p.12" in cite and "table 1" in cite and "r4c3" in cite

    def test_sections_are_detected(self):
        doc = self.doc
        assert len(doc.sections) > 3
        assert any("Balance Sheet" in s for s in doc.sections)


class TestFailureHandling:
    def test_missing_file_is_reported_not_raised(self, tmp_path):
        doc = extract_document(str(tmp_path / "nope.pdf"), "x")
        assert doc.quality.tables_found == 0
        assert doc.quality.problems

    def test_text_only_extraction_skips_tables(self, tmp_path):
        import pymupdf

        p = tmp_path / "t.pdf"
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), "Revenue 1,234", fontsize=11)
        d.save(p)
        d.close()
        doc = extract_document(str(p), "x", extract_tables=False)
        assert doc.quality.tables_found == 0
        assert "Revenue" in doc.pages[0].text


class TestHeadingDetection:
    """Section names are written into every chunk title and every citation, so a
    wrong one misdescribes the evidence a reader is asked to check. These cases
    are the ones the original rule got wrong on the real filing."""

    @pytest.mark.parametrize(
        "line",
        [
            "Consolidated Balance Sheet",
            # Function words are lowercase in title case; counting them dragged
            # this real heading to 0.67 and it was being rejected.
            "Consolidated Statement of Profit and Loss",
            "2.9 Cash and cash equivalents",
            "CONSOLIDATED FINANCIAL STATEMENTS",
        ],
    )
    def test_real_headings_are_recognised(self, line):
        assert _looks_like_heading(line)

    @pytest.mark.parametrize(
        "line",
        [
            "Expenses",      # tagged the whole P&L page with this
            "Deletions",
            "Derivative",
            "3 years",        # tagged the trade receivables note with this
            "290",
            "Total",
            "the accompanying notes form an integral part of the statements.",
        ],
    )
    def test_table_row_labels_are_not_headings(self, line):
        assert not _looks_like_heading(line)

    @pytest.mark.parametrize(
        "line",
        [
            "As at March 31,",                       # a table column header
            "In accordance with Section 69 of the Companies Act, 2013,",
            "Infosys Applied AI, Infosys Cortex, Stater digital platform and",
            "Economic Zone Re-investment reserve under Note 2.12 Equity)",
        ],
    )
    def test_wrapped_line_fragments_are_not_headings(self, line):
        """A heading is a complete phrase. These four were the highest-volume
        wrong section names on the real filing - "As at March 31," alone was
        labelling 53 chunks."""
        assert not _looks_like_heading(line)

    def test_sentence_case_headings_need_their_note_number(self):
        """A known and accepted limit: "Segment reporting" is indistinguishable
        from a row label by capitalisation alone. The filing numbers its notes,
        so the real line carries the number and is recognised."""
        assert not _looks_like_heading("Segment reporting")
        assert _looks_like_heading("2.26 Segment reporting")


class TestHeaderRowMerging:
    def test_multi_row_header_keeps_the_fiscal_years(self):
        """The failure this exists to prevent: taking only row 0 kept the unit
        caption and threw away 'Year ended March 31, / 2024 2023', leaving
        '26,248  24,108' in a chunk with no way to tell which column is which
        year."""
        rows = (
            ("", "", "(In crore, except per share data)"),
            ("Particulars", "Note", "Year ended March 31,"),
            ("", "", "2024 2023"),
            ("Revenue from operations", "2.18", "153,670  146,767"),
        )
        header, count = _merge_header_rows(rows)
        assert count == 3
        assert "2024" in header[2] and "Year ended March 31," in header[2]
        assert header[0] == "Particulars"

    def test_a_table_that_opens_on_data_has_no_header(self):
        """Promoting a row of figures to header would repeat it into every part
        of a split table AND leave it in the body."""
        rows = (("Revenue", "2.18", "153,670"), ("Other income", "2.19", "4,711"))
        assert _merge_header_rows(rows) == (None, 0)

    def test_label_only_rows_are_never_lost_as_figures(self):
        """Grouping rows such as 'Assets' carry no figures, so folding them into
        the header cannot lose a number - which is what makes the cap safe."""
        rows = (
            ("", "", "2024", "2023"),
            ("Assets", "", "", ""),
            ("Non-current assets", "", "", ""),
            ("Property, plant and equipment", "2.2", "12,370", "13,346"),
        )
        header, count = _merge_header_rows(rows)
        assert count == 3
        assert header[2] == "2024"

    def test_empty_table(self):
        assert _merge_header_rows(()) == (None, 0)
