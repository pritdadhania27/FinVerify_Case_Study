"""QA metrics (spec §30, EVALUATION.md §3).

Small module, three load-bearing rules:

1. **Non-answers are incorrect, and separately counted.** Abstaining is safer
   behaviour than guessing, so it earns its own number - but it must not earn
   accuracy. Excluding abstentions from the denominator is the classic way a
   cautious system posts a spectacular accuracy figure on the four questions it
   was willing to answer.
2. **Failure to execute is incorrect, never excluded.** Execution accuracy over
   only the programs that ran measures the interpreter, not the channel.
3. **Exact match is TAU = 0 after normalisation**, not string equality. String
   equality over financial text measures formatting - "Rs. 1,234 crore" against
   "12340000000" - and would report zero for a perfect answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backend.core.financial_value import FinancialValue

from .correctness import TAU_PRIMARY, TAU_SENSITIVITY, CorrectnessJudgment, judge

__all__ = ["AnswerRecord", "QAReport", "evaluate_qa"]


@dataclass(frozen=True)
class AnswerRecord:
    """One question's outcome for one arm.

    `abstained` and `prediction is None` are deliberately separate. A system that
    said "I cannot determine this from the evidence" and a system whose reply
    could not be parsed both produce no number, but only the first is a decision.
    Collapsing them would report a parser bug as principled caution.
    """

    question_id: str
    prediction: FinancialValue | None
    gold: FinancialValue | None
    abstained: bool = False
    # None means this arm has no program channel at all (B1, B2), which is not
    # the same as having one that failed. Execution accuracy is only defined
    # over arms where the value is a bool.
    program_executed: bool | None = None
    ambiguous: bool = False
    stratum: str | None = None


@dataclass(frozen=True)
class QAReport:
    n: int
    exact_match: float
    numerical_accuracy: float
    execution_accuracy: float | None
    abstention_rate: float
    parse_failure_rate: float
    unit_assumption_rate: float
    tau_sensitivity: dict[str, float]
    mismatch_counts: dict[str, int]
    judgments: tuple[CorrectnessJudgment, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "exact_match": self.exact_match,
            "numerical_accuracy": self.numerical_accuracy,
            "execution_accuracy": self.execution_accuracy,
            "abstention_rate": self.abstention_rate,
            "parse_failure_rate": self.parse_failure_rate,
            "unit_assumption_rate": self.unit_assumption_rate,
            "tau_sensitivity": self.tau_sensitivity,
            "mismatch_counts": self.mismatch_counts,
            "notes": list(self.notes),
        }


def evaluate_qa(
    records: list[AnswerRecord],
    *,
    tau: Decimal = TAU_PRIMARY,
    taus: tuple[Decimal, ...] = TAU_SENSITIVITY,
    include_ambiguous: bool = False,
) -> QAReport:
    """Aggregate QA metrics over `records`.

    `include_ambiguous` is False by default per D22: the ambiguity subset is
    scored on its own and never pooled into a headline figure, because a
    question with three defensible answers measures the dataset rather than the
    system. Pooling it would move the headline number by an amount determined by
    how many ambiguous questions happened to be written.
    """
    graded = [r for r in records if include_ambiguous or not r.ambiguous]
    notes: list[str] = []
    excluded = len(records) - len(graded)
    if excluded:
        notes.append(
            f"{excluded} ambiguous question(s) excluded from these figures per D22; "
            "they are reported as their own subset"
        )

    if not graded:
        return QAReport(
            n=0,
            exact_match=0.0,
            numerical_accuracy=0.0,
            execution_accuracy=None,
            abstention_rate=0.0,
            parse_failure_rate=0.0,
            unit_assumption_rate=0.0,
            tau_sensitivity={str(t): 0.0 for t in taus},
            mismatch_counts={},
            notes=tuple(notes + ["no gradable records"]),
        )

    ungradable = [r for r in graded if r.gold is None]
    if ungradable:
        notes.append(
            f"{len(ungradable)} record(s) have no gold value and are excluded from "
            "accuracy; a question without gold cannot be right or wrong"
        )
    graded = [r for r in graded if r.gold is not None]
    if not graded:
        return QAReport(
            n=0,
            exact_match=0.0,
            numerical_accuracy=0.0,
            execution_accuracy=None,
            abstention_rate=0.0,
            parse_failure_rate=0.0,
            unit_assumption_rate=0.0,
            tau_sensitivity={str(t): 0.0 for t in taus},
            mismatch_counts={},
            notes=tuple(notes),
        )

    judgments = [judge(r.prediction, r.gold, tau=tau) for r in graded]
    exact = [judge(r.prediction, r.gold, tau=Decimal(0)) for r in graded]

    mismatch_counts: dict[str, int] = {}
    for j in judgments:
        mismatch_counts[j.mismatch.value] = mismatch_counts.get(j.mismatch.value, 0) + 1

    executable = [r for r in graded if r.program_executed is not None]
    if executable:
        execution_accuracy = sum(
            1
            for r, j in zip(graded, judgments, strict=True)
            if r.program_executed and j.correct
        ) / len(executable)
    else:
        execution_accuracy = None
        notes.append("no program channel in this arm: execution accuracy is not defined")

    n = len(graded)
    return QAReport(
        n=n,
        exact_match=sum(1 for j in exact if j.correct) / n,
        numerical_accuracy=sum(1 for j in judgments if j.correct) / n,
        execution_accuracy=execution_accuracy,
        abstention_rate=sum(1 for r in graded if r.abstained) / n,
        # A prediction that is absent WITHOUT an abstention is a parse or
        # pipeline failure. Reported separately because the fix is different:
        # abstention is a model behaviour, this is a bug.
        parse_failure_rate=sum(
            1 for r in graded if r.prediction is None and not r.abstained
        ) / n,
        unit_assumption_rate=sum(1 for j in judgments if j.unit_assumed) / n,
        tau_sensitivity={
            str(t): sum(1 for r in graded if judge(r.prediction, r.gold, tau=t).correct) / n
            for t in taus
        },
        mismatch_counts=mismatch_counts,
        judgments=tuple(judgments),
        notes=tuple(notes),
    )
