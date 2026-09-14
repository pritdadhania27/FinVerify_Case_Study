"""Tests for financial knowledge structuring (spec Module 4).

The trap this module exists to close is the one that produces a wrong answer
nobody questions: a figure that is real, printed on the cited page, and belongs
to the other year. Nothing about it looks wrong. So the tests below are mostly
about *refusing* to attribute a year rather than about extracting values.
"""

from __future__ import annotations

from decimal import Decimal

from backend.documents.facts import (
    YearSource,
    column_years,
    facts_from_chunk,
    facts_from_chunks,
    index_by_metric,
    parse_row,
    split_merged_cell,
    years_in_cell,
)


def table(text: str, **kw) -> dict:
    base = {
        "chunk_id": "d1:p12:t0:0",
        "kind": "table",
        "text": text,
        "document_id": "d1",
        "page": 12,
        "section": "Consolidated Balance Sheet",
        "table_index": 0,
        "company": "Infosys Limited",
        "fiscal_year": "2023-24",
        "context_scale": "crore",
        "context_currency": "INR",
    }
    base.update(kw)
    return base


BALANCE_SHEET = table(
    "| Particulars | Note | 2024 | 2023 |\n"
    "| Trade payables | 2.14 | 3,956 | 3,865 |\n"
    "| Goodwill | 2.6 | 7,303 | 7,248 |\n"
)


class TestTheColumnIsThePoint:
    def test_the_current_year_column_is_labelled(self):
        facts = facts_from_chunk(BALANCE_SHEET)
        current = [f for f in facts if f.metric == "trade payables" and f.year == "2024"]
        assert len(current) == 1
        assert current[0].value.amount == Decimal("3956")
        assert current[0].year_source == YearSource.COLUMN_HEADER

    def test_the_prior_year_is_kept_and_labelled_as_the_prior_year(self):
        """Not discarded. A comparison question needs it, and mislabelling it is
        the failure this module exists to prevent."""
        facts = facts_from_chunk(BALANCE_SHEET)
        prior = [f for f in facts if f.metric == "trade payables" and f.year == "2023"]
        assert len(prior) == 1
        assert prior[0].value.amount == Decimal("3865")

    def test_a_note_column_is_not_mistaken_for_a_figure(self):
        """2.14 is a note reference. Read as an amount it is a real number on the
        right page answering nothing."""
        facts = facts_from_chunk(BALANCE_SHEET)
        assert all(f.value.amount != Decimal("2.14") for f in facts)

    def test_an_unreadable_header_leaves_the_year_unstated(self):
        facts = facts_from_chunk(
            table("| Particulars | | |\n| Trade payables | 3,956 | 3,865 |\n")
        )
        assert all(not f.year_is_stated for f in facts)
        assert all(f.year_source == YearSource.DOCUMENT for f in facts)

    def test_a_header_naming_two_years_in_one_cell_is_not_guessed(self):
        """"2024 2023" as a single column label could mean either."""
        assert column_years(["Particulars", "2024 2023"]) == {}


class TestMergedColumns:
    """Camelot collapses adjacent year columns into one cell. Silently taking
    the first figure is right by convention and wrong the moment a table leads
    with the comparative."""

    def test_two_figures_in_one_cell_yield_two_facts(self):
        facts = facts_from_chunk(
            table("| Particulars | |\n| Revenue from operations | 153,670  146,767 |\n")
        )
        amounts = sorted(f.value.amount for f in facts)
        assert amounts == [Decimal("146767"), Decimal("153670")]

    def test_a_merged_cell_is_flagged(self):
        facts = facts_from_chunk(
            table("| Particulars | |\n| Revenue from operations | 153,670  146,767 |\n")
        )
        assert all("merged_columns" in f.warnings for f in facts)

    def test_a_merged_cell_with_no_recoverable_year_says_so(self):
        facts = facts_from_chunk(
            table("| Particulars | |\n| Revenue from operations | 153,670  146,767 |\n")
        )
        assert all(f.year is None for f in facts)
        assert all("year_not_resolvable_in_merged_cell" in f.warnings for f in facts)

    def test_a_merged_header_recovers_the_pairing(self):
        """When the document merged the header too, the pairing is stated rather
        than assumed."""
        facts = facts_from_chunk(
            table("| Particulars | 2024 2023 |\n| Revenue from operations | 153,670  146,767 |\n")
        )
        by_year = {f.year: f.value.amount for f in facts}
        assert by_year == {"2024": Decimal("153670"), "2023": Decimal("146767")}
        assert all(f.year_source == YearSource.COLUMN_HEADER for f in facts)

    def test_an_ordinary_cell_is_not_treated_as_merged(self):
        assert split_merged_cell("3,956") == ["3,956"]
        assert len(split_merged_cell("153,670  146,767")) == 2

    def test_years_in_cell_reads_them_in_order(self):
        assert years_in_cell("2024 2023") == ["2024", "2023"]
        assert years_in_cell("as at March 31, 2024") == ["2024"]
        assert years_in_cell("Particulars") == []


class TestScaleAndSign:
    def test_the_scale_banner_carries_into_every_cell(self):
        fact = next(
            f for f in facts_from_chunk(BALANCE_SHEET) if f.metric == "trade payables"
        )
        assert fact.value.scale.label == "crore"
        assert fact.canonical == Decimal("39560000000")

    def test_a_parenthesised_negative_is_negative(self):
        facts = facts_from_chunk(
            table("| Particulars | 2024 |\n| Finance cost | (1,234) |\n")
        )
        assert facts[0].value.amount == Decimal("-1234")

    def test_indian_digit_grouping_survives(self):
        facts = facts_from_chunk(
            table("| Particulars | 2024 |\n| Total assets | 1,49,468 |\n")
        )
        assert facts[0].value.amount == Decimal("149468")

    def test_a_ratio_metric_does_not_inherit_a_currency_scale(self):
        """EPS in a crore-scaled table is still rupees per share, not crores."""
        facts = facts_from_chunk(
            table("| Particulars | 2024 |\n| Basic earnings per share | 63.39 |\n")
        )
        assert facts[0].value.scale.label == "unit"


class TestProvenanceIsComplete:
    def test_every_spec_field_is_present(self):
        """Spec §12 names the fields. A fact missing one is a number."""
        payload = facts_from_chunk(BALANCE_SHEET)[0].as_dict()
        for key in (
            "metric", "value", "unit", "currency", "year", "company",
            "document_id", "page", "section", "table", "row", "column",
        ):
            assert key in payload, key

    def test_the_citation_names_the_column(self):
        fact = next(
            f for f in facts_from_chunk(BALANCE_SHEET)
            if f.metric == "goodwill" and f.year == "2024"
        )
        assert "p.12" in fact.citation
        assert "2024" in fact.citation


class TestMetricMatching:
    def test_a_longer_line_item_does_not_match_a_shorter_metric(self):
        facts = facts_from_chunk(
            table(
                "| Particulars | 2024 |\n"
                "| Trade payables ageing schedule - disputed dues | 12 |\n"
            )
        )
        assert facts == []

    def test_the_longest_matching_alias_wins(self):
        facts = facts_from_chunk(
            table("| Particulars | 2024 |\n| Total current assets | 91,000 |\n")
        )
        assert facts[0].metric == "total current assets"


class TestIndexing:
    def test_requiring_a_stated_year_drops_assumed_ones(self):
        stated = facts_from_chunk(BALANCE_SHEET)
        assumed = facts_from_chunk(
            table("| Particulars | |\n| Total equity | 88,461 |\n")
        )
        facts = stated + assumed
        loose = index_by_metric(facts)
        strict = index_by_metric(facts, require_stated_year=True)
        assert "total equity" in loose
        assert "total equity" not in strict

    def test_filtering_by_year_selects_one_column(self):
        index = index_by_metric(facts_from_chunk(BALANCE_SHEET), year="2023")
        assert index["trade payables"][0].value.amount == Decimal("3865")


class TestFailureCases:
    def test_a_text_chunk_yields_no_facts(self):
        """Prose figures have no row or column, so they cannot carry spec §12
        provenance and must not be presented as though they could."""
        assert facts_from_chunk(table("Revenue was 153,670 crore.", kind="text")) == []

    def test_an_empty_table_does_not_crash(self):
        assert facts_from_chunk(table("")) == []

    def test_a_malformed_row_is_skipped(self):
        assert parse_row("not a table row") is None

    def test_a_row_with_no_figures_yields_nothing(self):
        facts = facts_from_chunk(
            table("| Particulars | 2024 |\n| Trade payables | – |\n")
        )
        assert facts == []

    def test_chunks_without_tables_aggregate_to_nothing(self):
        assert facts_from_chunks([table("x", kind="text"), table("")]) == []
