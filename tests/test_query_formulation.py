"""Tests for the query-formulation arms (RX-017) and the Module 7 live probe.

Both scripts exist to produce measurements, so what needs guarding is the part
that decides *what was measured*: if `without_company` silently failed to remove
the company, the arm would be a copy of the baseline and the experiment would
report "no difference" for the wrong reason. A null result from a broken
transform is indistinguishable from a null result from a real one, and RX-017's
headline IS a null result.

The sampling guard is the same principle as D35's interleaving: a probe that
draws 10 questions and gets 10 HDFC Bank ones measures one bank's phrasing while
claiming to measure the corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.measure_query_formulation import (  # noqa: E402
    ARMS,
    without_company,
    without_gloss,
)

TEMPLATE = (
    "For HDFC Bank Limited, as reported for the year ended March 31, 2023, "
    "what were advances (total advances as reported on the consolidated "
    "balance sheet)?"
)


class TestWithoutCompany:
    def test_the_issuer_is_removed(self):
        out = without_company(TEMPLATE, "HDFC Bank Limited")
        assert "HDFC" not in out and "Bank Limited" not in out

    def test_the_metric_and_gloss_survive(self):
        """Only the issuer goes. Removing more would confound the two arms."""
        out = without_company(TEMPLATE, "HDFC Bank Limited")
        assert "advances" in out
        assert "consolidated balance sheet" in out
        assert "March 31, 2023" in out

    def test_the_short_form_is_removed_too(self):
        """"Reliance Industries" appears without "Limited" throughout its filing."""
        question = "For Reliance Industries Limited, what were borrowings?"
        assert "Reliance" not in without_company(question, "Reliance Industries Limited")

    def test_a_question_without_the_company_is_unchanged(self):
        question = "How much were trade payables as at March 31, 2024?"
        assert without_company(question, "Infosys Limited") == question

    def test_an_empty_company_does_not_blank_the_question(self):
        """A blank name must not become a regex that matches everywhere."""
        assert without_company(TEMPLATE, "") == TEMPLATE


class TestWithoutGloss:
    def test_the_trailing_definition_is_removed(self):
        out = without_gloss(TEMPLATE, "HDFC Bank Limited")
        assert out.endswith("what were advances?")
        assert "consolidated balance sheet" not in out

    def test_a_question_with_no_gloss_is_unchanged(self):
        question = "How much were trade payables as at March 31, 2024?"
        assert without_gloss(question, "") == question

    def test_a_mid_sentence_parenthetical_is_left_alone(self):
        """Only the trailing gloss is the template's; an inline one is content.

        "Dividend per share (H)1" is a row label in HDFC Bank's ten-year summary,
        and stripping every parenthetical would remove the currency marker that
        distinguishes it.
        """
        question = "what was dividend per share (H) in 2023-24 for the group?"
        assert "(H)" in without_gloss(question, "")


class TestArmsAreDistinct:
    def test_every_arm_produces_a_different_query(self):
        """If two arms coincide, the comparison between them measures nothing."""
        produced = {name: fn(TEMPLATE, "HDFC Bank Limited") for name, fn in ARMS.items()}
        assert len(set(produced.values())) == len(ARMS)

    def test_asis_is_genuinely_unmodified(self):
        assert ARMS["asis"](TEMPLATE, "HDFC Bank Limited") == TEMPLATE

    def test_both_is_the_composition_of_the_other_two(self):
        expected = without_gloss(without_company(TEMPLATE, "HDFC Bank Limited"), "")
        assert ARMS["both"](TEMPLATE, "HDFC Bank Limited") == expected


class TestSampling:
    def test_a_small_sample_still_reaches_every_company(self):
        """Same principle as D35: an unbalanced prefix measures one filing.

        Uses the real dataset because the property under test is about this
        corpus's actual company distribution - HDFC Bank alone is 127 of 268,
        so a naive sample of 10 would very likely be mostly HDFC.
        """
        from scripts.measure_question_understanding import sample

        drawn = sample(10, seed=1)
        assert len({q["company"] for q in drawn}) == 5

    def test_the_sample_is_reproducible(self):
        from scripts.measure_question_understanding import sample

        assert [q["qid"] for q in sample(12, seed=7)] == [
            q["qid"] for q in sample(12, seed=7)
        ]

    def test_a_different_seed_draws_differently(self):
        from scripts.measure_question_understanding import sample

        assert [q["qid"] for q in sample(12, seed=7)] != [
            q["qid"] for q in sample(12, seed=8)
        ]

    def test_asking_for_more_than_exists_returns_what_exists(self):
        """The dataset size is read, not hardcoded.

        This asserted 268 and broke when the dataset was legitimately
        rebuilt (D42). A test that pins a count tests the snapshot; the
        property worth holding is that an over-large request is clamped to
        what exists rather than raising or padding.
        """
        import json

        from backend.core.paths import project_path
        from scripts.measure_question_understanding import sample

        payload = json.loads(
            project_path("datasets/finverify_ind/finverify_ind_v1.json").read_text(
                encoding="utf-8"
            )
        )
        total = len(payload["questions"] if isinstance(payload, dict) else payload)
        assert len(sample(10_000, seed=1)) == total
