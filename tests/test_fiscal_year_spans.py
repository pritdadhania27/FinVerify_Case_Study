"""An Indian fiscal-year column is labelled by the year it ENDS in (RX-029).

"2022-23" is the year ended 31 March 2023. It contains exactly one four-digit
literal, so reading the first literal gives 2022 - a year early - and the
question generated from that column asks about the wrong year while pointing at
a real figure on a real page with correct provenance. Nothing about the result
looks wrong.

The convention is not a choice made here: every other header in this corpus
already resolves to the ending year ("As at March 31, 2024" -> 2024), so a span
that resolved to its starting year was inconsistent with the same file's other
columns.

Found by the validator, not by a test: six Reliance questions carried this and
all six were rejected, 6 for 6.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.documents.facts import (  # noqa: E402
    _normalise_year,
    column_years,
    fiscal_span_end,
)


class TestTheSpanEndsTheYearItNames:
    def test_a_two_digit_tail(self):
        assert fiscal_span_end("2022-23") == "2023"
        assert fiscal_span_end("2023-24") == "2024"

    def test_a_four_digit_tail(self):
        assert fiscal_span_end("2022-2023") == "2023"

    def test_a_slash_separator(self):
        assert fiscal_span_end("2022/23") == "2023"

    def test_the_span_survives_surrounding_text(self):
        """Reliance's header cell is "(C in crore) 2022-23" - the scale banner
        and the year share a cell."""
        assert fiscal_span_end("(C in crore) 2022-23") == "2023"

    def test_a_century_rollover_does_not_jump_a_hundred_years(self):
        """1999-00 is 2000, not 2100. Completing the tail against the current
        century instead of the start year's is the obvious wrong shortcut."""
        assert fiscal_span_end("1999-00") == "2000"

    def test_text_naming_no_span_yields_nothing(self):
        assert fiscal_span_end("As at March 31, 2024") is None
        assert fiscal_span_end("Total borrowings") is None
        assert fiscal_span_end("") is None


class TestNormalisationPrefersTheSpan:
    def test_a_span_beats_the_bare_literal_inside_it(self):
        """The whole bug in one assertion. "2022-23" holds exactly one
        four-digit literal, so the literal branch would answer 2022."""
        assert _normalise_year("2022-23") == "2023"

    def test_a_plain_date_still_reads_as_before(self):
        assert _normalise_year("As at March 31, 2024") == "2024"
        assert _normalise_year("31st March, 2024") == "2024"

    def test_fy_shorthand_still_reads_as_before(self):
        assert _normalise_year("FY24") == "2024"

    def test_a_cell_naming_two_separate_years_is_still_ambiguous(self):
        """Guessing which of two columns a merged header means is the error
        `_normalise_year` exists to refuse. The span fix must not weaken it."""
        assert _normalise_year("2024 2023") is None

    def test_the_convention_matches_the_rest_of_the_corpus(self):
        """A span and an explicit date for the same period must agree, or the
        same table can carry two different labels for one column."""
        assert _normalise_year("2023-24") == _normalise_year("As at March 31, 2024")


class TestTheHeaderRowMapsCorrectly:
    def test_a_span_header_maps_the_column_to_its_ending_year(self):
        header = ["Particulars", "(C in crore) 2022-23", "(C in crore) 2021-22"]
        assert column_years(header) == {1: "2023", 2: "2022"}

    def test_span_and_date_headers_can_coexist_in_one_row(self):
        header = ["Particulars", "2023-24", "As at March 31, 2023"]
        assert column_years(header) == {1: "2024", 2: "2023"}
