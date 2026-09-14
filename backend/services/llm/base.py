"""LLM provider interface (spec section 5).

The spec requires an abstraction layer rather than hard-coding one vendor, for
two reasons that both matter here: it permits controlled research comparisons,
and it prevents vendor lock-in.

It matters more than usual for this project. The central research question asks
whether *independent* reasoning channels detect numerical hallucination, and the
strongest available form of independence is running the two channels on
different vendors' models entirely - different training data, different
architectures, different failure modes. That is only possible behind an
interface like this one.

Every response carries the exact configuration that produced it. Spec section 18
requires each experiment to record model, provider, prompt version and decoding
configuration; attaching that to the response itself means the record cannot
drift from what actually ran.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "usage_record",
    "Role",
    "Message",
    "Usage",
    "LLMResponse",
    "LLMProvider",
    "LLMError",
    "RateLimitError",
    "AuthError",
    "ModelNotFoundError",
    "ProviderUnavailableError",
]


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role.value, "content": self.content}


@dataclass(frozen=True)
class Usage:
    """Token and timing accounting for one call.

    `cost_usd` is 0.0 on a free tier, which would make the spec's cost analysis
    (section 33) vacuous if that were the only number recorded. So
    `equivalent_cost_usd` carries what the same call would cost at the provider's
    published paid rates. The efficiency comparison then still means something to
    a reader reproducing this on paid infrastructure, and the free-tier figure is
    not quietly passed off as evidence that the method is cheap.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Hidden reasoning tokens. Both Groq's gpt-oss and Gemini's flash models
    # think before answering and bill for it, but report it separately from
    # `completion_tokens` - Gemini does not include it in either visible field.
    reasoning_tokens: int = 0
    # The provider's OWN total. Recorded rather than derived, because
    # prompt + completion silently undercounts on reasoning models: an observed
    # Gemini call reported prompt=29, completion=2, total=223. Deriving the total
    # would have understated that call's cost roughly sevenfold and corrupted the
    # efficiency analysis (spec section 33).
    reported_total_tokens: int = 0
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    equivalent_cost_usd: float = 0.0
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        """Provider-reported total when available, else the derived sum."""
        derived = self.prompt_tokens + self.completion_tokens + self.reasoning_tokens
        return max(self.reported_total_tokens, derived)

    def as_dict(self) -> dict:
        """For the run artifact.

        Nothing persisted this until 2026-08-30, so `evaluation/metrics/
        efficiency.py` - which expects exactly these fields - had no input from
        any run, and the spec §33 cost analysis could not be computed at all
        from the artifacts that were supposed to feed it. Two finished modules
        with no wire between them.

        It is also what makes the campaign's token budget empirical rather than
        an extrapolation from a single 429 (RX-022).
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reported_total_tokens": self.reported_total_tokens,
            "total_tokens": self.total_tokens,
            "latency_seconds": round(self.latency_seconds, 3),
            # Zero on a free tier, and labelled as such wherever it is read.
            "cost_usd": self.cost_usd,
            "equivalent_cost_usd": self.equivalent_cost_usd,
            "cached": self.cached,
        }

    @property
    def billable_output_tokens(self) -> int:
        """Output actually paid for, including hidden reasoning."""
        visible_and_reasoning = self.completion_tokens + self.reasoning_tokens
        inferred = self.reported_total_tokens - self.prompt_tokens
        return max(visible_and_reasoning, inferred, 0)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            reported_total_tokens=self.reported_total_tokens + other.reported_total_tokens,
            latency_seconds=self.latency_seconds + other.latency_seconds,
            cost_usd=self.cost_usd + other.cost_usd,
            equivalent_cost_usd=self.equivalent_cost_usd + other.equivalent_cost_usd,
            cached=self.cached and other.cached,
        )


def usage_record(usage) -> dict | None:
    """Serialise a channel's `usage`, or None when the call never happened.

    None and a zeroed record are different facts: the first means no call was
    made (a skipped arm, a provider outage before the request), the second means
    a call was made and reported nothing. Collapsing them would make an
    unmeasured arm look free.
    """
    if usage is None:
        return None
    if hasattr(usage, "as_dict"):
        return usage.as_dict()
    return None

@dataclass(frozen=True)
class LLMResponse:
    """A completion plus the full configuration that produced it."""

    text: str
    provider: str
    model: str
    usage: Usage
    # Reasoning models expose their internal deliberation separately from the
    # answer. Captured for research logs and error analysis, but spec section 24
    # forbids surfacing hidden chain-of-thought to users - the explainability
    # layer shows evidence and a concise summary instead.
    reasoning: str | None = None
    finish_reason: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    prompt_version: str | None = None
    attempts: int = 1
    # Anything about HOW the reply was obtained that differs from what was
    # asked for - e.g. server-side JSON mode being rejected and dropped. A
    # reproducibility record that hid that would misdescribe the run.
    notes: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, repr=False)

    def reproducibility_record(self) -> dict:
        """The fields spec section 18 requires be recorded per experiment."""
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "prompt_version": self.prompt_version,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "total_tokens": self.usage.total_tokens,
            "latency_seconds": round(self.usage.latency_seconds, 3),
            "cost_usd": self.usage.cost_usd,
            "equivalent_cost_usd": round(self.usage.equivalent_cost_usd, 6),
            "cached": self.usage.cached,
            "finish_reason": self.finish_reason,
            "attempts": self.attempts,
        }


class LLMError(RuntimeError):
    """Base class. Callers distinguish subclasses to decide on retry."""


class RateLimitError(LLMError):
    """429. Expected on free tiers and retried with backoff, not a defect."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhaustedError(RateLimitError):
    """A 429 that says the *allowance* is spent, not that we are going too fast.

    These are not the same event and must not share a retry policy. A per-minute
    429 clears in seconds and retrying is correct; an exhausted free-tier
    allowance clears at the provider's next reset, which can be hours away, and
    retrying it burns wall-clock and further quota for a request that cannot
    succeed. One question in the first live slice run spent **621 seconds** in a
    retry loop against a quota that was already spent.

    Subclasses RateLimitError so existing handlers still catch it; the retry loop
    checks for it explicitly and stops.
    """


class AuthError(LLMError):
    """401/403. Never retried - a wrong key does not become right on retry."""


class ModelNotFoundError(LLMError):
    """404 on the model. Free providers rotate their model lineups."""


class ProviderUnavailableError(LLMError):
    """5xx or a network failure. Retryable."""


class LLMProvider(ABC):
    """Minimal surface: complete a chat and describe yourself."""

    name: str

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        prompt_version: str | None = None,
        response_format_json: bool = False,
    ) -> LLMResponse:
        """Run one completion. Raises an LLMError subclass on failure."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Model ids this provider currently serves.

        Free providers rotate their lineups without notice, so model
        availability is discovered rather than assumed.
        """

    def health_check(self, model: str) -> tuple[bool, str]:
        """One real minimal call. Never reports success without making it.

        The budget is deliberately generous. Current free models on both Groq and
        Gemini reason before answering and bill for it, so a small `max_tokens`
        is spent thinking and returns empty content - a health check that used 16
        tokens reported three healthy models as failures. The check must exercise
        the models the way the pipeline will, not a cheaper way.
        """
        try:
            start = time.monotonic()
            response = self.complete(
                [Message(Role.USER, "What is 12 plus 13? Reply with only the number.")],
                model=model,
                max_tokens=1024,
            )
            elapsed = time.monotonic() - start
            usage = response.usage
            correct = "25" in response.text
            detail = (
                f"{self.name}/{model} {elapsed:5.2f}s "
                f"tok={usage.total_tokens} (reasoning={usage.reasoning_tokens}) "
                f"-> {response.text.strip()[:30]!r}"
            )
            if not correct:
                detail += "  [WARNING: arithmetic check failed]"
            return True, detail
        except LLMError as exc:
            return False, f"{self.name}/{model} failed: {type(exc).__name__}: {exc}"
