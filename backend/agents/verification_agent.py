"""The verification agent (spec Module 13).

Triggered when the channels disagree. Re-reads the evidence, looks at what each
channel claimed and why, and adjudicates: which answer the evidence supports, or
that neither is supportable.

**This is the one component that IS allowed to see the channel answers**, and the
distinction matters. Channel B is forbidden from seeing Channel A (D1) because
the two are meant to be independent estimates whose disagreement is the signal.
The verification agent is not a third estimate — it is the arbiter *of* that
signal, and it can only arbitrate what it can see. Its position in the graph is
strictly downstream: nothing it produces is fed back into either channel, so it
cannot contaminate the disagreement it was called to resolve.

**It is measured separately and must never be folded into channel accuracy.**
`EVALUATION.md` §5.4 requires verification-agent resolution accuracy as its own
number. Counting a correct adjudication as a channel success would credit the
channels for work the arbiter did, and would make the dual-channel mechanism look
stronger than the evidence supports.

**Abstention is a first-class outcome.** When the evidence does not settle the
disagreement, saying so is the correct answer. An arbiter that always picks a
side manufactures resolution, and a manufactured resolution is worse than an
open disagreement — the open disagreement at least flags the answer as risky,
which is the behaviour the whole system exists to produce.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum

from backend.services.llm.settings import llm_settings
from backend.agents.evidence import EvidenceBlock, format_evidence
from backend.agents.model_output import extract_json_object
from backend.core.financial_value import FinancialValue, parse_financial_value

__all__ = [
    "Resolution",
    "VerificationResult",
    "VERIFICATION_AGENT_PROMPT",
    "PROMPT_VERSION",
    "parse_verification_response",
    "run_verification_agent",
    "should_verify",
]

PROMPT_VERSION = "verification_v1"


class Resolution(str, Enum):
    """Which way the arbiter went."""

    SUPPORTS_A = "supports_a"
    SUPPORTS_B = "supports_b"
    SUPPORTS_NEITHER = "supports_neither"
    OWN_ANSWER = "own_answer"
    UNRESOLVED = "unresolved"


# Deliberately does NOT name the channels by their implementation ("the natural
# language channel", "the program"). Telling the arbiter that one answer came
# from executed code invites it to defer to that channel on authority rather than
# on evidence, which would make resolution a function of the architecture instead
# of the filing.
VERIFICATION_AGENT_PROMPT = """\
You are auditing two candidate answers to a question about a company's audited \
financial statements. They disagree. Decide what the evidence actually supports.

EVIDENCE
{evidence}

QUESTION
{question}

CANDIDATE 1: {answer_a}
CANDIDATE 2: {answer_b}

Rules:
- Judge ONLY from the evidence above. Do not use outside knowledge of the company.
- Check the figures each candidate would have had to read, and the units.
- Figures are in the units named in each evidence header. Parentheses mean \
negative: (1,234) is -1234.
- If the evidence does not settle it, say so. Do not pick a side to be decisive.
- If both are wrong and the evidence gives a different figure, give that figure.

Reply with ONLY a JSON object:
{{"resolution": "candidate_1" | "candidate_2" | "neither" | "unresolved",
  "answer": <the number the evidence supports, or null>,
  "unit": "<e.g. INR crore, percent, ratio, or null>",
  "reasoning": "<what in the evidence decided it>",
  "evidence_used": ["<refs like E1, E3>"],
  "figures_used": ["<the exact figures you relied on>"]}}
"""

_RESOLUTION_BY_LABEL = {
    "candidate_1": Resolution.SUPPORTS_A,
    "candidate_2": Resolution.SUPPORTS_B,
    "neither": Resolution.SUPPORTS_NEITHER,
    "unresolved": Resolution.UNRESOLVED,
}


def program_goes_first(question: str) -> bool:
    """Does the program channel occupy CANDIDATE 1 for this question?

    The prompt above deliberately hides which channel produced which answer - and
    until 2026-09-06 it hid the *labels* while leaving the *positions* fixed,
    which is not hiding anything. Channel A was always CANDIDATE 1 and Channel B
    always CANDIDATE 2, so position was a perfect proxy for channel identity, and
    an arbiter with any first-position preference would express it as a
    systematic preference for the natural channel (RX-045).

    Deterministic, not random: runs are pinned at temperature 0 (D7a) and an
    arbiter that answered differently on re-run would make campaigns
    irreproducible. Keyed on the question, so the assignment is stable for a
    given question, balanced across a benchmark, and unpredictable from anything
    the arbiter can see.
    """
    return hashlib.sha256(question.encode("utf-8")).digest()[0] % 2 == 1


def _unswap(result: VerificationResult) -> VerificationResult:
    """Undo the presentation swap, so callers always speak in channel terms.

    Only the two positional verdicts move. `neither`, `unresolved` and
    `own_answer` name no candidate, so swapping them would be a bug rather than
    a correction.
    """
    flipped = {
        Resolution.SUPPORTS_A: Resolution.SUPPORTS_B,
        Resolution.SUPPORTS_B: Resolution.SUPPORTS_A,
    }.get(result.resolution)
    return result if flipped is None else replace(result, resolution=flipped)


@dataclass(frozen=True)
class VerificationResult:
    resolution: Resolution
    value: FinancialValue | None = None
    reasoning: str = ""
    evidence_used: tuple[str, ...] = ()
    figures_used: tuple[str, ...] = ()
    stated_unit: str | None = None
    available: bool = True
    failure_reason: str | None = None
    raw_text: str = ""
    usage: object | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        """Did the arbiter actually settle the disagreement?"""
        return self.available and self.resolution in (
            Resolution.SUPPORTS_A,
            Resolution.SUPPORTS_B,
            Resolution.OWN_ANSWER,
        )


def should_verify(report) -> bool:
    """Is this a disagreement worth spending an arbiter call on?

    DISAGREE only. UNCERTAIN means a channel produced nothing, and an arbiter
    cannot adjudicate between an answer and an absence - there is no competing
    claim to weigh, so calling it would spend quota to restate the gap. That
    exclusion is also why abstention rate and resolution accuracy have to be
    reported separately: they describe different populations.
    """
    verdict = getattr(report, "verdict", None)
    return getattr(verdict, "name", "") == "DISAGREE"


def _describe(answer) -> str:
    """One line for the prompt, WITHOUT naming which channel produced it."""
    if not getattr(answer, "available", False) or answer.value is None:
        return "no answer produced"
    value = answer.value
    unit = value.currency or ""
    if value.scale.label != "unit":
        unit = f"{unit} {value.scale.label}".strip()
    if value.unit_kind.value in {"percent", "ratio"}:
        unit = value.unit_kind.value
    return f"{value.amount} {unit}".strip()


def parse_verification_response(text: str) -> VerificationResult:
    """Parse the arbiter's reply. Every failure path abstains rather than guesses."""
    payload = extract_json_object(text)
    if payload is None:
        return VerificationResult(
            resolution=Resolution.UNRESOLVED,
            available=False,
            failure_reason=(
                "no JSON object in the reply"
                if text.strip()
                else "empty reply (reasoning budget likely exhausted, see D17)"
            ),
            raw_text=text,
        )

    label = str(payload.get("resolution", "")).strip().lower()
    resolution = _RESOLUTION_BY_LABEL.get(label, Resolution.UNRESOLVED)

    unit = payload.get("unit")
    unit = str(unit) if unit else None
    raw_answer = payload.get("answer")

    value = None
    if raw_answer is not None:
        spoken = f"{raw_answer} {unit}".strip() if unit else str(raw_answer)
        value = parse_financial_value(spoken)
        if value is None:
            return VerificationResult(
                resolution=Resolution.UNRESOLVED,
                reasoning=str(payload.get("reasoning", "") or ""),
                available=False,
                failure_reason=f"arbiter's figure {spoken!r} could not be parsed",
                raw_text=text,
                stated_unit=unit,
            )

    # "Neither candidate, and here is the figure" is a distinct outcome from
    # "neither candidate, and I cannot say" - the first is a resolution, the
    # second is not, and collapsing them would overstate resolution accuracy.
    if resolution is Resolution.SUPPORTS_NEITHER and value is not None:
        resolution = Resolution.OWN_ANSWER

    return VerificationResult(
        resolution=resolution,
        value=value,
        reasoning=str(payload.get("reasoning", "") or ""),
        evidence_used=tuple(str(x) for x in payload.get("evidence_used", []) or ()),
        figures_used=tuple(str(x) for x in payload.get("figures_used", []) or ()),
        stated_unit=unit,
        raw_text=text,
    )


def run_verification_agent(
    question: str,
    evidence: list[EvidenceBlock],
    channel_a,
    channel_b,
    *,
    provider,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> VerificationResult:
    """Adjudicate a disagreement between two channel answers.

    Unlike the channels, this function is *given* both answers - that is the
    point of an arbiter. What it must never do is feed its conclusion back into
    either channel, which is a property of the call graph rather than of this
    signature: nothing here returns into `run_natural_channel` or
    `run_program_channel`.
    """
    settings = llm_settings()
    temperature = settings.temperature if temperature is None else temperature
    max_tokens = settings.max_tokens if max_tokens is None else max_tokens

    # Which channel is shown first is decided per question, not fixed. See
    # `program_goes_first`: a constant order makes position a perfect proxy for
    # channel identity, which is the thing this prompt takes care not to reveal.
    swapped = program_goes_first(question)
    first, second = (
        (channel_b, channel_a) if swapped else (channel_a, channel_b)
    )
    prompt = VERIFICATION_AGENT_PROMPT.format(
        evidence=format_evidence(evidence),
        question=question,
        answer_a=_describe(first),
        answer_b=_describe(second),
    )
    from backend.services.llm.base import Message, Role

    # Recorded so a run artifact says which order the arbiter actually saw.
    # Without it, a position effect could never be measured after the fact.
    metadata = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "candidate_1": "program" if swapped else "natural",
    }
    try:
        response = provider.complete(
            [Message(Role.USER, prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=True,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:  # noqa: BLE001 - an arbiter failure leaves the
        # disagreement standing, which is the safe outcome: the answer stays
        # flagged as risky rather than being resolved by a component that did
        # not run.
        return VerificationResult(
            resolution=Resolution.UNRESOLVED,
            available=False,
            failure_reason=f"{type(exc).__name__}: {exc}",
            metadata={**metadata, "error_type": type(exc).__name__},
        )

    result = parse_verification_response(response.text or "")
    if swapped:
        # Back into channel terms before anything downstream reads it. Callers
        # and every recorded artifact speak of channels, never of positions.
        result = _unswap(result)
    return VerificationResult(
        resolution=result.resolution,
        value=result.value,
        reasoning=result.reasoning,
        evidence_used=result.evidence_used,
        figures_used=result.figures_used,
        stated_unit=result.stated_unit,
        available=result.available,
        failure_reason=result.failure_reason,
        raw_text=result.raw_text,
        usage=getattr(response, "usage", None),
        metadata={**metadata, "model": getattr(response, "model", model)},
    )
