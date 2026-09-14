"""Verify LLM providers by actually calling them (spec section 3, section 7 rule 2).

Free providers rotate their model lineups without notice, so a model id that
worked last month may 404 today. This script does not trust configuration - it
lists what each provider currently serves and makes a real minimal completion
against each configured channel binding.

Usage:
    python scripts/verify_llm_providers.py            # health-check bindings
    python scripts/verify_llm_providers.py --list     # list available models
    python scripts/verify_llm_providers.py --quota    # show quota consumed today

Exit code 0 only when every configured channel binding actually responded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as `python scripts/verify_llm_providers.py` from the repo root, which
# otherwise leaves the project package off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The repository's .env, not one relative to wherever the process started:
# run this from another directory and every API key silently goes missing,
# which reads as "no providers configured" rather than as a path problem.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

from dotenv import load_dotenv  # noqa: E402

from backend.services.llm import (
    CHANNEL_ROLES,
    PROVIDER_SPECS,
    LLMError,
    available_providers,
    build_provider,
    channels_are_independent,
    resolve_channel,
)


def cmd_list() -> int:
    configured = available_providers()
    if not configured:
        print("No provider credentials found. Set GROQ_API_KEY and/or GEMINI_API_KEY in .env")
        return 1

    for name in configured:
        spec = PROVIDER_SPECS[name]
        print(f"\n=== {name} ===")
        print(f"  endpoint: {spec.base_url}")
        print(
            f"  free tier: {spec.rate_limit.requests_per_minute} req/min"
            + (
                f", {spec.rate_limit.requests_per_day} req/day"
                if spec.rate_limit.requests_per_day
                else ""
            )
        )
        try:
            models = build_provider(name).list_models()
        except (LLMError, RuntimeError) as exc:
            print(f"  models: UNAVAILABLE - {exc}")
            continue
        print(f"  models ({len(models)}):")
        for model in models:
            print(f"    {model}")
    return 0


def cmd_quota() -> int:
    for name in available_providers():
        provider = build_provider(name)
        limiter = provider.limiter
        remaining = limiter.remaining_today()
        cap = limiter.limit.requests_per_day
        print(
            f"{name:12} used today: {limiter.requests_today:6}"
            + (f" / {cap}  remaining: {remaining}" if cap else "  (no daily cap)")
        )
    return 0


def cmd_health() -> int:
    configured = available_providers()
    print(f"providers with credentials: {configured or 'NONE'}")
    if not configured:
        print("\nSet GROQ_API_KEY and/or GEMINI_API_KEY in .env (both free, no card).")
        return 1

    # The research-validity check, surfaced before any results are produced.
    try:
        independent, detail = channels_are_independent()
        marker = "OK  " if independent else "WARN"
        print(f"\n[{marker}] channel independence (D1): {detail}")
    except RuntimeError as exc:
        print(f"\n[FAIL] channel bindings unresolvable: {exc}")
        return 1

    print()
    failures = 0
    for role in CHANNEL_ROLES:
        try:
            binding = resolve_channel(role)
        except RuntimeError as exc:
            print(f"{role:24} UNCONFIGURED  {exc}")
            failures += 1
            continue

        if binding.provider not in configured:
            print(
                f"{role:24} BLOCKED       {binding.label} - "
                f"{PROVIDER_SPECS[binding.provider].key_env} not set"
            )
            failures += 1
            continue

        try:
            provider = build_provider(binding.provider)
            ok, detail = provider.health_check(binding.model)
        except (LLMError, RuntimeError) as exc:
            ok, detail = False, str(exc)

        print(f"{role:24} {'VERIFIED' if ok else 'FAILED  '}      {detail}")
        failures += 0 if ok else 1

    print()
    if failures:
        print(f"{failures} channel binding(s) not verified. No experiment can run until fixed.")
        return 1
    print("all channel bindings VERIFIED with real API calls")
    return 0


def main() -> int:
    load_dotenv(_ENV_FILE)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list available models")
    parser.add_argument("--quota", action="store_true", help="show quota used today")
    args = parser.parse_args()

    if args.list:
        return cmd_list()
    if args.quota:
        return cmd_quota()
    return cmd_health()


if __name__ == "__main__":
    sys.exit(main())
