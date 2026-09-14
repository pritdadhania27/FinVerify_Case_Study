"""The generator answers from the section its question names (RX-026, D42).

A filing states most metrics twice. Tata Motors' total borrowings are 13,771.04
crore standalone and 98,500.09 crore consolidated - seven times apart, because
the consolidated figures carry Jaguar Land Rover. A question that names one
basis and is answered from the other is simply wrong, and nothing about it looks
wrong: the figure is real, it is printed on the cited page, and the provenance
is accurate.

Three rules, and the third is the one that is easy to get wrong under pressure
to keep the dataset large.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.documents.statement_basis import CONSOLIDATED, STANDALONE, UNKNOWN  # noqa: E402
from scripts.build_finverify_ind import (  # noqa: E402
    _by_basis,
    _honest_definition,
    _restate_basis,
    _stable_qid,
)


class Fact:
    """Only the attribute `_by_basis` reads."""

    def __init__(self, page: int):
        self.page = page

    def __repr__(self):
        return f"Fact(p{self.page})"


BASIS = {10: CONSOLIDATED, 11: CONSOLIDATED, 90: STANDALONE, 50: UNKNOWN}


class TestChoosingTheBasis:
    def test_consolidated_is_preferred_when_both_exist(self):
        basis, chosen = _by_basis([Fact(90), Fact(10)], BASIS)
        assert basis == CONSOLIDATED
        assert [f.page for f in chosen] == [10], "only the consolidated facts survive"

    def test_standalone_is_used_when_that_is_all_there_is(self):
        """Not dropped. A standalone figure answers a standalone question, and
        the question is reworded to say so."""
        basis, chosen = _by_basis([Fact(90)], BASIS)
        assert basis == STANDALONE
        assert [f.page for f in chosen] == [90]

    def test_unplaceable_pages_yield_nothing(self):
        """The rule that keeps the dataset honest rather than large.

        A front-of-report highlights table cannot be assigned a basis, so no
        truthful question can be asked of it. HDFC Bank's "Summary of Financial
        Performance" states advances as 1,600,585.9 where the consolidated
        balance sheet says 1,661,949.29 - naming either basis would be a lie
        about one of them, and this branch is why 103 such HDFC candidates were
        dropped rather than asked.
        """
        assert _by_basis([Fact(50)], BASIS) is None

    def test_an_unplaceable_page_never_dilutes_a_placeable_one(self):
        basis, chosen = _by_basis([Fact(50), Fact(10), Fact(11)], BASIS)
        assert basis == CONSOLIDATED
        assert {f.page for f in chosen} == {10, 11}

    def test_no_facts_at_all_is_not_an_error(self):
        assert _by_basis([], BASIS) is None


class TestTheQuestionSaysWhichBasis:
    DEFINITION = "total borrowings as reported on the consolidated balance sheet"

    def test_a_consolidated_question_is_left_alone(self):
        assert _restate_basis(self.DEFINITION, CONSOLIDATED) == self.DEFINITION

    def test_a_standalone_question_says_standalone(self):
        assert _restate_basis(self.DEFINITION, STANDALONE) == (
            "total borrowings as reported on the standalone balance sheet"
        )

    def test_the_rewrite_is_case_insensitive_and_whole_word(self):
        assert "standalone" in _restate_basis("Consolidated statements", STANDALONE)
        # Not a substring match on some longer word that happens to contain it.
        assert _restate_basis("unconsolidated_flag", STANDALONE) == "unconsolidated_flag"

    def test_a_question_whose_wording_disagrees_with_its_source_is_the_defect(self):
        """Stated as a test because it is the whole point: the wording and the
        page must agree, or the question is wrong however good the figure is."""
        wording = _restate_basis(self.DEFINITION, STANDALONE)
        assert "consolidated" not in wording.lower()


class TestTheIdSurvivesRegeneration:
    """A counter renumbered every question on every run, so one regeneration
    silently invalidated every human verdict - the one input this project
    cannot re-manufacture."""

    def test_the_same_question_keeps_its_id(self):
        assert _stable_qid("doc", "borrowings", "2023") == _stable_qid(
            "doc", "borrowings", "2023"
        )

    def test_different_questions_get_different_ids(self):
        ids = {
            _stable_qid("doc", "borrowings", "2023"),
            _stable_qid("doc", "borrowings", "2024"),
            _stable_qid("doc", "advances", "2023"),
            _stable_qid("other", "borrowings", "2023"),
        }
        assert len(ids) == 4

    def test_the_parts_cannot_run_together_into_a_collision(self):
        """("ab", "c") and ("a", "bc") are different questions and must not
        hash alike - the separator is load-bearing, not decoration."""
        assert _stable_qid("ab", "c") != _stable_qid("a", "bc")

    def test_the_id_is_shaped_like_the_old_one(self):
        qid = _stable_qid("doc", "borrowings", "2023")
        assert qid.startswith("FI") and len(qid) == 10


class TestTheDefinitionDoesNotOverclaim:
    """Under Ind AS the consolidated balance sheet splits borrowings into
    current and non-current and prints no total. A question asking for "total
    borrowings as reported on the consolidated balance sheet" is therefore
    asking for a figure that is not printed there, and any single row mined for
    it is one of the two parts - which is exactly what a validator rejected on
    Reliance (2,22,712 non-current offered against a total of 3,24,622).
    """

    CONSOLIDATED_TOTAL = "total borrowings as reported on the consolidated balance sheet"

    def test_a_total_claim_is_dropped_when_the_row_is_not_a_total(self):
        assert _honest_definition(self.CONSOLIDATED_TOTAL, "borrowings", "Borrowings") == (
            "the 'Borrowings' line item as reported on the consolidated balance sheet"
        )

    def test_a_total_claim_survives_when_the_row_really_is_the_total(self):
        """Tata Motors' standalone capital-management note does print a row
        labelled "Total borrowings", and that question is answerable as worded."""
        assert (
            _honest_definition(self.CONSOLIDATED_TOTAL, "borrowings", "Total borrowings")
            == self.CONSOLIDATED_TOTAL
        )

    def test_a_definition_that_claims_no_total_is_untouched(self):
        original = "profit for the year attributable to owners of the company"
        assert _honest_definition(original, "profit for the year", "Profit") == original

    def test_the_row_label_reaches_the_reader(self):
        """The validator has to see which line was read, because two rows on one
        balance sheet can share a label."""
        assert "Non-Current Borrowings" in _honest_definition(
            self.CONSOLIDATED_TOTAL, "borrowings", "Non-Current Borrowings"
        )
