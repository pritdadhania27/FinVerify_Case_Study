"""Tests for the case study (spec Module 27).

A case study is exactly where a plausible sentence slips past the evidence, so
the properties under test are about refusal: refusing to render a document from
no data, refusing to present a three-question cell as a finding, and refusing to
bury the both-agree-wrong count.
"""

from __future__ import annotations

import pytest

from evaluation.case_study import (
    MIN_CELL,
    build_case_study,
    render_markdown,
)
from evaluation.dataset import DatasetQuestion, GoldAnswer
from evaluation.error_analysis import AnalysedCase
from backend.verification.taxonomy import (
    ErrorKind,
    ErrorLabel,
    ErrorProvenance,
    LabelConfidence,
)


def question(qid: str, company: str = "Infosys Limited", kind: str = "lookup"):
    return DatasetQuestion(
        qid=qid,
        question=f"q {qid}",
        company=company,
        fiscal_year="2023-24",
        document_id="d1",
        answer=GoldAnswer(text="3,956", unit="INR crore"),
        definition="as reported",
        question_type=kind,
    )


def case(qid: str, *, correct=True, agreed=True, label=None, ambiguous=False):
    return AnalysedCase(
        qid=qid,
        arm="A",
        question=f"q {qid}",
        correct=correct,
        risk_score=0.1 if correct else 0.9,
        agreed=agreed,
        ambiguous=ambiguous,
        all_evidence_retrieved=True,
        label=label,
    )


def label(kind=ErrorKind.WRONG_SCALE, provenance=ErrorProvenance.REASONING,
          confidence=LabelConfidence.MECHANICAL):
    return ErrorLabel(
        is_error=True, provenance=provenance, kind=kind, confidence=confidence,
        evidence=("x",),
    )


def row(qid: str, **kw):
    base = {
        "question_id": qid,
        "arm": "A",
        "latency_seconds": 3.0,
        "abstained": False,
        "verification": {"triggered": False},
    }
    base.update(kw)
    return base


class TestRefusesToInventACaseStudy:
    def test_no_data_produces_no_table(self):
        """A template of zeroes reads as a system that scored zero."""
        study = build_case_study([], {}, [])
        assert study.has_data is False
        text = render_markdown(study)
        assert "|---" not in text
        assert "Not yet produced" in text

    def test_it_says_what_to_run(self):
        text = render_markdown(build_case_study([], {}, []))
        assert "run_campaign" in text
        assert "validate" in text

    def test_the_corpus_section_survives_having_no_results(self):
        """Selecting a representative set IS part of spec 35, and that part is
        done. Dropping it would make completed work look absent."""
        text = render_markdown(
            build_case_study([], {}, []), corpus="## The corpus\n\nFive companies."
        )
        assert "Five companies." in text
        assert "Not yet produced" in text


class TestSpecCoverage:
    """Spec §35 lists eight things to analyse. Each must be present."""

    def _study(self):
        questions = {f"Q{i}": question(f"Q{i}") for i in range(20)}
        cases = [case(f"Q{i}", correct=i % 4 != 0, agreed=i % 4 != 0) for i in range(20)]
        rows = [row(f"Q{i}") for i in range(20)]
        return build_case_study(cases, questions, rows)

    def test_accuracy_is_reported(self):
        assert self._study().accuracy == pytest.approx(0.75)

    def test_disagreement_rate_is_reported(self):
        assert self._study().disagreement_rate == pytest.approx(0.25)

    def test_a_real_disagreement_is_reported_apart_from_not_agreeing(self):
        """`agreed` is `verdict == AGREE`, so "did not agree" pools DISAGREE with
        UNCERTAIN. On the held-out arm A the headline called 42 rows the
        "Disagreement rate" when 3 of them were disagreements and 39 were a
        channel producing no figure."""
        questions = {f"Q{i}": question(f"Q{i}") for i in range(4)}
        cases = [case("Q0"), case("Q1", agreed=False), case("Q2", agreed=False),
                 case("Q3", agreed=False)]
        rows = [row("Q0", verdict="AGREE"), row("Q1", verdict="DISAGREE"),
                row("Q2", verdict="UNCERTAIN"), row("Q3", verdict="UNCERTAIN")]
        study = build_case_study(cases, questions, rows)

        assert study.disagreement_rate == pytest.approx(0.75)
        assert study.numeric_disagreements == 1
        text = render_markdown(study)
        assert "| Disagreement rate |" not in text
        assert "did not agree" in text.lower()
        assert "1 of 4" in text

    def test_the_blind_spot_is_in_the_headline_not_an_appendix(self):
        cases = [case("Q1", correct=False, agreed=True), case("Q2")]
        text = render_markdown(
            build_case_study(cases, {"Q1": question("Q1"), "Q2": question("Q2")},
                             [row("Q1"), row("Q2")])
        )
        headline = text.split("## By company")[0]
        assert "both were wrong" in headline.lower()

    def test_error_types_are_reported_on_both_axes(self):
        cases = [case("Q1", correct=False, agreed=False, label=label())]
        study = build_case_study(cases, {"Q1": question("Q1")}, [row("Q1")])
        assert study.error_kinds == {"wrong_scale": 1}
        assert study.error_provenance == {"reasoning": 1}

    def test_difficult_question_categories_are_ordered_worst_first(self):
        questions = {
            **{f"L{i}": question(f"L{i}", kind="lookup") for i in range(4)},
            **{f"M{i}": question(f"M{i}", kind="multi_hop") for i in range(4)},
        }
        cases = [case(f"L{i}") for i in range(4)] + [
            case(f"M{i}", correct=False) for i in range(4)
        ]
        rows = [row(q) for q in questions]
        study = build_case_study(cases, questions, rows)
        assert study.hardest_question_types()[0][0] == "multi_hop"

    def test_latency_is_reported(self):
        study = self._study()
        assert study.latency_p50 == pytest.approx(3.0)
        assert study.latency_p95 is not None

    def test_verification_effectiveness_separates_resolved_from_abstained(self):
        questions = {"Q1": question("Q1"), "Q2": question("Q2")}
        rows = [
            row("Q1", verification={"triggered": True, "resolved": True}),
            row("Q2", verification={"triggered": True, "resolved": False}),
        ]
        study = build_case_study([case("Q1"), case("Q2")], questions, rows)
        assert study.verification_trigger_rate == pytest.approx(1.0)
        assert study.verification_resolved == 1
        assert study.verification_abstained == 1

    def test_an_abstention_is_not_rendered_as_a_failure(self):
        questions = {"Q1": question("Q1")}
        rows = [row("Q1", verification={"triggered": True, "resolved": False})]
        text = render_markdown(build_case_study([case("Q1")], questions, rows))
        assert "An abstention is not a failure" in text


class TestThinCells:
    def test_a_small_company_cell_is_marked(self):
        questions = {"Q1": question("Q1", company="Tata Motors Limited")}
        study = build_case_study([case("Q1")], questions, [row("Q1")])
        assert study.companies[0].thin is True
        assert any("must not be read as findings" in note for note in study.notes)

    def test_a_large_cell_is_not_marked(self):
        n = MIN_CELL + 5
        questions = {f"Q{i}": question(f"Q{i}") for i in range(n)}
        cases = [case(f"Q{i}") for i in range(n)]
        study = build_case_study(cases, questions, [row(f"Q{i}") for i in range(n)])
        assert study.companies[0].thin is False

    def test_the_rendering_flags_thin_cells_in_the_table(self):
        questions = {"Q1": question("Q1")}
        text = render_markdown(build_case_study([case("Q1")], questions, [row("Q1")]))
        assert "**thin cell**" in text

    def test_a_thin_headline_is_marked_before_the_numbers_not_after(self):
        """The per-company table marked thin cells from the start; the headline
        did not. So a one-question run rendered "Numerical accuracy 100.0%" at
        the top of the page, above a table calling that same cell too thin to
        read. The headline is the line that gets quoted, so the warning has to
        reach the reader before the figure does."""
        questions = {"Q1": question("Q1")}
        text = render_markdown(build_case_study([case("Q1")], questions, [row("Q1")]))
        warning = text.index("too thin to be a finding")
        assert warning < text.index("Numerical accuracy")

    def test_a_thick_headline_carries_no_warning(self):
        n = MIN_CELL + 5
        questions = {f"Q{i}": question(f"Q{i}") for i in range(n)}
        text = render_markdown(
            build_case_study(
                [case(f"Q{i}") for i in range(n)], questions, [row(f"Q{i}") for i in range(n)]
            )
        )
        assert "too thin to be a finding" not in text

    def test_the_headline_warning_names_the_step_size(self):
        """"1 question" is abstract; "moves in steps of 100 points" is not. The
        step size is what makes an n of 1 obviously unreadable."""
        questions = {f"Q{i}": question(f"Q{i}") for i in range(4)}
        text = render_markdown(
            build_case_study(
                [case(f"Q{i}") for i in range(4)], questions, [row(f"Q{i}") for i in range(4)]
            )
        )
        assert "steps of 25 points" in text


class TestCostIsAbsentNotZero:
    def test_missing_token_usage_is_none(self):
        questions = {"Q1": question("Q1")}
        study = build_case_study([case("Q1")], questions, [row("Q1")])
        assert study.tokens_per_question is None
        assert any("absent rather than zero" in note for note in study.notes)

    def test_recorded_usage_is_averaged(self):
        questions = {"Q1": question("Q1")}
        rows = [row("Q1", natural={"tokens": 800}, program={"tokens": 400})]
        study = build_case_study([case("Q1")], questions, rows)
        assert study.tokens_per_question == pytest.approx(1200)


class TestHonestFraming:
    def _text(self):
        n = MIN_CELL + 5
        questions = {f"Q{i}": question(f"Q{i}") for i in range(n)}
        cases = [case(f"Q{i}", correct=i % 3 != 0) for i in range(n)]
        rows = [row(f"Q{i}") for i in range(n)]
        return render_markdown(build_case_study(cases, questions, rows))

    def test_free_tier_caveat_is_always_present(self):
        assert "free-tier-model figures" in self._text()

    def test_the_two_taxonomy_axes_are_explained(self):
        questions = {"Q1": question("Q1")}
        cases = [case("Q1", correct=False, label=label())]
        text = render_markdown(build_case_study(cases, questions, [row("Q1")]))
        assert "One label cannot serve" in text

    def test_suggested_labels_are_counted_not_hidden(self):
        questions = {"Q1": question("Q1")}
        cases = [
            case("Q1", correct=False,
                 label=label(kind=ErrorKind.UNCLASSIFIED,
                             confidence=LabelConfidence.NEEDS_HUMAN))
        ]
        study = build_case_study(cases, questions, [row("Q1")])
        assert any("need a human" in note for note in study.notes)


class TestFailureCases:
    def test_a_case_with_no_matching_question_does_not_crash(self):
        study = build_case_study([case("Q1")], {}, [row("Q1")])
        assert study.companies[0].company == "unknown"

    def test_detection_block_is_omitted_when_absent(self):
        questions = {"Q1": question("Q1")}
        text = render_markdown(build_case_study([case("Q1")], questions, [row("Q1")]))
        assert "## Detection" not in text

    def test_an_undefined_auroc_renders_as_a_dash(self):
        questions = {"Q1": question("Q1")}
        study = build_case_study(
            [case("Q1")], questions, [row("Q1")],
            detection={"auroc": None, "positives": 0, "notes": ["undefined"], "auroc_ci": {}},
        )
        text = render_markdown(study)
        assert "AUROC —" in text


# --------------------------------------------------------------------------
# Token accounting. Regression tests for RX-052.
#
# The case study read `record[channel]["tokens"]`, a key the recorder stopped
# writing in 2026-09, so every row of every modern campaign looked like it had
# spent nothing and the document printed "no row recorded token usage" over
# artifacts where all 61 rows recorded it. The identical defect had already been
# found and fixed in evaluation/metrics/efficiency.py and was never propagated
# here - one wrong key, two readers, one fixed.
# --------------------------------------------------------------------------


def test_tokens_are_read_from_the_usage_block_the_recorder_writes():
    """The shape every campaign since 2026-09 actually has."""
    from evaluation.case_study import _row_tokens

    record = {
        "natural": {"usage": {"total_tokens": 2655, "prompt_tokens": 2175}},
        "program": {"usage": {"total_tokens": 3473}},
    }
    assert _row_tokens(record) == 6128


def test_the_rows_own_total_wins_over_summing_channels():
    """`tokens_used` includes calls that record no per-channel usage block.

    The verification agent is the known case: it is invoked without its usage
    being recorded, so summing `natural` and `program` undercounts any arm whose
    arbiter fired. Preferring the row's own total is what keeps that spend
    visible instead of silently dropping it.
    """
    from evaluation.case_study import _row_tokens

    record = {
        "tokens_used": 7000,
        "natural": {"usage": {"total_tokens": 2655}},
        "program": {"usage": {"total_tokens": 3473}},
    }
    assert _row_tokens(record) == 7000


def test_the_pre_2026_09_flat_key_still_reads():
    """experiments/runs/ is append-only and committed, so this path cannot retire."""
    from evaluation.case_study import _row_tokens

    assert _row_tokens({"natural": {"tokens": 100}, "program": {"tokens": 50}}) == 150


def test_a_row_that_truly_recorded_nothing_reads_as_zero_not_as_a_guess():
    from evaluation.case_study import _row_tokens

    assert _row_tokens({"natural": {}, "program": None}) == 0
    assert _row_tokens({}) == 0


def test_the_real_test_campaign_yields_a_token_figure_matching_the_efficiency_report():
    """The cross-check that would have caught the original bug.

    Two independent readers over the same artifact - this module and
    evaluation/report.py - must agree. They disagreed by the whole figure for
    eleven days and nothing noticed, because each was only ever read alone.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    run = root / "experiments" / "runs" / "campaign_20260905T112212Z" / "results.jsonl"
    report = root / "evaluation" / "reports" / "results_campaign_20260905T112212Z.json"
    if not run.exists() or not report.exists():
        pytest.skip("the test-split campaign artifacts are not present")

    from evaluation.case_study import _row_tokens

    totals = [
        _row_tokens(json.loads(line))
        for line in run.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("arm") == "A"
    ]
    measured = [t for t in totals if t]
    assert measured, "arm A recorded no token usage at all, which contradicts the artifact"
    mine = sum(measured) / len(measured)

    published = json.loads(report.read_text(encoding="utf-8"))
    theirs = published["arms"]["A"]["efficiency"]["tokens_per_question"]
    assert abs(mine - theirs) < 0.01, f"case study reads {mine}, efficiency report says {theirs}"
