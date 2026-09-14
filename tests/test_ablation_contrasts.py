"""Module 24 contrasts: arm A minus exactly one field.

These exist because RX-049's table was first computed in a scratch script, and a
transcription error in one of its ten cells survived into four documents. The
number a paper prints has to come from a function a test can call.
"""

from __future__ import annotations

import pytest

from evaluation.ablation import ABLATION_ARMS, ablation_contrasts
from evaluation.ablation.contrasts import (
    MIN_ERRORS_FOR_A_CONTRAST,
    MIN_PAIRED_FOR_A_CONTRAST,
)
from evaluation.error_analysis import AnalysedCase

RESAMPLES = 200


def case(qid: str, arm: str, *, correct: bool, risk: float | None, committed: bool = True):
    """One graded question. `predicted=None` is exactly how an abstention is stored."""
    return AnalysedCase(
        qid=qid,
        arm=arm,
        question=f"question {qid}",
        correct=correct,
        risk_score=risk,
        agreed=None,
        ambiguous=False,
        all_evidence_retrieved=True,
        predicted="1234" if committed else None,
    )


def arms_where(baseline_ranks_well: bool, *, n: int = 40, errors: int = 16):
    """Two arms over the same questions; only the baseline's score tracks error.

    The ablated arm is given a score uncorrelated with correctness, so
    AUROC(A) - AUROC(X) is positive by construction. That is the shape the real
    contrast has, and it lets the test assert a direction without pinning a
    figure that a bootstrap seed change would break.
    """
    base, other = [], []
    for i in range(n):
        wrong = i < errors
        informative = 0.9 if wrong else 0.1
        base.append(case(f"Q{i}", "A", correct=not wrong, risk=informative))
        ablated = 0.5 + (i % 2) / 10 if baseline_ranks_well else informative
        other.append(case(f"Q{i}", "C", correct=not wrong, risk=ablated))
    return {"A": base, "C": other}


class TestDirectionAndPairing:
    def test_removing_a_component_that_carries_signal_shows_a_positive_contrast(self):
        result = ablation_contrasts(arms_where(True), resamples=RESAMPLES)
        measured = result["contrasts"]["A-C"]["all"]
        assert measured["testable"]
        assert measured["point"] > 0
        assert measured["comparison"] == "AUROC(A) - AUROC(C)"

    def test_arms_sharing_no_questions_are_untestable_not_zero(self):
        """The RX-047 failure mode: a contrast across disjoint sets means nothing."""
        cases = {
            "A": [case(f"Q{i}", "A", correct=i % 3 == 0, risk=i / 40) for i in range(20)],
            "C": [case(f"Z{i}", "C", correct=i % 3 == 0, risk=i / 40) for i in range(20)],
        }
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]["all"]
        assert measured["testable"] is False
        assert measured["n"] == 0
        assert "shared" in measured["reason"]

    def test_only_questions_both_arms_answered_are_counted(self):
        cases = arms_where(True, n=30, errors=12)
        cases["C"] = cases["C"][:20]
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]["all"]
        assert measured["n"] == 20

    @pytest.mark.parametrize("shared", [0, MIN_PAIRED_FOR_A_CONTRAST - 1])
    def test_too_few_paired_questions_is_reported_rather_than_computed(self, shared):
        cases = arms_where(True, n=30, errors=12)
        cases["C"] = cases["C"][:shared]
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]["all"]
        assert measured["testable"] is False


class TestTheCommittedFamily:
    def test_committed_requires_both_arms_to_have_committed(self):
        """An abstention in EITHER arm removes the question from the pair.

        Filtering on the baseline alone was one of the candidate explanations
        for RX-049's bad cell, and it silently changes every figure in the
        family.
        """
        cases = arms_where(True, n=40, errors=16)
        for row in cases["C"][:10]:
            cases["C"][cases["C"].index(row)] = case(
                row.qid, "C", correct=row.correct, risk=row.risk_score, committed=False
            )
        result = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]
        assert result["all"]["n"] == 40
        assert result["committed"]["n"] == 30

    def test_a_contrast_resting_on_too_few_errors_is_flagged_underpowered(self):
        """The real defect in RX-049: p<0.0001 on three errors, read as a finding."""
        cases = arms_where(True, n=40, errors=3)
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]["all"]
        assert measured["testable"] is True
        assert measured["underpowered"] is True
        assert measured["errors"] == 3
        assert "ranking over errors" in measured["reason"]

    def test_a_well_powered_contrast_carries_no_underpowered_flag(self):
        cases = arms_where(True, n=60, errors=MIN_ERRORS_FOR_A_CONTRAST + 5)
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-C"]["all"]
        assert "underpowered" not in measured
        assert measured["errors"] >= MIN_ERRORS_FOR_A_CONTRAST


class TestNonDetectorArms:
    def test_an_arm_with_no_risk_score_is_not_a_detector_scoring_zero(self):
        cases = arms_where(True)
        cases["D"] = [case(c.qid, "D", correct=c.correct, risk=None) for c in cases["A"]]
        measured = ablation_contrasts(cases, resamples=RESAMPLES)["contrasts"]["A-D"]
        for family in ("all", "committed"):
            assert measured[family]["testable"] is False
            assert "not a detector" in measured[family]["reason"]

    def test_the_baseline_never_contrasts_against_itself(self):
        result = ablation_contrasts(arms_where(True), resamples=RESAMPLES)
        assert "A-A" not in result["contrasts"]


class TestFamilyWiseCorrection:
    def test_correction_is_applied_within_each_family_separately(self):
        """`all` and `committed` are different claims and must not share a family.

        Pooling them would divide every threshold by twice as many tests and
        change which contrasts survive - and the two families do not even
        contain the same questions.
        """
        cases = arms_where(True, n=40, errors=16)
        cases["E"] = [
            case(c.qid, "E", correct=c.correct, risk=0.5) for c in cases["A"]
        ]
        result = ablation_contrasts(cases, resamples=RESAMPLES)
        correction = result["family_wise_correction"]
        for family in correction:
            ranks = sorted(row["rank"] for row in correction[family].values())
            assert ranks == list(range(1, len(ranks) + 1))

    def test_every_ablation_arm_names_the_single_field_it_removes(self):
        """A contrast whose label nobody wrote down is a contrast nobody can read."""
        result = ablation_contrasts(arms_where(True), resamples=RESAMPLES)
        assert result["contrasts"]["A-C"]["removes"] == ABLATION_ARMS["C"]
        assert all(ABLATION_ARMS[arm] for arm in ABLATION_ARMS)
