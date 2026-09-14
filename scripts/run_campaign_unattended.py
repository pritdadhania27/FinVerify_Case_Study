"""Keep resuming one campaign until it finishes or the deadline passes.

A campaign stops the moment its allowance is spent (RX-033 made that stop work),
and left alone it needs a person to resume it once per window.

**Since D44 a full campaign usually finishes inside one day**, so this is now
insurance rather than the normal path: Channel A moved to NVIDIA, which has no
observed daily token cap, and six arms over 45 questions cost 720 requests. What
can still stop a run is the request cap, and that rolls on the UTC day like
everything else the limiter counts - so the waiting logic below is unchanged and
still correct.

**Run it with the venv interpreter, explicitly, from the project root.** On
Windows a bare `run_campaign_unattended.py` is handed to whichever Python owns
the `.py` file association - here the system 3.12, which has `dotenv` but not
`langgraph` - and the run dies several imports deep on a ModuleNotFoundError
that reads like a missing dependency rather than a wrong interpreter.

    cmd.exe:
        cd /d D:\\Claude_Cowork_Code\\AS\\Case_Study
        .venv\\Scripts\\python.exe scripts\\run_campaign_unattended.py ^
            --resume campaign_20260901T105355Z --arms A B5 G H B1 B4

    PowerShell:
        cd D:\\Claude_Cowork_Code\\AS\\Case_Study
        .\\.venv\\Scripts\\python.exe scripts\\run_campaign_unattended.py `
            --resume campaign_20260901T105355Z --arms A B5 G H B1 B4

    `campaign_20260831T235851Z` is the PRE-D44 run and must not be resumed: it
    ran with Channel A on Groq, and result rows do not record the model that
    produced them, so continuing it would mix two Channel A models inside one
    run with nothing in the data to separate them.

The caret and the backtick are line continuations and are NOT interchangeable:
a backtick in cmd.exe is not a continuation, and the arguments after it are
lost.

**This is not a retry loop, and ENGINEERING_RULES.md's rule against those still holds.** It
makes no API calls of its own and never re-attempts a failed call. It waits for
a quota window to refill and then re-invokes the campaign runner, which applies
the same limiter it always does. The limiter remains the only thing deciding
whether a request may be made, so this cannot spend more than an attended run
would - only fewer hours idle.

**Why the wait is hours rather than minutes.** Every resume reloads the
embedding model and rebuilds the BM25 cache, which takes minutes - so resuming
every few minutes would spend most of its life starting up. Two hours is the
default compromise.

**Correction (2026-09-01): the wait is usually until midnight UTC, not two
hours.** This script was written on RX-024's finding that Groq's window is
rolling at roughly 170 tokens a minute, and said so - but that describes the
*provider*, and no call reaches the provider without passing `RateLimiter`
first. The limiter models a calendar day and rolls its counters only when
`utc_day()` changes. A run that spends its 200,000 tokens at 05:00Z is therefore
refused locally for nineteen hours, however much the provider has quietly
refilled, and polling every two hours through that window means nine cycles that
each load a model and are then refused by a counter.

So a cycle that gains nothing and stops on quota now sleeps to the reset. The
alternative fix - teaching the limiter the rolling window, which would recover
roughly a fifth more allowance a day and spread it evenly - is a change to the
thing that protects the account from 429s, and is deliberately NOT made here.

Stops for good on the deadline, on a completed campaign, or on an exit code that
is neither success nor `run_campaign.STOPPED_EARLY` - a run failing for a real
reason should not be restarted every two hours for a week. That constant is
imported rather than written out, because assuming a quota stop exits 0 is what
ended the first supervised run four rows into a 180-row campaign.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Before ANY third-party import. The wrong interpreter has to be named
# while the failure is still legible - once dotenv or langgraph raises,
# the message says 'missing dependency' and points at pip.
from scripts._console import require_project_interpreter, use_utf8  # noqa: E402

use_utf8()
require_project_interpreter()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from backend.core.paths import project_path  # noqa: E402

# Imported, never hard-coded: the two must agree about what "stopped on quota"
# looks like. Treating it as a failure stops a week-long campaign at its first
# quota stop, which is what happened on 2026-09-01 four rows into a 180-row run.
from scripts.run_campaign import STOPPED_EARLY  # noqa: E402

DEFAULT_WAIT_MINUTES = 120


def completed_pairs(run_id: str) -> set[tuple[str, str]]:
    """(arm, question_id) pairs on disk - the same view `--resume` takes."""
    results = project_path(f"experiments/runs/{run_id}/results.jsonl")
    if not results.exists():
        return set()
    done: set[tuple[str, str]] = set()
    for line in results.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue          # a torn trailing write; it will be recomputed
        arm, qid = record.get("arm"), record.get("question_id")
        if arm and qid:
            done.add((arm, qid))
    return done


def expected_pairs(run_id: str) -> int | None:
    """How many (arm, question) pairs a finished run holds, from its config.

    Re-read every cycle, never cached. `config.json` records the scope of the
    LAST invocation, so a run first created by a small `--limit` smoke test
    reports that smaller scope until the next resume rewrites it. Reading it
    once up front would have stopped this campaign at 8 pairs instead of 180.
    """
    config = project_path(f"experiments/runs/{run_id}/config.json")
    if not config.exists():
        return None
    payload = json.loads(config.read_text(encoding="utf-8"))
    arms = payload.get("arms") or []
    questions = payload.get("questions")
    if not arms or not questions:
        return None
    return len(arms) * int(questions)


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def next_allowance_reset(now: datetime) -> datetime:
    """When the LOCAL limiter will next hand out allowance.

    Not when the provider will. RX-024 measured Groq's own window as rolling,
    and this script was written on that basis - but every call is gated by
    `RateLimiter`, which rolls its counters only when `utc_day()` changes
    (`_roll_day_if_needed`). Whatever the provider is doing, nothing gets
    through until midnight UTC.

    Those two facts were in the repository at the same time and contradicted
    each other: the docstring promised a wait of "about half an hour" for a
    useful slice of allowance, while a run that spent its 200,000 tokens at
    05:00Z could not make another call for nineteen hours. Waiting on the thing
    that actually refuses is the fix; modelling the provider's rolling window in
    the limiter is a separate change with real quota risk and is NOT made here.
    """
    return (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", required=True, help="run id to continue")
    parser.add_argument("--arms", nargs="+", default=["A", "B5", "G", "H"])
    parser.add_argument("--split", default="validation")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--deadline", default="2026-09-09",
                        help="UTC date to stop for good (D43)")
    parser.add_argument("--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES)
    parser.add_argument("--max-cycles", type=int, default=200,
                        help="backstop against an unattended process running forever")
    args = parser.parse_args()

    deadline = datetime.fromisoformat(args.deadline).replace(tzinfo=UTC)
    import os

    env = dict(os.environ, LLM_MAX_TOKENS=str(args.max_tokens))
    command = [
        sys.executable, "scripts/run_campaign.py",
        "--resume", args.resume,
        "--arms", *args.arms,
        "--split", args.split,
    ]

    print(f"  supervising {args.resume} until {args.deadline}")
    print(f"  resume every {args.wait_minutes} min while the allowance is spent\n")

    for cycle in range(1, args.max_cycles + 1):
        now = datetime.now(UTC)
        if now >= deadline:
            print(f"\n[{stamp()}] deadline {args.deadline} reached; stopping")
            return 0

        target = expected_pairs(args.resume)
        done = len(completed_pairs(args.resume))
        if target and done >= target:
            print(f"\n[{stamp()}] {done}/{target} pairs done; campaign complete")
            return 0

        print(f"\n[{stamp()}] cycle {cycle}: {done}/{target or '?'} pairs done")
        sys.stdout.flush()
        result = subprocess.run(command, cwd=PROJECT_ROOT, env=env)

        after = len(completed_pairs(args.resume))
        if result.returncode not in (0, STOPPED_EARLY):
            # A quota stop is STOPPED_EARLY and is the normal state here.
            # Anything else is a real error, and restarting a broken run every
            # two hours for a week helps nobody.
            print(f"\n[{stamp()}] campaign exited {result.returncode}; not restarting")
            return result.returncode
        # Re-read: the cycle just rewrote config.json with the scope it actually
        # ran, which is the authoritative target from here on.
        target = expected_pairs(args.resume)
        if target and after >= target:
            print(f"\n[{stamp()}] {after}/{target} pairs done; campaign complete")
            return 0

        gained = after - done
        now = datetime.now(UTC)
        wait_until = now + timedelta(minutes=args.wait_minutes)
        reason = f"{args.wait_minutes} min"
        if gained == 0 and result.returncode == STOPPED_EARLY:
            # Nothing got through and the stop was a quota stop, so the daily
            # bucket is spent and no amount of polling before midnight UTC will
            # change that. Each pointless cycle reloads the embedding model and
            # rebuilds the BM25 cache - minutes of work to be refused instantly
            # by a local counter. Sleep to the reset instead.
            reset = next_allowance_reset(now)
            if reset > wait_until:
                wait_until, reason = reset, "for the daily allowance to reset"
        if wait_until >= deadline:
            print(f"\n[{stamp()}] the next window would start after the deadline; stopping")
            return 0
        print(f"[{stamp()}] +{gained} pair(s) this cycle; "
              f"waiting {reason}, until {wait_until:%Y-%m-%d %H:%M}Z")
        sys.stdout.flush()
        time.sleep(max(0.0, (wait_until - datetime.now(UTC)).total_seconds()))

    print(f"\n[{stamp()}] hit --max-cycles={args.max_cycles}; stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
