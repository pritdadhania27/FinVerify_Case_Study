"""How often does each channel fail to produce a *readable* answer? (RX-019)

Not how often it is right — that needs gold and a human. This measures the layer
below correctness: given real evidence, does the channel emit something the
pipeline can read at all?

The distinction is load-bearing for H1. If Channel A returns prose instead of
JSON on a fifth of questions, its answer rate collapses and the disagreement
metric is measuring an output-format problem while reporting it as a reasoning
one. `EVALUATION.md` keeps `parse_failure_rate` and `abstention_rate` apart for
exactly this reason, and until now neither had ever been measured.

Both channels are classified into the same four buckets:

    answered            a value the pipeline could parse
    abstained           declined, or called the evidence insufficient - HONEST
    parse_failure       replied, but not in a form that could be read
    unavailable         the provider or the sandbox failed

Abstention is a correct behaviour and is counted separately from failure
throughout. A channel that refuses when the evidence is thin is working.

**Uses candidate questions, never gold answers.** Whether a reply is *readable*
does not depend on whether it is *right*, so this needs no validated data and
touches none.

Costs two requests per question, one per channel, on two different vendors.
Channel B additionally needs Docker: a program that cannot be sandboxed is
recorded as blocked, never run on the host.

    .\\.venv\\Scripts\\python.exe scripts\\measure_channel_reliability.py --limit 20
    .\\.venv\\Scripts\\python.exe scripts\\measure_channel_reliability.py --limit 5 --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.agents.evidence import evidence_from_results  # noqa: E402
from backend.agents.natural_channel import run_natural_channel  # noqa: E402
from backend.agents.program_channel import run_program_channel  # noqa: E402
from backend.agents.question_understanding import parse_question  # noqa: E402
from backend.rag.embedding import Embedder  # noqa: E402
from backend.rag.indexing import QdrantIndex  # noqa: E402
from backend.rag.manifest import IndexConfigurationError, preflight  # noqa: E402
from backend.retrieval.hybrid import HybridRetriever  # noqa: E402
from backend.retrieval.planned import retrieve_for_spec  # noqa: E402
from backend.services.llm.registry import build_provider, resolve_channel  # noqa: E402
from backend.services.llm.settings import llm_settings  # noqa: E402
from scripts.measure_question_understanding import sample  # noqa: E402

RUNS = PROJECT_ROOT / "experiments/runs"
BUCKETS = ("answered", "abstained", "parse_failure", "unavailable")


# The channel records the exception CLASS in metadata precisely so a caller can
# tell a day-ending quota exhaustion apart from a model that declined. This
# classifier originally matched on message TEXT and got it wrong: a Groq
# QuotaExhaustedError reads "groq: quota exhausted: {...}", which contains
# neither "provider" nor "unavailable", so 19 of 20 exhausted calls were filed
# as `parse_failure` - the exact misattribution this script exists to prevent,
# committed by the script itself.
_PROVIDER_FAILURES = {
    "QuotaExhaustedError",
    "DailyQuotaExhausted",
    "RateLimitError",
    "ProviderUnavailableError",
    "AuthError",
    "ModelNotFoundError",
    "TimeoutError",
    "ReadTimeout",
    "ConnectError",
}


def _provider_failed(result) -> str | None:
    """The exception class, when the channel failed before it ever replied."""
    error_type = (getattr(result, "metadata", None) or {}).get("error_type")
    return error_type if error_type in _PROVIDER_FAILURES else None


def classify_natural(result) -> tuple[str, str]:
    """-> (bucket, reason). The reason strings come from `parse_natural_response`."""
    answer = result.answer
    if answer.available:
        return "answered", ""
    reason = answer.failure_reason or "unknown"
    failure = _provider_failed(result)
    if failure:
        return "unavailable", reason
    if "declined" in reason or "insufficient" in reason:
        return "abstained", reason
    # "no JSON object in the reply", "empty reply", "could not be parsed as a
    # financial value" - all three are the channel replying unreadably.
    return "parse_failure", reason


def classify_program(result) -> tuple[str, str]:
    answer = result.answer
    if answer.available:
        return "answered", ""
    reason = answer.failure_reason or "unknown"
    lowered = reason.lower()
    if _provider_failed(result):
        return "unavailable", reason
    if result.validation_violations:
        return "parse_failure", f"AST rejected: {'; '.join(result.validation_violations)}"
    if "no program" in lowered or "could not be parsed" in lowered:
        return "parse_failure", reason
    if "declined" in lowered or "insufficient" in lowered:
        return "abstained", reason
    return "unavailable", reason


def compare_budgets(questions, retriever, provider, model, *, budgets, top_k) -> int:
    """Ask each question at both token budgets, back to back.

    Paired on the question AND adjacent in time, because the confound is
    provider drift: two runs of this script with identical seeds and
    temperature 0 produced 35% and 45% Channel A parse failures on the same 20
    questions. An unpaired before/after cannot tell a budget effect from that.
    """
    low, high = budgets
    print(f"  paired on Channel A: max_tokens {low} vs {high}\n")
    rows = []
    for i, q in enumerate(questions, start=1):
        spec = parse_question(q["question"], company=q["company"])
        blocks = evidence_from_results(
            retrieve_for_spec(
                retriever, spec, top_k=top_k, document_id=q.get("document_id")
            ).results
        )
        outcome = {}
        for budget in (low, high):
            result = run_natural_channel(
                q["question"], blocks, provider=provider, model=model,
                max_tokens=budget,
            )
            outcome[budget] = classify_natural(result)[0]
        rows.append(
            {"qid": q["qid"], "blocks": len(blocks),
             **{str(b): outcome[b] for b in budgets}}
        )
        mark = "  " if outcome[low] == outcome[high] else "->"
        print(f"  [{i:>3}/{len(questions)}] {q['qid']}  {outcome[low]:<14} {mark} {outcome[high]}")

    n = len(rows)
    print()
    print(f"  {'':<16}{low:>10}{high:>10}")
    for bucket in BUCKETS:
        a = sum(1 for r in rows if r[str(low)] == bucket)
        b = sum(1 for r in rows if r[str(high)] == bucket)
        print(f"  {bucket:<16}{a:>10}{b:>10}")
    def failed(row, budget: int) -> bool:
        return row[str(budget)] == "parse_failure"

    fixed = sum(1 for r in rows if failed(r, low) and not failed(r, high))
    broke = sum(1 for r in rows if not failed(r, low) and failed(r, high))
    print()
    print(f"  parse_failure at {low} but not at {high}: {fixed}/{n}")
    print(f"  parse_failure at {high} but not at {low}: {broke}/{n}")
    print()
    print("  McNemar's discordant pairs are the whole evidence here; the")
    print("  concordant ones carry none, which is why the totals above are the")
    print("  less informative half of this table.")

    out = RUNS / f"budget_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    out.mkdir(parents=True)
    (out / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    (out / "metrics.json").write_text(
        json.dumps({"n": n, "low": low, "high": high,
                    "fixed_by_raising": fixed, "broken_by_raising": broke}, indent=2),
        encoding="utf-8",
    )
    print(f"  written to {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--compare-max-tokens",
        nargs=2,
        type=int,
        metavar=("LOW", "HIGH"),
        help=(
            "paired A/B on Channel A's token budget: each question is asked "
            "twice, back to back, at both budgets. Paired because two runs of "
            "this script with identical seeds differed by 10 points on the same "
            "20 questions at temperature 0 - an unpaired before/after cannot "
            "separate the budget from that drift."
        ),
    )
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    args = parser.parse_args()

    natural = resolve_channel("natural_channel")
    program = resolve_channel("program_channel")
    if natural.provider == program.provider:
        print(f"  WARNING: both channels on {natural.provider}. D1 wants different "
              f"vendors; this run measures a same-vendor configuration.")
    print(f"  Channel A: {natural.provider}/{natural.model}")
    print(f"  Channel B: {program.provider}/{program.model}")

    natural_provider = build_provider(natural.provider)
    program_provider = build_provider(program.provider)

    embedder = Embedder(args.model)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    try:
        preflight(index, embedding_model=args.model)
    except IndexConfigurationError as exc:
        print(f"retrieval preflight FAILED\n{exc}", file=sys.stderr)
        return 1
    retriever = HybridRetriever(index=index, embedder=embedder)

    questions = sample(args.limit, args.seed)
    print(f"  {len(questions)} questions across "
          f"{len({q['company'] for q in questions})} companies\n")

    if args.compare_max_tokens:
        return compare_budgets(
            questions, retriever, natural_provider, natural.model,
            budgets=tuple(args.compare_max_tokens), top_k=args.top_k,
        )

    records = []
    for i, q in enumerate(questions, start=1):
        spec = parse_question(q["question"], company=q["company"])
        planned = retrieve_for_spec(
            retriever, spec, top_k=args.top_k, document_id=q.get("document_id")
        )
        blocks = evidence_from_results(planned.results)

        started = time.monotonic()
        a = run_natural_channel(
            q["question"], blocks, provider=natural_provider, model=natural.model
        )
        a_seconds = time.monotonic() - started

        started = time.monotonic()
        b = run_program_channel(
            q["question"], blocks, provider=program_provider, model=program.model
        )
        b_seconds = time.monotonic() - started

        a_bucket, a_reason = classify_natural(a)
        b_bucket, b_reason = classify_program(b)
        record = {
            "qid": q["qid"],
            "company": q["company"],
            "evidence_blocks": len(blocks),
            "natural": {
                "bucket": a_bucket, "reason": a_reason,
                "seconds": round(a_seconds, 2),
                "stated_unit": a.stated_unit,
                "sufficient": a.sufficient,
            },
            "program": {
                "bucket": b_bucket, "reason": b_reason,
                "seconds": round(b_seconds, 2),
                "program_generated": bool(b.program.strip()),
                "executed": b.executed,
                "violations": list(b.validation_violations),
            },
        }
        records.append(record)
        print(f"  [{i:>3}/{len(questions)}] {q['qid']}  blocks={len(blocks):<3} "
              f"A={a_bucket:<14} B={b_bucket:<14} {a_seconds:>5.1f}s/{b_seconds:>5.1f}s")
        if args.verbose or a_bucket != "answered":
            if a_reason:
                print(f"          A: {a_reason[:110]}")
        if args.verbose or b_bucket != "answered":
            if b_reason:
                print(f"          B: {b_reason[:110]}")

    n = len(records)
    print()
    print(f"  {'':<18}{'Channel A':>14}{'Channel B':>14}")
    print("  " + "-" * 46)
    for bucket in BUCKETS:
        a_n = sum(1 for r in records if r["natural"]["bucket"] == bucket)
        b_n = sum(1 for r in records if r["program"]["bucket"] == bucket)
        print(f"  {bucket:<18}{a_n:>7} {a_n / n:>6.1%}{b_n:>7} {b_n / n:>6.1%}")
    print("  " + "-" * 46)
    print(f"  {'n':<18}{n:>7}       {n:>7}")
    print()
    generated = sum(1 for r in records if r["program"]["program_generated"])
    executed = sum(1 for r in records if r["program"]["executed"])
    print(f"  Channel B: program generated {generated}/{n}, executed in sandbox {executed}/{n}")
    print()
    print("  `abstained` is a correct behaviour, not a failure: a channel that")
    print("  refuses when the evidence is thin is working. It is counted apart")
    print("  from parse_failure throughout, and must stay apart in the write-up.")

    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"channels_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    out.mkdir()
    (out / "config.json").write_text(
        json.dumps(
            {
                "script": "scripts/measure_channel_reliability.py",
                "natural": f"{natural.provider}/{natural.model}",
                "program": f"{program.provider}/{program.model}",
                "limit": args.limit, "seed": args.seed, "top_k": args.top_k,
                "collection": args.collection,
                # The resolved call settings, not the file they came from. Two
                # runs with identical configs differed by 10 points here and
                # nothing in the artifact said what budget either had used.
                "llm_settings": vars(llm_settings()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "n": n,
                "natural": {
                    b: sum(1 for r in records if r["natural"]["bucket"] == b) / n
                    for b in BUCKETS
                },
                "program": {
                    b: sum(1 for r in records if r["program"]["bucket"] == b) / n
                    for b in BUCKETS
                },
                "program_generated": generated / n,
                "program_executed": executed / n,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
