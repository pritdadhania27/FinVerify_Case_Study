"""Efficiency and cost metrics (spec §33, EVALUATION.md §6).

The trap this module exists to avoid: the runtime is a free tier (D14), so
`cost_usd` is 0.00 for every arm. Reporting that as the cost analysis would state
"dual-channel verification is free" - true of this deployment, false of the
method, and the kind of claim a reviewer would be right to reject the paper over.

So three figures are kept apart and never merged:

| figure | meaning |
|---|---|
| `cost_usd` | actual spend. Zero here, and labelled as a free-tier zero |
| `equivalent_cost_usd` | the same tokens at the provider's published PAID rate |
| `requests` | quota consumed - the constraint that actually limits throughput |

`equivalent_cost_usd` is only meaningful where a paid rate has been *recorded
with a source*. `registry.ProviderSpec.price_per_mtok` defaults to (0.0, 0.0)
meaning "not established", and no provider currently carries a rate. This module
therefore reports it as **unestablished** rather than as $0.00: a zero in the
equivalent-cost column is indistinguishable from "we looked it up and it costs
nothing", which is the exact confusion the three-figure split exists to prevent.
Populating those rates is a citation task - a published price page with a
retrieval date - not a value to be recalled.

Cost-matching (EVALUATION.md §6): a method that wins by spending more has not
been shown to be better. `cost_matched_comparison` reports the comparison in
tokens and calls, which are provider-agnostic, and refuses to express a dollar
ratio when the rates behind it were never established.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

__all__ = [
    "CallCost",
    "QuestionCost",
    "EfficiencyReport",
    "aggregate_efficiency",
    "cost_matched_comparison",
]


@dataclass(frozen=True)
class CallCost:
    """One model call's resource footprint, as recorded by the provider layer."""

    channel: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    equivalent_cost_usd: float = 0.0
    # False when the provider's published paid rate was never recorded, which is
    # the current state for every provider in the registry.
    rate_established: bool = False
    cached: bool = False


@dataclass(frozen=True)
class QuestionCost:
    question_id: str
    calls: tuple[CallCost, ...] = field(default_factory=tuple)
    sandbox_executions: int = 0
    retrieval_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    verification_agent_triggered: bool = False

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.calls)

    @property
    def model_calls(self) -> int:
        """Uncached calls only - a cache hit consumes no quota and no compute."""
        return sum(1 for c in self.calls if not c.cached)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class EfficiencyReport:
    n: int
    latency_p50: float
    latency_p95: float
    tokens_per_question: float
    tokens_by_channel: dict[str, int]
    calls_per_question: float
    sandbox_executions_per_question: float
    verification_trigger_rate: float
    cost_usd: float
    equivalent_cost_usd: float | None
    quota_requests: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "latency_p50_seconds": self.latency_p50,
            "latency_p95_seconds": self.latency_p95,
            "tokens_per_question": self.tokens_per_question,
            "tokens_by_channel": self.tokens_by_channel,
            "calls_per_question": self.calls_per_question,
            "sandbox_executions_per_question": self.sandbox_executions_per_question,
            "verification_trigger_rate": self.verification_trigger_rate,
            "cost_usd": self.cost_usd,
            "cost_usd_note": "free tier: an actual spend of zero, not a cheap method",
            "equivalent_cost_usd": self.equivalent_cost_usd,
            "quota_requests": self.quota_requests,
            "notes": list(self.notes),
        }


def aggregate_efficiency(costs: list[QuestionCost]) -> EfficiencyReport:
    if not costs:
        return EfficiencyReport(
            n=0,
            latency_p50=0.0,
            latency_p95=0.0,
            tokens_per_question=0.0,
            tokens_by_channel={},
            calls_per_question=0.0,
            sandbox_executions_per_question=0.0,
            verification_trigger_rate=0.0,
            cost_usd=0.0,
            equivalent_cost_usd=None,
            quota_requests=0,
            notes=("no questions",),
        )

    latencies = [c.wall_clock_seconds for c in costs]
    by_channel: dict[str, int] = {}
    for question in costs:
        for call in question.calls:
            by_channel[call.channel] = by_channel.get(call.channel, 0) + call.total_tokens

    all_calls = [call for question in costs for call in question.calls]
    established = [c for c in all_calls if c.rate_established]
    notes: list[str] = []
    if all_calls and not established:
        equivalent: float | None = None
        notes.append(
            "equivalent_cost_usd is UNESTABLISHED: no provider in the registry "
            "carries a published paid rate. Reporting 0.00 here would read as "
            "'the method is free', which is a claim about this free tier and not "
            "about the method. Populate ProviderSpec.price_per_mtok from a cited "
            "price page before quoting a monetary figure."
        )
    elif len(established) < len(all_calls):
        equivalent = sum(c.equivalent_cost_usd for c in established)
        notes.append(
            f"equivalent_cost_usd covers only {len(established)}/{len(all_calls)} "
            "calls: the rest ran on providers with no recorded paid rate, so the "
            "figure is a lower bound, not a total"
        )
    else:
        equivalent = sum(c.equivalent_cost_usd for c in all_calls)

    n = len(costs)
    return EfficiencyReport(
        n=n,
        latency_p50=median(latencies) if latencies else 0.0,
        latency_p95=_percentile(latencies, 0.95),
        tokens_per_question=sum(c.total_tokens for c in costs) / n,
        tokens_by_channel=by_channel,
        calls_per_question=sum(c.model_calls for c in costs) / n,
        sandbox_executions_per_question=sum(c.sandbox_executions for c in costs) / n,
        verification_trigger_rate=sum(1 for c in costs if c.verification_agent_triggered) / n,
        cost_usd=sum(call.cost_usd for call in all_calls),
        equivalent_cost_usd=equivalent,
        quota_requests=sum(c.model_calls for c in costs),
        notes=tuple(notes),
    )


def cost_matched_comparison(
    left_name: str,
    left: EfficiencyReport,
    right_name: str,
    right: EfficiencyReport,
) -> dict:
    """Resource ratios between two arms (EVALUATION.md §6).

    Tokens and calls are primary because they are provider-agnostic and were
    actually measured. The dollar ratio appears only when both sides have an
    established rate; otherwise the field carries the reason it is absent rather
    than a number nobody can defend.

    This is what makes H1 falsifiable in the form HYPOTHESES.md states it - "at
    matched API cost". A dual-channel arm that spends 2.4x the tokens of
    self-consistency and wins on AUROC has not yet beaten it; the comparison has
    to be made at equal spend, which means reporting the multiplier so the
    self-consistency arm can be run at n samples matched to it.
    """
    def ratio(a: float, b: float) -> float | None:
        return a / b if b else None

    monetary: dict[str, object]
    if left.equivalent_cost_usd is None or right.equivalent_cost_usd is None:
        monetary = {
            "available": False,
            "reason": (
                "at least one arm has no established paid rate; a cost ratio "
                "computed from unestablished rates would be invented"
            ),
        }
    else:
        monetary = {
            "available": True,
            "ratio": ratio(left.equivalent_cost_usd, right.equivalent_cost_usd),
        }

    return {
        "left": left_name,
        "right": right_name,
        "token_ratio": ratio(left.tokens_per_question, right.tokens_per_question),
        "call_ratio": ratio(left.calls_per_question, right.calls_per_question),
        "latency_ratio": ratio(left.latency_p50, right.latency_p50),
        "equivalent_cost": monetary,
    }
