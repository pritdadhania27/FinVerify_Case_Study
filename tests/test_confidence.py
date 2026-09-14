"""Tests for confidence and risk scoring (spec Module 15).

The suite is written around the ways this module can fail *silently*. A risk
score that is inverted, saturated, or confounded still produces a number in
[0,1] for every question, still writes a clean run artifact, and still yields a
publishable-looking AUROC. Nothing crashes. So the tests assert the properties
the score has to have, not just that it returns.
"""

from decimal import Decimal

import pytest

from backend.core.financial_value import parse_financial_value
from backend.verification.confidence import (
    DEFAULT_ABSTAIN_THRESHOLD,
    FacetStatus,
    assess,
)
from backend.verification.consistency import ChannelAnswer, compare

A, B, D = "channel_a", "channel_b", "deterministic"


def ch(name: str, text: str | None, **kw):
    if text is None:
        return ChannelAnswer(
            name,
            None,
            available=False,
            failure_reason=kw.get("reason", "failed"),
            applicable=kw.get("applicable", True),
        )
    return ChannelAnswer(name, parse_financial_value(text, **kw))


def inapplicable(name: str = D):
    """The deterministic channel on a lookup: nothing to verify, by design."""
    return ChannelAnswer(
        name, None, available=False, failure_reason="no arithmetic", applicable=False
    )


class TestScoreDirection:
    """The single most dangerous defect in this module.

    EVALUATION.md 5.1 defines s as RISK - higher means more likely WRONG - while
    the consistency engine's score runs the opposite way. Emitting one where the
    other is expected inverts every AUROC in the paper to 1-AUROC. A working
    detector then reports as broken and a broken one reports as working, and
    absolutely nothing in the pipeline complains.
    """

    def test_agreement_produces_LOW_risk(self):
        r = assess(compare(ch(A, "25%"), ch(B, "25%")), evidence_blocks=4)
        assert r.risk_score < 0.25
        assert r.band() == "LOW"

    def test_disagreement_produces_HIGH_risk(self):
        r = assess(compare(ch(A, "25%"), ch(B, "80%")), evidence_blocks=4)
        assert r.risk_score > 0.6
        assert r.band() == "HIGH"

    def test_risk_and_confidence_are_complements(self):
        r = assess(compare(ch(A, "25%"), ch(B, "25%")), evidence_blocks=4)
        assert r.risk_score + r.confidence == pytest.approx(1.0)

    def test_worse_agreement_never_lowers_risk(self):
        """Monotonicity. The ranking is the whole product; AUROC uses nothing else."""
        gaps = ["25%", "26%", "30%", "50%", "90%"]
        risks = [
            assess(compare(ch(A, "25%"), ch(B, g)), evidence_blocks=4).risk_score
            for g in gaps
        ]
        assert risks == sorted(risks), risks


class TestEvidenceFloor:
    def test_no_evidence_is_maximum_risk_even_when_channels_agree(self):
        """Agreement on no evidence is agreement about nothing.

        Both channels can agree confidently while inventing the same number from
        prior knowledge, which is the exact failure this project exists to
        detect. Channel agreement must not be able to rescue it.
        """
        agreeing = compare(ch(A, "25%"), ch(B, "25%"))
        r = assess(agreeing, evidence_blocks=0)
        assert r.risk_score == pytest.approx(1.0)
        assert r.facets["evidence"] is FacetStatus.FAILED

    def test_evidence_present_and_all_channels_answered_is_verified(self):
        r = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%")), evidence_blocks=6
        )
        assert r.facets["evidence"] is FacetStatus.VERIFIED


class TestDeterministicConflict:
    def test_verifier_overruling_agreeing_channels_is_high_risk(self):
        """The 'both agree, both wrong' blind spot (EVALUATION.md 5.4).

        Channel agreement alone cannot detect it, so if this path does not raise
        risk sharply the third channel is decorative.
        """
        report = compare(ch(A, "25%"), ch(B, "25%"), ch(D, "80%"))
        r = assess(report, evidence_blocks=4)
        assert r.risk_score > 0.6
        assert r.facets["arithmetic"] is FacetStatus.FAILED
        assert any("both agree" in n for n in r.notes)

    def test_conflict_outranks_plain_disagreement(self):
        """Ordering claim from the weight block, asserted rather than asserted-in-prose."""
        conflict = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, "80%")), evidence_blocks=4
        ).risk_score
        plain = assess(
            compare(ch(A, "25%"), ch(B, "26.5%")), evidence_blocks=4
        ).risk_score
        assert conflict > plain


class TestCoverageIsNotContradiction:
    """RX-007's confound, guarded at the Module 15 boundary this time.

    Missing corroboration is uncertainty; a failed check is evidence of error.
    Collapsing them made the score encode "is this a lookup?" instead of "is this
    wrong?" - a systematic confound in the primary metric.
    """

    def test_lookup_is_not_penalised_for_an_inapplicable_verifier(self):
        lookup = assess(
            compare(ch(A, "3,956"), ch(B, "3,956"), inapplicable()), evidence_blocks=4
        )
        computed = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%")), evidence_blocks=4
        )
        assert lookup.risk_score == pytest.approx(computed.risk_score)

    def test_lookup_reports_arithmetic_as_not_applicable_not_failed(self):
        r = assess(
            compare(ch(A, "3,956"), ch(B, "3,956"), inapplicable()), evidence_blocks=4
        )
        assert r.facets["arithmetic"] is FacetStatus.NOT_APPLICABLE

    def test_a_channel_that_could_have_answered_and_did_not_costs(self):
        """Distinct from the case above: this one COULD apply and failed."""
        missing = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, None)), evidence_blocks=4
        )
        full = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%")), evidence_blocks=4
        )
        assert missing.risk_score > full.risk_score

    def test_missing_corroboration_costs_LESS_than_a_failed_check(self):
        """The ordering that keeps uncertainty from masquerading as error."""
        missing = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, None)), evidence_blocks=4
        ).risk_score
        failed = assess(
            compare(ch(A, "25%"), ch(B, "25%"), ch(D, "80%")), evidence_blocks=4
        ).risk_score
        assert missing < failed


class TestUnitFacet:
    def test_scale_mismatch_fails_the_unit_facet(self):
        """Crore/lakh/million confusion is the project's headline error class."""
        r = assess(compare(ch(A, "1 crore"), ch(B, "1 million")), evidence_blocks=4)
        assert r.facets["unit"] is FacetStatus.FAILED
        assert r.risk_score > 0.6

    def test_same_quantity_different_scale_units_passes(self):
        r = assess(compare(ch(A, "1 crore"), ch(B, "10 million")), evidence_blocks=4)
        assert r.facets["unit"] is FacetStatus.VERIFIED
        assert r.risk_score < 0.25


class TestBandsAreDerived:
    def test_bands_follow_the_score(self):
        low = assess(compare(ch(A, "25%"), ch(B, "25%")), evidence_blocks=4)
        high = assess(compare(ch(A, "25%"), ch(B, "90%")), evidence_blocks=4)
        assert low.band() == "LOW" and low.confidence_band() == "HIGH"
        assert high.band() == "HIGH" and high.confidence_band() == "LOW"

    def test_abstention_tracks_the_threshold(self):
        """A moderate disagreement, deliberately: a large one saturates.

        25% vs 90% scores ~0.993 and abstains at every plausible threshold, so it
        cannot distinguish "the threshold is respected" from "the score is stuck
        at 1". 25% vs 25.5% lands mid-range and actually exercises the knob.
        """
        moderate = assess(compare(ch(A, "25%"), ch(B, "25.5%")), evidence_blocks=4)
        assert 0.5 < moderate.risk_score < 0.99, moderate.risk_score
        assert moderate.should_abstain(DEFAULT_ABSTAIN_THRESHOLD)
        assert not moderate.should_abstain(0.99)


class TestHonestCalibration:
    def test_assessment_reports_itself_as_uncalibrated(self):
        """Weights are provisional until fitted on FinVerify-IND (D23).

        AUROC is rank-based and therefore valid uncalibrated, but Brier and ECE
        are not. A consumer must be able to see the difference; quoting a
        reliability figure off these weights would be fabricated evidence.
        """
        r = assess(compare(ch(A, "25%"), ch(B, "25%")), evidence_blocks=4)
        assert r.calibrated is False
        assert r.as_dict()["calibrated"] is False


class TestScoreProperties:
    def test_score_is_always_in_range(self):
        cases = [
            compare(ch(A, "25%"), ch(B, "25%")),
            compare(ch(A, "25%"), ch(B, "-25%")),
            compare(ch(A, None), ch(B, None)),
            compare(ch(A, "1 crore"), ch(B, "1 lakh"), ch(D, "5%")),
        ]
        for report in cases:
            for blocks in (0, 1, 8):
                s = assess(report, evidence_blocks=blocks).risk_score
                assert 0.0 <= s <= 1.0

    def test_features_survive_serialisation(self):
        """Run artifacts must carry enough to refit weights WITHOUT re-running.

        A full campaign is quota-bound (D21); re-running to recalibrate would be
        measured in days, so the features have to be in the artifact.
        """
        r = assess(
            compare(ch(A, "25%"), ch(B, "80%"), ch(D, None)), evidence_blocks=3
        )
        d = r.as_dict()["features"]
        assert set(d) >= {
            "contradiction",
            "coverage",
            "usable_channels",
            "applicable_channels",
            "evidence_blocks",
            "verdict",
            "deterministic_conflict",
        }
        assert d["evidence_blocks"] == 3


class TestFailureCases:
    def test_both_channels_dead_is_not_confidently_anything(self):
        r = assess(compare(ch(A, None), ch(B, None)), evidence_blocks=4)
        assert r.risk_score > 0.25
        assert r.facets["agreement"] is FacetStatus.PARTIAL

    def test_arbiter_abstention_does_not_reduce_risk(self):
        """An arbiter that declines has resolved nothing.

        Treating its silence as resolution would manufacture confidence out of a
        component explicitly refusing to provide any.
        """
        report = compare(ch(A, "25%"), ch(B, "80%"))
        without = assess(report, evidence_blocks=4).risk_score
        with_abstention = assess(
            report, evidence_blocks=4, arbiter_abstained=True
        ).risk_score
        assert with_abstention >= without

    def test_zero_evidence_beats_every_other_signal(self):
        """Nothing may rank above an ungrounded answer."""
        ungrounded = assess(
            compare(ch(A, "25%"), ch(B, "25%")), evidence_blocks=0
        ).risk_score
        worst_grounded = assess(
            compare(ch(A, "1 crore"), ch(B, "1 lakh"), ch(D, "5%")),
            evidence_blocks=4,
            arbiter_abstained=True,
        ).risk_score
        assert ungrounded >= worst_grounded

    def test_negative_and_zero_values_do_not_break_scoring(self):
        r = assess(
            compare(
                ChannelAnswer(A, parse_financial_value("(1,234)")),
                ChannelAnswer(B, parse_financial_value("-1234")),
            ),
            evidence_blocks=4,
        )
        assert r.risk_score < 0.25
        zero = assess(
            compare(ChannelAnswer(A, parse_financial_value("0")),
                    ChannelAnswer(B, parse_financial_value("0"))),
            evidence_blocks=4,
        )
        assert 0.0 <= zero.risk_score <= 1.0


class TestConsistencyReportExposesBothFactors:
    """Module 15 needs contradiction and coverage apart, not pre-multiplied."""

    def test_base_score_excludes_the_coverage_penalty(self):
        report = compare(ch(A, "25%"), ch(B, "25%"), ch(D, None))
        assert report.base_score > report.score
        assert report.coverage < 1.0
        assert report.score == pytest.approx(report.base_score * report.coverage)

    def test_shortfall_ignores_inapplicable_channels(self):
        lookup = compare(ch(A, "3,956"), ch(B, "3,956"), inapplicable())
        assert lookup.usable_shortfall == 0
        failed = compare(ch(A, "25%"), ch(B, "25%"), ch(D, None))
        assert failed.usable_shortfall == 1

    def test_decimal_values_are_untouched_by_scoring(self):
        """Scoring uses float; money must never round-trip through it."""
        report = compare(ch(A, "1,37,814"), ch(B, "1,37,814"))
        assert report.pairs[0].relative_difference == Decimal(0)
