"""Consistency engine (spec Module 11) and disagreement typing (Module 12).

Compares the independent outputs of Channel A (natural-language reasoning),
Channel B (executed program), and the deterministic verifier, and reports both a
categorical verdict and a **continuous** consistency score.

The continuous score is not a nicety. AUROC is the primary detection metric
(`EVALUATION.md` section 5), and AUROC over three ordinal buckets is not
meaningful. HIGH/MEDIUM/LOW is a presentation layer derived from this score,
never the underlying representation.

Two design points worth stating explicitly:

* **Both channels agreeing does not mean correct.** They share one retrieved
  evidence set, so a retrieval error makes both compute faithfully from the wrong
  number and agree. The deterministic verifier is therefore treated as an
  independent authority that can overrule unanimous channels - that path is the
  only one able to catch the "both agree, both wrong" case, which
  `EVALUATION.md` section 5.4 identifies as the method's blind spot.
* **Scale disagreement is singled out.** When two answers differ by a clean power
  of ten, that is almost never coincidence in financial QA - it is the
  crore/lakh/million confusion, and naming it turns an opaque mismatch into a
  diagnosable error class.

Scores use float; monetary comparison uses Decimal. The score is a heuristic
ranking quantity, not an amount of money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from backend.core.financial_value import FinancialValue, UnitKind

__all__ = [
    "Verdict",
    "DisagreementType",
    "ChannelAnswer",
    "ConsistencyReport",
    "compare",
    "DEFAULT_TOLERANCE",
]

# Matches the primary correctness tolerance in EVALUATION.md section 2.2, so that
# "the channels agree" and "the answer is correct" are judged on the same scale.
DEFAULT_TOLERANCE = Decimal("0.005")

# Provisional weights. Spec section 23 requires the scoring methodology be
# experimentally justified, so these are calibration parameters awaiting the
# validation-set sweep - NOT validated constants. They are named and grouped here
# so the calibration has one place to change.
_COVERAGE_PENALTY = {3: 1.00, 2: 0.90, 1: 0.55, 0: 0.50}
_DETERMINISTIC_OVERRULE_PENALTY = 0.35


class Verdict(Enum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    UNCERTAIN = "UNCERTAIN"


class DisagreementType(Enum):
    NONE = "none"
    SCALE_MISMATCH = "scale_mismatch"
    SIGN_MISMATCH = "sign_mismatch"
    UNIT_KIND_MISMATCH = "unit_kind_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    MAGNITUDE_MISMATCH = "magnitude_mismatch"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    DETERMINISTIC_CONFLICT = "deterministic_conflict"


@dataclass(frozen=True)
class ChannelAnswer:
    """One channel's numeric answer, or its absence.

    `value is None` means the channel produced no usable number - the program
    failed to execute, the model abstained, a timeout fired. That is materially
    different from producing a wrong number and must not be conflated with it.
    """

    name: str
    value: FinancialValue | None
    available: bool = True
    failure_reason: str | None = None
    # False when the channel COULD NOT APPLY to this question at all, as opposed
    # to having tried and failed. The deterministic verifier is inapplicable to a
    # lookup - there is no arithmetic to check - and that is not a shortfall of
    # evidence, it is the maximum evidence the question admits. See
    # `_coverage_penalty` for why conflating the two biased the primary metric.
    applicable: bool = True

    @property
    def canonical(self) -> Decimal | None:
        return self.value.canonical() if self.value is not None else None


@dataclass(frozen=True)
class PairComparison:
    left: str
    right: str
    score: float
    relative_difference: Decimal | None
    disagreement: DisagreementType
    note: str = ""


@dataclass(frozen=True)
class ConsistencyReport:
    verdict: Verdict
    score: float
    disagreements: frozenset[DisagreementType]
    pairs: tuple[PairComparison, ...]
    available_channels: int
    notes: tuple[str, ...] = field(default_factory=tuple)
    # The two factors of `score`, kept separately so a calibration layer can
    # weigh them independently.
    #
    # `score` is `base_score * coverage` (times an overrule penalty when the
    # deterministic verifier contradicts agreeing channels). Those two factors
    # mean different things: `base_score` is *evidence of error* - the channels
    # actively contradict each other - while `coverage` is *absence of
    # corroboration*, which is uncertainty, not error. Pre-multiplying them
    # throws that distinction away, and Module 15 cannot recover it from the
    # product. Exposing both lets calibration learn their relative weight from
    # data instead of inheriting the guess made here.
    base_score: float = 0.5
    coverage: float = 1.0
    applicable_channels: int = 0

    @property
    def agreed(self) -> bool:
        return self.verdict is Verdict.AGREE

    @property
    def usable_shortfall(self) -> int:
        """Channels that COULD have answered and did not.

        A channel that could not apply is excluded, so this counts missing
        corroboration rather than absent opportunity.
        """
        return max(0, self.applicable_channels - self.available_channels)

    def band(self) -> str:
        """Presentation-only banding. Derived from the score, never the source."""
        if self.verdict is Verdict.UNCERTAIN:
            return "UNCERTAIN"
        return "HIGH" if self.score >= 0.85 else "MEDIUM" if self.score >= 0.5 else "LOW"


def _coverage_penalty(usable: int, applicable: int) -> float:
    """Penalty for corroboration that was POSSIBLE but not obtained.

    The distinction matters because the penalty feeds the continuous score, and
    the score is what AUROC ranks on.

    Measured before this existed: on a lookup question the deterministic channel
    abstains by design - there is no arithmetic to verify - so two of three
    channels answered and the score was capped at 0.90. A computed question where
    all three answered reached 1.00. Lookups are the majority of financial QA
    questions, so the score was partly encoding *"is this a lookup?"* rather than
    *"is this answer wrong?"*, and it ranked every correct lookup below every
    correct computed answer. That is a systematic confound in the primary metric,
    not a rounding detail.

    Now the shortfall is measured against what was ACHIEVABLE. A channel that
    could not apply removes itself from the denominator; a channel that could
    have answered and did not still costs.
    """
    shortfall = max(0, applicable - usable)
    return _COVERAGE_PENALTY[max(0, min(3 - shortfall, 3))]


def _relative_difference(a: Decimal, b: Decimal) -> Decimal:
    """Symmetric relative difference, safe at zero."""
    scale = max(abs(a), abs(b))
    if scale == 0:
        return Decimal(0)
    return abs(a - b) / scale


def _is_power_of_ten_apart(a: Decimal, b: Decimal, tolerance: Decimal) -> int | None:
    """Return n where |a| ~= |b| * 10**n and n != 0, else None.

    In financial QA a clean factor-of-ten gap is a scale-unit confusion, not
    coincidence: 1 crore reported as 1 million is 10x, as 10 million is 1x, and
    lakh/crore confusion is 100x.
    """
    if a == 0 or b == 0:
        return None
    ratio = abs(a) / abs(b)
    for n in (-12, -9, -7, -6, -5, -3, -2, -1, 1, 2, 3, 5, 6, 7, 9, 12):
        target = Decimal(10) ** n
        if _relative_difference(ratio, target) <= tolerance:
            return n
    return None


# Unit kinds grouped by DIMENSION. Two answers are comparable when they measure
# the same kind of thing, which is not the same as carrying the same label.
#
# PERCENT and RATIO are one dimension - a dimensionless fraction - and
# `FinancialValue.canonical()` exists precisely so that 29.77% and 0.2977 compare
# equal. Treating the labels as a mismatch rejected the pair BEFORE canonical()
# was ever called, so a return-on-equity question on which all three channels
# agreed numerically (0.2977, 0.2977, 0.2967) was scored DISAGREE at 0.000.
#
# That is a false positive in the detector itself, on exactly the question type
# the detector exists for. In the evaluation it would inflate the disagreement
# rate and depress AUROC - the method would look worse than it is, for a reason
# having nothing to do with the models. Found by running the three-channel slice,
# not by any test.
#
# Genuine confusion between a rate and a multiple is still caught: "29.77 as a
# ratio" and "29.77 percent" canonicalise to 29.77 and 0.2977, which is a factor
# of 100 and surfaces as SCALE_MISMATCH - a more accurate diagnosis than
# UNIT_KIND_MISMATCH was.
_DIMENSIONS: dict[UnitKind, str] = {
    UnitKind.PERCENT: "dimensionless",
    UnitKind.RATIO: "dimensionless",
    UnitKind.CURRENCY: "currency",
    UnitKind.COUNT: "count",
}


def _dimensions_compatible(left: UnitKind, right: UnitKind) -> bool:
    """Can these two unit kinds be compared numerically at all?

    UNKNOWN is compatible with everything: a channel that did not state its units
    has not contradicted anything, and refusing to compare it would convert a
    missing label into a manufactured disagreement.
    """
    if UnitKind.UNKNOWN in (left, right) or left is right:
        return True
    return _DIMENSIONS.get(left) == _DIMENSIONS.get(right) is not None


def _compare_pair(
    left: ChannelAnswer, right: ChannelAnswer, tolerance: Decimal
) -> PairComparison:
    if not left.available or left.value is None or not right.available or right.value is None:
        missing = left.name if (left.value is None or not left.available) else right.name
        return PairComparison(
            left.name,
            right.name,
            score=0.5,
            relative_difference=None,
            disagreement=DisagreementType.CHANNEL_UNAVAILABLE,
            note=f"{missing} produced no usable value",
        )

    lv, rv = left.value, right.value

    if not _dimensions_compatible(lv.unit_kind, rv.unit_kind):
        return PairComparison(
            left.name,
            right.name,
            0.0,
            None,
            DisagreementType.UNIT_KIND_MISMATCH,
            f"{lv.unit_kind.value} vs {rv.unit_kind.value}",
        )

    if lv.currency and rv.currency and lv.currency != rv.currency:
        return PairComparison(
            left.name,
            right.name,
            0.0,
            None,
            DisagreementType.CURRENCY_MISMATCH,
            f"{lv.currency} vs {rv.currency}",
        )

    a, b = lv.canonical(), rv.canonical()

    # Sign is checked before magnitude: a profit reported as a loss is a
    # categorical error, not a large numeric one.
    if (a > 0) != (b > 0) and a != 0 and b != 0:
        return PairComparison(
            left.name, right.name, 0.0, _relative_difference(a, b),
            DisagreementType.SIGN_MISMATCH, f"{a} vs {b}",
        )

    rel = _relative_difference(a, b)
    if rel <= tolerance:
        return PairComparison(left.name, right.name, 1.0, rel, DisagreementType.NONE)

    power = _is_power_of_ten_apart(a, b, tolerance)
    if power is not None:
        return PairComparison(
            left.name, right.name, 0.0, rel, DisagreementType.SCALE_MISMATCH,
            f"differ by 10^{power} - likely a scale-unit confusion",
        )

    # Decays like tolerance/difference: 2x tolerance -> 0.5, 10x -> 0.1.
    score = float(tolerance / rel) if rel > 0 else 1.0
    return PairComparison(
        left.name, right.name, min(1.0, score), rel,
        DisagreementType.MAGNITUDE_MISMATCH, f"{a} vs {b}",
    )


def compare(
    channel_a: ChannelAnswer,
    channel_b: ChannelAnswer,
    deterministic: ChannelAnswer | None = None,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> ConsistencyReport:
    """Compare independent answers and produce a verdict plus a continuous score."""
    channels = [channel_a, channel_b]
    if deterministic is not None:
        channels.append(deterministic)

    usable = [c for c in channels if c.available and c.value is not None]
    applicable = [c for c in channels if c.applicable]
    notes: list[str] = []
    for c in channels:
        if c.value is None or not c.available:
            kind = "not applicable" if not c.applicable else "unavailable"
            notes.append(f"{c.name} {kind}: {c.failure_reason or 'no value produced'}")

    pairs: list[PairComparison] = []
    for i, left in enumerate(channels):
        for right in channels[i + 1 :]:
            pairs.append(_compare_pair(left, right, tolerance))

    disagreements = {p.disagreement for p in pairs if p.disagreement is not DisagreementType.NONE}
    coverage = _coverage_penalty(len(usable), len(applicable))

    if len(usable) < 2:
        # Nothing can be cross-checked. This is genuinely uncertain, and calling
        # it agreement would manufacture confidence out of missing data.
        notes.append("fewer than two channels produced a value; no cross-check possible")
        return ConsistencyReport(
            Verdict.UNCERTAIN,
            0.5 * coverage,
            frozenset(disagreements | {DisagreementType.CHANNEL_UNAVAILABLE}),
            tuple(pairs),
            len(usable),
            tuple(notes),
            base_score=0.5,
            coverage=coverage,
            applicable_channels=len(applicable),
        )

    comparable = [p for p in pairs if p.disagreement is not DisagreementType.CHANNEL_UNAVAILABLE]
    # Weakest link, not average: one channel contradicting the others is the
    # signal, and averaging it away is precisely what must not happen.
    base = min((p.score for p in comparable), default=0.5)

    ab = next(
        (p for p in pairs if {p.left, p.right} == {channel_a.name, channel_b.name}),
        None,
    )
    channels_agree = ab is not None and ab.disagreement is DisagreementType.NONE

    deterministic_conflict = False
    if deterministic is not None and deterministic.value is not None and channels_agree:
        det_pairs = [
            p
            for p in pairs
            if deterministic.name in (p.left, p.right)
            and p.disagreement is not DisagreementType.CHANNEL_UNAVAILABLE
        ]
        if any(p.disagreement is not DisagreementType.NONE for p in det_pairs):
            deterministic_conflict = True
            disagreements.add(DisagreementType.DETERMINISTIC_CONFLICT)
            notes.append(
                "both reasoning channels agree but the deterministic verifier "
                "disagrees - the 'agree and both wrong' case, which channel "
                "agreement alone cannot detect"
            )

    score = base * coverage
    if deterministic_conflict:
        score *= _DETERMINISTIC_OVERRULE_PENALTY

    if deterministic_conflict:
        verdict = Verdict.DISAGREE
    elif any(p.disagreement is not DisagreementType.NONE for p in comparable):
        verdict = Verdict.DISAGREE
    elif len(usable) < len(channels):
        # Everything that could be compared agreed, but a channel is missing, so
        # the evidence is thinner than a full agreement.
        verdict = Verdict.AGREE
        notes.append("agreement established on a reduced set of channels")
    else:
        verdict = Verdict.AGREE

    return ConsistencyReport(
        verdict,
        max(0.0, min(1.0, score)),
        frozenset(disagreements) or frozenset({DisagreementType.NONE}),
        tuple(pairs),
        len(usable),
        tuple(notes),
        base_score=max(0.0, min(1.0, base)),
        coverage=coverage,
        applicable_channels=len(applicable),
    )
