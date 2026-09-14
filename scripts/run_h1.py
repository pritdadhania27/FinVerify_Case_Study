"""Run the H1 campaign at the largest scope the remaining allowance affords.

H1 is the cheapest thing that produces a real finding: arm A (natural channel
alone) against arm B5 (self-consistency), on validated questions. Everything
else in the ablation is a larger version of this.

The reason this script exists rather than a command in a document: the scope is
a function of how much allowance is left before the deadline, and that number
changes every hour. Picking `--limit` by hand means either under-running the
budget or discovering at 80% that it does not fit. Here the limit is computed
from the budget, and the budget is read from the same limiter the campaign
obeys, so the two cannot disagree.

    # what would run, spending nothing
    .\\.venv\\Scripts\\python.exe scripts\\run_h1.py --dry-run

    # settle max_tokens first (this costs allowance - see --help)
    .\\.venv\\Scripts\\python.exe scripts\\measure_channel_reliability.py \\
        --limit 6 --compare-max-tokens 1024 4096

    # then run, and analyse when it finishes
    .\\.venv\\Scripts\\python.exe scripts\\run_h1.py --max-tokens 1024

Nothing here decides anything the campaign runner would not: it stops on quota,
records what it spent, and can be resumed. This only chooses how much to attempt
and chains the analysis onto the end.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import require_project_interpreter, use_utf8  # noqa: E402

use_utf8()
require_project_interpreter()

from backend.core.paths import project_path  # noqa: E402

STATE = project_path("experiments/.rate_limit_state.json")
DATASET = project_path("datasets/finverify_ind/finverify_ind_v1.json")

# Groq's cap, kept because it is the figure RX-023 planned against and the one
# the historical scope tables were computed from. It is NO LONGER assumed to be
# the binding provider: D44 moved Channel A to NVIDIA, and the binding provider
# is now whatever `natural_channel` resolves to. Read it through
# `provider_tokens_per_day()` rather than reaching for this directly.
GROQ_TOKENS_PER_DAY = 200_000

# Measured, not assumed (RX-023): the prompt floor is ~1,780 tokens and the
# reserved completion budget is charged whether or not it is used.
PROMPT_TOKENS = 1_780

# Calls per question depend on WHICH arms run - arm H puts both channels on the
# natural provider, so it costs that provider more than a split-provider arm
# does. Rather than keep a second table of that here, the campaign's own
# estimator is asked. Two sources of truth for the budget is exactly how a plan
# and a run come to disagree.
GROQ = "groq"


def natural_provider() -> str:
    """The provider actually serving Channel A, the arbiter and B5's samples.

    Hard-coded to Groq until D44. Three calls in four go to whichever provider
    this is, which is what makes it the one worth planning against - the
    identity of that provider is configuration, not a constant.
    """
    from backend.services.llm.registry import resolve_channel

    return resolve_channel("natural_channel").provider


def provider_tokens_per_day(provider: str) -> int | None:
    """That provider's daily TOKEN cap, or None when it has never been observed.

    None is a real answer and not a zero: NVIDIA publishes no daily token cap
    and has never refused on one, so planning it as 0 would report that nothing
    fits, and planning it as a guess would repeat the Gemini error (registry
    said 1,500/day, provider enforced 20).
    """
    from backend.services.llm.registry import PROVIDER_SPECS

    spec = PROVIDER_SPECS.get(provider)
    return spec.rate_limit.tokens_per_day if spec else None


def tokens_available(now: datetime, provider: str | None = None) -> tuple[int | None, str]:
    """Tokens the binding provider is expected to allow today.

    Read from the limiter's own persisted state so this cannot disagree with
    what the campaign will actually be permitted to spend. The model is a
    calendar day; Groq's real window is rolling (RX-024), which makes this
    PESSIMISTIC during a day and optimistic at the boundary. Pessimistic is the
    right direction for a plan.

    Returns `(None, note)` when the provider has no observed token cap, so the
    caller reports the constraint it actually has instead of inventing one.
    """
    provider = provider or natural_provider()
    per_day = provider_tokens_per_day(provider)
    if per_day is None:
        return None, f"{provider} publishes no daily token cap and has never refused on one"

    spent_today = 0
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        record = state.get(provider, {})
        if record.get("day") == now.date().isoformat():
            spent_today = int(record.get("tokens", 0))
    remaining_today = max(0, per_day - spent_today)
    return remaining_today, (
        f"{spent_today:,} of {per_day:,} spent today"
        if spent_today
        else "nothing spent today"
    )


def groq_tokens_available(now: datetime) -> tuple[int, str]:
    """Retained for callers that specifically mean Groq. Prefer `tokens_available`."""
    remaining, note = tokens_available(now, GROQ)
    return (remaining if remaining is not None else 0), note


def whole_days_before(deadline: datetime, now: datetime) -> int:
    """Full UTC days that begin after `now` and end at or before the deadline.

    Today's remainder is counted separately, from the limiter's own state, so
    only untouched days belong here. Deliberately whole days: the allowance
    resets at the boundary, and a plan built on a fraction of one would assume
    a refill that has not happened.
    """
    next_boundary = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if next_boundary >= deadline:
        return 0
    return (deadline - next_boundary).days


def tokens_per_question(
    arm_names: list[str], max_tokens: int, provider: str | None = None
) -> int:
    """Tokens one question costs on `provider`, zero if it serves none of it.

    Zero is returned honestly. This used to be `max(1, ...)`, which was a guard
    against dividing by zero and became a lie the moment D44 moved Channel A off
    Groq: every Groq figure went to zero, the floor turned it into 1, and the
    planner reported an affordable scope of two hundred thousand questions.
    """
    from evaluation.arms import ALL_ARMS
    from experiments.campaign import estimate_requests

    provider = provider or natural_provider()
    arms = [ALL_ARMS[name] for name in arm_names]
    # Costed over 10 questions and divided, because per-question call counts are
    # not all integers - B5 draws samples, the arbiter fires only on
    # disagreement - and rounding a single question up overstates the cost by
    # enough to lose a question from the scope.
    probe = 10
    tokens = estimate_requests(arms, probe).tokens_by_provider(max_tokens).get(provider, 0)
    return tokens // probe


def groq_tokens_per_question(arm_names: list[str], max_tokens: int) -> int:
    """Retained for callers that specifically mean Groq."""
    return tokens_per_question(arm_names, max_tokens, GROQ)


def affordable_questions(budget_tokens: int, per_question: int) -> int | None:
    """How many questions the budget pays for, or None when it does not bind.

    None where `per_question` is zero: the provider serves none of this scope,
    so its token allowance places no limit at all. That is not the same as
    "unlimited questions", and returning a number here would say it was.
    """
    if per_question <= 0:
        return None
    return budget_tokens // per_question


def campaign_runs() -> set[str]:
    """Names of the campaign run directories that exist right now."""
    root = project_path("experiments/runs")
    if not root.exists():
        return set()
    return {d.name for d in root.iterdir() if d.is_dir() and d.name.startswith("campaign_")}


def validated_count() -> int:
    if not DATASET.exists():
        return 0
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = payload["questions"] if isinstance(payload, dict) else payload
    return sum(
        1
        for q in questions
        if q.get("split") == "validation"
        and q.get("validation", {}).get("status") == "validated"
        and not q.get("ambiguous")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # 4096, not 1024. RX-032 measured the two head to head on Channel A: at
    # 1024 three of six questions came back as parse failures that 4096
    # answered, and none failed the other way. A budget that truncates the
    # reply spends the whole allowance measuring output length.
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument(
        "--deadline",
        default="2026-09-09",
        help="UTC date the result must exist by (D24)",
    )
    parser.add_argument("--arms", nargs="+", default=["A", "B5", "G", "H"])
    parser.add_argument("--limit", type=int, help="override the computed scope")
    parser.add_argument("--dry-run", action="store_true", help="plan only, spend nothing")
    parser.add_argument("--no-analyse", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC)
    deadline = datetime.fromisoformat(args.deadline).replace(tzinfo=UTC)
    if deadline <= now:
        print(f"  the deadline {args.deadline} has passed")
        return 1

    provider = natural_provider()
    per_day = provider_tokens_per_day(provider)
    remaining_today, spent_note = tokens_available(now, provider)
    full_days = whole_days_before(deadline, now)
    budget = (
        None if remaining_today is None or per_day is None
        else remaining_today + full_days * per_day
    )

    usable = validated_count()
    per_question = tokens_per_question(args.arms, args.max_tokens, provider)
    affordable = None if budget is None else affordable_questions(budget, per_question)
    # Every validated question is attempted, NOT `min(usable, affordable)`.
    # The campaign is question-major and resumable: quota exhaustion stops it
    # cleanly, having written a balanced prefix where every finished question
    # has every arm, and `--resume` re-spends nothing. So truncating to what
    # today's estimate says is affordable can only lose questions - if calls
    # come in under the reserved budget the run simply gets further, and if
    # they do not it stops exactly where a pre-truncated run would have.
    # `affordable` is now a forecast printed below, not a limit imposed here.
    scope = args.limit or usable

    print(f"  now {now.isoformat(timespec='seconds')}   deadline {args.deadline}")
    print(f"  Channel A is served by {provider}")
    if budget is None:
        # Not a failure and not "unlimited": the token allowance simply does not
        # bind here, so saying how many questions it affords would be inventing
        # a constraint. The request cap is what to watch, and the campaign
        # runner prints it.
        print(f"  {provider}: {spent_note}")
        print("  daily TOKEN allowance does not bind this scope; the constraint is")
        print("  requests. `run_campaign.py --budget-only` reports the request count.")
    else:
        print(f"  {provider}: {spent_note}; {remaining_today:,} left today "
              f"+ {full_days} whole day(s) before the deadline")
        print(f"  budget to plan against: {budget:,} tokens")
    print(f"  arms {' '.join(args.arms)} at max_tokens={args.max_tokens}: "
          f"{PROMPT_TOKENS + args.max_tokens:,} per call, "
          f"{per_question:,} {provider} tokens per question")
    print(f"  validated and usable: {usable}    "
          f"affordable: {'not bounded by tokens' if affordable is None else affordable}")
    print(f"  SCOPE: {scope} question(s) x {len(args.arms)} arm(s)\n")

    if scope <= 0:
        print("  nothing fits. Either the allowance is spent (it rolls at 00:00 UTC)")
        print("  or no questions are validated yet.")
        return 1
    if affordable is not None and affordable < scope:
        print(f"  FORECAST: the allowance is expected to reach about {affordable} of "
              f"{scope} question(s)")
        print("  before it stops. That is a forecast, not a limit: the run is")
        print("  question-major, so it stops on a whole question with every arm done,")
        print("  and `--resume <run_id>` continues it without re-spending. The order")
        print("  is the dataset's own, shuffled under a fixed seed, so the prefix that")
        print("  completes is not chosen by how any question behaves.")
        print("  The per-call cost above RESERVES the completion budget; calls that")
        print("  come in shorter get further than this forecast, never less far.\n")

    campaign = [
        sys.executable, "scripts/run_campaign.py",
        "--arms", *args.arms,
        "--split", "validation",
        "--limit", str(scope),
    ]
    if args.dry_run:
        campaign.append("--budget-only")
    print("  $ " + " ".join(campaign) + "\n")

    env_note = f"LLM_MAX_TOKENS={args.max_tokens}"
    import os

    env = dict(os.environ, LLM_MAX_TOKENS=str(args.max_tokens))
    print(f"  ({env_note})\n")
    # Snapshot BEFORE launching, so the run this invocation created can be
    # identified by being new rather than by sorting. See below.
    before = campaign_runs()
    # The child writes straight to this terminal, so anything still buffered
    # here would appear after it and read as if the plan followed the run.
    sys.stdout.flush()
    result = subprocess.run(campaign, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print("\n  the campaign did not finish cleanly; not analysing a partial run")
        return result.returncode
    if args.dry_run or args.no_analyse:
        return 0

    # The run this invocation created, identified by being NEW rather than by
    # sorting. `sorted(glob("*/metrics.json"))[-1]` is alphabetical, so it
    # analysed `qu-llm_20260829T181529Z` - a stale run from two days earlier
    # that happens to sort after `campaign_` - and printed its "NOT TESTABLE"
    # hypotheses as though they came from the campaign that had just finished
    # (RX-033). Analysing the wrong run is worse than analysing none: the
    # output is shaped exactly like the right answer.
    after = campaign_runs()
    fresh = after - before
    if not fresh:
        print("\n  the campaign wrote no new run directory; nothing to analyse")
        return 1
    if len(fresh) > 1:
        print(f"\n  {len(fresh)} new run directories appeared; not guessing which is ours")
        return 1
    run_id = fresh.pop()
    print(f"\n  analysing {run_id}\n")
    return subprocess.run(
        [sys.executable, "scripts/analyse_campaign.py", "--run", run_id],
        cwd=PROJECT_ROOT,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
