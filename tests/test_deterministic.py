"""Tests for deterministic numerical verification (spec Module 10).

Includes the failure cases spec section 38 requires: zero denominator, missing
evidence, incompatible units. A verifier that only handles the happy path
overstates its own coverage.
"""

from decimal import Decimal

import pytest

from backend.core.financial_value import Scale, UnitKind, parse_financial_value
from backend.verification.deterministic import (
    Operation,
    calculate,
)


def v(text: str, **kw):
    parsed = parse_financial_value(text, **kw)
    assert parsed is not None, f"fixture failed to parse: {text!r}"
    return parsed


class TestCrossScaleArithmetic:
    """The highest-value behaviour: combining values written at different scales."""

    def test_crore_and_million_add_correctly(self):
        """1 crore + 10 million = 20,000,000, not 11 of something."""
        r = calculate(Operation.SUM, [v("1 crore"), v("10 million")])
        assert r.ok
        assert r.value == Decimal("20000000")

    def test_ratio_across_scales_is_dimensionless(self):
        """1 crore / 10 million == 1. Raw magnitudes would give 1/10."""
        r = calculate(Operation.RATIO, [v("1 crore"), v("10 million")])
        assert r.ok
        assert r.value == Decimal("1")

    def test_lakh_and_crore_difference(self):
        r = calculate(Operation.DIFFERENCE, [v("1 crore"), v("50 lakh")])
        assert r.ok
        assert r.value == Decimal("5000000")


class TestPercentageChange:
    def test_simple_growth(self):
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("100 crore"), v("125 crore")])
        assert r.ok
        assert r.value == Decimal("0.25")
        assert r.as_percent() == Decimal("25")
        assert r.unit_kind is UnitKind.PERCENT

    def test_decline_is_negative(self):
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("200 crore"), v("150 crore")])
        assert r.as_percent() == Decimal("-25")

    def test_growth_across_different_scales(self):
        """Prior year in crore, current in million - a real filing pattern."""
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("10 crore"), v("125 million")])
        assert r.ok
        assert r.as_percent() == Decimal("25")

    def test_zero_base_is_a_structured_failure_not_a_crash(self):
        """Spec section 38 failure case: zero denominator."""
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("0"), v("100 crore")])
        assert not r.ok
        assert r.value is None
        assert "undefined" in r.error.lower()

    def test_negative_base_is_computed_but_flagged(self):
        """Growth from a prior-year loss: real, and sign-ambiguous."""
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("(50) crore"), v("100 crore")])
        assert r.ok
        assert any("WARNING" in s and "negative" in s for s in r.steps)


class TestMarginAndRatio:
    def test_margin(self):
        r = calculate(Operation.MARGIN, [v("250 crore"), v("1000 crore")])
        assert r.ok
        assert r.as_percent() == Decimal("25")

    def test_margin_zero_denominator(self):
        """Spec section 38 failure case."""
        r = calculate(Operation.MARGIN, [v("250 crore"), v("0")])
        assert not r.ok
        assert "zero" in r.error.lower()

    def test_ratio_zero_denominator(self):
        r = calculate(Operation.RATIO, [v("250 crore"), v("0")])
        assert not r.ok
        assert "zero" in r.error.lower()

    def test_negative_margin_from_a_loss(self):
        r = calculate(Operation.MARGIN, [v("(100) crore"), v("1000 crore")])
        assert r.ok
        assert r.as_percent() == Decimal("-10")


class TestCAGR:
    def test_doubling_over_one_year_is_100_percent(self):
        r = calculate(Operation.CAGR, [v("100 crore"), v("200 crore")], years=1)
        assert r.ok
        assert r.as_percent() == Decimal("100")

    def test_known_compound_rate(self):
        """100 -> 121 over 2 years is exactly 10% compounded."""
        r = calculate(Operation.CAGR, [v("100 crore"), v("121 crore")], years=2)
        assert r.ok
        assert abs(r.as_percent() - Decimal("10")) < Decimal("0.0000001")

    def test_flat_growth_is_zero(self):
        r = calculate(Operation.CAGR, [v("100 crore"), v("100 crore")], years=5)
        assert r.ok
        assert abs(r.value) < Decimal("0.0000001")

    def test_negative_start_is_undefined_not_silently_wrong(self):
        """A company recovering from a loss has no real compound growth rate."""
        r = calculate(Operation.CAGR, [v("(50) crore"), v("100 crore")], years=3)
        assert not r.ok
        assert "undefined" in r.error.lower()

    def test_zero_start_is_undefined(self):
        r = calculate(Operation.CAGR, [v("0"), v("100 crore")], years=3)
        assert not r.ok

    def test_missing_years_is_a_failure_not_an_assumption(self):
        r = calculate(Operation.CAGR, [v("100 crore"), v("200 crore")])
        assert not r.ok
        assert "years" in r.error.lower()

    def test_zero_years(self):
        r = calculate(Operation.CAGR, [v("100 crore"), v("200 crore")], years=0)
        assert not r.ok


class TestUnitCompatibility:
    def test_cannot_add_percent_to_currency(self):
        r = calculate(Operation.SUM, [v("25%"), v("100 crore")])
        assert not r.ok
        assert "incompatible" in r.error.lower()

    def test_cannot_add_different_currencies(self):
        """Requires an exchange rate, which this module does not invent."""
        r = calculate(Operation.SUM, [v("₹100 crore"), v("$50 million")])
        assert not r.ok
        assert "currenc" in r.error.lower()

    def test_same_currency_adds_fine(self):
        r = calculate(Operation.SUM, [v("₹100 crore"), v("₹50 crore")])
        assert r.ok
        assert r.currency == "INR"

    def test_unknown_unit_kind_is_tolerated(self):
        """Un-annotated table cells are normal; they should not block arithmetic."""
        r = calculate(Operation.SUM, [v("100", context_scale=Scale.CRORE), v("₹50 crore")])
        assert r.ok


class TestMissingEvidence:
    """Spec section 38 failure case: missing evidence must not become zero."""

    def test_none_operand_fails_cleanly(self):
        r = calculate(Operation.SUM, [v("100 crore"), None])
        assert not r.ok
        assert "missing" in r.error.lower()

    def test_empty_operands_fails(self):
        assert not calculate(Operation.SUM, []).ok

    def test_wrong_operand_count_for_difference(self):
        r = calculate(Operation.DIFFERENCE, [v("1 crore"), v("2 crore"), v("3 crore")])
        assert not r.ok
        assert "exactly 2" in r.error


class TestDeterminism:
    """The property that makes this a verification authority."""

    def test_repeated_calls_are_identical(self):
        args = [v("1234.56 crore"), v("987.65 crore")]
        results = [calculate(Operation.PERCENTAGE_CHANGE, args).value for _ in range(20)]
        assert len(set(results)) == 1

    def test_no_floating_point_drift(self):
        """0.1 + 0.2 == 0.3 exactly. With floats it would not."""
        r = calculate(Operation.SUM, [v("0.1"), v("0.2")])
        assert r.value == Decimal("0.3")

    def test_precision_survives_a_long_sum(self):
        r = calculate(Operation.SUM, [v("0.01") for _ in range(100)])
        assert r.value == Decimal("1.00")


class TestTraceability:
    def test_steps_are_recorded(self):
        r = calculate(Operation.PERCENTAGE_CHANGE, [v("100 crore"), v("125 crore")])
        assert r.steps
        assert any("=" in s for s in r.steps)

    def test_failures_also_carry_a_reason(self):
        r = calculate(Operation.MARGIN, [v("1 crore"), v("0")])
        assert r.error is not None and r.error.strip()


class TestAverageAndProduct:
    def test_average(self):
        r = calculate(Operation.AVERAGE, [v("100 crore"), v("200 crore"), v("300 crore")])
        assert r.ok
        assert r.value == Decimal("2000000000")

    def test_average_across_scales(self):
        r = calculate(Operation.AVERAGE, [v("1 crore"), v("30 million")])
        assert r.ok
        assert r.value == Decimal("20000000")

    def test_product(self):
        r = calculate(Operation.PRODUCT, [v("2"), v("3"), v("4")])
        assert r.ok
        assert r.value == Decimal("24")


class TestPercentMagnitudeMixing:
    """Regression: a percentage summed with a magnitude produced a clean,
    meaningless number (25% + 100 crore -> 1000000000.25).

    The UNKNOWN-tolerance rule is right for un-annotated magnitude cells but must
    not extend to percentages, which are dimensionless.
    """

    @pytest.mark.parametrize("op", [Operation.SUM, Operation.DIFFERENCE, Operation.AVERAGE])
    def test_percent_never_combines_additively_with_a_magnitude(self, op):
        r = calculate(op, [v("25%"), v("100 crore")])
        assert not r.ok
        assert "percentage" in r.error.lower()

    def test_percent_with_untyped_number_also_rejected(self):
        r = calculate(Operation.SUM, [v("25%"), v("1234")])
        assert not r.ok

    def test_two_percentages_may_be_combined(self):
        """Summing margins across segments is legitimate."""
        r = calculate(Operation.SUM, [v("10%"), v("15%")])
        assert r.ok
        assert r.value == Decimal("0.25")

    def test_percent_may_still_be_a_ratio_operand(self):
        """The prohibition is on additive mixing, not on all use together."""
        assert calculate(Operation.RATIO, [v("50%"), v("25%")]).ok
