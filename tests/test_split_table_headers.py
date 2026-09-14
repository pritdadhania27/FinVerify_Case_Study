"""A table split across chunks keeps its column years (RX-027).

The chunker repeats a table's preamble in every part - caption, unit banner,
rule - but not the header row that names the years. So the second part of a
split balance sheet contained no year at all, every figure in it fell back to
the document's fiscal year, and `year_is_stated` went False.

That was invisible until it was chased. The dataset generator requires a stated
year, so it skipped the consolidated balance sheet and took its answers from a
front-of-report summary table instead - which is how HDFC Bank's advances came
to be 1,600,585.9 (a summary table) rather than 1,661,949.29 (the consolidated
balance sheet, p412). The wrong-section answers of RX-026 were a SYMPTOM; this
was the cause.

The inheritance is deliberately one-directional and deliberately weak: an
earlier part reaches a later one, never the reverse, and only a part that names
no year of its own accepts one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.documents.facts import _table_key, facts_from_chunk, facts_from_chunks  # noqa: E402

HEADER_PART = {
    "chunk_id": "doc:p412:t0:0",
    "kind": "table",
    "page": 412,
    "document_id": "doc",
    "text": "\n".join(
        [
            "(All figures in crore)",
            "|  |  |  |  |",
            "|---|---|---|---|",
            "|  |  | March 31, 2024 | March 31, 2023 |",
            "| Total equity | 1 | 452,982.84 | 287,762.33 |",
        ]
    ),
}

# The same table, continued. Preamble repeated, header row absent - which is
# exactly what the chunker emits.
BODY_PART = {
    "chunk_id": "doc:p412:t0:1",
    "kind": "table",
    "page": 412,
    "document_id": "doc",
    "text": "\n".join(
        [
            "(All figures in crore)",
            "|  |  |  |  |",
            "|---|---|---|---|",
            "| Advances | 9 | 2,565,891.41 | 1,661,949.29 |",
        ]
    ),
}


def advances(facts):
    return [f for f in facts if f.metric == "advances"]


class TestTheHeaderCarriesAcrossParts:
    def test_alone_the_body_part_can_state_no_year(self):
        """The defect, pinned. Without the sibling there is genuinely no year
        in this text, and inventing one would be worse than admitting it."""
        found = advances(facts_from_chunk(BODY_PART))
        assert found, "the row is still extracted"
        assert not any(f.year_is_stated for f in found)

    def test_together_the_body_part_inherits_the_years(self):
        found = advances(facts_from_chunks([HEADER_PART, BODY_PART]))
        by_year = {f.year: f.value.amount for f in found if f.year_is_stated}
        assert by_year, "the header part's years must reach the body part"
        assert str(by_year.get("2023")) == "1661949.29", (
            "the FY2023 column of HDFC's consolidated balance sheet - the "
            "figure the summary table disagreed with"
        )
        assert str(by_year.get("2024")) == "2565891.41"

    def test_a_part_with_its_own_header_is_not_overwritten(self):
        """Inheritance must never beat a part that names its own years, or a
        table whose parts really do differ would be silently relabelled."""
        own = dict(BODY_PART)
        own["text"] = "\n".join(
            [
                "|  |  |  |  |",
                "|---|---|---|---|",
                "|  |  | March 31, 2019 | March 31, 2018 |",
                "| Advances | 9 | 11.00 | 22.00 |",
            ]
        )
        found = advances(facts_from_chunks([HEADER_PART, own]))
        years = {f.year for f in found if f.year_is_stated}
        assert years == {"2019", "2018"}, f"kept its own header, got {years}"

    def test_a_different_table_does_not_inherit(self):
        other = dict(BODY_PART)
        other["chunk_id"] = "doc:p412:t1:0"
        found = advances(facts_from_chunks([HEADER_PART, other]))
        assert not any(f.year_is_stated for f in found), (
            "t1 is a different table from t0 and must not borrow its columns"
        )

    def test_a_later_part_does_not_reach_an_earlier_one(self):
        """Order matters: chunks arrive in document order, and a header found
        late must not retroactively relabel rows already emitted."""
        found = advances(facts_from_chunks([BODY_PART, HEADER_PART]))
        assert not any(f.year_is_stated for f in found)


class TestTheTableKey:
    def test_parts_of_one_table_share_a_key(self):
        assert _table_key(HEADER_PART) == _table_key(BODY_PART) == "doc:p412:t0"

    def test_a_different_table_has_a_different_key(self):
        assert _table_key({"chunk_id": "doc:p412:t1:0"}) == "doc:p412:t1"

    def test_a_prose_chunk_has_no_key_and_inherits_nothing(self):
        assert _table_key({"chunk_id": "doc:p412:prose"}) is None
        assert _table_key({}) is None
