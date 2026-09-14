"""Channel A: natural-language reasoning over retrieved evidence (spec Module 8).

Reads the evidence and reasons in prose to a number, the way an analyst would.
No code is generated and nothing is executed - that is Channel B's job, and the
two must stay separable for their disagreement to mean anything (D1).

**What this channel is for.** It is not the "good" channel or the "fast" one. It
is one of two independent estimates whose *disagreement* is the signal being
studied. It therefore has to be a fair attempt: a deliberately weakened Channel A
would inflate the disagreement rate and make the method look better than it is.

**Output is parsed, not trusted.** The model is asked for JSON, but a free-tier
model returns prose around JSON, JSON in a fenced block, or an empty string when
its reasoning budget ran out (D17). All three are handled, and a channel that
cannot be parsed becomes an *unavailable* channel rather than a fabricated
answer - `ChannelAnswer(value=None, available=False)`, which the consistency
engine already treats as UNCERTAIN rather than as agreement.

**The value is parsed by `parse_financial_value`, not by `float()`.** The model
answers "1,37,814 crore", and reading that as 137814 - or as 1.0 - is precisely
the scale error the project exists to catch. Units the model states are carried
through so a unit mismatch between channels is detectable rather than silently
resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.llm.settings import llm_settings
from backend.agents.evidence import EvidenceBlock, format_evidence
from backend.agents.model_output import extract_json_object
from backend.core.financial_value import FinancialValue, parse_financial_value
from backend.verification.consistency import ChannelAnswer

__all__ = [
    "NaturalChannelResult",
    "NATURAL_CHANNEL_PROMPT",
    "PROMPT_VERSION",
    "parse_natural_response",
    "run_natural_channel",
]

PROMPT_VERSION = "natural_v1"

NATURAL_CHANNEL_PROMPT = """\
You are a financial analyst answering a question about a company's audited \
financial statements. You reason in words. You do not write code.

EVIDENCE
{evidence}

QUESTION
{question}

Rules:
- Use ONLY the evidence above. If it does not contain what you need, say so.
- Figures are stated in the units named in each evidence header. Report your \
answer in those units and name them.
- Parentheses around a figure mean it is negative: (1,234) is -1234.
- Quote the evidence reference and the exact figure you used for each number.
- Do not round beyond what the evidence supports.

Reply with ONLY a JSON object:
{{"answer": <number or null>,
  "unit": "<e.g. INR crore, percent, ratio, or null>",
  "reasoning": "<your steps, briefly>",
  "evidence_used": ["<refs like E1, E3>"],
  "figures_used": ["<the exact figures you took from the evidence>"],
  "sufficient": <true if the evidence was enough, false otherwise>}}
"""

@dataclass(frozen=True)
class NaturalChannelResult:
    """Channel A's output, including the reasons it may have failed."""

    answer: ChannelAnswer
    reasoning: str = ""
    evidence_used: tuple[str, ...] = ()
    figures_used: tuple[str, ...] = ()
    sufficient: bool = True
    raw_text: str = ""
    stated_unit: str | None = None
    usage: object | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def value(self) -> FinancialValue | None:
        return self.answer.value


def _extract_json(text: str) -> dict | None:
    """Delegates to the shared reader. See `model_output` for why a reasoning
    model's `<think>` block has to be removed before any JSON is looked for."""
    return extract_json_object(text)


def parse_natural_response(text: str, *, name: str = "natural") -> NaturalChannelResult:
    """Parse Channel A's reply into a ChannelAnswer.

    Every failure path produces `available=False` with a reason rather than a
    number. A channel that could not be read is not a channel that agrees.
    """
    payload = _extract_json(text)
    if payload is None:
        return NaturalChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=(
                    "no JSON object in the reply"
                    if text.strip()
                    else "empty reply (reasoning budget likely exhausted, see D17)"
                ),
            ),
            raw_text=text,
        )

    reasoning = str(payload.get("reasoning", "") or "")
    evidence_used = tuple(str(x) for x in payload.get("evidence_used", []) or ())
    figures_used = tuple(str(x) for x in payload.get("figures_used", []) or ())
    sufficient = bool(payload.get("sufficient", True))
    unit = payload.get("unit")
    unit = str(unit) if unit else None

    raw_answer = payload.get("answer")
    if raw_answer is None:
        return NaturalChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=(
                    "channel declined to answer"
                    + ("" if sufficient else "; reported the evidence as insufficient")
                ),
            ),
            reasoning=reasoning, evidence_used=evidence_used, figures_used=figures_used,
            sufficient=sufficient, raw_text=text, stated_unit=unit,
        )

    # The model's own words for the number, with the unit it stated, so scale
    # survives. float() here would read "1,37,814 crore" as a scale error.
    spoken = f"{raw_answer} {unit}".strip() if unit else str(raw_answer)
    value = parse_financial_value(spoken)
    if value is None:
        return NaturalChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=f"answer {spoken!r} could not be parsed as a financial value",
            ),
            reasoning=reasoning, evidence_used=evidence_used, figures_used=figures_used,
            sufficient=sufficient, raw_text=text, stated_unit=unit,
        )

    return NaturalChannelResult(
        answer=ChannelAnswer(name=name, value=value, available=True),
        reasoning=reasoning,
        evidence_used=evidence_used,
        figures_used=figures_used,
        sufficient=sufficient,
        raw_text=text,
        stated_unit=unit,
    )


def run_natural_channel(
    question: str,
    evidence: list[EvidenceBlock],
    *,
    provider,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    name: str = "natural",
) -> NaturalChannelResult:
    """Ask Channel A for an answer.

    `max_tokens` is deliberately generous: these models spend budget on hidden
    reasoning before emitting anything, and a small budget yields an empty string
    with HTTP 200 (D17). A provider failure returns an unavailable channel rather
    than raising, because one channel failing is an expected event the
    consistency engine is built to handle - it is not a pipeline error.
    """
    settings = llm_settings()
    temperature = settings.temperature if temperature is None else temperature
    max_tokens = settings.max_tokens if max_tokens is None else max_tokens

    prompt = NATURAL_CHANNEL_PROMPT.format(
        evidence=format_evidence(evidence), question=question
    )
    from backend.services.llm.base import Message, Role

    try:
        response = provider.complete(
            [Message(Role.USER, prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=True,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:  # noqa: BLE001 - a channel failure is data, not a crash
        return NaturalChannelResult(
            answer=ChannelAnswer(
                name=name, value=None, available=False,
                failure_reason=f"{type(exc).__name__}: {exc}",
            ),
            # The exception CLASS, not just its message. The campaign runner
            # has to tell a day-ending quota exhaustion apart from a model
            # that simply declined, and both arrive here as "no value". Only
            # the first should stop a campaign; matching on message text
            # would be a provider-wording dependency.
            metadata={
                "prompt_version": PROMPT_VERSION,
                "model": model,
                "error_type": type(exc).__name__,
            },
        )

    result = parse_natural_response(response.text, name=name)
    return NaturalChannelResult(
        answer=result.answer,
        reasoning=result.reasoning,
        evidence_used=result.evidence_used,
        figures_used=result.figures_used,
        sufficient=result.sufficient,
        raw_text=result.raw_text,
        stated_unit=result.stated_unit,
        usage=getattr(response, "usage", None),
        metadata={
            "prompt_version": PROMPT_VERSION,
            "model": getattr(response, "model", model),
        },
    )
