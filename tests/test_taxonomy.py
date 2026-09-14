"""Tests for the hallucination and error taxonomy (spec Module 14).

The taxonomy is what the research question is stated in, so its failure modes are
interpretive rather than operational: a label that is wrong still serialises, still
aggregates, and still produces a clean table in the paper. These tests pin the
distinctions the conclusions depend on.
"""

import pytest

from backend.core.financial_value import parse_financial_value
from backend.verification.consistency import DisagreementType
from backend.verification.taxonomy import (
    ErrorKind,
    ErrorProvenance,
    LabelConfidence,
    classify,
    determine_provenance,
    suggest_kind,
)


def fv(text: str):
    return parse_financial_value(text)


class TestProvenanceOrdering:
    """Provenance is the FIRST point the pipeline went wrong, not the last.

    A retrieval miss makes everything downstream moot - the channels never had the
    number, so their reasoning cannot be blamed for failing to use it. Getting the
    order wrong silently reattributes error between D12's strata, which is the
    split H2 is tested on.
    """

    def test_retrieval_miss_outranks_reasoning(self):
        p, conf, _ = determine_provenance(
            all_evidence_retrieved=False,
            question_parsed_correctly=True,
            extraction_faulty=False,
        )
        assert p is ErrorProvenance.RETRIEVAL
        assert conf is LabelConfidence.MECHANICAL

    def test_question_misparse_outranks_retrieval(self):
        """D19: one QuestionSpec feeds both channels, so a mis-parse precedes
        retrieval - the wrong thing was searched for in the first place."""
        p, _, _ = determine_provenance(
            all_evidence_retrieved=False, question_parsed_correctly=False
        )
        assert p is ErrorProvenance.QUESTION_UNDERSTANDING

    def test_ambiguity_outranks_everything(self):
        p, _, why = determine_provenance(
            all_evidence_retrieved=False,
            question_parsed_correctly=False,
            definition_ambiguous=True,
        )
        assert p is ErrorProvenance.DEFINITIONAL_AMBIGUITY
        assert "D22" in why

    def test_reasoning_requires_everything_else_checked(self):
        p, conf, _ = determine_provenance(
            all_evidence_retrieved=True,
            question_parsed_correctly=True,
            extraction_faulty=False,
        )
        assert p is ErrorProvenance.REASONING
        assert conf is LabelConfidence.MECHANICAL


class TestUnknownIsNotFalse:
    """The distinction that keeps D12's stratification honest.

    "We checked and it was fine" and "nobody looked" must not collapse into the
    same label. If they do, REASONING becomes the default bucket for every error
    nobody investigated, and the reasoning-caused stratum - the one the method is
    supposed to work on - silently absorbs unexamined failures.
    """

    def test_unchecked_question_parse_does_not_become_reasoning(self):
        p, conf, why = determine_provenance(
            all_evidence_retrieved=True,
            question_parsed_correctly=None,
            extraction_faulty=False,
        )
        assert p is ErrorProvenance.UNDETERMINED
        assert conf is LabelConfidence.NEEDS_HUMAN
        assert "not checked" in why

    def test_unchecked_extraction_does_not_become_reasoning(self):
        p, conf, _ = determine_provenance(
            all_evidence_retrieved=True,
            question_parsed_correctly=True,
            extraction_faulty=None,
        )
        assert p is ErrorProvenance.UNDETERMINED
        assert conf is LabelConfidence.NEEDS_HUMAN

    def test_extraction_fault_is_neither_retrieval_nor_reasoning(self):
        p, _, why = determine_provenance(
            all_evidence_retrieved=True,
            question_parsed_correctly=True,
            extraction_faulty=True,
        )
        assert p is ErrorProvenance.EXTRACTION
        assert "retrieval and reasoning both behaved correctly" in why


class TestKindFromArithmetic:
    def test_power_of_ten_gap_is_a_scale_error(self):
        """The headline error class: crore read as million."""
        k, conf, _ = suggest_kind(fv("1 crore"), fv("1 million"))
        assert k is ErrorKind.WRONG_SCALE
        assert conf is LabelConfidence.MECHANICAL

    def test_lakh_crore_confusion_is_a_scale_error(self):
        k, _, _ = suggest_kind(fv("100 lakh"), fv("100 crore"))
        assert k is ErrorKind.WRONG_SCALE

    def test_opposite_signs_is_a_sign_error(self):
        """A parenthesised negative read as positive."""
        k, conf, why = suggest_kind(fv("1,234"), fv("(1,234)"))
        assert k is ErrorKind.SIGN_ERROR
        assert conf is LabelConfidence.MECHANICAL
        assert "parenthesised" in why

    def test_missing_answer_is_an_unsupported_claim(self):
        k, conf, _ = suggest_kind(None, fv("25%"))
        assert k is ErrorKind.UNSUPPORTED_CLAIM
        assert conf is LabelConfidence.MECHANICAL

    def test_a_merely_different_number_is_not_guessed_at(self):
        """Wrong metric vs wrong formula vs arithmetic slip needs a human.

        Comparing two numbers cannot distinguish them, and a plausible invented
        label is worse than an honest gap - the reader cannot falsify it.
        """
        k, conf, why = suggest_kind(fv("31.2%"), fv("25%"))
        assert k is ErrorKind.UNCLASSIFIED
        assert conf is LabelConfidence.NEEDS_HUMAN
        assert "requires reading the reasoning" in why


class TestDisagreementIsNotError:
    """Channel disagreement is the PREDICTOR; error is the LABEL.

    Conflating them lets the system grade its own homework: if every disagreement
    is an error by definition, detection is trivially perfect and measures
    nothing. Disagreement may only ever downgrade a label to SUGGESTED.
    """

    def test_disagreement_hint_is_never_mechanical(self):
        k, conf, why = suggest_kind(
            fv("25%"),
            fv("31%"),
            disagreements=frozenset({DisagreementType.SCALE_MISMATCH}),
        )
        assert k is ErrorKind.WRONG_SCALE
        assert conf is LabelConfidence.SUGGESTED
        assert "not proof" in why

    def test_arithmetic_evidence_outranks_a_disagreement_hint(self):
        """When the numbers themselves show the answer, the hint is redundant."""
        k, conf, _ = suggest_kind(
            fv("1 crore"),
            fv("1 million"),
            disagreements=frozenset({DisagreementType.SIGN_MISMATCH}),
        )
        assert k is ErrorKind.WRONG_SCALE
        assert conf is LabelConfidence.MECHANICAL


class TestClassifyRequiresGold:
    def test_a_correct_answer_is_not_an_error(self):
        label = classify(
            fv("25%"), fv("25%"), is_correct=True, all_evidence_retrieved=True
        )
        assert label.is_error is False
        assert label.counts_as_hallucination is False

    def test_no_gold_leaves_the_kind_for_a_human(self):
        label = classify(
            fv("25%"), None, is_correct=False, all_evidence_retrieved=True,
            question_parsed_correctly=True, extraction_faulty=False,
        )
        assert label.kind is ErrorKind.UNCLASSIFIED
        assert label.confidence is LabelConfidence.NEEDS_HUMAN

    def test_label_confidence_is_the_weaker_of_the_two_axes(self):
        """A label is only as trustworthy as its least certain half.

        Provenance here is MECHANICAL and kind is NEEDS_HUMAN; reporting the
        label as MECHANICAL would overstate what is known about the kind.
        """
        label = classify(
            fv("31.2%"), fv("25%"), is_correct=False, all_evidence_retrieved=True,
            question_parsed_correctly=True, extraction_faulty=False,
        )
        assert label.provenance is ErrorProvenance.REASONING
        assert label.kind is ErrorKind.UNCLASSIFIED
        assert label.confidence is LabelConfidence.NEEDS_HUMAN


class TestAmbiguityIsNotHallucination:
    """D22 / RX-007. Excluding it from the headline is a research decision.

    Return on equity split three ways on three standard definitions. Counting
    those as hallucinations would make the headline figure a measure of the
    dataset's precision rather than the detector's quality.
    """

    def test_ambiguous_error_is_excluded_from_the_headline(self):
        label = classify(
            fv("32.08%"), fv("29.67%"), is_correct=False,
            all_evidence_retrieved=True, definition_ambiguous=True,
        )
        assert label.is_error is True
        assert label.provenance is ErrorProvenance.DEFINITIONAL_AMBIGUITY
        assert label.counts_as_hallucination is False

    def test_an_ordinary_error_does_count(self):
        label = classify(
            fv("1 crore"), fv("1 million"), is_correct=False,
            all_evidence_retrieved=True, question_parsed_correctly=True,
            extraction_faulty=False,
        )
        assert label.counts_as_hallucination is True


class TestTwoAxesStayIndependent:
    """The structural reason this module does not use the spec's flat list.

    D12 stratifies by provenance while Module 25 analyses by kind. One enum
    cannot serve both: labelling a scale confusion RETRIEVAL_ERROR discards the
    kind, and labelling it WRONG_SCALE discards the stratum.
    """

    @pytest.mark.parametrize(
        "retrieved,expected",
        [(False, ErrorProvenance.RETRIEVAL), (True, ErrorProvenance.REASONING)],
    )
    def test_same_kind_can_carry_either_provenance(self, retrieved, expected):
        label = classify(
            fv("1 crore"), fv("1 million"), is_correct=False,
            all_evidence_retrieved=retrieved, question_parsed_correctly=True,
            extraction_faulty=False,
        )
        assert label.kind is ErrorKind.WRONG_SCALE
        assert label.provenance is expected

    def test_serialisation_carries_both_axes(self):
        d = classify(
            fv("1 crore"), fv("1 million"), is_correct=False,
            all_evidence_retrieved=True, question_parsed_correctly=True,
            extraction_faulty=False,
        ).as_dict()
        assert d["provenance"] == "reasoning"
        assert d["kind"] == "wrong_scale"
        assert d["counts_as_hallucination"] is True
        assert len(d["evidence"]) == 2


class TestSpecCoverage:
    def test_every_spec_mandated_kind_is_declared(self):
        """Spec 22 names ten minimum categories. Two are provenance, not kind.

        DECLARED, not assigned - and the difference is the point. This test was
        named `..._exists` and read as coverage, while six of these kinds (wrong
        evidence, number, year, formula, metric, arithmetic error) are produced by
        no code path: over 1,196 labelled errors in the committed reports they
        occur zero times. That is why module 14 is PARTIAL (D52). This guards the
        enum only; it says nothing about whether a label can ever carry the kind.
        """
        required = {
            "wrong_evidence", "wrong_number", "wrong_year", "wrong_unit",
            "arithmetic_error", "wrong_formula", "wrong_metric",
            "unsupported_claim",
        }
        assert required <= {k.value for k in ErrorKind}

    def test_the_two_provenance_categories_from_the_spec_list_exist(self):
        assert {"retrieval", "reasoning"} <= {p.value for p in ErrorProvenance}

    def test_documented_additions_are_present(self):
        """Spec 22 permits expansion provided it is documented; see the module
        docstring and D25."""
        assert {"wrong_scale", "sign_error"} <= {k.value for k in ErrorKind}
        assert {
            "question_understanding", "extraction", "definitional_ambiguity"
        } <= {p.value for p in ErrorProvenance}


class TestFailureCases:
    def test_zero_values_do_not_break_scale_detection(self):
        k, _, _ = suggest_kind(fv("0"), fv("1 crore"))
        assert k is not ErrorKind.WRONG_SCALE

    def test_both_zero_is_not_a_sign_error(self):
        k, _, _ = suggest_kind(fv("0"), fv("0"))
        assert k is not ErrorKind.SIGN_ERROR

    def test_percent_versus_ratio_is_a_unit_error_not_a_scale_error(self):
        """29.77 as a ratio vs 29.77% canonicalises 100x apart.

        Both readings are defensible from the text, so the useful label names the
        confusion rather than the arithmetic gap it produces.
        """
        k, _, _ = suggest_kind(fv("29.77"), fv("29.77%"))
        assert k in {ErrorKind.WRONG_SCALE, ErrorKind.WRONG_UNIT}
