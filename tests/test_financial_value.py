"""Tests for financial value parsing (spec Module 5).

Every case here is a pattern that appears in real Indian filings. The scale and
sign cases matter most: those failures produce answers that are wrong by orders
of magnitude while looking completely reasonable.
"""

from decimal import Decimal

import pytest

from backend.core.financial_value import (
    ParseWarning,
    Scale,
    UnitKind,
    parse_financial_value,
)


class TestScale:
    """Scale errors are 10x-100x wrong answers that look plausible."""

    @pytest.mark.parametrize(
        "text,expected_scale,expected_amount",
        [
            ("1,234 crore", Scale.CRORE, Decimal("1234")),
            ("1,234 cr", Scale.CRORE, Decimal("1234")),
            ("56.7 lakh", Scale.LAKH, Decimal("56.7")),
            ("56.7 lakhs", Scale.LAKH, Decimal("56.7")),
            ("12 lac", Scale.LAKH, Decimal("12")),
            ("2.4 billion", Scale.BILLION, Decimal("2.4")),
            ("2.4 bn", Scale.BILLION, Decimal("2.4")),
            ("890 million", Scale.MILLION, Decimal("890")),
            ("890 mn", Scale.MILLION, Decimal("890")),
            ("45 thousand", Scale.THOUSAND, Decimal("45")),
        ],
    )
    def test_scale_words(self, text, expected_scale, expected_amount):
        v = parse_financial_value(text)
        assert v is not None
        assert v.scale is expected_scale
        assert v.amount == expected_amount

    def test_canonical_applies_multiplier_exactly(self):
        v = parse_financial_value("2.4 billion")
        assert v.canonical() == Decimal("2400000000")

    def test_the_spec_example_billion_to_million(self):
        """Spec section 13: 2.4 billion -> 2400 million."""
        v = parse_financial_value("2.4 billion")
        assert v.rescaled_to(Scale.MILLION) == Decimal("2400")

    def test_crore_to_million_conversion(self):
        """The conversion an Indian-filing question actually needs."""
        v = parse_financial_value("100 crore")
        assert v.canonical() == Decimal("1000000000")
        assert v.rescaled_to(Scale.MILLION) == Decimal("1000")

    def test_longer_token_wins_over_shorter_substring(self):
        """'crore' must not be shadowed by the 'cr' abbreviation."""
        assert parse_financial_value("5 crore").scale is Scale.CRORE

    def test_scale_inherited_from_table_header_is_flagged(self):
        """Tables state scale once in a header and omit it from every cell.

        Inheriting is correct behaviour, but the result must announce that the
        scale was assumed rather than read.
        """
        v = parse_financial_value("1,234", context_scale=Scale.CRORE)
        assert v.scale is Scale.CRORE
        assert ParseWarning.SCALE_INHERITED_FROM_CONTEXT in v.warnings

    def test_missing_scale_is_flagged_not_silently_assumed(self):
        v = parse_financial_value("1,234")
        assert v.scale is Scale.UNIT
        assert ParseWarning.NO_SCALE_DETERMINED in v.warnings

    def test_explicit_scale_beats_context(self):
        v = parse_financial_value("5 lakh", context_scale=Scale.CRORE)
        assert v.scale is Scale.LAKH
        assert ParseWarning.SCALE_INHERITED_FROM_CONTEXT not in v.warnings


class TestSign:
    """A profit reported as a loss is not a near-miss."""

    def test_accounting_parentheses_are_negative(self):
        assert parse_financial_value("(1,234)").amount == Decimal("-1234")

    def test_parenthesised_with_currency_and_scale(self):
        v = parse_financial_value("₹ (1,234.56) crore")
        assert v.amount == Decimal("-1234.56")
        assert v.currency == "INR"
        assert v.scale is Scale.CRORE

    def test_leading_minus(self):
        assert parse_financial_value("-500").amount == Decimal("-500")

    @pytest.mark.parametrize("dash", ["−", "–", "—"])
    def test_unicode_minus_variants_from_pdf_extraction(self, dash):
        """PDF text layers emit U+2212 and dashes rather than ASCII hyphen."""
        assert parse_financial_value(f"{dash}500").amount == Decimal("-500")

    def test_lettered_parentheses_are_not_negation(self):
        """'(a)' is a footnote marker, not an accounting negative."""
        v = parse_financial_value("1,234(a)")
        assert v.amount == Decimal("1234")
        assert ParseWarning.FOOTNOTE_MARKER_STRIPPED in v.warnings


class TestPercent:
    def test_percent_becomes_fraction_in_canonical_form(self):
        """Spec section 13: 25% -> 0.25."""
        v = parse_financial_value("25%")
        assert v.unit_kind is UnitKind.PERCENT
        assert v.amount == Decimal("25")
        assert v.canonical() == Decimal("0.25")

    @pytest.mark.parametrize("text", ["25 percent", "25 per cent"])
    def test_spelled_out_percent(self, text):
        assert parse_financial_value(text).unit_kind is UnitKind.PERCENT

    def test_negative_percent(self):
        v = parse_financial_value("(12.5)%")
        assert v.canonical() == Decimal("-0.125")

    def test_percent_has_no_magnitude_scale(self):
        with pytest.raises(ValueError):
            parse_financial_value("25%").rescaled_to(Scale.CRORE)

    def test_percent_does_not_warn_about_missing_scale(self):
        """A percentage legitimately has no scale; warning would be noise."""
        assert ParseWarning.NO_SCALE_DETERMINED not in parse_financial_value("25%").warnings


class TestDigitGrouping:
    def test_western_grouping(self):
        assert parse_financial_value("1,234,567").amount == Decimal("1234567")

    def test_indian_lakh_crore_grouping(self):
        """Indian filings write 12,34,567 for 1234567."""
        v = parse_financial_value("12,34,567")
        assert v.amount == Decimal("1234567")
        assert ParseWarning.IRREGULAR_GROUPING not in v.warnings

    def test_irregular_grouping_is_flagged(self):
        """Unrecognised grouping often means extraction merged two cells."""
        v = parse_financial_value("1,23456,7")
        assert ParseWarning.IRREGULAR_GROUPING in v.warnings

    def test_decimals_preserved_exactly(self):
        """Decimal, not float - 1234.56 must not become 1234.5599999999999."""
        v = parse_financial_value("1,234.56")
        assert v.amount == Decimal("1234.56")
        assert str(v.amount) == "1234.56"


class TestCurrency:
    @pytest.mark.parametrize(
        "text,code",
        [("₹ 500", "INR"), ("Rs. 500", "INR"), ("INR 500", "INR"),
         ("$500", "USD"), ("USD 500", "USD"), ("€500", "EUR"), ("£500", "GBP")],
    )
    def test_currency_detection(self, text, code):
        assert parse_financial_value(text).currency == code

    def test_currency_implies_currency_unit_kind(self):
        assert parse_financial_value("₹ 500").unit_kind is UnitKind.CURRENCY

    def test_context_currency_used_when_absent(self):
        assert parse_financial_value("500", context_currency="INR").currency == "INR"


class TestPdfArtifacts:
    def test_non_breaking_space(self):
        assert parse_financial_value("1 234 crore") is not None

    @pytest.mark.parametrize("marker", ["*", "**", "#", "†"])
    def test_footnote_markers_stripped(self, marker):
        v = parse_financial_value(f"1,234{marker}")
        assert v.amount == Decimal("1234")
        assert ParseWarning.FOOTNOTE_MARKER_STRIPPED in v.warnings

    def test_surrounding_whitespace(self):
        assert parse_financial_value("   1,234 crore  ").amount == Decimal("1234")


class TestNonValues:
    """Returning None matters: a cell with no number must not become 0."""

    @pytest.mark.parametrize("text", ["", "   ", "-", "N/A", "not applicable", None])
    def test_returns_none_rather_than_zero(self, text):
        assert parse_financial_value(text) is None

    def test_zero_is_a_real_value_not_a_non_value(self):
        v = parse_financial_value("0")
        assert v is not None
        assert v.amount == Decimal("0")


class TestTraceability:
    """Spec section 13: all transformations must be traceable."""

    def test_every_transformation_is_recorded(self):
        v = parse_financial_value("₹ (1,234.56) crore*")
        joined = " | ".join(v.trace).lower()
        assert "currency" in joined
        assert "scale" in joined
        assert "negative" in joined
        assert "footnote" in joined

    def test_raw_text_is_preserved(self):
        raw = "₹ (1,234.56) crore"
        assert parse_financial_value(raw).raw_text == raw

    def test_inherited_scale_is_explained_in_trace(self):
        v = parse_financial_value("1,234", context_scale=Scale.CRORE)
        assert any("inherited" in step for step in v.trace)


class TestRealFilingPatterns:
    """Composite cases as they actually appear in annual reports."""

    @pytest.mark.parametrize(
        "text,canonical",
        [
            ("₹ 1,23,456 crore", Decimal("1234560000000")),
            ("Rs. (2,345.67) crore", Decimal("-23456700000")),
            ("12.5%", Decimal("0.125")),
            ("$ 1,234.5 mn", Decimal("1234500000")),
        ],
    )
    def test_composite_patterns(self, text, canonical):
        assert parse_financial_value(text).canonical() == canonical

    def test_two_scales_of_the_same_quantity_are_equal_in_canonical_form(self):
        """The core reason canonical() exists: 1 crore == 10 million."""
        crore = parse_financial_value("1 crore")
        million = parse_financial_value("10 million")
        assert crore.canonical() == million.canonical()


class TestSubstringMatchingRegressions:
    """Regressions for two live bugs found by probing, not by the first test pass.

    Both came from plain substring matching of scale/currency tokens. They are
    the highest-severity class of bug in this module: they produce confident,
    catastrophically wrong values from ordinary English prose.
    """

    def test_cr_inside_increase_is_not_crore(self):
        """'in(cr)ease of 500' once parsed as 500 crore - a 10,000,000x error."""
        v = parse_financial_value("increase of 500")
        assert v.scale is Scale.UNIT
        assert v.canonical() == Decimal("500")

    def test_rs_inside_first_is_not_a_rupee_sign(self):
        """'fi(rs)t half' once yielded currency INR."""
        assert parse_financial_value("first half 1,234").currency is None

    @pytest.mark.parametrize(
        "text",
        [
            "increase of 500",
            "decrease of 500",
            "accrual 500",
            "concrete 500",
        ],
    )
    def test_cr_never_matches_mid_word(self, text):
        assert parse_financial_value(text).scale is Scale.UNIT

    @pytest.mark.parametrize("text", ["first 500", "worst 500", "burst 500"])
    def test_rs_never_matches_mid_word(self, text):
        assert parse_financial_value(text).currency is None

    def test_genuine_abbreviations_still_work_after_the_fix(self):
        """The fix must not break the abbreviations it was protecting."""
        assert parse_financial_value("500 cr").scale is Scale.CRORE
        assert parse_financial_value("500 cr.").scale is Scale.CRORE
        assert parse_financial_value("500 mn").scale is Scale.MILLION
        assert parse_financial_value("500 bn").scale is Scale.BILLION
        assert parse_financial_value("Rs 500").currency == "INR"
        assert parse_financial_value("Rs. 500").currency == "INR"

    def test_scale_word_in_a_realistic_table_header(self):
        assert parse_financial_value("(Rs. in crore) 1,234").scale is Scale.CRORE


# --------------------------------------------------------------------------
# Compound scale ambiguity. Found by running the pipeline live on HDFC Bank
# (2026-08-27), not by a test.
# --------------------------------------------------------------------------


def test_k_cr_reads_as_crore_because_the_filing_says_so():
    """HDFC Bank's FY24 highlights head a column "Deposits (K Cr)" and print
    23,79,786. The same filing's narrative on p.217 gives that figure as
    "23,79,786 crore". So "K Cr" means crore for this filer, and reading it as
    thousand-crore would be 1000x wrong.

    This asserts the reading the document supports, against the source, so a
    later "obvious" fix promoting `k` to a scale fails here rather than in a
    published number.
    """
    value = parse_financial_value("23,79,786 K Cr")
    assert value.scale is Scale.CRORE
    assert value.canonical() == Decimal("23797860000000")


def test_k_cr_is_flagged_even_though_it_is_read_correctly():
    """Right by accident is still worth flagging.

    The parser matches "cr" and discards the "K" - it does not reason about the
    pair. A different filer using "K Cr" to mean what it says would be misread
    by three orders of magnitude with no signal at all.
    """
    assert (
        ParseWarning.AMBIGUOUS_COMPOUND_SCALE
        in parse_financial_value("23,79,786 K Cr").warnings
    )


def test_an_unambiguous_scale_is_not_flagged():
    """The warning must stay rare enough to mean something. A flag on every
    figure is a flag on none."""
    for text in ("23,79,786 crore", "5 lakh", "1,234.5 million", "INR 1,234 crore"):
        assert not parse_financial_value(text).warnings, text


def test_thousand_crore_is_flagged_rather_than_silently_wrong():
    """"1 thousand crore" is 10^10. The parser takes the first scale token it
    matches and stops, so it reads 10^3 - wrong by seven orders of magnitude.

    The arithmetic is deliberately NOT changed: no document in this corpus uses
    the phrase, and altering scale handling to satisfy a case nothing exercises
    is how D10 reached two conclusions that measurement overturned. Flagged so
    the value cannot pass as confident.
    """
    value = parse_financial_value("1 thousand crore")
    assert ParseWarning.AMBIGUOUS_COMPOUND_SCALE in value.warnings


def test_the_trace_names_both_scale_words():
    """A warning that does not say what it saw sends a reader back to the PDF."""
    trace = " ".join(parse_financial_value("23,79,786 K Cr").trace).lower()
    assert "crore" in trace and "'k'" in trace
