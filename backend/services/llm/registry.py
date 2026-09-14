"""Provider registry and channel→model binding.

Two responsibilities:

1. Know how to construct each supported free provider (endpoint, key variable,
   free-tier limits, published paid rates for the equivalent-cost figure).
2. Resolve a *role* - "natural channel", "program channel", "verification agent"
   - to a concrete provider and model.

The second is where the research design lives. Decision D1 requires the two
reasoning channels to be genuinely independent, and binding them to **different
vendors** is the strongest form of that available: different training corpora,
different architectures, different failure modes. Same-vendor and same-model
bindings remain expressible, because they are the ablation arms that test
whether the independence matters (hypothesis H4).

Free-tier limits below were checked against provider documentation on
2026-08-23. They change; `scripts/verify_llm_providers.py` re-checks the parts
that can be observed, and stale limits show up as 429s the limiter absorbs
rather than as silent corruption.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from backend.services.llm.base import LLMProvider
from backend.services.llm.openai_compatible import OpenAICompatibleProvider
from backend.services.llm.ratelimit import RateLimit

__all__ = [
    "ProviderSpec",
    "PROVIDER_SPECS",
    "ChannelBinding",
    "build_provider",
    "available_providers",
    "resolve_channel",
    "CHANNEL_ROLES",
]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    key_env: str
    rate_limit: RateLimit
    # Published PAID rates (input, output) USD per million tokens. Used only to
    # compute the equivalent-cost figure so the efficiency analysis still
    # transfers to paid infrastructure. 0.0 means "not established", never
    # "free" - the free-tier cost is recorded separately as cost_usd=0.
    price_per_mtok: tuple[float, float] = (0.0, 0.0)
    requires_key: bool = True
    notes: str = ""


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        # tokens_per_minute is OBSERVED from a live 429: "tokens per minute
        # (TPM): Limit 8000". It is the limit that actually binds - 30 requests a
        # minute is generous, but a prompt carrying eight evidence blocks is
        # ~3,000 tokens, so TPM refuses after two calls while the request counter
        # still shows 28 slots free. Without it the limiter paces the wrong
        # dimension and every channel reports a spurious failure.
        rate_limit=RateLimit(
            requests_per_minute=30,
            requests_per_day=14_400,
            tokens_per_minute=8_000,
            # OBSERVED from a live 429 on 2026-08-30: "tokens per day (TPD):
            # Limit 200000, Used 199170". This is the limit that actually
            # ends a day's work - at ~4k tokens a call it is about 50 calls,
            # not the 14,400 the request counter implies.
            tokens_per_day=200_000,
        ),
        notes="Free tier, no credit card. Key: console.groq.com",
    ),
    "gemini": ProviderSpec(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env="GEMINI_API_KEY",
        # OBSERVED, not documented. On 2026-08-24 the first live slice run hit
        # HTTP 429 from gemini-3.7-flash after ~17 requests in a day, with the
        # body naming `generate_content_free_tier_requests, limit: 20`. The
        # previous value here (1,500/day) came from published Flash-class limits
        # and is wrong for this model by roughly 75x.
        #
        # 20/day is recorded because it is what the provider enforced. It has NOT
        # been confirmed against Google's documentation for this model id, and
        # newer model ids appear to carry far tighter free allowances than the
        # 2.5-Flash generation. Treat this as a floor to plan against, re-check
        # with scripts/verify_llm_providers.py --quota, and do not raise it
        # without evidence.
        #
        # Consequence for the evaluation (D14): Channel A on Gemini can answer
        # ~20 questions per day. A 500-question run across several arms is
        # therefore months of wall-clock on this binding alone - see the risk in
        # PROJECT_STATUS.md.
        rate_limit=RateLimit(requests_per_minute=15, requests_per_day=20),
        notes="Free tier, no credit card. Key: aistudio.google.com",
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        # 50/day at a zero balance is too thin for a full experiment; usable as
        # a third opinion or a spillover provider.
        rate_limit=RateLimit(requests_per_minute=20, requests_per_day=50),
        notes="Free ':free' model ids. Daily cap is very low at zero balance.",
    ),
    "nvidia": ProviderSpec(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        key_env="NVIDIA_API_KEY",
        # OBSERVED 2026-08-28 (RX-014). D21 steps 1 and 2 both PASS - step 2
        # being the one an earlier candidate failed on (RX-008), which is why
        # the checklist demands a real completion rather than a reachable
        # endpoint. `GET /models` returns an 85-model lineup including
        # nvidia/nemotron-3-ultra-550b-a55b, and completions return HTTP 200
        # with real content. temperature=0 is accepted (D7a).
        #
        # requests_per_minute=20 is OBSERVED as a floor, not a ceiling: 10
        # requests paced 3s apart gave 9 successes. No 429 has ever been seen,
        # so the request-rate limit was never actually reached and its true
        # value is unknown.
        #
        # requests_per_day is NOT OBSERVED. NVIDIA returns no rate-limit
        # headers at all, and no daily refusal has been triggered. The value
        # below is a conservative pacing placeholder so the limiter has
        # something to work with - it is NOT a measurement, and quoting it as
        # one would repeat the Gemini error exactly (registry said 1,500/day,
        # provider enforced 20).
        #
        # The real constraint here is different in kind: this endpoint returns
        # HTTP 503 "Service temporarily overloaded" under contention - 7
        # requests in 25s triggered one, and so did a cold first request. It is
        # a shared free endpoint for a 550B model, not a per-key quota. 503 is
        # already in _RETRYABLE_STATUS so the adapter absorbs it, but latency
        # varies from 0.42s to 12.5s on identical prompts, which makes any
        # latency figure measured on this provider close to meaningless.
        rate_limit=RateLimit(requests_per_minute=20, requests_per_day=1_000),
        notes=(
            "Free tier, no credit card. Key: build.nvidia.com. Reasoning model: "
            "returns reasoning_content separately and spends completion budget "
            "on it, so max_tokens must stay generous. Daily limit NOT observed."
        ),
    ),
    "ollama": ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434/v1",
        key_env="OLLAMA_API_KEY",
        rate_limit=RateLimit(requests_per_minute=1_000),
        requires_key=False,
        notes="Local, no key, no quota. CPU-only here: slow, weak at program synthesis.",
    ),
}

# Roles the pipeline binds. Kept as data so an ablation can rebind them without
# touching call sites.
CHANNEL_ROLES = (
    "natural_channel",
    "program_channel",
    "verification_agent",
    "question_understanding",
)


@dataclass(frozen=True)
class ChannelBinding:
    role: str
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


def build_provider(
    name: str, *, state_path: str | None = "experiments/.rate_limit_state.json"
) -> LLMProvider:
    """Construct a provider from environment configuration.

    Raises RuntimeError with an actionable message when the key is absent -
    never returns a stub that would let a caller believe an unconfigured
    provider works.
    """
    spec = PROVIDER_SPECS.get(name)
    if spec is None:
        raise RuntimeError(
            f"unknown provider {name!r}; known: {sorted(PROVIDER_SPECS)}"
        )
    key = os.environ.get(spec.key_env, "")
    if spec.requires_key and not key:
        raise RuntimeError(
            f"{spec.key_env} is not set, so provider {name!r} cannot be used. "
            f"{spec.notes}"
        )
    return OpenAICompatibleProvider(
        name=spec.name,
        base_url=os.environ.get(f"{name.upper()}_BASE_URL", spec.base_url),
        api_key=key or "not-required",
        rate_limit=spec.rate_limit,
        state_path=state_path,
        price_per_mtok=spec.price_per_mtok,
    )


def available_providers() -> list[str]:
    """Providers this environment is actually configured to use.

    A keyless provider (Ollama) is only counted when explicitly enabled. Without
    that gate it would always appear "available" even with no server running,
    which is precisely the kind of unverified claim spec section 7 rule 2
    forbids.
    """
    names = []
    for name, spec in PROVIDER_SPECS.items():
        if spec.requires_key:
            if os.environ.get(spec.key_env):
                names.append(name)
        elif os.environ.get(f"{name.upper()}_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
            names.append(name)
    return names


def resolve_channel(role: str) -> ChannelBinding:
    """Resolve a role to (provider, model) from environment configuration.

    Configured as `NATURAL_CHANNEL_MODEL=groq/openai/gpt-oss-120b`, i.e.
    `provider/model`. Keeping provider and model in one variable makes the
    binding explicit at a glance and impossible to half-change.
    """
    if role not in CHANNEL_ROLES:
        raise RuntimeError(f"unknown role {role!r}; known: {CHANNEL_ROLES}")
    raw = os.environ.get(f"{role.upper()}_MODEL", "")
    if not raw:
        raise RuntimeError(
            f"{role.upper()}_MODEL is not set. Expected 'provider/model', "
            f"e.g. 'groq/openai/gpt-oss-120b'. See .env.example."
        )
    if "/" not in raw:
        raise RuntimeError(
            f"{role.upper()}_MODEL={raw!r} must be 'provider/model'. "
            f"Known providers: {sorted(PROVIDER_SPECS)}"
        )
    provider, _, model = raw.partition("/")
    if provider not in PROVIDER_SPECS:
        raise RuntimeError(
            f"{role.upper()}_MODEL names unknown provider {provider!r}; "
            f"known: {sorted(PROVIDER_SPECS)}"
        )
    return ChannelBinding(role=role, provider=provider, model=model)


def channels_are_independent() -> tuple[bool, str]:
    """Report whether the two reasoning channels are bound to different models.

    Decision D1's central risk is that the channels quietly become two wrappers
    over one reasoning process, which would make their disagreement meaningless
    and the research question unanswerable. This makes that condition
    inspectable at runtime rather than assumed from a config file nobody re-reads.
    """
    natural = resolve_channel("natural_channel")
    program = resolve_channel("program_channel")
    if natural.provider != program.provider:
        return True, f"cross-provider: {natural.label} vs {program.label}"
    if natural.model != program.model:
        return True, f"same provider, different models: {natural.label} vs {program.label}"
    return False, (
        f"both channels bound to {natural.label} - this is the H4 ablation arm, "
        "not a valid configuration for the headline experiment"
    )
