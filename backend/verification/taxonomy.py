"""Hallucination and error taxonomy (spec Module 14).

The vocabulary the research question is stated in. Everything downstream -
stratified detection metrics (D12), error analysis (Module 25), the ablation's
interpretation - is expressed in these terms, so the shape of this module
constrains what the project is able to conclude.

**The spec's list is one axis; this module uses two.** Spec §22 asks for, at
minimum: wrong evidence, wrong number, wrong year, wrong unit, arithmetic error,
wrong formula, wrong metric, unsupported claim, retrieval error, reasoning error.

That list mixes two different questions. "Wrong unit" says *what the error looks
like*; "retrieval error" says *where it entered the pipeline*. They are not
alternatives - a wrong unit has a provenance, and a retrieval error has a
visible form. Forced into one flat enum they compete for the same slot, and
labelling an answer `RETRIEVAL_ERROR` silently discards the fact that it was a
scale confusion.

That would be fatal for D12, which requires detection metrics stratified by
provenance *while* Module 25 analyses by kind. One axis cannot serve both. So:

    provenance  - WHERE the error entered   (retrieval / reasoning / ...)
    kind        - WHAT the error looks like (wrong scale / wrong year / ...)

Every label carries both, and either may be UNDETERMINED.

**Additions beyond the spec's minimum, documented as §22 requires:**

* `WRONG_SCALE` split out of `WRONG_UNIT`. Crore/lakh/million confusion is this
  project's headline error class and the one the dual-channel design is most
  likely to catch; folding it into a general unit error would make the headline
  result unreportable.
* `SIGN_ERROR` split out of `WRONG_NUMBER`. A parenthesised negative read as
  positive is a categorical misreading of financial notation, not a numeric slip,
  and it has a different fix.
* Provenance `QUESTION_UNDERSTANDING` (D19) - both channels consume one
  `QuestionSpec`, so a mis-parse makes both wrong identically and their agreement
  proves nothing. It is neither retrieval- nor reasoning-caused.
* Provenance `EXTRACTION` - the figure was already wrong in the chunk, so
  retrieval succeeded and reasoning was faithful. Blaming either would
  misattribute a document-intelligence defect.
* Provenance `DEFINITIONAL_AMBIGUITY` (D22, RX-007) - the answer differs from
  gold because the question admits several standard definitions. **This is not a
  hallucination**, and pooling it with real errors would understate the method by
  counting defensible answers as failures.

**Classification requires gold, and this module refuses without it.** An error is
a disagreement with a *known correct answer*. Channel disagreement is not error -
it is the signal the detector uses to *predict* error, and conflating the two
would let the system grade its own homework: labelling every disagreement an
error makes detection trivially perfect and completely meaningless. `classify`
therefore takes the gold answer, and `LabelConfidence` records how much of each
label came from a rule versus needing a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from backend.core.financial_value import FinancialValue
from backend.verification.consistency import DisagreementType

__all__ = [
    "ErrorProvenance",
    "ErrorKind",
    "LabelConfidence",
    "ErrorLabel",
    "determine_provenance",
    "suggest_kind",
    "classify",
]


class ErrorProvenance(Enum):
    """WHERE the error entered the pipeline. Drives D12's stratification."""

    RETRIEVAL = "retrieval"
    QUESTION_UNDERSTANDING = "question_understanding"
    EXTRACTION = "extraction"
    REASONING = "reasoning"
    DEFINITIONAL_AMBIGUITY = "definitional_ambiguity"
    UNDETERMINED = "undetermined"


class ErrorKind(Enum):
    """WHAT the error looks like. Drives Module 25's analysis."""

    WRONG_EVIDENCE = "wrong_evidence"
    WRONG_NUMBER = "wrong_number"
    WRONG_YEAR = "wrong_year"
    WRONG_UNIT = "wrong_unit"
    WRONG_SCALE = "wrong_scale"
    SIGN_ERROR = "sign_error"
    ARITHMETIC_ERROR = "arithmetic_error"
    WRONG_FORMULA = "wrong_formula"
    WRONG_METRIC = "wrong_metric"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    UNCLASSIFIED = "unclassified"


class LabelConfidence(Enum):
    """How much of this label came from a rule, and how much needs a human.

    Kept explicit so an error-analysis table can report what it actually knows.
    A taxonomy that presents guessed labels beside derived ones, indistinguishably,
    produces a breakdown that looks authoritative and is partly invented.
    """

    MECHANICAL = "mechanical"
    SUGGESTED = "suggested"
    NEEDS_HUMAN = "needs_human"


# A channel disagreement is EVIDENCE ABOUT a kind, never proof of one. The
# mapping is used only to seed a human's judgement, and every label it produces is
# marked SUGGESTED. Two channels can disagree on scale while the final answer is
# right; the answer can be wrong with no disagreement at all.
_KIND_HINTS: dict[DisagreementType, ErrorKind] = {
    DisagreementType.SCALE_MISMATCH: ErrorKind.WRONG_SCALE,
    DisagreementType.SIGN_MISMATCH: ErrorKind.SIGN_ERROR,
    DisagreementType.CURRENCY_MISMATCH: ErrorKind.WRONG_UNIT,
    DisagreementType.UNIT_KIND_MISMATCH: ErrorKind.WRONG_UNIT,
}

# Powers of ten that name a scale confusion in Indian and Western notation:
# lakh/crore (10^2), crore/million (10), thousands (10^3), and the wider gaps a
# mis-read unit banner produces. A tuple, not a generator - a module-level
# generator would be silently exhausted after its first use.
_SCALE_POWERS = (-12, -9, -7, -6, -5, -3, -2, -1, 1, 2, 3, 5, 6, 7, 9, 12)

# Matches DEFAULT_TOLERANCE in consistency.py and EVALUATION.md 2.2, so "the
# channels agree", "the answer is correct", and "this is a clean power of ten"
# are all judged on one scale.
_TOLERANCE = Decimal("0.005")


@dataclass(frozen=True)
class ErrorLabel:
    """One classified answer. `is_error=False` still carries a provenance of NONE."""

    is_error: bool
    provenance: ErrorProvenance
    kind: ErrorKind
    confidence: LabelConfidence
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def counts_as_hallucination(self) -> bool:
        """Whether this belongs in the headline detection numbers.

        Definitional ambiguity is excluded (D22): the answer is defensible and the
        disagreement reflects the question, not the system. Counting it as a
        hallucination would penalise the method for being right in a way the gold
        did not anticipate - and would make the headline figure a measure of the
        dataset's precision rather than the detector's.
        """
        return self.is_error and self.provenance is not ErrorProvenance.DEFINITIONAL_AMBIGUITY

    def as_dict(self) -> dict:
        return {
            "is_error": self.is_error,
            "provenance": self.provenance.value,
            "kind": self.kind.value,
            "confidence": self.confidence.value,
            "counts_as_hallucination": self.counts_as_hallucination,
            "evidence": list(self.evidence),
        }


def determine_provenance(
    *,
    all_evidence_retrieved: bool,
    question_parsed_correctly: bool | None = None,
    definition_ambiguous: bool = False,
    extraction_faulty: bool | None = None,
) -> tuple[ErrorProvenance, LabelConfidence, str]:
    """Decide where a wrong answer's error entered.

    Order matters, and it is not arbitrary: each test is applied only where the
    ones before it have been ruled out, because provenance is about the FIRST
    point the pipeline went wrong. A retrieval miss makes everything downstream
    moot - the channels never had the number, so their reasoning cannot be blamed
    for not using it.

    `question_parsed_correctly` and `extraction_faulty` are `None` when unknown,
    and an unknown never silently becomes a `False`. D12's stratification is only
    honest if "we checked and it was fine" is distinguishable from "nobody looked".
    """
    if definition_ambiguous:
        return (
            ErrorProvenance.DEFINITIONAL_AMBIGUITY,
            LabelConfidence.MECHANICAL,
            "the question admits more than one standard definition (D22)",
        )

    if question_parsed_correctly is False:
        return (
            ErrorProvenance.QUESTION_UNDERSTANDING,
            LabelConfidence.MECHANICAL,
            (
                "the QuestionSpec misread the question, so both channels were "
                "identically misdirected (D19)"
            ),
        )

    if not all_evidence_retrieved:
        return (
            ErrorProvenance.RETRIEVAL,
            LabelConfidence.MECHANICAL,
            (
                "a required gold evidence group was absent from the retrieved set, "
                "so no channel could have answered correctly (D12)"
            ),
        )

    if extraction_faulty:
        return (
            ErrorProvenance.EXTRACTION,
            LabelConfidence.MECHANICAL,
            (
                "the figure was already wrong in the retrieved chunk; retrieval and "
                "reasoning both behaved correctly"
            ),
        )

    if question_parsed_correctly is None or extraction_faulty is None:
        return (
            ErrorProvenance.UNDETERMINED,
            LabelConfidence.NEEDS_HUMAN,
            (
                "evidence was retrieved and the answer is wrong, but question "
                "understanding and/or extraction were not checked - so 'reasoning' "
                "cannot be concluded, only assumed"
            ),
        )

    return (
        ErrorProvenance.REASONING,
        LabelConfidence.MECHANICAL,
        (
            "every gold evidence group was retrieved, the question was parsed "
            "correctly, and the extracted figures were sound - the error is in the "
            "reasoning over them (D12)"
        ),
    )


def _is_scale_apart(a: Decimal, b: Decimal) -> bool:
    if a == 0 or b == 0:
        return False
    ratio = abs(a) / abs(b)
    for n in _SCALE_POWERS:
        target = Decimal(10) ** n
        scale = max(abs(ratio), abs(target))
        if scale and abs(ratio - target) / scale <= _TOLERANCE:
            return True
    return False


def suggest_kind(
    answer: FinancialValue | None,
    gold: FinancialValue | None,
    *,
    disagreements: frozenset[DisagreementType] = frozenset(),
) -> tuple[ErrorKind, LabelConfidence, str]:
    """Propose what the error looks like, comparing the answer against gold.

    Deliberately weak. Comparing two numbers can show that they differ by a power
    of ten or in sign; it cannot show *why*, and "wrong formula" versus "wrong
    metric" is exactly the distinction a human has to make by reading the
    reasoning. Anything this function cannot derive from the arithmetic is
    returned as UNCLASSIFIED rather than guessed, because a plausible wrong label
    in an error-analysis table is worse than an honest gap - it is unfalsifiable
    by the reader.
    """
    if answer is None:
        return (
            ErrorKind.UNSUPPORTED_CLAIM,
            LabelConfidence.MECHANICAL,
            "no answer was produced",
        )
    if gold is None:
        return (
            ErrorKind.UNCLASSIFIED,
            LabelConfidence.NEEDS_HUMAN,
            "no gold value to compare against",
        )

    a, g = answer.canonical(), gold.canonical()

    if a != 0 and g != 0 and (a > 0) != (g > 0):
        return (
            ErrorKind.SIGN_ERROR,
            LabelConfidence.MECHANICAL,
            (
                f"answer {a} and gold {g} have opposite signs - in a filing this "
                "is usually a parenthesised negative read as positive"
            ),
        )

    if _is_scale_apart(a, g):
        return (
            ErrorKind.WRONG_SCALE,
            LabelConfidence.MECHANICAL,
            (
                f"answer {a} and gold {g} differ by a clean power of ten - a "
                "crore/lakh/million confusion rather than a reasoning slip"
            ),
        )

    if answer.unit_kind is not gold.unit_kind:
        return (
            ErrorKind.WRONG_UNIT,
            LabelConfidence.MECHANICAL,
            f"unit kind {answer.unit_kind.value} vs gold {gold.unit_kind.value}",
        )

    if answer.currency and gold.currency and answer.currency != gold.currency:
        return (
            ErrorKind.WRONG_UNIT,
            LabelConfidence.MECHANICAL,
            f"currency {answer.currency} vs gold {gold.currency}",
        )

    for disagreement, kind in _KIND_HINTS.items():
        if disagreement in disagreements:
            return (
                kind,
                LabelConfidence.SUGGESTED,
                (
                    f"channels showed {disagreement.value}; this is a hint about "
                    "the kind, not proof of it"
                ),
            )

    return (
        ErrorKind.UNCLASSIFIED,
        LabelConfidence.NEEDS_HUMAN,
        (
            "the answer is simply a different number - distinguishing wrong metric "
            "from wrong formula from arithmetic error requires reading the reasoning"
        ),
    )


def classify(
    answer: FinancialValue | None,
    gold: FinancialValue | None,
    *,
    is_correct: bool,
    all_evidence_retrieved: bool,
    disagreements: frozenset[DisagreementType] = frozenset(),
    question_parsed_correctly: bool | None = None,
    definition_ambiguous: bool = False,
    extraction_faulty: bool | None = None,
) -> ErrorLabel:
    """Label one answered question.

    `is_correct` is supplied by the caller rather than recomputed here, because
    correctness is defined in EVALUATION.md §2.2 with its own tolerance rules and
    must have exactly one definition in the project. A second implementation
    inside the taxonomy would be a second definition, and the two would drift.
    """
    if is_correct:
        return ErrorLabel(
            is_error=False,
            provenance=ErrorProvenance.UNDETERMINED,
            kind=ErrorKind.UNCLASSIFIED,
            confidence=LabelConfidence.MECHANICAL,
            evidence=("answer matches gold within tolerance",),
        )

    provenance, p_conf, p_why = determine_provenance(
        all_evidence_retrieved=all_evidence_retrieved,
        question_parsed_correctly=question_parsed_correctly,
        definition_ambiguous=definition_ambiguous,
        extraction_faulty=extraction_faulty,
    )
    kind, k_conf, k_why = suggest_kind(answer, gold, disagreements=disagreements)

    # The weaker of the two governs: a label is only as trustworthy as its least
    # certain half, and reporting MECHANICAL because one axis was derivable would
    # overstate what is known about the other.
    order = {
        LabelConfidence.MECHANICAL: 0,
        LabelConfidence.SUGGESTED: 1,
        LabelConfidence.NEEDS_HUMAN: 2,
    }
    confidence = max((p_conf, k_conf), key=lambda c: order[c])

    return ErrorLabel(
        is_error=True,
        provenance=provenance,
        kind=kind,
        confidence=confidence,
        evidence=(p_why, k_why),
    )
