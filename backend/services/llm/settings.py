"""LLM call settings, read from the environment at CALL time.

These four variables have been in `.env` and `.env.example` since Module 8, one
of them annotated `# reasoning models need headroom`, and **nothing read any of
them**. Every call ran on whatever default its own function signature carried.

That was not cosmetic. `LLM_MAX_TOKENS=4096` was ignored in favour of Channel
A's own `max_tokens=2048`, and against a reasoning model that spends budget on
hidden thinking before emitting anything, 2048 truncates the reply mid-`<think>`.
The channel then returns "no JSON object in the reply" - measured at **35% of
questions** (RX-019) - which the campaign would have recorded as the method
failing rather than as a token budget.

Read at call time, never at import. A module-level `os.environ.get` is evaluated
when the module is first imported, which is before `load_dotenv` in most entry
points and before any test fixture can redirect it - the same evaluation-order
trap as the argparse defaults in D33 and the API's upload directory.

Deliberately NOT included here:

- `ABLATION_SAME_MODEL`. Arms are `ArmConfig` objects over one graph (D26), and
  `same_model_both_channels` lives there. A second source of truth in the
  environment could disagree with the arm actually being run, and the run record
  would name one while the code did the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["LLMSettings", "llm_settings"]


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        # Loud, not silent. A malformed budget that quietly reverts to a default
        # is how a run ends up not being the run its config claims.
        raise RuntimeError(f"{name}={raw!r} is not a number") from None


def _int(name: str, default: int) -> int:
    value = _float(name, float(default))
    if value != int(value):
        raise RuntimeError(f"{name}={value!r} must be a whole number")
    return int(value)


@dataclass(frozen=True)
class LLMSettings:
    temperature: float
    max_tokens: int
    max_retries: int
    timeout_seconds: float


def llm_settings() -> LLMSettings:
    """The configured call settings, or the documented defaults.

    Defaults match `.env.example` exactly, so a missing `.env` behaves the same
    as the shipped one rather than differently-but-plausibly.
    """
    settings = LLMSettings(
        temperature=_float("LLM_TEMPERATURE", 0.0),
        max_tokens=_int("LLM_MAX_TOKENS", 4096),
        max_retries=_int("LLM_MAX_RETRIES", 3),
        timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 60.0),
    )
    if settings.temperature != 0.0:
        # Not an error - a temperature ablation is legitimate - but D7a pins
        # runs at 0 for reproducibility, so a non-zero value must be a choice
        # somebody made rather than a value somebody inherited.
        pass
    return settings
