"""Tests for the consistency engine (spec Modules 11 and 12)."""

from decimal import Decimal


from backend.core.financial_value import (
    FinancialValue,
    Scale,
    UnitKind,
    parse_financial_value,
)
from backend.verification.consistency import (
    ChannelAnswer,
    DisagreementType,
    Verdict,
    compare,
)


def ch(name: str, text: str | None, **kw):
    """Build a channel answer; text=None means the channel produced nothing."""
    if text is None:
        return ChannelAnswer(name, None, available=False, failure_reason=kw.get("reason", "failed"))
    return ChannelAnswer(name, parse_financial_value(text, **kw))


A, B, D = "channel_a", "channel_b", "deterministic"


class TestAgreement:
    def test_identical_answers_agree(self):
        r = compare(ch(A, "25%"), ch(B, "25%"))
        assert r.verdict is Verdict.AGREE
        assert r.score > 0.85

    def test_agreement_within_tolerance(self):
        """Rounding to published precision must not read as disagreement."""
        r = compare(ch(A, "25.00%"), ch(B, "25.02%"))
        assert r.verdict is Verdict.AGREE

    def test_agreement_across_scales(self):
        """1 crore and 10 million are the same quantity."""
        r = compare(ch(A, "1 crore"), ch(B, "10 million"))
        assert r.verdict is Verdict.AGREE

    def test_coverage_measures_checks_ACHIEVED_over_checks_POSSIBLE(self):
        """Changed deliberately; the previous assertion was `three > two`.

        The coverage penalty now expresses "of the checks that were possible,
        how many succeeded", not "how many channels answered in absolute terms".
        Under the old rule a lookup was capped at 0.90 because the deterministic
        channel abstains BY DESIGN - there is no arithmetic on a lookup to check
        - while a computed question reached 1.00. Lookups are the majority of
        financial QA questions, so the score was partly encoding *"is this a
        lookup?"* rather than *"is this wrong?"*, and it ranked every correct
        lookup below every correct computed answer. Penalising a question for a
        check that could never apply is indefensible.

        How many checks were POSSIBLE is still recorded, separately, in
        `available_channels` - so calibration (Module 15) can use it as its own
        feature instead of having it pre-mixed into the score by an unvalidated
        constant.
        """
        two = compare(ch(A, "25%"), ch(B, "25%"))
        three = compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%"))
        assert two.score == three.score == 1.0
        assert two.available_channels == 2
        assert three.available_channels == 3

    def test_a_channel_that_COULD_have_answered_and_did_not_still_costs(self):
        """The other half of the distinction: a real shortfall of evidence."""
        failed = ChannelAnswer(D, None, available=False, failure_reason="could not bind")
        report = compare(ch(A, "25%"), ch(B, "25%"), failed)
        assert report.score < 1.0

    def test_a_channel_that_could_not_apply_costs_nothing(self):
        inapplicable = ChannelAnswer(
            D, None, available=False, applicable=False,
            failure_reason="lookup: no arithmetic to verify",
        )
        report = compare(ch(A, "25%"), ch(B, "25%"), inapplicable)
        assert report.score == 1.0


class TestDisagreementTyping:
    """Spec Module 12: the *type* of disagreement is the diagnostic payload."""

    def test_scale_mismatch_is_named_not_just_flagged(self):
        """The crore/million confusion - the signature financial error."""
        r = compare(ch(A, "1 crore"), ch(B, "1 million"))
        assert r.verdict is Verdict.DISAGREE
        assert DisagreementType.SCALE_MISMATCH in r.disagreements

    def test_lakh_crore_confusion_is_100x(self):
        r = compare(ch(A, "1 crore"), ch(B, "1 lakh"))
        assert DisagreementType.SCALE_MISMATCH in r.disagreements

    def test_sign_mismatch_profit_vs_loss(self):
        r = compare(ch(A, "100 crore"), ch(B, "(100) crore"))
        assert r.verdict is Verdict.DISAGREE
        assert DisagreementType.SIGN_MISMATCH in r.disagreements

    def test_unit_kind_mismatch_percent_vs_currency(self):
        r = compare(ch(A, "25%"), ch(B, "₹25 crore"))
        assert DisagreementType.UNIT_KIND_MISMATCH in r.disagreements

    def test_currency_mismatch(self):
        r = compare(ch(A, "₹100 crore"), ch(B, "$100 crore"))
        assert DisagreementType.CURRENCY_MISMATCH in r.disagreements

    def test_plain_magnitude_mismatch(self):
        r = compare(ch(A, "100 crore"), ch(B, "137 crore"))
        assert DisagreementType.MAGNITUDE_MISMATCH in r.disagreements
        assert DisagreementType.SCALE_MISMATCH not in r.disagreements

    def test_sign_is_checked_before_magnitude(self):
        """A profit reported as a loss is categorical, not merely a big gap."""
        r = compare(ch(A, "100 crore"), ch(B, "(101) crore"))
        assert DisagreementType.SIGN_MISMATCH in r.disagreements


class TestBothAgreeButWrong:
    """EVALUATION.md 5.4: the blind spot of channel agreement.

    Both channels read the same evidence, so a retrieval error makes both compute
    faithfully from the wrong number and agree. Only the deterministic verifier
    can overrule them.
    """

    def test_deterministic_overrules_unanimous_channels(self):
        r = compare(ch(A, "30%"), ch(B, "30%"), ch(D, "25%"))
        assert r.verdict is Verdict.DISAGREE
        assert DisagreementType.DETERMINISTIC_CONFLICT in r.disagreements

    def test_overruled_agreement_scores_far_below_true_agreement(self):
        overruled = compare(ch(A, "30%"), ch(B, "30%"), ch(D, "25%"))
        genuine = compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%"))
        assert overruled.score < genuine.score / 2

    def test_the_conflict_is_explained_in_notes(self):
        r = compare(ch(A, "30%"), ch(B, "30%"), ch(D, "25%"))
        assert any("both" in n.lower() and "wrong" in n.lower() for n in r.notes)

    def test_no_conflict_flag_when_channels_already_disagree(self):
        """DETERMINISTIC_CONFLICT specifically means 'overruled a consensus'."""
        r = compare(ch(A, "30%"), ch(B, "40%"), ch(D, "25%"))
        assert DisagreementType.DETERMINISTIC_CONFLICT not in r.disagreements


class TestUnavailableChannels:
    """Spec section 38 failure cases: execution failure, model timeout."""

    def test_program_channel_failure_is_uncertain_not_disagreement(self):
        """A crashed sandbox is missing evidence, not contradicting evidence."""
        r = compare(ch(A, "25%"), ch(B, None, reason="sandbox execution failed"))
        assert r.verdict is Verdict.UNCERTAIN
        assert DisagreementType.CHANNEL_UNAVAILABLE in r.disagreements

    def test_uncertain_does_not_manufacture_confidence(self):
        r = compare(ch(A, "25%"), ch(B, None))
        assert r.score <= 0.5

    def test_failure_reason_is_recorded(self):
        r = compare(ch(A, "25%"), ch(B, None, reason="model timeout"))
        assert any("timeout" in n for n in r.notes)

    def test_both_channels_missing(self):
        r = compare(ch(A, None), ch(B, None))
        assert r.verdict is Verdict.UNCERTAIN

    def test_deterministic_can_substitute_for_a_failed_channel(self):
        """Two of three still permits a cross-check."""
        r = compare(ch(A, "25%"), ch(B, None, reason="exec failed"), ch(D, "25%"))
        assert r.verdict is Verdict.AGREE
        assert r.available_channels == 2

    def test_reduced_agreement_scores_below_full_agreement(self):
        reduced = compare(ch(A, "25%"), ch(B, None), ch(D, "25%"))
        full = compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%"))
        assert reduced.score < full.score


class TestContinuousScore:
    """AUROC requires a continuous score, not three ordinal buckets."""

    def test_score_is_a_float_in_unit_interval(self):
        r = compare(ch(A, "25%"), ch(B, "27%"))
        assert isinstance(r.score, float)
        assert 0.0 <= r.score <= 1.0

    def test_score_decreases_monotonically_with_divergence(self):
        scores = [
            compare(ch(A, "100 crore"), ch(B, f"{v} crore")).score
            for v in ("100", "101", "110", "150", "300")
        ]
        assert scores == sorted(scores, reverse=True), scores

    def test_many_distinct_score_values_exist(self):
        """A three-bucket score would collapse these into three values."""
        scores = {
            compare(ch(A, "100 crore"), ch(B, f"{100 + i} crore")).score
            for i in range(1, 25)
        }
        assert len(scores) > 10

    def test_band_is_derived_from_the_score_not_the_source(self):
        assert compare(ch(A, "25%"), ch(B, "25%")).band() == "HIGH"
        assert compare(ch(A, "100 crore"), ch(B, "300 crore")).band() == "LOW"
        assert compare(ch(A, "25%"), ch(B, None)).band() == "UNCERTAIN"


class TestPairwiseDetail:
    def test_all_pairs_are_reported(self):
        r = compare(ch(A, "25%"), ch(B, "25%"), ch(D, "25%"))
        assert len(r.pairs) == 3

    def test_relative_difference_is_exposed(self):
        r = compare(ch(A, "100 crore"), ch(B, "200 crore"))
        pair = r.pairs[0]
        assert pair.relative_difference == Decimal("0.5")

    def test_scale_mismatch_note_names_the_power(self):
        r = compare(ch(A, "1 crore"), ch(B, "1 million"))
        assert any("10^" in p.note for p in r.pairs)


class TestZero:
    def test_two_zeros_agree(self):
        assert compare(ch(A, "0"), ch(B, "0")).verdict is Verdict.AGREE

    def test_zero_versus_nonzero_disagrees(self):
        assert compare(ch(A, "0"), ch(B, "100 crore")).verdict is Verdict.DISAGREE

    def test_zero_is_not_treated_as_a_scale_multiple(self):
        r = compare(ch(A, "0"), ch(B, "100 crore"))
        assert DisagreementType.SCALE_MISMATCH not in r.disagreements


class TestDimensionCompatibility:
    """A percentage and a ratio expressing the same quantity must compare EQUAL.

    `FinancialValue.canonical()` exists precisely to make 29.77% and 0.2977
    comparable, but `_compare_pair` rejected the pair on its unit-kind LABEL
    before canonical() was ever called. A return-on-equity question where all
    three channels agreed numerically (0.2977, 0.2977, 0.2967) was therefore
    scored DISAGREE at 0.000 - a false positive in the detector itself, on
    exactly the question type the detector exists for.

    In the evaluation that inflates the disagreement rate and depresses AUROC:
    the method looks worse than it is for a reason having nothing to do with the
    models. Found by running the three-channel slice, not by any test.
    """

    @staticmethod
    def _fv(amount, kind, currency=None):
        return FinancialValue(Decimal(amount), Scale.UNIT, kind, currency=currency)

    def _answer(self, name, amount, kind, currency=None):
        return ChannelAnswer(name, self._fv(amount, kind, currency))

    def test_percent_and_ratio_of_the_same_quantity_agree(self):
        report = compare(
            self._answer("natural", "29.77", UnitKind.PERCENT),
            self._answer("deterministic", "0.29672", UnitKind.RATIO),
        )
        assert report.verdict is Verdict.AGREE
        assert report.pairs[0].disagreement is DisagreementType.NONE

    def test_a_genuine_rate_versus_multiple_confusion_is_still_caught(self):
        """"29.77 as a ratio" and "29.77 percent" are a factor of 100 apart. The
        fix must not buy the false positive back by hiding a real error."""
        report = compare(
            self._answer("natural", "29.77", UnitKind.PERCENT),
            self._answer("deterministic", "29.77", UnitKind.RATIO),
        )
        assert report.verdict is Verdict.DISAGREE
        assert report.pairs[0].disagreement is DisagreementType.SCALE_MISMATCH

    def test_currency_against_a_rate_remains_incompatible(self):
        """A rupee amount is not a rate, whatever the magnitudes are."""
        report = compare(
            self._answer("natural", "29.77", UnitKind.PERCENT),
            self._answer("program", "3956", UnitKind.CURRENCY, "INR"),
        )
        assert report.pairs[0].disagreement is DisagreementType.UNIT_KIND_MISMATCH

    def test_a_count_is_not_a_currency(self):
        report = compare(
            self._answer("natural", "100", UnitKind.COUNT),
            self._answer("program", "100", UnitKind.CURRENCY, "INR"),
        )
        assert report.pairs[0].disagreement is DisagreementType.UNIT_KIND_MISMATCH

    def test_unknown_units_never_manufacture_a_disagreement(self):
        """A channel that did not state its units has not contradicted anything;
        refusing to compare it would turn a missing label into a disagreement."""
        for kind in (UnitKind.PERCENT, UnitKind.CURRENCY, UnitKind.RATIO, UnitKind.COUNT):
            report = compare(
                self._answer("natural", "0.2977", UnitKind.UNKNOWN),
                self._answer("program", "0.2977", kind),
            )
            assert report.pairs[0].disagreement is not DisagreementType.UNIT_KIND_MISMATCH


class TestScoreOrdering:
    """The score feeds AUROC, so what it ranks by has to be stated and pinned.

    It is NOT a distance. It encodes two things at once: how far apart the
    channels are, AND how diagnosable the gap is. A clean power-of-ten gap is the
    signature of a scale-unit confusion - the project's headline silent-error
    class - so it is scored at the floor even though an 11x gap is numerically
    larger. Measured:

        ratio   9x -> 0.005 (magnitude)
        ratio  10x -> 0.000 (scale mismatch, maximum suspicion)
        ratio  11x -> 0.005 (magnitude)

    That is non-monotone in the RATIO and monotone in nothing in particular, and
    both points sit far below the agreement band, so any thresholded decision
    puts them on the same side. It is recorded here so the behaviour is
    deliberate rather than accidental, and so nobody "fixes" the scale case into
    looking safer than an arbitrary wrong answer.

    Calibrating these weights against real labelled data is Module 15's job
    (`EVALUATION.md` 5.2). Tuning them now against a synthetic probe would be
    exactly the optimise-against-what-you-looked-at failure the project's
    measurement discipline exists to prevent.
    """

    @staticmethod
    def _at(right):
        def fv(x):
            return FinancialValue(Decimal(str(x)), Scale.UNIT, UnitKind.CURRENCY, currency="INR")

        return compare(ChannelAnswer("a", fv(1000)), ChannelAnswer("b", fv(right)))

    def test_agreement_outranks_every_disagreement(self):
        agree = self._at(1000).score
        for right in (1010, 1100, 2000, 10000, 11000, 1000000):
            assert self._at(right).score < agree

    def test_a_gap_of_a_few_percent_is_medium_risk_not_maximum(self):
        """1% apart is twice the tolerance - wrong, but not the same event as
        being 10x out. Collapsing the two would throw away the gradation AUROC
        needs to rank on."""
        assert 0.3 < self._at(1010).score < 0.6
        assert self._at(1100).score < 0.1

    def test_the_score_decays_as_disagreement_grows(self):
        """Within the magnitude branch it must be monotone - that is the part
        AUROC actually ranks on."""
        scores = [self._at(r).score for r in (1010, 1050, 1100, 1500, 2000)]
        assert scores == sorted(scores, reverse=True), scores

    def test_a_clean_power_of_ten_is_scored_at_the_floor(self):
        report = self._at(10000)
        assert report.pairs[0].disagreement is DisagreementType.SCALE_MISMATCH
        assert report.score == 0.0

    def test_a_near_power_of_ten_is_not_treated_as_a_scale_error(self):
        """11x is not a unit confusion, and labelling it one would misdirect the
        error taxonomy."""
        assert self._at(11000).pairs[0].disagreement is DisagreementType.MAGNITUDE_MISMATCH

    def test_the_relative_difference_saturates(self):
        """Symmetric relative difference is bounded by 1, so the score cannot
        distinguish 100x from 1000x. Recorded because it caps what the score can
        express, not because it is wrong."""
        assert self._at(100_000).pairs[0].relative_difference < 1
        assert self._at(1_000_000).pairs[0].relative_difference < 1
