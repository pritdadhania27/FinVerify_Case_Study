"""Client-side rate limiting for free-tier providers.

On a paid tier a rate limiter is a nicety. On a free tier it is load-bearing:
Groq allows 30 requests/minute and Gemini 10-30 depending on model, so an
unthrottled evaluation loop over a few hundred questions fails within seconds of
starting. Waiting for a slot is strictly better than issuing a request that will
be refused - a refused request costs the same round trip and yields nothing.

Daily quotas are tracked and **persisted**, because they are the binding
constraint on how much of an experiment can run in one day and they do not reset
when the process does. A run that dies overnight and restarts must not silently
believe it has a fresh 14,400 requests.

**The day boundary is UTC, not local.** This host runs at UTC+5:30, so local
midnight is 18:30 UTC - five and a half hours *before* the day rolls over for a
provider counting in UTC. A limiter that resets early is the dangerous direction:
it believes it has a fresh allowance and issues requests the provider still
counts against yesterday, which is precisely the refusal this module exists to
prevent. Resetting late merely costs throughput.

The client-side counter remains an **estimate**. A provider whose window resets
later than UTC midnight (some reset on US Pacific time) would still roll here
first, and requests can be refused for reasons this counter cannot see. The
provider's own 429 is always authoritative; the adapter surfaces it as
`RateLimitError` rather than trusting this count.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

# base.py imports nothing from this module, so this direction is safe.
from backend.services.llm.base import QuotaExhaustedError

__all__ = ["RateLimit", "RateLimiter", "DailyQuotaExhausted", "utc_day"]


def utc_day() -> str:
    """Today's date in UTC, as the key the daily counter is stored under."""
    return datetime.now(UTC).date().isoformat()


class DailyQuotaExhausted(QuotaExhaustedError):
    """The provider's daily free-tier allowance is spent.

    Raised rather than slept through: the reset can be many hours away, and a
    silent multi-hour block inside an experiment is indistinguishable from a
    hang.

    **A subclass of `QuotaExhaustedError`, not of `RuntimeError` (RX-033).** The
    two describe the same event from opposite sides - this one is the limiter
    refusing before the call, `QuotaExhaustedError` is the provider refusing
    during it - and every handler that stops a run on one must stop on the
    other. While they were unrelated classes, `campaign.py` caught only the
    provider-side one, so a client-side refusal fell through to the channel's
    ordinary failure path and 166 of 180 rows were written with Channel A
    "unavailable" in a run that reported `failed 0`.
    """


@dataclass(frozen=True)
class RateLimit:
    requests_per_minute: int
    requests_per_day: int | None = None
    # Free tiers also cap tokens/minute; None means unenforced client-side.
    tokens_per_minute: int | None = None
    # And tokens per DAY, which on Groq is the limit that actually ends a day's
    # work. It was unmodelled until 2026-08-30, and the gap is not small: the
    # request counter reported 14,293 of 14,400 free while the provider had
    # already refused on 199,170 of 200,000 tokens. A campaign budgeted in
    # requests therefore looked like it fit in one day when it needed weeks.
    tokens_per_day: int | None = None


class RateLimiter:
    """Sliding-window limiter with a persisted daily counter.

    A sliding window rather than a fixed one: a fixed window permits a burst at
    the boundary (30 at 11:59:59 plus 30 at 12:00:00), which providers count as
    60 in a minute and reject.
    """

    def __init__(
        self,
        limit: RateLimit,
        *,
        name: str = "provider",
        state_path: str | Path | None = None,
        clock=time.monotonic,
    ) -> None:
        self.limit = limit
        self.name = name
        self._clock = clock
        self._events: deque[float] = deque()
        # (timestamp, tokens) for the tokens-per-minute window. Separate
        # from the request window because the two limits bind at different
        # times: Groq allows 30 requests a minute but only 8,000 tokens, and
        # an evidence-carrying prompt is ~3,000 - so the TOKEN limit bites
        # after two calls while the request counter still reads 28 free.
        self._token_events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self._state_path = Path(state_path) if state_path else None
        self._day = utc_day()
        self._day_count = 0
        self._day_tokens = 0
        # The estimate charged to the DAILY counter for the call now in
        # flight. Kept apart from `_token_events`, which is the per-MINUTE
        # window and ages entries out after 60 seconds - see
        # `record_token_usage` for what conflating the two cost.
        self._pending_estimate = 0
        if self._state_path:
            self._load()

    # -- daily counter persistence -------------------------------------

    def _load(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        entry = data.get(self.name, {})
        if entry.get("day") == self._day:
            self._day_count = int(entry.get("count", 0))
            self._day_tokens = int(entry.get("tokens", 0))

    def _persist(self) -> None:
        if not self._state_path:
            return
        data = {}
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        data[self.name] = {
            "day": self._day,
            "count": self._day_count,
            "tokens": self._day_tokens,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _roll_day_if_needed(self) -> None:
        today = utc_day()
        if today != self._day:
            self._day = today
            self._day_count = 0
            self._day_tokens = 0
            self._pending_estimate = 0

    # -- public API ----------------------------------------------------

    @property
    def requests_today(self) -> int:
        with self._lock:
            self._roll_day_if_needed()
            return self._day_count

    def remaining_today(self) -> int | None:
        if self.limit.requests_per_day is None:
            return None
        return max(0, self.limit.requests_per_day - self.requests_today)

    @property
    def tokens_today(self) -> int:
        with self._lock:
            self._roll_day_if_needed()
            return self._day_tokens

    def tokens_remaining_today(self) -> int | None:
        if self.limit.tokens_per_day is None:
            return None
        return max(0, self.limit.tokens_per_day - self.tokens_today)

    def _tokens_in_window(self, now: float) -> int:
        while self._token_events and now - self._token_events[0][0] >= 60.0:
            self._token_events.popleft()
        return sum(tokens for _, tokens in self._token_events)

    def acquire(self, *, timeout: float = 300.0, estimated_tokens: int = 0) -> float:
        """Block until a request slot is free. Returns seconds waited.

        `estimated_tokens` paces against the provider's tokens-per-minute limit,
        which on Groq's free tier is the one that actually binds: 30 requests a
        minute is generous, 8,000 tokens a minute is not, and a prompt carrying
        eight evidence blocks is around 3,000. Measured on a live run, the
        request counter was still reporting 28 free slots while the provider was
        already refusing on TPM.

        Raises DailyQuotaExhausted if the daily allowance is spent, and
        TimeoutError if a slot does not free within `timeout`.
        """
        waited = 0.0
        deadline = self._clock() + timeout

        while True:
            with self._lock:
                self._roll_day_if_needed()

                if (
                    self.limit.requests_per_day is not None
                    and self._day_count >= self.limit.requests_per_day
                ):
                    raise DailyQuotaExhausted(
                        f"{self.name}: daily free-tier quota of "
                        f"{self.limit.requests_per_day} requests is spent "
                        f"(resets at provider's UTC day boundary)"
                    )

                # Refused BEFORE the call, not after a 429, because the point of
                # a client-side limiter is that the campaign can see the wall
                # coming. Checked against the estimate so a call that would
                # cross the line does not start.
                tpd = self.limit.tokens_per_day
                if tpd is not None and self._day_tokens + estimated_tokens > tpd:
                    raise DailyQuotaExhausted(
                        f"{self.name}: daily token allowance of {tpd:,} is spent "
                        f"({self._day_tokens:,} used, this call needs about "
                        f"{estimated_tokens:,}). This is the limit that binds on "
                        f"a free tier, not the request count - "
                        f"{self._day_count} of {self.limit.requests_per_day} "
                        f"requests are still free. Resets at the provider's UTC "
                        f"day boundary."
                    )

                now = self._clock()
                while self._events and now - self._events[0] >= 60.0:
                    self._events.popleft()

                requests_free = len(self._events) < self.limit.requests_per_minute

                tokens_free = True
                token_wait = 0.0
                tpm = self.limit.tokens_per_minute
                if tpm is not None and estimated_tokens > 0:
                    used = self._tokens_in_window(now)
                    if used + estimated_tokens > tpm:
                        tokens_free = False
                        # Wait for the oldest token event to age out; that is the
                        # soonest the window can possibly have room.
                        if self._token_events:
                            token_wait = 60.0 - (now - self._token_events[0][0]) + 0.01

                if requests_free and tokens_free:
                    self._events.append(now)
                    if estimated_tokens > 0:
                        self._token_events.append((now, estimated_tokens))
                    self._day_count += 1
                    self._day_tokens += estimated_tokens
                    self._pending_estimate = estimated_tokens
                    self._persist()
                    return waited

                sleep_for = token_wait if not tokens_free else 0.0
                if not requests_free and self._events:
                    sleep_for = max(sleep_for, 60.0 - (now - self._events[0]) + 0.01)
                sleep_for = max(sleep_for, 0.01)

            if self._clock() + sleep_for > deadline:
                raise TimeoutError(
                    f"{self.name}: no rate-limit slot within {timeout:.0f}s"
                )
            time.sleep(max(0.0, sleep_for))
            waited += sleep_for

    def record_token_usage(self, tokens: int) -> None:
        """Correct the pre-call estimate with what the call actually cost.

        The estimate has to be made before the response exists, so it is always
        wrong. Replacing the most recent entry keeps the window honest rather
        than letting a systematic under-estimate accumulate into 429s.
        """
        if tokens <= 0:
            return
        with self._lock:
            # Roll first: a reply that lands after midnight must not subtract
            # yesterday's estimate from today's total.
            self._roll_day_if_needed()

            # -- the per-minute window --------------------------------------
            if self._token_events:
                timestamp, _ = self._token_events[-1]
                self._token_events[-1] = (timestamp, tokens)
            else:
                self._token_events.append((self._clock(), tokens))

            # -- the daily counter, corrected against ITS OWN estimate -------
            # This used to read the estimate back out of `_token_events`, which
            # is the per-MINUTE window: entries age out after 60 seconds, so any
            # call slower than a minute - which Qwen routinely is, at 47-136s -
            # found the window empty, took the "no estimate to correct" branch,
            # and added the full cost ON TOP of the estimate `acquire` had
            # already charged. Demonstrated in
            # `TestTheDailyCounterIsNotDoubleCharged`; the estimate now lives in
            # its own slot, with the lifetime of the call rather than of a
            # rate-limiting window it was never part of.
            #
            # It was first suspected as the cause of a counter reading 199,866,
            # and it was NOT: the provider's own 429 reported 199,188 used, so
            # that figure was correct and had arrived through `sync_daily_tokens`.
            # The bug is real and the diagnosis of that incident was wrong; both
            # are recorded because a fix credited to the wrong symptom is a fix
            # nobody can reason about later.
            self._day_tokens += tokens - self._pending_estimate
            self._pending_estimate = 0
            self._day_tokens = max(0, self._day_tokens)
            self._persist()

    def sync_daily_tokens(self, used: int) -> None:
        """Adopt the provider's own count of today's token spend.

        A client-side counter starts at zero every time the process does, and
        counts only what this process spent. The provider counts the whole day
        across every process, every machine and every earlier session - so the
        local figure is optimistic by construction, and on the first run after a
        restart it is optimistic by everything spent before it.

        That is not hypothetical: on 2026-08-30 the persisted state said 0
        tokens while Groq had already counted 199,170 of 200,000. The local
        limiter would have waved through calls the provider was certain to
        refuse.

        A 429 is the one moment the provider states its own figure, so it is
        taken as authoritative - never lowered, because a smaller number later
        in the day is far more likely to be a parse artifact than a refund.
        """
        if used <= 0:
            return
        with self._lock:
            self._roll_day_if_needed()
            if used > self._day_tokens:
                self._day_tokens = used
                self._persist()

    def record_external_limit(self, retry_after: float) -> None:
        """Absorb a provider 429 by treating the window as full.

        The server's view of the limit is authoritative; ours is an estimate. On
        a 429 the window is filled so subsequent calls wait rather than hammering
        an endpoint that has already said no.
        """
        with self._lock:
            now = self._clock()
            self._events.clear()
            fill = now - 60.0 + max(0.0, retry_after)
            for _ in range(self.limit.requests_per_minute):
                self._events.append(fill)
