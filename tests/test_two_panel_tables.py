"""A two-panel balance sheet does not leak figures across the panels (RX-027).

Indian balance sheets are often printed as two panels side by side - assets on
the left, equity and liabilities on the right - and extraction flattens both
into a single row:

    | Goodwill |  | 14,989 | 15,270 | Total Equity |  | 9,25,788 | 8,28,881 |

Reading `cells[0]` as the label and then taking every numeric cell to the end of
the row attributed the right panel's figures to the left panel's metric. On
Reliance p110 that gave `goodwill` four facts, all four marked `year_is_stated`:
14,989 and 15,270, which are goodwill, and 9,25,788 and 8,28,881, which are
total equity.

That is the worst shape a defect can take here. The wrong facts carry a correct
page, a correct row label, a correct year and a stated-year flag, so nothing
downstream can tell them from the right ones - and the error is 62x, in a
project whose subject is detecting numerically wrong answers.

The fix segments a row on its label cells. It also unlocks the right panel,
which was never extractable before: equity, borrowings and payables all live
there.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.documents.facts import facts_from_chunk, row_segments  # noqa: E402

TWO_PANEL = {
    "chunk_id": "doc:p110:t0:0",
    "kind": "table",
    "page": 110,
    "document_id": "doc",
    "text": "\n".join(
        [
            "|  |  | As at | As at |  |  | As at | As at |",
            "|---|---|---|---|---|---|---|---|",
            (
                "|  | Notes | 31st March, 2024 | 31st March, 2023 |"
                "  | Notes | 31st March, 2024 | 31st March, 2023 |"
            ),
            "| Goodwill |  | 14,989 | 15,270 | Total Equity |  | 9,25,788 | 8,28,881 |",
        ]
    ),
}


def values(facts, metric):
    return {f.value.amount for f in facts if f.metric == metric}


class TestTheRowSegmenter:
    def test_a_single_panel_row_is_one_segment(self):
        assert row_segments(["Advances", "9", "2,565,891.41", "1,661,949.29"]) == [
            (0, range(1, 4))
        ]

    def test_a_two_panel_row_splits_at_the_second_label(self):
        cells = ["Goodwill", "", "14,989", "15,270", "Total Equity", "", "9,25,788", "8,28,881"]
        assert row_segments(cells) == [(0, range(1, 4)), (4, range(5, 8))]

    def test_a_schedule_reference_does_not_start_a_new_panel(self):
        """HDFC Bank writes schedule references as "18 (4)". Treating that as a
        label would cut the row before its figures and lose them entirely - the
        regression this threshold exists to avoid."""
        cells = ["Employees stock options outstanding", "18 (4)", "2,652.72", "1,117.20"]
        assert row_segments(cells) == [(0, range(1, 4))]

    def test_a_row_with_no_label_yields_nothing(self):
        assert row_segments(["", "", "1,234"]) == []

    def test_a_trailing_text_column_ends_the_value_run(self):
        cells = ["Investments", "51,348", "38,635", "reclassified to Profit or Loss"]
        assert row_segments(cells) == [(0, range(1, 3)), (3, range(4, 4))]


class TestNoLeakBetweenPanels:
    def test_the_left_metric_keeps_only_its_own_figures(self):
        facts = facts_from_chunk(TWO_PANEL)
        assert values(facts, "goodwill") == {14989, 15270}, (
            "9,25,788 is total equity and must never be attributed to goodwill"
        )

    def test_the_right_panel_is_extracted_under_its_own_label(self):
        facts = facts_from_chunk(TWO_PANEL)
        assert values(facts, "total equity") == {925788, 828881}

    def test_both_panels_keep_the_years_of_their_own_columns(self):
        facts = facts_from_chunk(TWO_PANEL)
        by = {
            (f.metric, f.year): f.value.amount for f in facts if f.year_is_stated
        }
        assert by[("goodwill", "2024")] == 14989
        assert by[("goodwill", "2023")] == 15270
        assert by[("total equity", "2024")] == 925788
        assert by[("total equity", "2023")] == 828881

    def test_a_leaked_fact_would_have_looked_completely_valid(self):
        """Stated as a test because it is the reason this one matters.

        The wrong facts were not malformed. They had a page, a row label, a
        column index, a year read from the table's own header, and
        `year_is_stated` True. Nothing downstream could have rejected them.
        """
        facts = facts_from_chunk(TWO_PANEL)
        leaked = [
            f for f in facts if f.metric == "goodwill" and f.value.amount > 100000
        ]
        assert not leaked, f"leaked and fully-formed: {leaked}"
