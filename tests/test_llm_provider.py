"""Tests for the LLM provider layer (spec section 5).

Every test uses a mock transport. A suite that called the real endpoints would
consume the free-tier quota the experiments need, and would fail for reasons
having nothing to do with the code.
"""

import json

import httpx
import pytest

from backend.services.llm import (
    AuthError,
    Message,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimit,
    RateLimitError,
    Role,
    Usage,
)
from backend.services.llm.openai_compatible import OpenAICompatibleProvider
from backend.services.llm.base import QuotaExhaustedError
from backend.services.llm.ratelimit import DailyQuotaExhausted, RateLimiter
from backend.services.llm.registry import (
    PROVIDER_SPECS,
    available_providers,
    build_provider,
    channels_are_independent,
    resolve_channel,
)
from datetime import UTC

CHAT_OK = {
    "model": "test-model",
    "choices": [{"message": {"content": "25.0"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 8},
}


def provider(handler, *, retries: int = 3, rpm: int = 1000, **kw) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        base_url="https://example.invalid/v1",
        api_key="k",
        rate_limit=RateLimit(requests_per_minute=rpm),
        max_retries=retries,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,  # no real waiting in tests
        **kw,
    )


def ask(p) -> object:
    return p.complete([Message(Role.USER, "q")], model="test-model")


class TestSuccess:
    def test_returns_text_and_usage(self):
        p = provider(lambda r: httpx.Response(200, json=CHAT_OK))
        response = ask(p)
        assert response.text == "25.0"
        assert response.usage.prompt_tokens == 120
        assert response.usage.total_tokens == 128
        assert response.finish_reason == "stop"

    def test_free_tier_cost_is_zero_but_equivalent_cost_is_recorded(self):
        """Spec section 33's cost analysis would be vacuous at a flat zero."""
        p = provider(lambda r: httpx.Response(200, json=CHAT_OK), price_per_mtok=(1.0, 5.0))
        usage = ask(p).usage
        assert usage.cost_usd == 0.0
        assert usage.equivalent_cost_usd == pytest.approx(120 / 1e6 * 1.0 + 8 / 1e6 * 5.0)

    def test_reproducibility_record_has_the_spec_fields(self):
        """Spec section 18."""
        p = provider(lambda r: httpx.Response(200, json=CHAT_OK))
        record = p.complete(
            [Message(Role.USER, "q")], model="test-model", temperature=0.0,
            prompt_version="natural_v1",
        ).reproducibility_record()
        for field in ("provider", "model", "temperature", "prompt_version",
                      "prompt_tokens", "completion_tokens", "latency_seconds"):
            assert field in record
        assert record["prompt_version"] == "natural_v1"

    def test_temperature_is_sent(self):
        """These models accept temperature, unlike the frontier hosted ones (D7)."""
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=CHAT_OK)

        provider(handler).complete([Message(Role.USER, "q")], model="m", temperature=0.0)
        assert seen["temperature"] == 0.0

    def test_json_mode_requested_when_asked(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=CHAT_OK)

        provider(handler).complete(
            [Message(Role.USER, "q")], model="m", response_format_json=True
        )
        assert seen["response_format"] == {"type": "json_object"}


class TestErrorMapping:
    @pytest.mark.parametrize("status,expected", [(401, AuthError), (403, AuthError)])
    def test_auth_errors_are_not_retried(self, status, expected):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(status, text="bad key")

        with pytest.raises(expected):
            ask(provider(handler))
        assert len(calls) == 1, "a wrong key does not become right on retry"

    def test_missing_model_is_not_retried(self):
        """A 404 that NAMES something is a retired model id. Retrying it only
        burns quota, which is why it is on the no-retry list."""
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(404, text="model not found")

        with pytest.raises(ModelNotFoundError):
            ask(provider(handler))
        assert len(calls) == 1

    def test_a_bodiless_404_is_transient_and_IS_retried(self):
        """A 404 with an empty body makes no claim, and silence is not evidence.

        Observed in RX-019: NVIDIA returned a bodiless 404 for a model that had
        answered correctly seconds earlier and answered again afterwards. The
        terminal classification meant no retry was even attempted, and two
        questions were permanently lost to a blip on a provider already known
        to shed load mid-request (RX-014).
        """
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(404, text="")

        with pytest.raises(ProviderUnavailableError):
            ask(provider(handler, retries=3))
        assert len(calls) == 3, "silence about the model is not a claim about it"

    def test_a_whitespace_only_404_body_counts_as_empty(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(404, text="   \n  ")

        with pytest.raises(ProviderUnavailableError):
            ask(provider(handler, retries=2))
        assert len(calls) == 2

    def test_server_error_is_retried_then_raised(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(503, text="unavailable")

        with pytest.raises(ProviderUnavailableError):
            ask(provider(handler, retries=3))
        assert len(calls) == 3

    def test_transient_failure_then_success(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] < 3:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json=CHAT_OK)

        response = ask(provider(handler, retries=5))
        assert response.text == "25.0"
        assert response.attempts == 3

    def test_empty_choices_is_an_error_not_an_empty_answer(self):
        """An empty answer silently becomes a wrong answer downstream."""
        p = provider(lambda r: httpx.Response(200, json={"choices": []}))
        with pytest.raises(ProviderUnavailableError):
            ask(p)


class TestRateLimitHandling:
    def test_429_is_retried(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(429, text="slow down", headers={"retry-after": "1"})
            return httpx.Response(200, json=CHAT_OK)

        assert ask(provider(handler, retries=3)).text == "25.0"

    def test_exhausted_retries_on_429_raises_rate_limit_error(self):
        p = provider(lambda r: httpx.Response(429, text="nope"), retries=2)
        with pytest.raises(RateLimitError):
            ask(p)


class TestRateLimiter:
    """Load-bearing on a free tier: 30 req/min hard-fails an unthrottled loop."""

    def test_allows_up_to_the_limit_without_waiting(self):
        limiter = RateLimiter(RateLimit(requests_per_minute=5), clock=lambda: 0.0)
        assert all(limiter.acquire() == 0.0 for _ in range(5))

    def test_sliding_window_releases_after_sixty_seconds(self):
        now = {"t": 0.0}
        limiter = RateLimiter(RateLimit(requests_per_minute=2), clock=lambda: now["t"])
        limiter.acquire()
        limiter.acquire()
        now["t"] = 61.0
        assert limiter.acquire() == 0.0, "window should have slid"

    def test_daily_quota_raises_rather_than_blocking_for_hours(self):
        """A reset can be many hours away; a silent block looks like a hang."""
        limiter = RateLimiter(
            RateLimit(requests_per_minute=100, requests_per_day=3), clock=lambda: 0.0
        )
        for _ in range(3):
            limiter.acquire()
        with pytest.raises(DailyQuotaExhausted):
            limiter.acquire()

    def test_remaining_today_is_reported(self):
        limiter = RateLimiter(
            RateLimit(requests_per_minute=100, requests_per_day=10), clock=lambda: 0.0
        )
        limiter.acquire()
        limiter.acquire()
        assert limiter.remaining_today() == 8

    def test_daily_count_survives_a_restart(self, tmp_path):
        """A run that dies overnight must not believe it has a fresh quota."""
        state = tmp_path / "quota.json"
        limit = RateLimit(requests_per_minute=100, requests_per_day=5)
        first = RateLimiter(limit, name="groq", state_path=state, clock=lambda: 0.0)
        first.acquire()
        first.acquire()

        second = RateLimiter(limit, name="groq", state_path=state, clock=lambda: 0.0)
        assert second.requests_today == 2
        assert second.remaining_today() == 3

    def test_separate_providers_have_separate_quotas(self, tmp_path):
        state = tmp_path / "quota.json"
        limit = RateLimit(requests_per_minute=100, requests_per_day=5)
        RateLimiter(limit, name="groq", state_path=state, clock=lambda: 0.0).acquire()
        gemini = RateLimiter(limit, name="gemini", state_path=state, clock=lambda: 0.0)
        assert gemini.requests_today == 0

    def test_external_429_fills_the_window(self):
        """The server's view of the limit is authoritative; ours is an estimate."""
        now = {"t": 100.0}
        limiter = RateLimiter(RateLimit(requests_per_minute=5), clock=lambda: now["t"])
        limiter.record_external_limit(retry_after=10.0)
        with pytest.raises(TimeoutError):
            limiter.acquire(timeout=0.001)

    def test_the_day_boundary_is_utc_not_local(self):
        """The quota day must roll on UTC, not on this host's clock.

        This host runs at UTC+5:30, so local midnight is 18:30 UTC - five and a
        half hours *before* a UTC-counting provider rolls over. A limiter using
        the local date resets early, believes it has a fresh allowance, and
        issues requests the provider still counts against yesterday: the exact
        refusal the limiter exists to prevent. Resetting late only costs
        throughput, so UTC is the safe direction.
        """
        from datetime import datetime

        from backend.services.llm.ratelimit import utc_day

        assert utc_day() == datetime.now(UTC).date().isoformat()

    def test_a_persisted_count_from_another_day_does_not_carry_over(self, tmp_path):
        state = tmp_path / "quota.json"
        state.write_text(
            json.dumps({"groq": {"day": "1999-01-01", "count": 14_000}}), encoding="utf-8"
        )
        limiter = RateLimiter(
            RateLimit(requests_per_minute=100, requests_per_day=14_400),
            name="groq",
            state_path=state,
            clock=lambda: 0.0,
        )
        assert limiter.requests_today == 0


class TestRegistry:
    def test_known_free_providers_are_registered(self):
        for name in ("groq", "gemini", "openrouter", "ollama"):
            assert name in PROVIDER_SPECS

    def test_free_tier_limits_are_recorded(self):
        """Gemini's figure is OBSERVED from a real 429, not taken from published
        Flash-class limits - which were wrong for this model id by ~75x and would
        have had the evaluation plan assuming 1,500 requests a day it does not
        have (D14)."""
        assert PROVIDER_SPECS["groq"].rate_limit.requests_per_day == 14_400
        assert PROVIDER_SPECS["gemini"].rate_limit.requests_per_day == 20

    def test_missing_key_raises_with_an_actionable_message(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
            build_provider("groq")

    def test_unknown_provider_rejected(self):
        with pytest.raises(RuntimeError, match="unknown provider"):
            build_provider("not-a-provider")

    def test_keyless_provider_needs_explicit_enabling(self, monkeypatch):
        """Otherwise Ollama always looks 'available' with no server running."""
        monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
        for spec in PROVIDER_SPECS.values():
            monkeypatch.delenv(spec.key_env, raising=False)
        assert available_providers() == []
        monkeypatch.setenv("OLLAMA_ENABLED", "1")
        assert "ollama" in available_providers()


class TestChannelBinding:
    def test_binding_parses_provider_and_model(self, monkeypatch):
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "gemini/gemini-2.5-flash")
        binding = resolve_channel("natural_channel")
        assert binding.provider == "gemini"
        assert binding.model == "gemini-2.5-flash"

    def test_missing_binding_is_actionable(self, monkeypatch):
        monkeypatch.delenv("NATURAL_CHANNEL_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="provider/model"):
            resolve_channel("natural_channel")

    def test_binding_without_a_slash_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "gemini-2.5-flash")
        with pytest.raises(RuntimeError, match="provider/model"):
            resolve_channel("natural_channel")

    def test_unknown_provider_in_binding_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "acme/model-x")
        with pytest.raises(RuntimeError, match="unknown provider"):
            resolve_channel("natural_channel")


class TestChannelIndependence:
    """Decision D1's central risk, made inspectable at runtime."""

    def test_cross_provider_binding_is_independent(self, monkeypatch):
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "gemini/gemini-2.5-flash")
        monkeypatch.setenv("PROGRAM_CHANNEL_MODEL", "groq/llama-3.3-70b-versatile")
        independent, detail = channels_are_independent()
        assert independent
        assert "cross-provider" in detail

    def test_same_provider_different_models_is_still_independent(self, monkeypatch):
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "groq/llama-3.3-70b-versatile")
        monkeypatch.setenv("PROGRAM_CHANNEL_MODEL", "groq/qwen-3-32b")
        assert channels_are_independent()[0]

    def test_identical_binding_is_flagged_as_the_ablation_arm(self, monkeypatch):
        """If both channels are one model, disagreement measures sampling noise
        and the research question becomes unanswerable."""
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "groq/llama-3.3-70b-versatile")
        monkeypatch.setenv("PROGRAM_CHANNEL_MODEL", "groq/llama-3.3-70b-versatile")
        independent, detail = channels_are_independent()
        assert not independent
        assert "H4" in detail


class TestUsageAccounting:
    """Reasoning models bill for tokens they do not report as completion.

    Observed against the live free tier: a Gemini call reported prompt=29,
    completion=2, total=223. Deriving the total as prompt+completion would have
    understated that call's output roughly sevenfold and corrupted the efficiency
    analysis (spec section 33 / RQ4 / H5).
    """

    def test_usage_adds(self):
        total = Usage(
            prompt_tokens=10, completion_tokens=5, latency_seconds=1.0, equivalent_cost_usd=0.001
        ) + Usage(
            prompt_tokens=20, completion_tokens=7, latency_seconds=2.0, equivalent_cost_usd=0.002
        )
        assert total.prompt_tokens == 30
        assert total.completion_tokens == 12
        assert total.total_tokens == 42
        assert total.latency_seconds == 3.0
        assert total.equivalent_cost_usd == pytest.approx(0.003)

    def test_providers_reported_total_wins_over_the_derived_sum(self):
        """The real Gemini shape: hidden reasoning invisible in both fields."""
        usage = Usage(prompt_tokens=29, completion_tokens=2, reported_total_tokens=223)
        assert usage.total_tokens == 223
        assert usage.billable_output_tokens == 194

    def test_explicit_reasoning_tokens_are_counted(self):
        """The Groq shape: reasoning reported in completion_tokens_details."""
        usage = Usage(
            prompt_tokens=95, completion_tokens=10, reasoning_tokens=104,
            reported_total_tokens=209,
        )
        assert usage.total_tokens == 209
        assert usage.billable_output_tokens == 114

    def test_derived_total_used_when_provider_reports_none(self):
        usage = Usage(prompt_tokens=10, completion_tokens=5)
        assert usage.total_tokens == 15

    def test_reasoning_tokens_accumulate(self):
        total = Usage(reasoning_tokens=100, reported_total_tokens=200) + Usage(
            reasoning_tokens=50, reported_total_tokens=100
        )
        assert total.reasoning_tokens == 150
        assert total.reported_total_tokens == 300


class TestEmptyContent:
    """An empty answer is a failure, not an answer.

    Live free models spent a small max_tokens budget on hidden reasoning and
    returned "" with tokens consumed. Passing that through would hand the
    pipeline an empty channel answer that looks like a real one.
    """

    def test_empty_content_is_an_error_not_an_empty_answer(self):
        response = {
            "model": "m",
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 95, "completion_tokens": 16, "total_tokens": 159,
                "completion_tokens_details": {"reasoning_tokens": 14},
            },
        }
        p = provider(lambda r: httpx.Response(200, json=response), retries=2)
        with pytest.raises(ProviderUnavailableError, match="empty content"):
            ask(p)

    def test_the_error_says_how_to_fix_it(self):
        response = {
            "model": "m",
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 16, "total_tokens": 40,
                      "completion_tokens_details": {"reasoning_tokens": 14}},
        }
        p = provider(lambda r: httpx.Response(200, json=response), retries=1)
        with pytest.raises(ProviderUnavailableError, match="raise max_tokens"):
            ask(p)

    def test_whitespace_only_content_is_also_empty(self):
        response = {
            "model": "m",
            "choices": [{"message": {"content": "   \n  "}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        p = provider(lambda r: httpx.Response(200, json=response), retries=1)
        with pytest.raises(ProviderUnavailableError):
            ask(p)

    def test_a_retry_can_recover_an_empty_first_response(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(200, json={
                    "model": "m",
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 16, "total_tokens": 21},
                })
            return httpx.Response(200, json=CHAT_OK)

        assert ask(provider(handler, retries=3)).text == "25.0"


class TestReasoningCapture:
    def test_reasoning_field_is_captured_for_research_logs(self):
        """Spec section 24 forbids showing hidden chain-of-thought to users, but
        error analysis needs it recorded."""
        response = {
            "model": "m",
            "choices": [{
                "message": {"content": "25", "reasoning": "compute 125/100 - 1"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 30},
        }
        result = ask(provider(lambda r: httpx.Response(200, json=response)))
        assert result.text == "25"
        assert result.reasoning == "compute 125/100 - 1"

    def test_reasoning_content_alias_is_also_captured(self):
        response = {
            "model": "m",
            "choices": [{
                "message": {"content": "25", "reasoning_content": "thinking"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 30},
        }
        assert ask(provider(lambda r: httpx.Response(200, json=response))).reasoning == "thinking"


class TestQuotaExhaustion:
    """A spent allowance and a pace limit share HTTP 429 and must not share a
    retry policy. Retrying a spent free-tier quota burned 621 seconds on a single
    question in the first live slice run, and consumed more of the allowance
    doing it."""

    GEMINI_QUOTA_BODY = json.dumps(
        {
            "error": {
                "code": 429,
                "message": (
                    "You exceeded your current quota. * Quota exceeded for metric: "
                    "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
                    "limit: 20, model: gemini-3.7-flash"
                ),
                "status": "RESOURCE_EXHAUSTED",
            }
        }
    )

    def test_quota_exhaustion_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429, text=self.GEMINI_QUOTA_BODY)

        with pytest.raises(QuotaExhaustedError):
            ask(provider(handler, retries=5))
        assert len(calls) == 1, "a spent allowance does not refill on retry"

    def test_a_plain_pace_limit_is_still_retried(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(429, text="Too many requests, slow down")
            return httpx.Response(200, json=CHAT_OK)

        assert ask(provider(handler, retries=3)).text == "25.0"

    def test_quota_exhaustion_is_still_a_rate_limit_error(self):
        """Subclassing keeps existing handlers working."""
        assert issubclass(QuotaExhaustedError, RateLimitError)

    def test_the_recorded_gemini_limit_matches_what_was_observed(self):
        """Recorded from a real 429, not from documentation. If this is ever
        raised, it must be on evidence - see the comment in registry.py."""
        assert PROVIDER_SPECS["gemini"].rate_limit.requests_per_day == 20


class TestRateLimitClassification:
    """A pace limit and a spent allowance share HTTP 429 and need opposite
    handling. Both misclassifications were observed on live runs:

    * retrying a spent Gemini allowance burned 621 seconds on one question;
    * classifying a 270-MILLISECOND Groq TPM limit as exhausted - because the
      word "billing" appears in Groq's upsell URL - marked the channel
      UNAVAILABLE without retrying, manufacturing failure events that were
      really just pacing.

    The provider's own stated wait separates them; wording does not.
    """

    GROQ_TPM = (
        '{"error":{"message":"Rate limit reached for model `qwen/qwen3.6-27b` in '
        'organization `org_x` service tier `on_demand` on tokens per minute (TPM): '
        "Limit 8000, Used 4093, Requested 3943. Please try again in 269.999999ms. "
        "Need more tokens? Upgrade to Dev Tier today at "
        'https://console.groq.com/settings/billing","type":"tokens",'
        '"code":"rate_limit_exceeded"}}'
    )

    def test_a_millisecond_pace_limit_is_retried(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(429, text=self.GROQ_TPM)
            return httpx.Response(200, json=CHAT_OK)

        assert ask(provider(handler, retries=3)).text == "25.0"
        assert state["n"] == 2, "a 270ms limit must be waited out, not given up on"

    def test_the_stated_delay_is_parsed_from_the_body(self):
        from backend.services.llm.openai_compatible import _stated_retry_delay

        assert _stated_retry_delay(self.GROQ_TPM) == pytest.approx(0.27, abs=0.01)
        assert _stated_retry_delay("Please retry in 7.7s") == pytest.approx(7.7)
        assert _stated_retry_delay("try again in 2 minutes") == pytest.approx(120.0)
        assert _stated_retry_delay("no hint here") is None

    def test_an_upsell_url_does_not_mean_the_allowance_is_spent(self):
        """The exact regression: "billing" in a marketing link is not evidence."""
        from backend.services.llm.openai_compatible import _is_allowance_exhausted

        assert not _is_allowance_exhausted(self.GROQ_TPM.lower(), 0.27)

    def test_a_daily_allowance_with_no_short_retry_is_exhausted(self):
        from backend.services.llm.openai_compatible import _is_allowance_exhausted

        body = (
            "you exceeded your current quota. quota exceeded for metric: "
            "generate_content_free_tier_requests, limit: 20"
        ).lower()
        assert _is_allowance_exhausted(body, None)


class TestTokensPerMinute:
    """Groq's free tier binds on TOKENS per minute, not requests. Measured live:
    the request counter still showed 28 free slots while the provider refused."""

    def test_the_token_window_blocks_before_the_request_window(self):
        now = {"t": 0.0}
        limiter = RateLimiter(
            RateLimit(requests_per_minute=30, tokens_per_minute=8_000),
            clock=lambda: now["t"],
        )
        limiter.acquire(estimated_tokens=3_000)
        limiter.acquire(estimated_tokens=3_000)
        # 6,000 of 8,000 spent and 28 request slots still free - but a third
        # 3,000-token call does not fit.
        with pytest.raises(TimeoutError):
            limiter.acquire(estimated_tokens=3_000, timeout=0.001)

    def test_the_token_window_slides(self):
        now = {"t": 0.0}
        limiter = RateLimiter(
            RateLimit(requests_per_minute=30, tokens_per_minute=8_000),
            clock=lambda: now["t"],
        )
        limiter.acquire(estimated_tokens=7_000)
        now["t"] = 61.0
        assert limiter.acquire(estimated_tokens=7_000) == 0.0

    def test_no_tpm_configured_means_no_token_pacing(self):
        limiter = RateLimiter(RateLimit(requests_per_minute=30), clock=lambda: 0.0)
        for _ in range(5):
            assert limiter.acquire(estimated_tokens=1_000_000) == 0.0

    def test_actual_usage_replaces_the_estimate(self):
        """The estimate is made before the response exists and is always wrong;
        letting a systematic under-estimate accumulate produces provider 429s."""
        limiter = RateLimiter(
            RateLimit(requests_per_minute=30, tokens_per_minute=8_000),
            clock=lambda: 0.0,
        )
        limiter.acquire(estimated_tokens=1_000)
        limiter.record_token_usage(7_500)
        with pytest.raises(TimeoutError):
            limiter.acquire(estimated_tokens=1_000, timeout=0.001)

    def test_groq_records_the_observed_tpm(self):
        assert PROVIDER_SPECS["groq"].rate_limit.tokens_per_minute == 8_000


class TestJsonModeDegradation:
    """Server-side JSON mode and a model that externalises its reasoning are
    mutually incompatible. Qwen on Groq insists on a <think> block, Groq's
    validator rejects the generation because the prose is not JSON, and the
    request 400s with an EMPTY failed_generation - observed live.

    Retrying the same request cannot help. Dropping the constraint can, because
    the reply is parsed leniently afterwards anyway.
    """

    REJECTED = json.dumps(
        {
            "error": {
                "message": "Failed to validate JSON. Please adjust your prompt.",
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "failed_generation": "",
            }
        }
    )

    def test_json_mode_is_dropped_and_the_call_succeeds(self):
        seen: list[dict] = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            if "response_format" in body:
                return httpx.Response(400, text=self.REJECTED)
            return httpx.Response(200, json=CHAT_OK)

        response = provider(handler, retries=3).complete(
            [Message(Role.USER, "q")], model="m", response_format_json=True
        )
        assert response.text == "25.0"
        assert "response_format" in seen[0]
        assert "response_format" not in seen[1], "the constraint must be dropped"

    def test_the_degradation_is_recorded_not_silent(self):
        """The request that succeeded is not the request that was asked for."""

        def handler(request):
            if "response_format" in json.loads(request.content):
                return httpx.Response(400, text=self.REJECTED)
            return httpx.Response(200, json=CHAT_OK)

        response = provider(handler, retries=3).complete(
            [Message(Role.USER, "q")], model="m", response_format_json=True
        )
        assert response.notes, "a silent degradation would misdescribe the run"
        assert "JSON mode" in response.notes[0]

    def test_a_plain_400_is_still_an_error(self):
        p = provider(lambda r: httpx.Response(400, text="malformed request"), retries=2)
        with pytest.raises(ProviderUnavailableError):
            ask(p)

    def test_no_json_mode_requested_means_nothing_to_drop(self):
        response = ask(provider(lambda r: httpx.Response(200, json=CHAT_OK)))
        assert response.notes == ()


class TestDailyTokenAllowance:
    """The limit that actually ends a free-tier day (RX-022).

    `requests_per_day` was the only daily counter until 2026-08-30. On Groq the
    request counter reported 14,293 of 14,400 still free while the provider had
    already refused on 199,170 of 200,000 tokens - so the campaign budget said
    0.30 days when the truth was 98.
    """

    def limiter(self, **kwargs):
        from backend.services.llm.ratelimit import RateLimit, RateLimiter

        return RateLimiter(
            RateLimit(requests_per_minute=1000, requests_per_day=1000, **kwargs),
            name="test",
            state_path=None,
        )

    def test_the_token_allowance_is_enforced_before_the_call(self):
        """Before, not after a 429: the point of a client-side limiter is that
        the campaign can see the wall coming and stop cleanly."""
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        limiter = self.limiter(tokens_per_day=1000)
        limiter.acquire(estimated_tokens=600)
        with pytest.raises(DailyQuotaExhausted, match="token allowance"):
            limiter.acquire(estimated_tokens=600)

    def test_the_message_names_tokens_and_not_requests(self):
        """The failure has to be diagnosable. "Quota exhausted" with 993 of 1000
        requests free is the confusing half of what went wrong."""
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        limiter = self.limiter(tokens_per_day=100)
        with pytest.raises(DailyQuotaExhausted) as exc:
            limiter.acquire(estimated_tokens=500)
        message = str(exc.value)
        assert "token" in message.lower()
        assert "requests are still free" in message

    def test_no_token_limit_means_no_token_gate(self):
        """NVIDIA sends no rate-limit headers, so its daily token cap is
        UNOBSERVED. Unobserved must mean unenforced, never guessed."""
        limiter = self.limiter()
        for _ in range(5):
            limiter.acquire(estimated_tokens=10_000_000)

    def test_the_real_figure_replaces_the_estimate(self):
        """The estimate is made before the reply exists, so it is always wrong.
        Left uncorrected, a systematic under-estimate walks the day's total away
        from the provider's and the wall arrives without warning."""
        limiter = self.limiter(tokens_per_day=10_000)
        limiter.acquire(estimated_tokens=4_500)
        assert limiter.tokens_today == 4_500
        limiter.record_token_usage(1_200)
        assert limiter.tokens_today == 1_200

    def test_the_counter_rolls_over_with_the_day(self, monkeypatch):
        from backend.services.llm import ratelimit as module

        limiter = self.limiter(tokens_per_day=1000)
        limiter.acquire(estimated_tokens=900)
        assert limiter.tokens_today == 900
        monkeypatch.setattr(module, "utc_day", lambda: "2099-01-01")
        assert limiter.tokens_today == 0

    def test_groq_carries_the_observed_limit(self):
        """OBSERVED from a live 429, not read off a documentation page - the
        registry already carries one figure that was wrong by 75x when taken
        from published docs."""
        from backend.services.llm.registry import PROVIDER_SPECS

        assert PROVIDER_SPECS["groq"].rate_limit.tokens_per_day == 200_000

    def test_the_campaign_budget_counts_tokens(self):
        """Budgeting in requests alone understated the campaign by ~90x."""
        from evaluation.arms import ALL_ARMS
        from experiments.campaign import estimate_requests

        budget = estimate_requests(list(ALL_ARMS.values()), 150)
        assert budget.estimated_tokens > budget.total
        by_requests = budget.days_at(14_400)
        by_tokens = budget.days_at_tokens(200_000)
        assert by_tokens > by_requests * 50, (
            "the token estimate must dominate; if it does not, the constant or "
            "the limit has drifted and the budget is misleading again"
        )
        assert "estimated_tokens" in budget.as_dict()


class TestTheProviderCountIsAuthoritative:
    """A client-side counter starts at zero every time the process does.

    The provider counts the whole day, across every process and session, so the
    local figure is optimistic by construction. On 2026-08-30 the persisted
    state said 0 tokens while Groq had already counted 199,170 of 200,000 - the
    limiter would have waved through calls the provider was certain to refuse.
    A 429 is the one moment the provider states its own figure.
    """

    TPD_BODY = (
        '{"error":{"message":"Rate limit reached for model `qwen/qwen3.6-27b` in '
        "organization `org_x` service tier `on_demand` on tokens per day (TPD): "
        'Limit 200000, Used 199170, Requested 4143. Please try again in 23m51.216s."}}'
    )

    def limiter(self, **kwargs):
        from backend.services.llm.ratelimit import RateLimit, RateLimiter

        return RateLimiter(
            RateLimit(requests_per_minute=1000, requests_per_day=1000, **kwargs),
            name="test",
            state_path=None,
        )

    def test_the_daily_figure_is_parsed_from_a_tpd_429(self):
        from backend.services.llm.openai_compatible import _stated_daily_tokens_used

        assert _stated_daily_tokens_used(self.TPD_BODY) == 199_170

    def test_a_per_minute_429_is_not_mistaken_for_a_daily_one(self):
        """Adopting a TPM figure as a daily total would make the limiter refuse
        for the rest of the day over a one-minute burst."""
        from backend.services.llm.openai_compatible import _stated_daily_tokens_used

        body = "on tokens per minute (TPM): Limit 8000, Used 7900, Requested 500"
        assert _stated_daily_tokens_used(body) is None

    def test_syncing_raises_the_local_count_to_the_provider_s(self):
        limiter = self.limiter(tokens_per_day=200_000)
        limiter.acquire(estimated_tokens=4_500)
        limiter.sync_daily_tokens(199_170)
        assert limiter.tokens_today == 199_170

    def test_syncing_never_lowers_the_count(self):
        """A smaller number later in the day is far more likely to be a parse
        artifact than a refund."""
        limiter = self.limiter(tokens_per_day=200_000)
        limiter.sync_daily_tokens(199_170)
        limiter.sync_daily_tokens(5)
        assert limiter.tokens_today == 199_170

    def test_after_syncing_the_next_call_is_refused_locally(self):
        """The whole point: refuse before the call, not after a second 429."""
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        limiter = self.limiter(tokens_per_day=200_000)
        limiter.sync_daily_tokens(199_170)
        with pytest.raises(DailyQuotaExhausted):
            limiter.acquire(estimated_tokens=4_500)

    def test_a_429_on_the_wire_syncs_the_limiter(self):
        """End to end through the provider, not just the parser."""
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429, text=self.TPD_BODY)

        prov = provider(handler)
        with pytest.raises((QuotaExhaustedError, RateLimitError)):
            ask(prov)
        assert prov.limiter.tokens_today >= 199_170, (
            "the provider stated its own daily total and it was not adopted"
        )


class TestTheDailyCounterIsNotDoubleCharged:
    """A call slower than a minute used to be charged twice.

    `_token_events` is the per-MINUTE window and ages entries out after 60
    seconds. `record_token_usage` read its pre-call estimate back out of that
    window, so any call slower than a minute - which Qwen routinely is, at
    47-136s - found it empty, took the "nothing to correct" branch, and added
    the real cost ON TOP of the estimate `acquire` had already charged.

    The daily counter is not a rate-limiting window and must not share its
    lifetime. These tests demonstrate the defect directly rather than through
    the incident that led to it: that incident turned out to have a different
    cause (the provider really had spent 199,188 tokens, on a rolling window),
    and a test justified by the wrong story is a test nobody can trust later.
    """

    def limiter(self, **kwargs):
        from backend.services.llm.ratelimit import RateLimit, RateLimiter

        return RateLimiter(
            RateLimit(requests_per_minute=100, requests_per_day=100, **kwargs),
            name="test",
            state_path=None,
        )

    def test_a_call_slower_than_the_minute_window_is_charged_once(self):
        limiter = self.limiter(tokens_per_day=100_000)
        limiter.acquire(estimated_tokens=4_100)
        # What a >60s call does to the per-minute window by the time the reply
        # lands: the entry has already aged out.
        limiter._token_events.clear()
        limiter.record_token_usage(1_500)
        assert limiter.tokens_today == 1_500

    def test_successive_slow_calls_do_not_accumulate_phantom_tokens(self):
        limiter = self.limiter(tokens_per_day=100_000)
        for real in (1_500, 1_200, 900):
            limiter.acquire(estimated_tokens=4_100)
            limiter._token_events.clear()
            limiter.record_token_usage(real)
        assert limiter.tokens_today == 3_600

    def test_a_fast_call_is_still_corrected_to_the_real_figure(self):
        """The window is intact here; the correction must work either way."""
        limiter = self.limiter(tokens_per_day=100_000)
        limiter.acquire(estimated_tokens=4_100)
        limiter.record_token_usage(1_500)
        assert limiter.tokens_today == 1_500

    def test_an_over_estimate_does_not_drive_the_counter_negative(self):
        limiter = self.limiter(tokens_per_day=100_000)
        limiter.acquire(estimated_tokens=4_100)
        limiter.record_token_usage(1)
        assert limiter.tokens_today >= 0

    def test_a_call_with_no_usage_report_keeps_its_estimate(self):
        """Some providers report nothing. The estimate is all there is, and
        dropping it would make those calls free."""
        limiter = self.limiter(tokens_per_day=100_000)
        limiter.acquire(estimated_tokens=4_100)
        assert limiter.tokens_today == 4_100

    def test_a_day_rollover_clears_the_pending_estimate(self, monkeypatch):
        from backend.services.llm import ratelimit as module

        limiter = self.limiter(tokens_per_day=100_000)
        limiter.acquire(estimated_tokens=4_100)
        monkeypatch.setattr(module, "utc_day", lambda: "2099-01-01")
        assert limiter.tokens_today == 0
        limiter.record_token_usage(1_500)
        assert limiter.tokens_today == 1_500, (
            "a reply landing after midnight must not subtract yesterday's estimate"
        )
