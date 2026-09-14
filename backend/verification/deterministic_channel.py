"""The deterministic third opinion (spec Module 10, D6).

Binds the question's operands to figures in the evidence, applies the arithmetic,
and returns a `ChannelAnswer` the consistency engine can weigh beside the two
reasoning channels.

**Why a third channel matters more than a third opinion usually would.** Two
channels can only ever agree or disagree. When they agree and are both wrong -
the both-agree-wrong cell that `EVALUATION.md` §5.4 calls the method's blind spot
- nothing in the system notices. The deterministic channel is the only component
that can overrule two agreeing channels, and `consistency.py` weights it to do
exactly that. Without it wired in, the engine has been running with two thirds of
its design.

**It is silent on lookups, by construction.** "What were trade payables?" has no
arithmetic to verify; the answer is a figure read off a row. Producing a
"deterministic answer" there would just be re-reading the evidence and calling
the result corroboration - a third channel that agrees with the other two for
structural reasons rather than independent ones, which would inflate apparent
agreement and make the detection metrics look better than the method is.

So this channel answers **only** computed questions, and abstains loudly
otherwise. Its abstention rate is high and that is the honest number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backend.core.financial_value import FinancialValue
from backend.verification.consistency import ChannelAnswer
from backend.verification.deterministic import Operation, calculate
from backend.verification.operand_binding import BindingResult, bind_operands

__all__ = ["DeterministicChannelResult", "OPERATION_BY_NAME", "run_deterministic_channel"]

# Question-understanding operation names -> the verifier's vocabulary. Operations
# absent here are ones the verifier cannot check, and a question carrying one
# gets an abstention rather than an approximation.
OPERATION_BY_NAME: dict[str, Operation] = {
    "sum": Operation.SUM,
    "difference": Operation.DIFFERENCE,
    "ratio": Operation.RATIO,
    "margin": Operation.MARGIN,
    "average": Operation.AVERAGE,
    "percentage_change": Operation.PERCENTAGE_CHANGE,
    "percentage_of": Operation.PERCENTAGE_OF,
    "cagr": Operation.CAGR,
}


@dataclass(frozen=True)
class DeterministicChannelResult:
    answer: ChannelAnswer
    binding: BindingResult | None = None
    operation: str | None = None
    steps: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)

    @property
    def value(self) -> FinancialValue | None:
        return self.answer.value


def _as_financial_value(result) -> FinancialValue:
    """CalculationResult (a Decimal) -> FinancialValue, respecting BOTH percent
    conventions.

    The two types disagree on what a percentage's magnitude is, and the disagreement
    is silent. `CalculationResult.value` holds a **fraction** (0.25) and offers
    `as_percent()` for display; `FinancialValue` holds the **percentage** (25) and
    divides by 100 in `canonical()`. Passing the fraction straight through would
    therefore canonicalise to 0.0025 - a 100x error, in the exact class this
    project exists to catch, inside the component whose job is catching it.

    Currency values are already canonical here, because `calculate` combines
    operands in canonical form; so scale is UNIT and the amount is the base-unit
    figure.
    """
    from backend.core.financial_value import Scale, UnitKind

    if result.unit_kind is UnitKind.PERCENT:
        amount = result.as_percent()
    else:
        amount = result.value
    return FinancialValue(
        amount=amount,
        scale=Scale.UNIT,
        unit_kind=result.unit_kind,
        currency=result.currency,
        raw_text=f"{amount} ({result.unit_kind.value})",
        trace=tuple(result.steps),
    )


def _abstain(reason: str, *, applicable: bool = True, **kw) -> DeterministicChannelResult:
    """Abstain, distinguishing "could not apply" from "tried and failed".

    `applicable=False` means the question admits no arithmetic for this channel
    to check, so nothing is missing and the consistency engine must not charge a
    coverage penalty for it. `applicable=True` means the check was possible and
    did not succeed, which IS a shortfall of evidence. Conflating the two capped
    every lookup question's score at 0.90 while computed questions reached 1.00 -
    a systematic confound in the metric AUROC ranks on.
    """
    return DeterministicChannelResult(
        answer=ChannelAnswer(
            name="deterministic",
            value=None,
            available=False,
            failure_reason=reason,
            applicable=applicable,
        ),
        **kw,
    )


def run_deterministic_channel(
    spec,
    evidence_blocks: list,
    *,
    column: int = 0,
    name: str = "deterministic",
) -> DeterministicChannelResult:
    """Compute the answer from the evidence with no model in the loop.

    Takes the question spec and the evidence. It never sees either reasoning
    channel's output - the same independence constraint the program channel is
    held to (D1), for the same reason: a verifier that can see the answers it is
    checking is not a verifier.
    """
    operation_name = getattr(spec, "operation", "lookup")

    if operation_name == "lookup":
        return _abstain(
            "no arithmetic to verify: a lookup answer is a figure read from a row, "
            "and re-reading it would be structural corroboration rather than an "
            "independent check",
            applicable=False,
            operation=operation_name,
        )

    operation = OPERATION_BY_NAME.get(operation_name)
    if operation is None:
        return _abstain(
            f"operation {operation_name!r} is outside the deterministic verifier's "
            f"vocabulary",
            applicable=False,
            operation=operation_name,
        )

    metrics = [sq.metric for sq in getattr(spec, "sub_questions", ())]
    if not metrics:
        return _abstain("the question spec carries no operands to bind",
                        applicable=False, operation=operation_name)

    binding = bind_operands(metrics, evidence_blocks, column=column)
    if not binding.complete:
        return _abstain(
            "could not bind every operand to a figure in the evidence: "
            + "; ".join(binding.notes[:3]),
            binding=binding,
            operation=operation_name,
        )

    years: Decimal | int | None = None
    if operation is Operation.CAGR:
        mentioned = (getattr(spec, "metadata", {}) or {}).get("years_mentioned") or []
        if len(mentioned) != 2:
            return _abstain(
                "CAGR needs an explicit period and the question names none",
                binding=binding, operation=operation_name,
            )
        years = abs(int(mentioned[1]) - int(mentioned[0]))

    result = calculate(operation, [op.value for op in binding.operands], years=years)
    if not result.ok:
        # Returned, not raised: "the verifier could not apply" is a different
        # event from "the verifier disagrees", and conflating them would let an
        # inapplicable check read as a passed one.
        return _abstain(
            f"{operation_name} could not be computed: {result.error}",
            binding=binding, operation=operation_name,
        )

    return DeterministicChannelResult(
        answer=ChannelAnswer(name=name, value=_as_financial_value(result), available=True),
        binding=binding,
        operation=operation_name,
        steps=tuple(result.steps),
        metadata={
            "operands": [op.as_dict() for op in binding.operands],
            "column_index": column,
        },
    )
