"""Confidence and risk scoring (spec Module 15).

Produces the **continuous risk score** the whole detection evaluation ranks on.

Three things about this module are load-bearing, and each is a place the research
result could quietly break.

**1. The score is RISK, not confidence.** `EVALUATION.md` §5.1 defines
`s ∈ [0,1]` with *higher meaning more likely wrong*, and the label `y = 1` iff
the final answer is incorrect. The consistency engine's score runs the other way -
higher means the channels agree. Emitting one where the other is expected does not
crash anything; it silently inverts every AUROC in the paper, landing at
`1 - AUROC`, which for a working detector looks like a broken one and for a broken
one looks like a working one. The inversion happens exactly once, here, and
`RiskAssessment` only ever exposes risk.

**2. Bands are derived, never stored.** The spec's user-facing example (§23) shows
`Confidence: HIGH / Risk: LOW`. Those are a presentation layer computed from `s`.
AUROC over three ordinal buckets is not a meaningful quantity, and AUROC is the
primary metric - so the continuous value is the representation and the band is a
view of it.

**3. Uncalibrated is honest, and is still valid for the primary metric.** AUROC is
rank-based, so it is invariant under any monotone transform of `s`: an uncalibrated
score ranks exactly as well as its calibrated version, and the headline number is
available before any calibration exists. What is *not* available is the reliability
claim - "when this says 0.9 it is wrong 90% of the time" - because that needs
Brier/ECE against held-out gold, which arrives with Module 26 (D23). So
`RiskAssessment.calibrated` is `False` and every consumer can see it. Reporting an
ECE from these weights would be fabricated evidence.

**Why coverage and contradiction are separate features.** They mean different
things. Contradiction is *evidence of error* - the channels actively disagree.
Coverage shortfall is *absence of corroboration* - uncertainty. A question no
verifier could apply to is not thereby more likely wrong. RX-007 showed what
happens when the two are multiplied together upstream: every lookup was capped
below every computed answer, so the score partly encoded "is this a lookup?"
instead of "is this wrong?", which is a systematic confound in the metric AUROC
ranks on. `ConsistencyReport` now exposes both factors and this module weighs them
apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.verification.consistency import (
    ConsistencyReport,
    DisagreementType,
    Verdict,
)

__all__ = [
    "FacetStatus",
    "RiskFeatures",
    "RiskAssessment",
    "assess",
    "band_for",
    "DEFAULT_ABSTAIN_THRESHOLD",
]


def band_for(risk_score: float | None) -> str:
    """The §23 presentation band for a risk score, or UNSCORED for no score.

    Module-level so that a caller holding only a stored float - the API serving
    a recorded answer, which has no `RiskAssessment` to hand - bands it by the
    same thresholds as the live path instead of copying them. They were briefly
    copied, and the recorded-answer endpoint reported `UNSCORED` for answers
    that carried a perfectly good score.

    `None` is UNSCORED and means it: arms B1-B4 and D have no detector, and
    that is not the same as a low risk.
    """
    if risk_score is None:
        return "UNSCORED"
    if risk_score >= 0.6:
        return "HIGH"
    return "MEDIUM" if risk_score >= 0.25 else "LOW"


class FacetStatus(Enum):
    """Per-dimension status for the user-facing summary (spec §23, Module 16)."""

    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Disagreements that are CATEGORICAL rather than graded. A scale confusion, a sign
# flip, or a currency mismatch is not "a bit wrong" - in financial QA it is a
# different number entirely, and the magnitude of the gap carries no extra
# information once the class is known. MAGNITUDE_MISMATCH is deliberately absent:
# it is already graded continuously inside the consistency base score, and
# double-counting it here would let one disagreement contribute twice.
_HARD_DISAGREEMENTS = frozenset(
    {
        DisagreementType.SCALE_MISMATCH,
        DisagreementType.SIGN_MISMATCH,
        DisagreementType.CURRENCY_MISMATCH,
        DisagreementType.UNIT_KIND_MISMATCH,
    }
)

_UNIT_DISAGREEMENTS = frozenset(
    {DisagreementType.SCALE_MISMATCH, DisagreementType.CURRENCY_MISMATCH,
     DisagreementType.UNIT_KIND_MISMATCH}
)

# PROVISIONAL WEIGHTS - calibration parameters, not validated constants.
#
# Spec §23 requires the scoring methodology be experimentally justified, and it
# cannot be until FinVerify-IND exists (D23). They are grouped here so the
# validation sweep has exactly one place to change, and so that no reader mistakes
# them for measured quantities. The ORDERING between them is the part that encodes
# real judgement and should survive calibration:
#
#   evidence absent  >  deterministic conflict  >  hard disagreement
#                    >  contradiction  >  no corroboration
#
# The reasoning: an answer with no evidence cannot be right, so it tops the scale.
# A deterministic conflict outranks a plain disagreement because it is the only
# signal that catches "both channels agree and both are wrong" - the blind spot
# EVALUATION.md §5.4 names. Missing corroboration sits lowest because it is
# uncertainty rather than error, and in finance the costly mistake is FNR - a wrong
# answer passed as trustworthy - so absence of a check must still raise risk, just
# never as much as a check that actively failed.
_W_NO_EVIDENCE = 1.00
_W_DETERMINISTIC_CONFLICT = 0.90
_W_HARD_DISAGREEMENT = 0.85
_W_CONTRADICTION = 1.00  # scaled by the observed contradiction, so this is a cap
_W_NO_CORROBORATION = 0.45
_W_ARBITER_ABSTAINED = 0.55

# How much a secondary signal may add to the dominant one. At 0.5 a stack of weak
# reasons to distrust can never outrank a single strong one, while still moving
# the score enough to break ties - which matters, because AUROC ranks and a score
# with few distinct values loses discriminative power.
_RESIDUAL_WEIGHT = 0.5

# Provisional. EVALUATION.md §5.2 requires the operating threshold be selected on
# validation and then FROZEN; selecting it on test would inflate precision and
# recall by construction. This value is a placeholder for the risk-coverage
# machinery to have something to call, not a chosen operating point.
DEFAULT_ABSTAIN_THRESHOLD = 0.5


@dataclass(frozen=True)
class RiskFeatures:
    """The inputs to the score, kept inspectable.

    Recorded per answer so the calibration sweep can refit weights from stored run
    artifacts without re-running the pipeline - which matters when a full campaign
    is quota-bound (D14/D21) and re-running is measured in days.
    """

    contradiction: float
    coverage: float
    usable_channels: int
    applicable_channels: int
    evidence_blocks: int
    verdict: Verdict
    hard_disagreements: frozenset[DisagreementType]
    deterministic_conflict: bool
    arbiter_abstained: bool

    def as_dict(self) -> dict:
        return {
            "contradiction": round(self.contradiction, 6),
            "coverage": round(self.coverage, 6),
            "usable_channels": self.usable_channels,
            "applicable_channels": self.applicable_channels,
            "evidence_blocks": self.evidence_blocks,
            "verdict": self.verdict.value,
            "hard_disagreements": sorted(d.value for d in self.hard_disagreements),
            "deterministic_conflict": self.deterministic_conflict,
            "arbiter_abstained": self.arbiter_abstained,
        }


@dataclass(frozen=True)
class RiskAssessment:
    risk_score: float
    facets: dict[str, FacetStatus]
    features: RiskFeatures
    # False until weights are fitted against held-out gold. Consumers that report
    # calibration-dependent metrics (Brier, ECE, reliability diagrams) must check
    # this and refuse rather than quoting a number these weights cannot support.
    calibrated: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def confidence(self) -> float:
        """The complement, for display only. `risk_score` is the representation."""
        return 1.0 - self.risk_score

    def band(self) -> str:
        """Presentation-only banding (spec §23). Derived, never stored."""
        return band_for(self.risk_score)

    def confidence_band(self) -> str:
        return {"HIGH": "LOW", "MEDIUM": "MEDIUM", "LOW": "HIGH"}[self.band()]

    def should_abstain(self, threshold: float = DEFAULT_ABSTAIN_THRESHOLD) -> bool:
        """Whether to withhold the answer at a given operating point.

        Drives the risk-coverage curve (EVALUATION.md §5.2): accuracy on the
        retained subset as the most-at-risk answers are abstained on.
        """
        return self.risk_score >= threshold

    def as_dict(self) -> dict:
        return {
            "risk_score": round(self.risk_score, 6),
            "risk_band": self.band(),
            "confidence_band": self.confidence_band(),
            "calibrated": self.calibrated,
            "facets": {k: v.value for k, v in self.facets.items()},
            "features": self.features.as_dict(),
            "notes": list(self.notes),
        }


def _combine(signals: list[float]) -> float:
    """Combine independent reasons to distrust into one bounded score.

    Damped noisy-OR: the strongest signal sets the floor, and each weaker one
    closes part of the remaining gap to 1. Chosen over the two obvious
    alternatives because both fail in ways that matter here:

    * **Sum** is unbounded and lets three mild signals outrank one certainty -
      an answer with slightly thin coverage on three axes would score above one
      with a confirmed sign flip.
    * **Max** is bounded and correctly ordered but coarse: many answers collapse
      onto identical scores, and AUROC rewards granular ranking. Ties are not
      neutral in AUROC - they count as half-discordant.

    Damping keeps the ordering of max while restoring the granularity.
    """
    risk = 0.0
    for signal in sorted(signals, reverse=True):
        if signal <= 0.0:
            continue
        weight = 1.0 if risk == 0.0 else _RESIDUAL_WEIGHT
        risk += (1.0 - risk) * signal * weight
    return max(0.0, min(1.0, risk))


def _facets(
    report: ConsistencyReport,
    *,
    evidence_blocks: int,
    deterministic_conflict: bool,
    deterministic_applied: bool,
) -> dict[str, FacetStatus]:
    """The four dimensions of spec §23's user-facing summary."""
    if evidence_blocks == 0:
        evidence = FacetStatus.FAILED
    elif report.usable_shortfall > 0:
        evidence = FacetStatus.PARTIAL
    else:
        evidence = FacetStatus.VERIFIED

    if not deterministic_applied:
        # The verifier abstained because the question has no arithmetic to check.
        # Reporting that as "unverified" would read as a failed check; it is the
        # maximum verification the question admits.
        arithmetic = FacetStatus.NOT_APPLICABLE
    elif deterministic_conflict:
        arithmetic = FacetStatus.FAILED
    else:
        arithmetic = FacetStatus.VERIFIED

    if report.verdict is Verdict.AGREE:
        agreement = FacetStatus.VERIFIED
    elif report.verdict is Verdict.UNCERTAIN:
        agreement = FacetStatus.PARTIAL
    else:
        agreement = FacetStatus.FAILED

    unit = (
        FacetStatus.FAILED
        if report.disagreements & _UNIT_DISAGREEMENTS
        else FacetStatus.VERIFIED
    )

    return {
        "evidence": evidence,
        "arithmetic": arithmetic,
        "agreement": agreement,
        "unit": unit,
    }


def assess(
    report: ConsistencyReport,
    *,
    evidence_blocks: int,
    arbiter_abstained: bool = False,
    deterministic_applied: bool | None = None,
) -> RiskAssessment:
    """Turn a consistency report into a continuous risk score.

    Args:
        report: output of `consistency.compare`.
        evidence_blocks: how many evidence blocks the channels were given. Zero
            means the answer cannot be grounded, whatever the channels said about
            it - agreement on no evidence is agreement about nothing.
        arbiter_abstained: whether Module 13 declined to resolve a disagreement.
            An arbiter that abstains has not reduced the risk; treating its
            silence as resolution would manufacture confidence.
        deterministic_applied: whether the deterministic verifier could apply.
            Defaults to inferring it from the report's applicable-channel count.
    """
    conflict = DisagreementType.DETERMINISTIC_CONFLICT in report.disagreements
    hard = frozenset(report.disagreements) & _HARD_DISAGREEMENTS

    if deterministic_applied is None:
        deterministic_applied = report.applicable_channels >= 3

    contradiction = max(0.0, 1.0 - report.base_score)
    notes: list[str] = []

    signals: list[float] = []

    if evidence_blocks == 0:
        signals.append(_W_NO_EVIDENCE)
        notes.append(
            "no evidence was retrieved; the answer cannot be grounded regardless "
            "of whether the channels agreed"
        )
    if conflict:
        signals.append(_W_DETERMINISTIC_CONFLICT)
        notes.append(
            "the deterministic verifier contradicts agreeing reasoning channels - "
            "the 'both agree, both wrong' case"
        )
    if hard:
        signals.append(_W_HARD_DISAGREEMENT)
        notes.append(
            "categorical disagreement: " + ", ".join(sorted(d.value for d in hard))
        )
    if contradiction > 0:
        signals.append(_W_CONTRADICTION * contradiction)
    if report.coverage < 1.0:
        # Scaled by how much corroboration was missed, so one absent channel out
        # of three costs less than two.
        signals.append(_W_NO_CORROBORATION * (1.0 - report.coverage))
        notes.append(
            "corroboration was possible but not obtained; this is uncertainty "
            "rather than evidence of error"
        )
    if arbiter_abstained:
        signals.append(_W_ARBITER_ABSTAINED)
        notes.append("the arbiter declined to resolve the disagreement")

    return RiskAssessment(
        risk_score=_combine(signals),
        facets=_facets(
            report,
            evidence_blocks=evidence_blocks,
            deterministic_conflict=conflict,
            deterministic_applied=deterministic_applied,
        ),
        features=RiskFeatures(
            contradiction=contradiction,
            coverage=report.coverage,
            usable_channels=report.available_channels,
            applicable_channels=report.applicable_channels,
            evidence_blocks=evidence_blocks,
            verdict=report.verdict,
            hard_disagreements=hard,
            deterministic_conflict=conflict,
            arbiter_abstained=arbiter_abstained,
        ),
        calibrated=False,
        notes=tuple(notes),
    )
