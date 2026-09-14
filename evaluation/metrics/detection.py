"""Hallucination-detection metrics (EVALUATION.md §5) - the core of the contribution.

The detection task, stated exactly once so nothing downstream has to guess:

    label  y in {0,1}   y = 1 iff the final answer is INCORRECT per correctness.judge
    score  s in [0,1]   the system's continuous RISK score, higher = more likely wrong

**The direction is the single easiest thing to get wrong here.** The consistency
engine's score runs the other way - higher means the channels agreed - and
feeding it in unflipped yields exactly `1 - AUROC`, which is a plausible-looking
number that inverts every conclusion. `backend.verification.confidence.assess`
already emits risk in the direction this module expects; anything else must be
flipped by its caller, not silently here. `roc_auc` refuses to guess.

**Ties are not free.** Half the reason to implement AUROC rather than call a
library is that a naive threshold sweep silently rewards tie-ordering luck. Every
estimator below is computed over distinct score thresholds with mid-ranks, so a
detector that assigns the same risk to a right and a wrong answer gets the 0.5
credit it has earned and no more. A detector that emits three ordinal bands is
mostly ties, which is why EVALUATION.md §5.1 requires a continuous score.

**Undefined is None, not 0.5.** With no incorrect answers in a stratum, AUROC has
no value; returning the chance level would put a number in a table that reads as
"the detector was no better than random" when the truth is "this stratum could
not test it". Callers must handle None. This bites in exactly the case that
matters - a small retrieval-caused stratum in the H2 split.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "roc_auc",
    "average_precision",
    "ThresholdMetrics",
    "metrics_at_threshold",
    "select_threshold",
    "RiskCoverage",
    "risk_coverage_curve",
    "DetectionReport",
    "evaluate_detection",
    "contingency",
]


def _validate(scores: list[float], labels: list[int]) -> None:
    if len(scores) != len(labels):
        raise ValueError(f"scores and labels differ in length: {len(scores)} vs {len(labels)}")
    bad = {label for label in labels if label not in (0, 1)}
    if bad:
        raise ValueError(f"labels must be 0 or 1; saw {sorted(bad)}")


def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    """AUROC by the Mann-Whitney U identity, with mid-ranks for ties.

    AUROC is the probability that a randomly chosen incorrect answer is scored
    riskier than a randomly chosen correct one, with ties counting half. Computed
    from ranks rather than a threshold sweep because the rank formulation handles
    ties exactly and needs no interpolation choice.

    Being rank-based is also why AUROC is legitimate on an UNCALIBRATED score:
    any strictly monotone recalibration leaves every rank unchanged, so it leaves
    AUROC unchanged. Brier score and ECE have no such property and must not be
    quoted while `RiskAssessment.calibrated` is False.

    Returns None when one class is absent - the metric is undefined, not 0.5.
    """
    _validate(scores, labels)
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        # Mid-rank: every member of a tied block gets the average of the ranks
        # the block occupies, which is what makes a tie worth exactly half.
        mid = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mid
        i = j + 1

    rank_sum = sum(ranks[i] for i in range(len(labels)) if labels[i] == 1)
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def average_precision(scores: list[float], labels: list[int]) -> float | None:
    """AUPRC as average precision, summed over distinct thresholds.

    EVALUATION.md §5.2 reports this beside AUROC because errors are the minority
    class and AUROC flatters imbalance: a detector can look strong at AUROC 0.85
    while its flagged set is mostly correct answers.

        AP = sum_k (R_k - R_{k-1}) * P_k

    over distinct score thresholds, with no interpolation between points.
    Trapezoidal interpolation on a PR curve is optimistic - it draws a line
    through operating points that do not exist - so it is not used.
    """
    _validate(scores, labels)
    positives = sum(labels)
    if positives == 0 or positives == len(labels):
        return None

    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ap = 0.0
    previous_recall = 0.0
    true_positives = 0
    seen = 0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            seen += 1
            true_positives += labels[order[k]]
        precision = true_positives / seen
        recall = true_positives / positives
        ap += (recall - previous_recall) * precision
        previous_recall = recall
        i = j + 1
    return ap


@dataclass(frozen=True)
class ThresholdMetrics:
    """Operating-point metrics. `flagged` means score >= threshold."""

    threshold: float
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Correct answers wrongly flagged - the cost of distrust."""
        denominator = self.false_positives + self.true_negatives
        return self.false_positives / denominator if denominator else 0.0

    @property
    def false_negative_rate(self) -> float:
        """Wrong answers passed as trustworthy - the cost that matters in finance."""
        denominator = self.false_negatives + self.true_positives
        return self.false_negatives / denominator if denominator else 0.0

    def as_dict(self) -> dict:
        return {
            "threshold": self.threshold,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "false_positive_rate": self.false_positive_rate,
            "false_negative_rate": self.false_negative_rate,
        }


def metrics_at_threshold(
    scores: list[float], labels: list[int], threshold: float
) -> ThresholdMetrics:
    _validate(scores, labels)
    tp = fp = tn = fn = 0
    for score, label in zip(scores, labels, strict=True):
        flagged = score >= threshold
        if flagged and label == 1:
            tp += 1
        elif flagged:
            fp += 1
        elif label == 1:
            fn += 1
        else:
            tn += 1
    return ThresholdMetrics(threshold, tp, fp, tn, fn)


def select_threshold(
    scores: list[float],
    labels: list[int],
    *,
    objective: str = "f1",
    min_recall: float | None = None,
) -> float:
    """Pick the operating threshold ON VALIDATION, to be frozen before test.

    EVALUATION.md §5.2 is explicit that choosing this on test inflates precision
    and recall by construction - the threshold would be fitted to the data it is
    then scored on. Nothing enforces which split the caller passes, so the
    campaign runner records the split this was selected on in the run artifact
    and the write-up quotes it.

    `min_recall` supports the finance-shaped objective: hold recall at or above a
    floor (missing a wrong answer is the expensive error) and take the best
    precision available there.
    """
    _validate(scores, labels)
    if not scores:
        raise ValueError("cannot select a threshold from an empty set")
    candidates = sorted({*scores, 0.0, 1.0 + 1e-9})

    best_threshold = candidates[0]
    best_key = (-1.0, -1.0)
    for threshold in candidates:
        m = metrics_at_threshold(scores, labels, threshold)
        if min_recall is not None and m.recall < min_recall:
            continue
        key = (m.precision, m.recall) if objective == "precision" else (m.f1, m.recall)
        if key > best_key:
            best_key, best_threshold = key, threshold
    if best_key == (-1.0, -1.0):
        # No threshold met the recall floor. Returning the lowest candidate
        # flags everything, which meets recall trivially and makes the failure
        # visible in the precision column rather than hiding it.
        return candidates[0]
    return best_threshold


@dataclass(frozen=True)
class RiskCoverage:
    """The selective-prediction view: what accuracy survives abstention."""

    points: tuple[tuple[float, float], ...]  # (coverage, error rate on retained)
    aurc: float
    optimal_aurc: float

    @property
    def excess_aurc(self) -> float:
        """AURC above the best any ranking of these same answers could achieve.

        Raw AURC is dominated by the arm's base error rate, so comparing raw
        AURC across arms partly compares their accuracy rather than their
        ranking. The excess isolates the ranking.
        """
        return self.aurc - self.optimal_aurc

    def as_dict(self) -> dict:
        return {
            "aurc": self.aurc,
            "optimal_aurc": self.optimal_aurc,
            "excess_aurc": self.excess_aurc,
            "points": [{"coverage": c, "error_rate": e} for c, e in self.points],
        }


def risk_coverage_curve(scores: list[float], labels: list[int]) -> RiskCoverage:
    """Error rate on the retained subset as the riskiest answers are abstained on.

    Answers are retained in ascending risk order, so coverage 0.6 means "the 60%
    the system was most confident about". This is the curve that says whether the
    detector is *useful*: a system that cannot separate right from wrong shows a
    flat line at its base error rate no matter how much it abstains.
    """
    _validate(scores, labels)
    if not scores:
        return RiskCoverage(points=(), aurc=0.0, optimal_aurc=0.0)

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    points: list[tuple[float, float]] = []
    errors = 0
    for position, index in enumerate(order, start=1):
        errors += labels[index]
        points.append((position / len(order), errors / position))
    aurc = sum(error for _, error in points) / len(points)

    # The best achievable curve on these same answers: every correct answer
    # ranked ahead of every incorrect one.
    ideal = sorted(labels)
    errors = 0
    optimal_points = []
    for position, label in enumerate(ideal, start=1):
        errors += label
        optimal_points.append(errors / position)
    optimal = sum(optimal_points) / len(optimal_points)
    return RiskCoverage(points=tuple(points), aurc=aurc, optimal_aurc=optimal)


def contingency(agreed: list[bool], correct: list[bool]) -> dict[str, int]:
    """Agreement x correctness (EVALUATION.md §5.4).

    `both_agree_wrong` is the method's blind spot and the reason this table is
    reported rather than summarised: a high AUROC with a large both-agree-wrong
    cell means the detector ranks well among the errors it can see while a whole
    class of errors is invisible to it. That is a different claim from "detects
    numerical hallucination", and the table is what stops the two being conflated.
    """
    if len(agreed) != len(correct):
        raise ValueError("agreed and correct differ in length")
    cells = {
        "agree_correct": 0,
        "both_agree_wrong": 0,
        "disagree_correct": 0,
        "disagree_wrong": 0,
    }
    for a, c in zip(agreed, correct, strict=True):
        if a and c:
            cells["agree_correct"] += 1
        elif a:
            cells["both_agree_wrong"] += 1
        elif c:
            cells["disagree_correct"] += 1
        else:
            cells["disagree_wrong"] += 1
    return cells


@dataclass(frozen=True)
class DetectionReport:
    n: int
    positives: int
    auroc: float | None
    auprc: float | None
    at_threshold: ThresholdMetrics | None
    risk_coverage: RiskCoverage
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "positives": self.positives,
            "base_error_rate": self.positives / self.n if self.n else 0.0,
            "auroc": self.auroc,
            "auprc": self.auprc,
            "at_threshold": self.at_threshold.as_dict() if self.at_threshold else None,
            "risk_coverage": self.risk_coverage.as_dict(),
            "notes": list(self.notes),
        }


def evaluate_detection(
    scores: list[float], labels: list[int], *, threshold: float | None = None
) -> DetectionReport:
    """Everything EVALUATION.md §5.2 asks for, in one pass.

    `threshold` is the value frozen on validation. Passing None omits the
    operating-point row entirely rather than inventing a default, because a
    precision figure at an unrecorded threshold is not reproducible.
    """
    _validate(scores, labels)
    notes: list[str] = []
    positives = sum(labels)
    if positives == 0:
        notes.append("no incorrect answers in this set: AUROC and AUPRC are undefined")
    elif positives == len(labels):
        notes.append("every answer is incorrect in this set: AUROC and AUPRC are undefined")
    if len(set(scores)) == 1 and scores:
        notes.append(
            "the risk score is constant across every question: AUROC is 0.5 by "
            "construction and carries no information"
        )
    return DetectionReport(
        n=len(labels),
        positives=positives,
        auroc=roc_auc(scores, labels),
        auprc=average_precision(scores, labels),
        at_threshold=(
            metrics_at_threshold(scores, labels, threshold) if threshold is not None else None
        ),
        risk_coverage=risk_coverage_curve(scores, labels),
        notes=tuple(notes),
    )
