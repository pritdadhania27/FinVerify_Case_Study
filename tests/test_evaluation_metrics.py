"""Tests for the evaluation framework (spec Module 22, EVALUATION.md §2-§6, §9).

These metrics decide every number this project publishes, and their failure mode
is silent: a wrong AUROC still lands between 0 and 1, still sorts arms into an
order, and still produces a table a reader will believe. So the tests here pin
the properties the conclusions depend on rather than sampling a few outputs -
direction, tie handling, undefined-vs-chance, and the exclusions that would
otherwise flatter a result.
"""

from decimal import Decimal

import pytest

from backend.core.financial_value import parse_financial_value
from evaluation.metrics.correctness import (
    TAU_PRIMARY,
    Mismatch,
    judge,
    sensitivity_table,
)
from evaluation.metrics.detection import (
    average_precision,
    contingency,
    evaluate_detection,
    metrics_at_threshold,
    risk_coverage_curve,
    roc_auc,
    select_threshold,
)
from evaluation.metrics.efficiency import (
    CallCost,
    QuestionCost,
    aggregate_efficiency,
    cost_matched_comparison,
)
from evaluation.metrics.qa import AnswerRecord, evaluate_qa
from evaluation.metrics.statistics import (
    bootstrap_ci,
    holm_bonferroni,
    paired_bootstrap_difference,
)


def fv(text: str):
    return parse_financial_value(text)


class TestCorrectnessRejectsNearMisses:
    """Sign and unit are categorical, not tolerable."""

    def test_a_loss_reported_as_a_profit_is_never_correct(self):
        result = judge(fv("1,234"), fv("(1,234)"))
        assert result.correct is False
        assert result.mismatch is Mismatch.SIGN

    def test_sign_is_checked_even_at_a_loose_tolerance(self):
        """Near zero the magnitudes converge while the meaning stays opposite."""
        result = judge(fv("0.001"), fv("-0.001"), tau=Decimal("100"))
        assert result.correct is False
        assert result.mismatch is Mismatch.SIGN

    def test_currency_mismatch_is_not_a_magnitude_question(self):
        prediction, gold = fv("$1,000"), fv("₹1,000")
        # Asserted rather than guarded: a parser change that stopped detecting
        # currency would otherwise turn this test into a no-op that still passes.
        assert (prediction.currency, gold.currency) == ("USD", "INR")
        assert judge(prediction, gold).mismatch is Mismatch.CURRENCY

    def test_a_missing_currency_label_is_not_a_contradiction(self):
        """Grading an answer on its formatting is not grading its correctness."""
        assert judge(fv("1,000"), fv("₹1,000")).correct is True

    def test_a_percentage_cannot_match_a_currency_amount(self):
        result = judge(fv("25%"), fv("₹25 crore"))
        assert result.correct is False
        assert result.mismatch is Mismatch.UNIT_KIND


class TestScaleTraps:
    """The highest-frequency source of plausible-looking wrong answers."""

    def test_crore_read_as_million_is_incorrect(self):
        assert judge(fv("1 crore"), fv("1 million")).correct is False

    def test_the_same_quantity_written_at_two_scales_matches(self):
        """100 lakh IS 1 crore; a grader that missed this would fail correct answers."""
        assert judge(fv("100 lakh"), fv("1 crore")).correct is True

    def test_rounding_to_published_precision_still_matches(self):
        """A report stating 12.4% for 12.4372% must not be graded wrong."""
        assert judge(fv("12.4372%"), fv("12.4%"), tau=TAU_PRIMARY).correct is True


class TestZeroGold:
    def test_zero_gold_uses_an_absolute_test(self):
        assert judge(fv("0"), fv("0")).correct is True

    def test_zero_gold_rejects_a_nonzero_prediction(self):
        result = judge(fv("1"), fv("0"))
        assert result.correct is False
        assert result.relative_error is None


class TestUngradableIsNotWrong:
    """A question with no gold is not a detection positive."""

    def test_missing_gold_is_flagged_rather_than_scored(self):
        result = judge(fv("25%"), None)
        assert result.mismatch is Mismatch.MISSING_GOLD

    def test_missing_prediction_is_incorrect_not_excluded(self):
        result = judge(None, fv("25%"))
        assert result.correct is False
        assert result.mismatch is Mismatch.MISSING_PREDICTION


class TestUnitAssumptionIsRecorded:
    """A bare number read under the gold convention must leave a trace."""

    def test_bare_number_read_as_a_percentage_is_marked(self):
        bare = fv("25")
        result = judge(bare, fv("25%"))
        assert result.correct is True
        assert result.unit_assumed is True

    def test_strict_mode_refuses_to_guess(self):
        result = judge(fv("25"), fv("25%"), assume_gold_convention=False)
        assert result.correct is False
        assert result.mismatch is Mismatch.UNIT_KIND


class TestGoldThatStatesNoUnitKind:
    """A gold record with no unit kind must not reject a prediction that has one.

    44 of the 115 validated FinVerify-IND answers carry a scale but no currency
    ("8,415.03 crore"), because the source tables say "in crore" without a
    symbol. Before RX-031 that gold rejected the most complete answer a model
    can give and accepted a barer one - grading the model on how little it said,
    and producing a units-failure rate that was an artefact of the label format.
    """

    def test_a_stated_currency_is_not_held_against_the_prediction(self):
        gold = fv("8415.03 crore")
        assert gold.unit_kind.value == "unknown", "the case under test stopped existing"
        assert judge(fv("8415.03 INR crore"), gold).correct is True

    def test_the_barer_answer_still_matches_too(self):
        """Both readings pass. The point is that neither is punished."""
        assert judge(fv("8415.03 crore"), fv("8415.03 crore")).correct is True

    def test_the_assumption_is_recorded_rather_than_silent(self):
        result = judge(fv("8415.03 INR crore"), fv("8415.03 crore"))
        assert any("gold states no unit kind" in line for line in result.trace)

    def test_magnitude_still_decides(self):
        """Skipping the dimension check must not let a wrong quantity through -
        canonical values are still compared."""
        assert judge(fv("8415.03 million"), fv("8415.03 crore")).correct is False

    def test_a_percentage_cannot_slip_past_an_unlabelled_gold(self):
        result = judge(fv("8415.03 %"), fv("8415.03 crore"))
        assert result.correct is False
        assert result.mismatch is Mismatch.MAGNITUDE

    def test_a_gold_that_does_state_its_kind_is_unaffected(self):
        assert judge(fv("25%"), fv("25 INR crore")).mismatch is Mismatch.UNIT_KIND


class TestSurfaceFormIsNotMeaning:
    """The same figure written six ways must grade the same way - and the two
    ways that are NOT the same figure must not collapse into each other.

    Every component here is tested in test_financial_value.py already. What is
    pinned here is the *composition*: RX-031 lived in the seam between a parser
    that was right and a grader that read its output wrongly, so a green parser
    suite proved nothing about the label a model actually receives. These go
    through judge() for that reason.
    """

    EQUIVALENT = ["14,062", "14062", "Rs 14,062", "14,062.0", "14,062.00", "  14,062  ", "14,062*"]

    @pytest.mark.parametrize("written", EQUIVALENT)
    def test_every_surface_form_grades_against_every_other(self, written):
        for other in self.EQUIVALENT:
            result = judge(fv(written), fv(other))
            assert result.correct is True, f"{written!r} vs {other!r}: {result.trace}"

    @pytest.mark.parametrize(
        "prediction,gold",
        [
            ("14,062", "14,062 crore"),  # a bare number is not a crore figure
            ("14,062 lakh", "14,062 crore"),  # nor is a lakh one
            ("140,620", "14,062"),  # the 10x error this project exists to catch
            ("1,406.2", "14,062"),
        ],
    )
    def test_scale_is_meaning_and_must_not_be_normalised_away(self, prediction, gold):
        """The danger in relaxing a units predicate is relaxing it into
        accepting everything. A scale error is the highest-frequency source of
        plausible-looking wrong answers in this domain, so it stays a mismatch."""
        assert judge(fv(prediction), fv(gold)).correct is False

    @pytest.mark.parametrize(
        "prediction,gold",
        [
            ("14,062 crore", "140.62 billion"),
            ("1 crore", "100 lakh"),
            ("1 crore", "10 million"),
            ("1,40,62,000", "14062000"),  # Indian grouping vs none
        ],
    )
    def test_one_quantity_at_two_scales_is_one_quantity(self, prediction, gold):
        assert judge(fv(prediction), fv(gold)).correct is True

    def test_a_ratio_gold_still_rejects_a_percent_reading_of_itself(self):
        """FI9e9260be is the only validated row where a bare gold meets a
        dimensionless metric, and it is a debt-to-equity ratio of 0.41. A
        prediction of "0.41%" is a hundredfold error, not a formatting variant,
        so the RX-031 branch must not reach through to accept it."""
        assert judge(fv("0.41%"), fv("0.41")).correct is False


class TestSensitivityTable:
    def test_a_looser_tolerance_never_lowers_accuracy(self):
        pairs = [(fv("100"), fv("100")), (fv("102"), fv("100")), (fv("140"), fv("100"))]
        table = sensitivity_table(pairs)
        values = [table[k] for k in sorted(table, key=float)]
        assert values == sorted(values)


class TestAurocDirection:
    """The inversion bug: a consistency score fed in unflipped yields 1 - AUROC.

    Both numbers look plausible on a slide. Only one of them says the detector
    works.
    """

    def test_a_perfect_risk_score_scores_one(self):
        # label 1 = the answer was wrong; higher score = riskier
        assert roc_auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0

    def test_a_perfectly_inverted_score_scores_zero(self):
        assert roc_auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0

    def test_the_two_are_complements(self):
        scores = [0.7, 0.2, 0.55, 0.9, 0.1]
        labels = [1, 0, 1, 0, 0]
        flipped = [1 - s for s in scores]
        assert roc_auc(scores, labels) + roc_auc(flipped, labels) == pytest.approx(1.0)


class TestTiesCountHalf:
    def test_a_constant_score_is_exactly_chance(self):
        assert roc_auc([0.5] * 6, [1, 0, 1, 0, 1, 0]) == 0.5

    def test_one_tied_pair_costs_half_a_concordance(self):
        """Two positives, two negatives; one positive tied with one negative."""
        auc = roc_auc([0.9, 0.5, 0.5, 0.1], [1, 1, 0, 0])
        assert auc == pytest.approx(0.875)

    def test_ordinal_bands_lose_resolution(self):
        """Why EVALUATION.md §5.1 demands a continuous score.

        The same ranking bucketed into three bands scores lower, because every
        within-band pair becomes a tie worth half.
        """
        scores = [0.95, 0.80, 0.60, 0.40, 0.20, 0.05]
        labels = [1, 1, 1, 0, 0, 0]
        banded = [1.0 if s >= 0.66 else 0.5 if s >= 0.33 else 0.0 for s in scores]
        assert roc_auc(scores, labels) == 1.0
        assert roc_auc(banded, labels) < 1.0


class TestUndefinedIsNotChance:
    """The H2 stratification will produce small strata. None is honest; 0.5 is not."""

    def test_no_errors_in_the_stratum_yields_none(self):
        assert roc_auc([0.1, 0.2, 0.3], [0, 0, 0]) is None

    def test_all_errors_in_the_stratum_yields_none(self):
        assert roc_auc([0.1, 0.2, 0.3], [1, 1, 1]) is None

    def test_the_report_says_why(self):
        report = evaluate_detection([0.1, 0.2], [0, 0])
        assert report.auroc is None
        assert any("undefined" in note for note in report.notes)

    def test_a_constant_score_is_called_out(self):
        report = evaluate_detection([0.4] * 4, [1, 0, 1, 0])
        assert report.auroc == 0.5
        assert any("constant" in note for note in report.notes)


class TestAveragePrecision:
    def test_perfect_ranking_scores_one(self):
        assert average_precision([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == pytest.approx(1.0)

    def test_undefined_without_both_classes(self):
        assert average_precision([0.9, 0.8], [1, 1]) is None

    def test_no_interpolation_inflation(self):
        """AP must not exceed the precision actually achievable at any point."""
        scores = [0.9, 0.8, 0.7, 0.6]
        labels = [0, 1, 0, 1]
        assert average_precision(scores, labels) < 1.0


class TestThresholdMetrics:
    def test_false_negative_rate_is_the_expensive_one(self):
        m = metrics_at_threshold([0.9, 0.1, 0.9, 0.1], [1, 1, 0, 0], threshold=0.5)
        assert m.true_positives == 1
        assert m.false_negatives == 1
        assert m.false_negative_rate == pytest.approx(0.5)
        assert m.false_positive_rate == pytest.approx(0.5)

    def test_threshold_selection_can_hold_a_recall_floor(self):
        scores = [0.9, 0.6, 0.4, 0.1]
        labels = [1, 1, 0, 0]
        threshold = select_threshold(scores, labels, objective="precision", min_recall=1.0)
        assert metrics_at_threshold(scores, labels, threshold).recall == 1.0

    def test_an_unreachable_recall_floor_fails_visibly(self):
        """Flagging everything meets recall; the precision column shows the cost."""
        scores = [0.5, 0.5]
        labels = [1, 0]
        threshold = select_threshold(scores, labels, min_recall=1.0)
        m = metrics_at_threshold(scores, labels, threshold)
        assert m.recall == 1.0
        assert m.precision == 0.5


class TestRiskCoverage:
    def test_a_perfect_ranking_has_zero_error_until_the_errors_start(self):
        curve = risk_coverage_curve([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
        assert curve.points[0][1] == 0.0
        assert curve.excess_aurc == pytest.approx(0.0)

    def test_excess_aurc_isolates_ranking_from_base_error_rate(self):
        """Two arms with the same base error rate, different rankings."""
        good = risk_coverage_curve([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
        bad = risk_coverage_curve([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1])
        assert good.optimal_aurc == bad.optimal_aurc
        assert bad.excess_aurc > good.excess_aurc


class TestContingency:
    def test_both_agree_wrong_is_its_own_cell(self):
        cells = contingency(
            agreed=[True, True, False, False],
            correct=[True, False, True, False],
        )
        assert cells["both_agree_wrong"] == 1
        assert cells["agree_correct"] == 1
        assert cells["disagree_correct"] == 1
        assert cells["disagree_wrong"] == 1


class TestQaMetrics:
    def _records(self):
        return [
            AnswerRecord("q1", fv("100"), fv("100"), program_executed=True),
            AnswerRecord("q2", fv("200"), fv("100"), program_executed=True),
            AnswerRecord("q3", None, fv("100"), abstained=True, program_executed=False),
            AnswerRecord("q4", None, fv("100"), abstained=False, program_executed=False),
        ]

    def test_abstention_is_counted_as_incorrect(self):
        report = evaluate_qa(self._records())
        assert report.n == 4
        assert report.numerical_accuracy == pytest.approx(0.25)
        assert report.abstention_rate == pytest.approx(0.25)

    def test_parse_failure_is_distinct_from_abstention(self):
        report = evaluate_qa(self._records())
        assert report.parse_failure_rate == pytest.approx(0.25)

    def test_execution_failure_counts_as_incorrect_not_excluded(self):
        report = evaluate_qa(self._records())
        assert report.execution_accuracy == pytest.approx(0.25)

    def test_an_arm_with_no_program_channel_has_no_execution_accuracy(self):
        report = evaluate_qa([AnswerRecord("q1", fv("100"), fv("100"))])
        assert report.execution_accuracy is None

    def test_ambiguous_questions_are_excluded_by_default(self):
        """D22: pooling them would make the headline a property of the dataset."""
        records = [
            AnswerRecord("q1", fv("100"), fv("100")),
            AnswerRecord("q2", fv("999"), fv("100"), ambiguous=True),
        ]
        assert evaluate_qa(records).numerical_accuracy == 1.0
        assert evaluate_qa(records, include_ambiguous=True).numerical_accuracy == 0.5

    def test_ungradable_records_do_not_silently_count_as_wrong(self):
        records = [
            AnswerRecord("q1", fv("100"), fv("100")),
            AnswerRecord("q2", fv("100"), None),
        ]
        report = evaluate_qa(records)
        assert report.n == 1
        assert report.numerical_accuracy == 1.0
        assert any("no gold value" in note for note in report.notes)


class TestStatistics:
    def test_bootstrap_interval_brackets_the_point_estimate(self):
        items = [1.0] * 30 + [0.0] * 70
        interval = bootstrap_ci(items, lambda xs: sum(xs) / len(xs), resamples=500)
        assert interval.low <= interval.point <= interval.high

    def test_bootstrap_is_deterministic_given_a_seed(self):
        items = list(range(50))

        def stat(xs):
            return sum(xs) / len(xs)

        a = bootstrap_ci(items, stat, resamples=200, seed=7)
        b = bootstrap_ci(items, stat, resamples=200, seed=7)
        assert (a.low, a.high) == (b.low, b.high)

    def test_undefined_resamples_are_counted_not_dropped_silently(self):
        """An AUROC resample can land with one class absent."""
        items = [1] + [0] * 9
        interval = bootstrap_ci(
            items,
            lambda xs: None if sum(xs) == 0 else sum(xs) / len(xs),
            resamples=300,
        )
        assert interval.undefined_resamples > 0

    def test_paired_bootstrap_detects_a_real_difference(self):
        items = list(range(100))
        ci, p = paired_bootstrap_difference(
            items,
            lambda xs: 1.0,
            lambda xs: 0.0,
            resamples=300,
        )
        assert ci.point == pytest.approx(1.0)
        assert ci.excludes_zero is True
        assert p < 0.05

    def test_paired_bootstrap_reports_no_difference_when_there_is_none(self):
        items = list(range(100))
        ci, p = paired_bootstrap_difference(
            items, lambda xs: 0.5, lambda xs: 0.5, resamples=300
        )
        assert ci.point == pytest.approx(0.0)
        assert ci.excludes_zero is False
        assert p == pytest.approx(1.0)

    def test_holm_is_stricter_than_uncorrected_alpha(self):
        results = holm_bonferroni({"H1": 0.001, "H2": 0.02, "H3": 0.03, "H4": 0.04, "H5": 0.6})
        assert results["H1"]["significant"] is True
        # 0.02 > 0.05/4, so it fails and every larger p-value fails with it.
        assert results["H2"]["significant"] is False
        assert results["H3"]["significant"] is False

    def test_holm_reports_every_hypothesis_including_failures(self):
        results = holm_bonferroni({"H1": 0.9, "H2": 0.8})
        assert set(results) == {"H1", "H2"}
        assert all(r["significant"] is False for r in results.values())


class TestEfficiencyRefusesToInventCost:
    """The free-tier zero must never be reported as the method's cost."""

    def _question(self, tokens: int, *, rate: bool = False) -> QuestionCost:
        return QuestionCost(
            question_id="q1",
            calls=(
                CallCost(
                    channel="natural",
                    provider="groq",
                    model="m",
                    total_tokens=tokens,
                    cost_usd=0.0,
                    equivalent_cost_usd=0.01 if rate else 0.0,
                    rate_established=rate,
                ),
            ),
            wall_clock_seconds=1.0,
        )

    def test_unestablished_rates_yield_none_not_zero(self):
        report = aggregate_efficiency([self._question(100)])
        assert report.equivalent_cost_usd is None
        assert any("UNESTABLISHED" in note for note in report.notes)

    def test_the_free_tier_zero_is_labelled(self):
        report = aggregate_efficiency([self._question(100)])
        assert report.cost_usd == 0.0
        assert "free tier" in report.as_dict()["cost_usd_note"]

    def test_partial_rate_coverage_is_a_lower_bound_not_a_total(self):
        report = aggregate_efficiency(
            [
                QuestionCost(
                    "q1",
                    calls=(
                        CallCost("a", "groq", "m", total_tokens=10, rate_established=True,
                                 equivalent_cost_usd=0.01),
                        CallCost("b", "groq", "m", total_tokens=10, rate_established=False),
                    ),
                )
            ]
        )
        assert any("lower bound" in note for note in report.notes)

    def test_cost_matching_reports_tokens_when_dollars_are_unavailable(self):
        left = aggregate_efficiency([self._question(200)])
        right = aggregate_efficiency([self._question(100)])
        comparison = cost_matched_comparison("P", left, "B5", right)
        assert comparison["token_ratio"] == pytest.approx(2.0)
        assert comparison["equivalent_cost"]["available"] is False


class TestFailureCases:
    def test_mismatched_lengths_are_rejected(self):
        with pytest.raises(ValueError):
            roc_auc([0.1, 0.2], [1])

    def test_non_binary_labels_are_rejected(self):
        with pytest.raises(ValueError):
            roc_auc([0.1, 0.2], [1, 2])

    def test_empty_input_does_not_crash_the_report(self):
        report = evaluate_detection([], [])
        assert report.n == 0
        assert report.auroc is None

    def test_empty_efficiency_input_does_not_crash(self):
        assert aggregate_efficiency([]).n == 0

    def test_selecting_a_threshold_from_nothing_is_an_error(self):
        with pytest.raises(ValueError):
            select_threshold([], [])
