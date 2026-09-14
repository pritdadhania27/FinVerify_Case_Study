"""The correctness predicate (EVALUATION.md §2.2) - the foundation every metric rests on.

Every number this project reports is downstream of one boolean: is this answer
correct? QA accuracy counts it, the detection label `y` *is* it, error analysis
classifies the cases where it is false, and the ablation compares arms on it. A
loose predicate here does not produce a slightly-wrong result; it produces a
result about a different question.

Three decisions in this module are research decisions rather than implementation
details, and each is recorded where it is made:

1. **Sign and unit mismatches are never near-misses.** A profit reported as a
   loss is wrong at any magnitude tolerance.
2. **Percent and ratio are the same dimension**, because `canonical()` maps 25%
   to 0.25. This matches the consistency engine's `_dimensions_compatible`, and
   the two must not diverge: a value the verifier calls "the same" and the grader
   calls "different" would put the detection label and its predictor in
   different universes.
3. **A bare number is read under the gold record's convention**, and the fact
   that an assumption was applied is recorded per judgment and reported in
   aggregate. See `assume_gold_convention` for why neither the permissive nor
   the strict rule is safe to apply silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from backend.core.financial_value import FinancialValue, UnitKind

__all__ = [
    "TAU_PRIMARY",
    "TAU_SENSITIVITY",
    "EPSILON",
    "Mismatch",
    "CorrectnessJudgment",
    "judge",
    "sensitivity_table",
]

# EVALUATION.md §2.2. Chosen so a gold value rounded to published precision
# (a report stating "12.4%" for 12.4372%) still matches, while a genuine
# arithmetic slip does not.
TAU_PRIMARY = Decimal("0.005")

# Every headline result is recomputed at each of these and the table published.
# If conclusions flip between tolerances, that IS the finding (EVALUATION.md §2.2).
TAU_SENSITIVITY: tuple[Decimal, ...] = (
    Decimal("0.001"),
    Decimal("0.005"),
    Decimal("0.01"),
    Decimal("0.05"),
)

EPSILON = Decimal("1e-9")

# Which unit kinds are numerically comparable at all. Mirrors
# `consistency._DIMENSIONS` deliberately - see the module docstring, point 2.
_DIMENSIONLESS = frozenset({UnitKind.PERCENT, UnitKind.RATIO})


class Mismatch(Enum):
    """Why a judgment came out false. Ordered by how early it is decided."""

    NONE = "none"
    MISSING_PREDICTION = "missing_prediction"
    MISSING_GOLD = "missing_gold"
    UNIT_KIND = "unit_kind"
    CURRENCY = "currency"
    SIGN = "sign"
    MAGNITUDE = "magnitude"


@dataclass(frozen=True)
class CorrectnessJudgment:
    """One correctness decision, with the trace that produced it.

    `trace` exists because spec §13 requires every transformation to be
    traceable: a correctness label that cannot be explained after the fact is a
    label nobody can audit, and gold-label disputes are settled by reading it.
    """

    correct: bool
    mismatch: Mismatch
    tau: Decimal
    relative_error: Decimal | None = None
    predicted_canonical: Decimal | None = None
    gold_canonical: Decimal | None = None
    # True when the prediction carried no unit kind and was read under the gold
    # record's convention. Aggregated as `unit_assumption_rate` so a reader can
    # see how much of the accuracy figure depends on that reading.
    unit_assumed: bool = False
    trace: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "correct": self.correct,
            "mismatch": self.mismatch.value,
            "tau": str(self.tau),
            "relative_error": (
                str(self.relative_error) if self.relative_error is not None else None
            ),
            "predicted_canonical": (
                str(self.predicted_canonical) if self.predicted_canonical is not None else None
            ),
            "gold_canonical": (
                str(self.gold_canonical) if self.gold_canonical is not None else None
            ),
            "unit_assumed": self.unit_assumed,
            "trace": list(self.trace),
        }


def _dimension(kind: UnitKind) -> str:
    return "dimensionless" if kind in _DIMENSIONLESS else kind.value


def _canonical_under(value: FinancialValue, convention: UnitKind) -> Decimal:
    """The prediction's magnitude read as though written in `convention`.

    Only ever called for an UNKNOWN prediction. A bare `25` against a gold of
    `25%` is 0.25 under the percent convention and 25 under a currency one;
    picking silently is what this exists to make visible.
    """
    if convention is UnitKind.PERCENT:
        return value.amount / Decimal(100)
    return value.amount * value.scale.multiplier


def judge(
    prediction: FinancialValue | None,
    gold: FinancialValue | None,
    *,
    tau: Decimal = TAU_PRIMARY,
    assume_gold_convention: bool = True,
) -> CorrectnessJudgment:
    """Decide whether `prediction` matches `gold` (EVALUATION.md §2.2).

    `assume_gold_convention` governs the one genuinely ambiguous case: the model
    returned a bare number with no unit marker.

    - **Permissive** (try every reading, accept if any matches) inflates
      accuracy by construction - a model that emits naked numbers gets several
      chances at each question while a model that states its units gets one.
    - **Strict** (a missing unit is a mismatch) fails a correct number for a
      formatting omission, and on these free-tier models the omission rate is
      high enough to swamp the signal.
    - **This**, the default: read the bare number under the gold record's stated
      convention - EVALUATION.md §2.1 already gives the gold `unit` field that
      job - and record that the reading was applied, so the rate is reportable
      and the sensitivity is checkable by flipping this flag.

    A missing prediction is INCORRECT, never excluded. EVALUATION.md §3 scores
    an abstention as wrong and tracks its rate separately; dropping it from the
    denominator would let a system score 100% by answering one question.
    """
    trace: list[str] = []

    if gold is None:
        # Not a grade. There is nothing to be right or wrong about, and calling
        # it "incorrect" would manufacture a detection label out of a gap.
        return CorrectnessJudgment(
            correct=False,
            mismatch=Mismatch.MISSING_GOLD,
            tau=tau,
            trace=("no gold value: this question cannot be graded",),
        )

    gold_canonical = gold.canonical()

    if prediction is None:
        return CorrectnessJudgment(
            correct=False,
            mismatch=Mismatch.MISSING_PREDICTION,
            tau=tau,
            gold_canonical=gold_canonical,
            trace=("no prediction: scored incorrect, counted as an abstention",),
        )

    unit_assumed = False
    if prediction.unit_kind is UnitKind.UNKNOWN and gold.unit_kind is not UnitKind.UNKNOWN:
        if not assume_gold_convention:
            return CorrectnessJudgment(
                correct=False,
                mismatch=Mismatch.UNIT_KIND,
                tau=tau,
                gold_canonical=gold_canonical,
                trace=(
                    (
                        f"prediction states no unit kind; gold is "
                        f"{gold.unit_kind.value}; strict mode does not guess"
                    ),
                ),
            )
        predicted_canonical = _canonical_under(prediction, gold.unit_kind)
        unit_assumed = True
        trace.append(
            "prediction carried no unit kind; read under the gold convention "
            f"({gold.unit_kind.value})"
        )
    elif gold.unit_kind is UnitKind.UNKNOWN:
        # The gold states no unit kind, so it has no basis on which to reject
        # the prediction's. Without this the asymmetry runs the wrong way: a
        # gold of "8,415.03 crore" (scale stated, kind unstated - what the
        # generator writes when the source table says "in crore" without a
        # currency symbol) rejected a prediction of "8,415.03 INR crore", the
        # MOST complete answer a model can give, while accepting the barer
        # "8,415.03 crore". That grades a model on how little it says.
        # The magnitude test below still runs on canonical values, so a percent
        # prediction cannot slip past a crore-scaled gold on this branch.
        predicted_canonical = prediction.canonical()
        if prediction.unit_kind is not UnitKind.UNKNOWN:
            trace.append(
                f"gold states no unit kind; not held against the prediction's "
                f"{prediction.unit_kind.value}"
            )
    else:
        if _dimension(prediction.unit_kind) != _dimension(gold.unit_kind):
            return CorrectnessJudgment(
                correct=False,
                mismatch=Mismatch.UNIT_KIND,
                tau=tau,
                gold_canonical=gold_canonical,
                predicted_canonical=prediction.canonical(),
                trace=(
                    (
                        f"unit kind {prediction.unit_kind.value} is not comparable "
                        f"with gold {gold.unit_kind.value}"
                    ),
                ),
            )
        predicted_canonical = prediction.canonical()

    # Currency is checked only when BOTH sides state one. A missing currency is
    # a missing label, not a contradiction, and turning it into one would grade
    # the answer on its formatting.
    if prediction.currency and gold.currency and prediction.currency != gold.currency:
        return CorrectnessJudgment(
            correct=False,
            mismatch=Mismatch.CURRENCY,
            tau=tau,
            gold_canonical=gold_canonical,
            predicted_canonical=predicted_canonical,
            unit_assumed=unit_assumed,
            trace=tuple(trace + [f"currency {prediction.currency} != gold {gold.currency}"]),
        )

    # Zero gold: relative error is undefined, so the test becomes absolute.
    if gold_canonical == 0:
        correct = abs(predicted_canonical) <= EPSILON
        trace.append(f"gold is zero; requiring |prediction| <= {EPSILON}")
        return CorrectnessJudgment(
            correct=correct,
            mismatch=Mismatch.NONE if correct else Mismatch.MAGNITUDE,
            tau=tau,
            relative_error=None,
            predicted_canonical=predicted_canonical,
            gold_canonical=gold_canonical,
            unit_assumed=unit_assumed,
            trace=tuple(trace),
        )

    # Sign before magnitude, and never subsumed by it. Far from zero a sign flip
    # also fails the magnitude test, but near zero the magnitudes converge while
    # the meaning stays opposite - and that is exactly where a loss gets read as
    # a profit.
    if (predicted_canonical > 0) != (gold_canonical > 0) and predicted_canonical != 0:
        return CorrectnessJudgment(
            correct=False,
            mismatch=Mismatch.SIGN,
            tau=tau,
            relative_error=abs(predicted_canonical - gold_canonical) / abs(gold_canonical),
            predicted_canonical=predicted_canonical,
            gold_canonical=gold_canonical,
            unit_assumed=unit_assumed,
            trace=tuple(trace + ["sign mismatch: a loss reported as a profit is not a near-miss"]),
        )

    relative_error = abs(predicted_canonical - gold_canonical) / max(abs(gold_canonical), EPSILON)
    correct = relative_error <= tau
    trace.append(f"relative error {relative_error} vs tau {tau}")
    return CorrectnessJudgment(
        correct=correct,
        mismatch=Mismatch.NONE if correct else Mismatch.MAGNITUDE,
        tau=tau,
        relative_error=relative_error,
        predicted_canonical=predicted_canonical,
        gold_canonical=gold_canonical,
        unit_assumed=unit_assumed,
        trace=tuple(trace),
    )


def sensitivity_table(
    pairs: list[tuple[FinancialValue | None, FinancialValue | None]],
    *,
    taus: tuple[Decimal, ...] = TAU_SENSITIVITY,
    assume_gold_convention: bool = True,
) -> dict[str, float]:
    """Accuracy at each tolerance in `taus` (EVALUATION.md §2.2).

    Published beside every headline number so a reader can see whether the
    conclusion survives the tolerance choice rather than taking 0.5% on trust.
    """
    table: dict[str, float] = {}
    for tau in taus:
        if not pairs:
            table[str(tau)] = 0.0
            continue
        hits = sum(
            1
            for prediction, gold in pairs
            if judge(
                prediction, gold, tau=tau, assume_gold_convention=assume_gold_convention
            ).correct
        )
        table[str(tau)] = hits / len(pairs)
    return table
