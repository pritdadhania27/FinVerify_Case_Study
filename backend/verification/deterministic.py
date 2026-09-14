"""Deterministic numerical verification (spec Module 10).

An independent verification authority with **no model in the arithmetic path**.
Given operands and an operation, this is pure Python: the same inputs produce the
same output, always, and the result can be checked by hand.

Scope limit, stated honestly (decision D6): this module is deterministic *given*
operands and an operation. Choosing *which* numbers a question refers to comes
from question understanding and retrieval, which are LLM-dependent. Calling the
whole pipeline "deterministic verification" would overclaim, so operand-binding
accuracy is measured separately from calculation correctness.

Failures are returned, not raised. The consistency engine needs to distinguish
"the deterministic verifier says 12.5%" from "the deterministic verifier could
not be applied" - collapsing the second into an exception loses that difference,
and a verifier that vanishes on hard cases silently overstates its coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from enum import Enum

from backend.core.financial_value import FinancialValue, Scale, UnitKind

__all__ = ["Operation", "CalculationResult", "calculate", "SUPPORTED_OPERATIONS"]


class Operation(Enum):
    SUM = "sum"
    DIFFERENCE = "difference"
    PRODUCT = "product"
    RATIO = "ratio"
    PERCENTAGE_CHANGE = "percentage_change"
    GROWTH_RATE = "growth_rate"
    MARGIN = "margin"
    AVERAGE = "average"
    CAGR = "cagr"
    PERCENTAGE_OF = "percentage_of"


SUPPORTED_OPERATIONS = frozenset(Operation)

# Enough precision that intermediate division does not lose significance in
# multi-step ratios, while staying well inside Decimal's exact-arithmetic domain.
_PRECISION = 34


@dataclass(frozen=True)
class CalculationResult:
    """Outcome of a deterministic calculation, including why it failed."""

    ok: bool
    operation: Operation
    value: Decimal | None = None
    unit_kind: UnitKind = UnitKind.UNKNOWN
    currency: str | None = None
    steps: tuple[str, ...] = ()
    error: str | None = None

    def as_percent(self) -> Decimal | None:
        """Percentage-style presentation (0.25 -> 25)."""
        if self.value is None or self.unit_kind is not UnitKind.PERCENT:
            return None
        return self.value * Decimal(100)

    def presented_at(self, scale: Scale) -> Decimal | None:
        if self.value is None or self.unit_kind is UnitKind.PERCENT:
            return None
        return self.value / scale.multiplier


def _fail(op: Operation, error: str, steps: list[str]) -> CalculationResult:
    return CalculationResult(ok=False, operation=op, steps=tuple(steps), error=error)


def _check_additive_compatibility(
    operands: list[FinancialValue], op: Operation, steps: list[str]
) -> str | None:
    """Adding a percentage to a rupee amount is meaningless, not merely odd.

    Returns an error string, or None when the operands may be combined.
    """
    kinds = {o.unit_kind for o in operands}

    # PERCENT is checked before the UNKNOWN tolerance below, because a percentage
    # is dimensionless-fractional and never combines additively with a magnitude.
    # Tolerating UNKNOWN alongside it would let "25% + 100 crore" produce
    # 1000000000.25 - arithmetically clean and completely meaningless.
    if UnitKind.PERCENT in kinds and len(kinds) > 1:
        others = sorted(k.value for k in kinds - {UnitKind.PERCENT})
        return (
            f"incompatible unit kinds for {op.value}: cannot combine a percentage "
            f"with {others}"
        )

    # Among magnitude-like kinds, UNKNOWN is tolerated - an un-annotated table
    # cell is common and its kind is usually inferable from its companions
    # rather than genuinely conflicting.
    concrete = kinds - {UnitKind.UNKNOWN}
    if len(concrete) > 1:
        return f"incompatible unit kinds for {op.value}: {sorted(k.value for k in concrete)}"

    currencies = {o.currency for o in operands if o.currency is not None}
    if len(currencies) > 1:
        return (
            f"cannot combine different currencies without an exchange rate: "
            f"{sorted(currencies)}"
        )
    if currencies:
        steps.append(f"currency {next(iter(currencies))} consistent across operands")
    return None


def calculate(
    operation: Operation,
    operands: list[FinancialValue],
    *,
    years: Decimal | int | None = None,
) -> CalculationResult:
    """Apply `operation` to `operands`. Never raises on bad input.

    Operands are combined in **canonical** form, so a value stated in crore and
    one stated in million combine correctly rather than by their written
    magnitudes. That conversion is the single most valuable thing this function
    does: comparing 1 crore against 10 million as raw magnitudes (1 vs 10) is a
    plausible-looking 10x error.
    """
    steps: list[str] = []

    if not operands:
        return _fail(operation, "no operands supplied", steps)
    if any(o is None for o in operands):
        return _fail(operation, "operand missing (evidence not found)", steps)

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        try:
            return _dispatch(operation, operands, years, steps)
        except (InvalidOperation, DivisionByZero, OverflowError, ValueError) as exc:
            # Arithmetic domain errors become structured failures rather than
            # crashing an evaluation run mid-experiment.
            return _fail(operation, f"arithmetic error: {type(exc).__name__}: {exc}", steps)


def _dispatch(
    operation: Operation,
    operands: list[FinancialValue],
    years: Decimal | int | None,
    steps: list[str],
) -> CalculationResult:
    canon = [o.canonical() for o in operands]
    for original, c in zip(operands, canon, strict=True):
        if original.scale is not Scale.UNIT or original.unit_kind is UnitKind.PERCENT:
            steps.append(f"{original.raw_text or original.amount!s} -> canonical {c}")
    currency = next((o.currency for o in operands if o.currency), None)

    if operation in (Operation.SUM, Operation.DIFFERENCE, Operation.AVERAGE):
        err = _check_additive_compatibility(operands, operation, steps)
        if err:
            return _fail(operation, err, steps)
        kind = next(
            (o.unit_kind for o in operands if o.unit_kind is not UnitKind.UNKNOWN),
            UnitKind.UNKNOWN,
        )

        if operation is Operation.SUM:
            total = sum(canon, Decimal(0))
            steps.append(f"sum of {len(canon)} operands = {total}")
            return CalculationResult(True, operation, total, kind, currency, tuple(steps))

        if operation is Operation.DIFFERENCE:
            if len(operands) != 2:
                return _fail(operation, "difference requires exactly 2 operands", steps)
            diff = canon[0] - canon[1]
            steps.append(f"{canon[0]} - {canon[1]} = {diff}")
            return CalculationResult(True, operation, diff, kind, currency, tuple(steps))

        avg = sum(canon, Decimal(0)) / Decimal(len(canon))
        steps.append(f"mean of {len(canon)} operands = {avg}")
        return CalculationResult(True, operation, avg, kind, currency, tuple(steps))

    if operation is Operation.PRODUCT:
        product = Decimal(1)
        for c in canon:
            product *= c
        steps.append(f"product of {len(canon)} operands = {product}")
        return CalculationResult(True, operation, product, UnitKind.UNKNOWN, currency, tuple(steps))

    # --- Two-operand ratio-like operations -------------------------------
    if operation in (
        Operation.RATIO,
        Operation.PERCENTAGE_CHANGE,
        Operation.GROWTH_RATE,
        Operation.MARGIN,
        Operation.PERCENTAGE_OF,
    ):
        if len(operands) != 2:
            return _fail(operation, f"{operation.value} requires exactly 2 operands", steps)
        a, b = canon

        if operation is Operation.RATIO:
            if b == 0:
                return _fail(operation, "division by zero: denominator is 0", steps)
            r = a / b
            steps.append(f"{a} / {b} = {r}")
            return CalculationResult(True, operation, r, UnitKind.RATIO, None, tuple(steps))

        if operation in (Operation.PERCENTAGE_CHANGE, Operation.GROWTH_RATE):
            # operands are (previous, current)
            previous, current = a, b
            if previous == 0:
                return _fail(
                    operation,
                    "percentage change from a base of 0 is undefined "
                    "(growth from nothing has no finite rate)",
                    steps,
                )
            change = (current - previous) / abs(previous)
            steps.append(f"({current} - {previous}) / |{previous}| = {change}")
            if previous < 0:
                # Growth measured from a negative base (a prior-year loss) is a
                # real case in filings and its sign convention is ambiguous, so
                # it is flagged rather than reported as if unremarkable.
                steps.append(
                    "WARNING: base is negative (prior-year loss); "
                    "percentage-change sign convention is ambiguous"
                )
            return CalculationResult(True, operation, change, UnitKind.PERCENT, None, tuple(steps))

        # MARGIN and PERCENTAGE_OF: operands are (part, whole)
        part, whole = a, b
        if whole == 0:
            return _fail(operation, "division by zero: denominator is 0", steps)
        frac = part / whole
        steps.append(f"{part} / {whole} = {frac}")
        return CalculationResult(True, operation, frac, UnitKind.PERCENT, None, tuple(steps))

    if operation is Operation.CAGR:
        if len(operands) != 2:
            return _fail(operation, "cagr requires exactly 2 operands (begin, end)", steps)
        if years is None:
            return _fail(operation, "cagr requires the number of years", steps)
        n = Decimal(years)
        if n <= 0:
            return _fail(operation, "cagr requires a positive number of years", steps)
        begin, end = canon
        if begin <= 0:
            return _fail(
                operation,
                "cagr is undefined for a non-positive starting value "
                "(a compound growth rate from zero or a loss has no real solution)",
                steps,
            )
        if end < 0:
            return _fail(operation, "cagr is undefined for a negative ending value", steps)
        ratio = end / begin
        rate = ratio ** (Decimal(1) / n) - Decimal(1)
        steps.append(f"({end} / {begin}) ^ (1/{n}) - 1 = {rate}")
        return CalculationResult(True, operation, rate, UnitKind.PERCENT, None, tuple(steps))

    return _fail(operation, f"unsupported operation {operation!r}", steps)
