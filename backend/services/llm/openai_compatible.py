"""One adapter for every OpenAI-compatible provider.

Groq, NVIDIA NIM, Google Gemini (via its OpenAI-compatibility endpoint),
OpenRouter, Mistral, Together and a local Ollama server all speak the same
`/chat/completions` shape. A single adapter therefore covers all of them, and
swapping providers becomes configuration rather than code - which is exactly
what spec section 5 asks for and what the cross-provider channel-independence
ablation needs.

Built on `httpx` rather than the `openai` SDK. The request and response shapes
used here are a few fields of plain JSON, while the genuinely hard part -
free-tier rate limiting with quota persistence - is custom regardless. Adding an
SDK would not have removed that work, only added a dependency around it.
"""

from __future__ import annotations

import re
import time

import httpx

from backend.services.llm.base import (
    QuotaExhaustedError,
    AuthError,
    LLMProvider,
    LLMResponse,
    Message,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitError,
    Usage,
)
from backend.services.llm.ratelimit import RateLimit, RateLimiter

__all__ = ["OpenAICompatibleProvider"]

# 404 is here only for the bodiless case; a 404 that names a model still
# raises ModelNotFoundError from _raise_for_status and is never retried.
_RETRYABLE_STATUS = {404, 408, 409, 429, 500, 502, 503, 504}

# Providers state a wait in the body when the limit is a pace limit: Groq says
# "Please try again in 269.999999ms", Gemini "Please retry in 7.7s".
_STATED_DELAY = re.compile(
    r"(?:try again|retry)\s+in\s+([\d.]+)\s*(ms|milliseconds?|s|secs?|seconds?|m|minutes?)",
    re.I,
)

# A pace limit that clears within this many seconds is worth waiting out. Beyond
# it, the wait is long enough that a caller should be told the allowance is gone
# rather than blocked - a silent multi-hour sleep inside an experiment is
# indistinguishable from a hang.
_TRANSIENT_LIMIT_SECONDS = 120.0

# Phrases that indicate the *allowance* is spent rather than the pace too high.
# Deliberately narrow. An earlier version also matched "billing" and "credits",
# which appear in Groq's upsell URL (console.groq.com/settings/billing) on an
# ordinary tokens-per-minute limit - so a 270-MILLISECOND pace limit was
# classified as an exhausted daily quota and never retried, manufacturing
# CHANNEL_UNAVAILABLE events that were really just pacing.
_EXHAUSTION_MARKERS = (
    "resource_exhausted",
    "quota exceeded",
    "exceeded your current quota",
    "insufficient_quota",
    "per day",
    "per_day",
    "requests_per_day",
    "daily limit",
)


def _stated_retry_delay(body: str) -> float | None:
    """Seconds the provider says to wait, parsed from the response body."""
    match = _STATED_DELAY.search(body)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2).lower()
    if unit.startswith("ms") or unit.startswith("millisecond"):
        return value / 1000.0
    if unit.startswith("m") and not unit.startswith("ms"):
        return value * 60.0
    return value


# "tokens per day (TPD): Limit 200000, Used 199170, Requested 4143"
_STATED_DAILY_TOKENS = re.compile(
    r"tokens?\s+per\s+day.*?used\s+(\d+)", re.I | re.S
)


def _stated_daily_tokens_used(body: str) -> int | None:
    """Today's token spend, as the PROVIDER counts it.

    Only read from a 429 that names a per-DAY token limit. A per-minute 429
    carries a different "Used" figure and adopting it as a daily total would
    make the limiter refuse for the rest of the day over a one-minute burst.
    """
    match = _STATED_DAILY_TOKENS.search(body)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_allowance_exhausted(body_lower: str, retry_after: float | None) -> bool:
    """Is this 429 a spent allowance, or just too fast?

    The provider's own stated wait is the strongest signal and outranks any
    wording: nothing that clears in under two minutes is an exhausted daily
    allowance, whatever the body says about upgrading.
    """
    if retry_after is not None and retry_after <= _TRANSIENT_LIMIT_SECONDS:
        return False
    return any(marker in body_lower for marker in _EXHAUSTION_MARKERS)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        rate_limit: RateLimit,
        timeout: float = 120.0,
        max_retries: int = 5,
        state_path: str | None = None,
        price_per_mtok: tuple[float, float] = (0.0, 0.0),
        extra_headers: dict[str, str] | None = None,
        # Injected only by tests, so error mapping, retry and backoff can be
        # exercised deterministically instead of against a live free tier whose
        # quota the test suite would consume.
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        # Published paid rates (input, output) per million tokens, used only for
        # the equivalent-cost figure. Zero means "unknown", never "free".
        self._price_per_mtok = price_per_mtok
        self._transport = transport
        self._sleep = sleep
        self.limiter = RateLimiter(rate_limit, name=name, state_path=state_path)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }

    # -- helpers -------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self._transport is not None:
            return httpx.Client(timeout=self._timeout, transport=self._transport)
        return httpx.Client(timeout=self._timeout)

    def _equivalent_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        inp, out = self._price_per_mtok
        return (prompt_tokens / 1e6) * inp + (completion_tokens / 1e6) * out

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        body = response.text[:500]
        if status in (401, 403):
            raise AuthError(f"{self.name}: authentication rejected ({status}): {body}")
        if status == 404:
            # A 404 that SAYS something is a retired model id: terminal, and
            # retrying it only burns quota (that is why ModelNotFoundError is on
            # the no-retry list). A 404 with an EMPTY body says nothing, and
            # treating silence as a claim is what cost two questions in RX-019:
            # NVIDIA returned a bodiless 404 for a model that had answered
            # correctly seconds earlier and answered again afterwards, and the
            # terminal classification meant no retry was even attempted.
            #
            # The evidence is the body, not the status. Without it, a provider
            # already known to shed load mid-request (RX-014: 503 under
            # contention, latency 0.42s-12.5s on identical prompts) is far more
            # likely to be blipping than to have retired a model between two
            # calls a minute apart.
            if body.strip():
                raise ModelNotFoundError(f"{self.name}: model or endpoint not found: {body}")
            raise ProviderUnavailableError(
                f"{self.name}: HTTP 404 with an empty body - no claim about the model, "
                f"so this is treated as transient and retried. A genuinely retired "
                f"model id returns a body naming it."
            )
        if status == 429:
            # Two different events share this status code, and the provider's
            # own stated wait separates them far more reliably than any wording
            # does. See _is_allowance_exhausted.
            lowered = body.lower()
            retry_after = self._retry_after(response) or _stated_retry_delay(body)
            exhausted = _is_allowance_exhausted(lowered, retry_after)
            error_cls = QuotaExhaustedError if exhausted else RateLimitError
            raise error_cls(
                f"{self.name}: {'quota exhausted' if exhausted else 'rate limited'}: {body}",
                retry_after=retry_after,
            )
        if status >= 500:
            raise ProviderUnavailableError(f"{self.name}: server error {status}: {body}")
        if status >= 400:
            raise ProviderUnavailableError(f"{self.name}: request rejected {status}: {body}")

    # -- LLMProvider ---------------------------------------------------

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
        payload: dict = {
            "model": model,
            "messages": [m.as_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        json_mode_dropped = False
        started = time.monotonic()

        # Rough pre-call token estimate for TPM pacing. ~4 chars/token on prose
        # plus the reply budget; it only has to be close enough to pace, and
        # `record_token_usage` replaces it with the real figure afterwards.
        estimated_tokens = sum(len(m.content) for m in messages) // 4 + max_tokens

        for attempt in range(1, self._max_retries + 1):
            self.limiter.acquire(estimated_tokens=estimated_tokens)
            try:
                with self._client() as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers,
                        json=payload,
                    )
                # Server-side JSON mode and a model that externalises its
                # reasoning are mutually incompatible: Qwen on Groq insists on a
                # <think> block, Groq's validator rejects the generation because
                # the prose is not JSON, and the request 400s with an EMPTY
                # `failed_generation`. Retrying the same request cannot help, but
                # dropping the constraint can - the reply is parsed by
                # `model_output` afterwards anyway, which handles think blocks,
                # fences and prose. So the constraint is a preference, not a
                # requirement, and it degrades instead of failing the channel.
                if (
                    response.status_code == 400
                    and "json_validate_failed" in response.text
                    and "response_format" in payload
                ):
                    payload.pop("response_format")
                    json_mode_dropped = True
                    continue

                if response.status_code in _RETRYABLE_STATUS:
                    if response.status_code == 429:
                        wait = self._retry_after(response) or min(2 ** attempt, 60)
                        self.limiter.record_external_limit(wait)
                        # The provider has just stated its own daily total. That
                        # is the only moment it does, and it is authoritative
                        # over a counter that starts at zero every time this
                        # process does.
                        stated = _stated_daily_tokens_used(response.text)
                        if stated is not None:
                            self.limiter.sync_daily_tokens(stated)
                    self._raise_for_status(response)
                self._raise_for_status(response)
                data = response.json()

            except (AuthError, ModelNotFoundError, QuotaExhaustedError) as exc:
                # A wrong key, a retired model id, and a spent daily allowance
                # all share one property: asking again does not help. The quota
                # case is the expensive one to get wrong - it retries for
                # minutes and consumes more of the allowance doing it.
                raise exc
            except (RateLimitError, ProviderUnavailableError) as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                delay = getattr(exc, "retry_after", None) or min(2 ** attempt, 60)
                self._sleep(delay)
                continue
            except httpx.HTTPError as exc:
                last_error = ProviderUnavailableError(f"{self.name}: transport error: {exc}")
                if attempt == self._max_retries:
                    break
                self._sleep(min(2 ** attempt, 60))
                continue

            choices = data.get("choices") or []
            if not choices:
                raise ProviderUnavailableError(f"{self.name}: response contained no choices")

            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            # Groq's gpt-oss exposes this as `reasoning`; other providers use
            # `reasoning_content`. Captured for research logs, never shown to
            # users (spec section 24).
            reasoning = message.get("reasoning") or message.get("reasoning_content")
            finish_reason = choices[0].get("finish_reason")

            raw_usage = data.get("usage") or {}
            prompt_tokens = int(raw_usage.get("prompt_tokens", 0))
            completion_tokens = int(raw_usage.get("completion_tokens", 0))
            reported_total = int(raw_usage.get("total_tokens", 0))
            details = raw_usage.get("completion_tokens_details") or {}
            reasoning_tokens = int(details.get("reasoning_tokens", 0))

            # An empty answer is a failure, not an answer. On reasoning models it
            # usually means the token budget was spent thinking with nothing left
            # to say - observed here with max_tokens=64 on gpt-oss-120b, which
            # burned 159 tokens and returned "". Returning that silently would
            # hand the pipeline an empty channel answer that looks like a real
            # one; the retry gives it another chance, and a persistent empty is
            # raised so it is recorded as a channel failure rather than an answer.
            if not text:
                detail = (
                    f"{self.name}/{model}: empty content "
                    f"(finish_reason={finish_reason}, completion_tokens={completion_tokens}, "
                    f"reasoning_tokens={reasoning_tokens}, max_tokens={max_tokens})"
                )
                if finish_reason == "length" or reasoning_tokens:
                    detail += " - the token budget was consumed by reasoning; raise max_tokens"
                last_error = ProviderUnavailableError(detail)
                if attempt == self._max_retries:
                    break
                continue

            usage = Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                reported_total_tokens=reported_total,
                latency_seconds=time.monotonic() - started,
                cost_usd=0.0,  # free tier
            )
            # Replace the pre-call estimate with what the call actually cost, so
            # a systematic under-estimate cannot accumulate into provider 429s.
            self.limiter.record_token_usage(usage.total_tokens)
            if json_mode_dropped:
                # Recorded, not silent: the request that succeeded is not the
                # request that was asked for, and a reproducibility log that hid
                # that would misdescribe the run.
                notes = (
                    (
                        "server-side JSON mode rejected by the provider and "
                        "dropped; reply parsed leniently instead"
                    ),
                )
            else:
                notes = ()
            return LLMResponse(
                text=text,
                provider=self.name,
                model=data.get("model", model),
                notes=notes,
                usage=Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    reported_total_tokens=reported_total,
                    latency_seconds=usage.latency_seconds,
                    cost_usd=0.0,
                    # Priced on billable output, which includes hidden reasoning.
                    equivalent_cost_usd=self._equivalent_cost(
                        prompt_tokens, usage.billable_output_tokens
                    ),
                ),
                reasoning=reasoning,
                finish_reason=finish_reason,
                temperature=temperature,
                max_tokens=max_tokens,
                prompt_version=prompt_version,
                attempts=attempt,
                raw=data,
            )

        raise last_error or ProviderUnavailableError(f"{self.name}: exhausted retries")

    def list_models(self) -> list[str]:
        try:
            with self._client() as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers)
            self._raise_for_status(response)
            return sorted(m.get("id", "") for m in response.json().get("data", []))
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name}: cannot list models: {exc}") from exc
