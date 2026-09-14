"""Channel B: program generation and sandboxed execution (spec Module 9).

Writes a Python program that computes the answer from the evidence, validates it
against an AST allowlist, and runs it in a disposable container. The answer is
whatever the program printed - not what the model said the answer would be.

**Independence is enforced by the signature, not by discipline** (D1). This
function accepts a question and evidence. There is no parameter through which
Channel A's answer, reasoning, confidence, or even its existence could be passed,
so the leak cannot be introduced by a caller who forgot. A test asserts the
prompt contains no Channel-A fields; if that test ever fails it is a
research-validity bug, not a test to update.

This matters more than it looks. If Channel B could see Channel A's answer, it
would anchor on it, the channels would agree far more often, and the agreement
would measure anchoring rather than independent corroboration - which would make
the project's central question unanswerable while appearing to answer it well.

**Execution is never optional.** `validate_program` runs first (policy), then the
container (containment). If Docker is unavailable the channel is BLOCKED and
reports so; it never falls back to running generated code on the host.

**The program's own arithmetic is trusted; its inputs are not.** The prompt
requires every figure to be written as a literal taken from the evidence, with a
comment naming its reference, so a wrong answer can be traced to a wrong reading
rather than to invisible arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from backend.services.llm.settings import llm_settings
from backend.agents.evidence import EvidenceBlock, format_evidence
from backend.agents.model_output import strip_reasoning_trace
from backend.core.financial_value import (
    FinancialValue,
    Scale,
    UnitKind,
    detect_units,
)
from backend.services.code_validator import validate_program
from backend.services.sandbox import ExecutionResult, execute_program
from backend.verification.consistency import ChannelAnswer

__all__ = [
    "ProgramChannelResult",
    "PROGRAM_CHANNEL_PROMPT",
    "PROMPT_VERSION",
    "extract_program",
    "run_program_channel",
]

PROMPT_VERSION = "program_v1"

# Structurally disjoint from the natural channel's prompt (D1): different task
# framing, different output contract, no shared reasoning scaffold. It asks for
# a program, never for an opinion about the answer.
PROGRAM_CHANNEL_PROMPT = """\
Write a Python program that computes the answer to a question from financial \
statement evidence.

EVIDENCE
{evidence}

QUESTION
{question}

Requirements:
- Output ONLY Python code. No explanation, no markdown fence.
- Take every figure from the evidence as a literal in the code, with a comment \
naming the evidence reference it came from. Do not use any number that is not in \
the evidence.
- Parentheses around a figure in the evidence mean it is negative: (1,234) is -1234.
- Keep figures in the units the evidence states, and set "unit" to those units.
- Use `decimal.Decimal` for money. Do not use float for currency arithmetic.
- The last line printed must be a JSON object:
    print(json.dumps({{"value": <number>, "unit": "<units>", "steps": ["..."]}}))
- If the evidence is insufficient, print {{"value": null, "unit": null, \
"steps": ["<why>"]}}.
- You may import only: json, math, decimal, statistics, fractions.
- No file access, no network, no input().
"""

_FENCE = re.compile(r"```(?:python|py)?\s*(.+?)```", re.S)


@dataclass(frozen=True)
class ProgramChannelResult:
    """Channel B's output, plus everything needed to audit how it got there."""

    answer: ChannelAnswer
    program: str = ""
    validation_violations: tuple[str, ...] = ()
    execution: ExecutionResult | None = None
    steps: tuple[str, ...] = ()
    stated_unit: str | None = None
    raw_text: str = ""
    usage: object | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def value(self) -> FinancialValue | None:
        return self.answer.value

    @property
    def executed(self) -> bool:
        return self.execution is not None and self.execution.ok


def extract_program(text: str) -> str:
    """Pull the program out of a reply that may be fenced or prefaced.

    Models are told not to fence and fence anyway. Feeding the fence markers to
    the AST validator produces a syntax error that reads as a refusal to run
    unsafe code, which would misattribute a formatting quirk to the safety layer.
    """
    if not text:
        return ""
    # A reasoning model's <think> block is prose, and prose is a syntax error.
    # Handing it to the AST validator reports "invalid syntax", which reads as
    # the security layer refusing unsafe code rather than as a formatting quirk.
    text = strip_reasoning_trace(text)
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def _unit_kind_for(unit: str | None) -> tuple[UnitKind, Scale, str | None]:
    """Map the program's stated unit onto the value model's vocabulary."""
    lowered = (unit or "").lower()
    if "percent" in lowered or "%" in lowered:
        return UnitKind.PERCENT, Scale.UNIT, None
    if "ratio" in lowered or "times" in lowered or lowered in {"x", "multiple"}:
        return UnitKind.RATIO, Scale.UNIT, None

    # One vocabulary, defined once, in the module that owns unit parsing. The
    # second copy that used to live here knew five long-form labels and no
    # abbreviations, so "Rs cr", "Rs mn", "USD bn", "INR lacs", "Rs. '000" and
    # "INR trillion" all collapsed to Scale.UNIT while Channel A resolved them
    # correctly through parse_financial_value - a clean power-of-ten split on
    # identical text, which the consistency engine then reported as
    # SCALE_MISMATCH. A parser gap was manufacturing the project's headline
    # error class, inflating the disagreement rate and poisoning the exact cell
    # of the error taxonomy the research claims to detect.
    #
    # The percent/ratio branch above stays hand-written: parse_financial_value
    # has no RATIO vocabulary ("times", "x", "multiple").
    detected_scale, currency = detect_units(lowered)
    scale = detected_scale or Scale.UNIT
    kind = UnitKind.CURRENCY if currency or scale is not Scale.UNIT else UnitKind.UNKNOWN
    return kind, scale, currency


def _value_from_execution(result: ExecutionResult) -> FinancialValue | None:
    payload = result.payload or {}
    raw = payload.get("value")
    if raw is None:
        return None
    try:
        # str() first: float -> Decimal carries binary rounding into money.
        amount = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    unit = payload.get("unit")
    kind, scale, currency = _unit_kind_for(unit if isinstance(unit, str) else None)
    return FinancialValue(
        amount=amount,
        scale=scale,
        unit_kind=kind,
        currency=currency,
        raw_text=f"{raw} {unit}".strip() if unit else str(raw),
        trace=("produced by the program channel and executed in the sandbox",),
    )


def run_program_channel(
    question: str,
    evidence: list[EvidenceBlock],
    *,
    provider,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    name: str = "program",
    sandbox_config=None,
) -> ProgramChannelResult:
    """Generate, validate and execute a program that answers `question`.

    NOTE the parameter list. There is deliberately no way to pass Channel A's
    output here (D1). Adding one would make channel agreement a measure of
    anchoring rather than of independent corroboration.
    """
    settings = llm_settings()
    temperature = settings.temperature if temperature is None else temperature
    max_tokens = settings.max_tokens if max_tokens is None else max_tokens

    prompt = PROGRAM_CHANNEL_PROMPT.format(
        evidence=format_evidence(evidence), question=question
    )
    from backend.services.llm.base import Message, Role

    metadata = {"prompt_version": PROMPT_VERSION, "model": model}

    try:
        response = provider.complete(
            [Message(Role.USER, prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:  # noqa: BLE001 - a channel failure is data, not a crash
        return ProgramChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=f"{type(exc).__name__}: {exc}",
            ),
            metadata={**metadata, "error_type": type(exc).__name__},
        )

    raw_text = response.text or ""
    program = extract_program(raw_text)
    usage = getattr(response, "usage", None)
    metadata["model"] = getattr(response, "model", model)

    if not program:
        return ProgramChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason="no program was produced (empty reply, see D17)",
            ),
            raw_text=raw_text, usage=usage, metadata=metadata,
        )

    # Policy before containment. Validation failures are reported, never
    # executed anyway "just to see".
    report = validate_program(program)
    if not report.ok:
        return ProgramChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=(
                    f"generated program rejected by the AST allowlist: "
                    f"{'; '.join(report.violations[:3])}"
                ),
            ),
            program=program, validation_violations=report.violations,
            raw_text=raw_text, usage=usage, metadata=metadata,
        )

    execution = execute_program(program, config=sandbox_config)
    if not execution.ok:
        return ProgramChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=f"{execution.status.name}: {execution.error or 'no detail'}",
            ),
            program=program, execution=execution, raw_text=raw_text,
            usage=usage, metadata=metadata,
        )

    payload = execution.payload or {}
    steps = tuple(str(s) for s in (payload.get("steps") or ()))
    unit = payload.get("unit")
    unit = str(unit) if unit else None
    value = _value_from_execution(execution)

    if value is None:
        # The program ran and deliberately returned null - an abstention, which
        # is materially different from a crash and is recorded as such.
        return ProgramChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason="program executed and reported the evidence as insufficient",
            ),
            program=program, execution=execution, steps=steps, stated_unit=unit,
            raw_text=raw_text, usage=usage, metadata=metadata,
        )

    return ProgramChannelResult(
        answer=ChannelAnswer(name=name, value=value, available=True),
        program=program,
        execution=execution,
        steps=steps,
        stated_unit=unit,
        raw_text=raw_text,
        usage=usage,
        metadata=metadata,
    )
