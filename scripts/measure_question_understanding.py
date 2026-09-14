"""Exercise Module 7's LLM refinement path against a live provider.

The path is written, unit-tested against a stub, and has **never run against a
real model**. That is a latent campaign-breaker: question understanding is
upstream of both channels, so a systematic failure here makes every downstream
number a measurement of a mis-parse. It is also the one common-mode path D19
names - a mis-parse makes both channels wrong *identically*, and their agreement
then proves nothing.

What this measures, per question:

    source        llm | deterministic  - did the refinement survive, or fall back?
    metrics       do the two parses name the same metrics?
    fiscal_year   do they agree on the year? (D36 made this load-bearing)
    latency

**Disagreement is not error.** Neither parse is gold here: the LLM may recover a
metric the lexicon lacks, and the deterministic parser may be right where the
model drifts. The output is an agreement rate and the disagreeing cases printed
in full, so they can be read. Calling either side correct without reading them
would be exactly the unverified claim this project forbids.

Costs one request per question. Results are written to `experiments/runs/`
like any other measurement.

    .\\.venv\\Scripts\\python.exe scripts\\measure_question_understanding.py --limit 30
    .\\.venv\\Scripts\\python.exe scripts\\measure_question_understanding.py --limit 5 --verbose
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.agents.question_understanding import parse_question, understand_question  # noqa: E402
from backend.services.llm.registry import build_provider, resolve_channel  # noqa: E402

DATASET = PROJECT_ROOT / "datasets/finverify_ind/finverify_ind_v1.json"
RUNS = PROJECT_ROOT / "experiments/runs"


def sample(limit: int, seed: int) -> list[dict]:
    """A company-balanced sample, for the same reason the worksheet is (D35).

    Drawn in company order rather than at random so a small `--limit` still
    touches every filing: a 10-question sample that happened to be 10 HDFC Bank
    questions would measure one bank's phrasing.
    """
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = payload["questions"] if isinstance(payload, dict) else payload

    by_company: dict[str, list[dict]] = {}
    for q in questions:
        by_company.setdefault(q["company"], []).append(q)
    rng = random.Random(seed)
    for bucket in by_company.values():
        rng.shuffle(bucket)

    order = sorted(by_company)
    out: list[dict] = []
    index = 0
    while len(out) < limit and any(index < len(by_company[c]) for c in order):
        for company in order:
            if index < len(by_company[company]) and len(out) < limit:
                out.append(by_company[company][index])
        index += 1
    return out


def metrics_of(spec) -> set[str]:
    return {sq.metric for sq in spec.sub_questions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--verbose", action="store_true", help="print every question")
    parser.add_argument("--tag", default="qu-llm")
    args = parser.parse_args()

    binding = resolve_channel("question_understanding")
    provider = build_provider(binding.provider)
    print(f"  binding: {binding.provider}/{binding.model}")

    questions = sample(args.limit, args.seed)
    print(f"  sample:  {len(questions)} questions across "
          f"{len({q['company'] for q in questions})} companies\n")

    records = []
    for i, q in enumerate(questions, start=1):
        baseline = parse_question(q["question"], company=q["company"])
        started = time.monotonic()
        try:
            refined = understand_question(
                q["question"], company=q["company"], provider=provider, model=binding.model
            )
            error = ""
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            # understand_question is documented never to raise. If it does, that
            # is the finding, so it is captured rather than allowed to abort.
            refined, error = baseline, f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started

        base_metrics, llm_metrics = metrics_of(baseline), metrics_of(refined)
        record = {
            "qid": q["qid"],
            "company": q["company"],
            "question": q["question"],
            "source": refined.source,
            "latency_seconds": round(elapsed, 3),
            "deterministic_metrics": sorted(base_metrics),
            "resolved_metrics": sorted(llm_metrics),
            "metrics_agree": base_metrics == llm_metrics,
            "deterministic_year": baseline.fiscal_year,
            "resolved_year": refined.fiscal_year,
            "year_agree": baseline.fiscal_year == refined.fiscal_year,
            "operation": refined.operation,
            "ambiguities": list(refined.ambiguities),
            "error": error,
        }
        records.append(record)

        mark = "=" if record["metrics_agree"] and record["year_agree"] else "!"
        print(f"  [{i:>3}/{len(questions)}] {mark} {record['source']:<13} "
              f"{elapsed:>5.2f}s  {q['qid']}")
        if args.verbose or mark == "!":
            print(f"          {q['question'][:96]}")
            print(f"          deterministic: {record['deterministic_metrics']} "
                  f"/ {record['deterministic_year']}")
            print(f"          resolved:      {record['resolved_metrics']} "
                  f"/ {record['resolved_year']}")
            if record["error"]:
                print(f"          ERROR: {record['error']}")

    n = len(records)
    llm = sum(1 for r in records if r["source"] == "llm")
    agree_metrics = sum(1 for r in records if r["metrics_agree"])
    agree_year = sum(1 for r in records if r["year_agree"])
    errors = sum(1 for r in records if r["error"])
    latencies = sorted(r["latency_seconds"] for r in records)

    print()
    print(f"  {'refinement used':<26} {llm}/{n}  ({llm / n:.1%})")
    print(f"  {'fell back to rules':<26} {n - llm}/{n}")
    print(f"  {'metrics agree':<26} {agree_metrics}/{n}  ({agree_metrics / n:.1%})")
    print(f"  {'fiscal year agrees':<26} {agree_year}/{n}  ({agree_year / n:.1%})")
    print(f"  {'raised (must be 0)':<26} {errors}/{n}")
    print(f"  {'latency median / max':<26} {latencies[n // 2]:.2f}s / {latencies[-1]:.2f}s")
    print()
    print("  Disagreement is not error: neither parse is gold. Read the '!' rows.")

    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = RUNS / f"{args.tag}_{stamp}"
    out.mkdir()
    (out / "config.json").write_text(
        json.dumps(
            {
                "script": "scripts/measure_question_understanding.py",
                "provider": binding.provider,
                "model": binding.model,
                "limit": args.limit,
                "seed": args.seed,
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
                "refinement_used": llm,
                "metrics_agreement": agree_metrics / n,
                "year_agreement": agree_year / n,
                "raised": errors,
                "latency_median_seconds": latencies[n // 2],
                "latency_max_seconds": latencies[-1],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
