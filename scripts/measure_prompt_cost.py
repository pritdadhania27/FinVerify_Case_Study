"""Where does a call's token budget actually go? (RX-023)

RX-022 found the campaign needs 98 days on Groq's free tier, not the 0.30 the
request counter implied, and left one lever unquantified: what a call actually
costs, and which part of it is tunable.

Costs **nothing**. Prompts are assembled exactly as the channels assemble them -
same retrieval, same `evidence_from_results`, same templates - and measured
without being sent. The completion side is taken from `LLM_MAX_TOKENS`, because
a live 429 reported `Requested 4143` against `max_tokens=4096` and a short
prompt, which says the provider reserves the completion budget rather than
charging the reply.

That last point is what makes this worth measuring: if the reserve dominates,
`max_tokens` is a near-linear lever on campaign length and the prompt is not.
If the prompt dominates, `DEFAULT_MAX_BLOCKS` and `DEFAULT_MAX_CHARS_PER_BLOCK`
are the levers instead. The two lead to opposite decisions.

    .\\.venv\\Scripts\\python.exe scripts\\measure_prompt_cost.py --limit 40
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.agents.evidence import evidence_from_results, format_evidence  # noqa: E402
from backend.agents.natural_channel import NATURAL_CHANNEL_PROMPT  # noqa: E402
from backend.agents.program_channel import PROGRAM_CHANNEL_PROMPT  # noqa: E402
from backend.agents.question_understanding import parse_question  # noqa: E402
from backend.rag.embedding import Embedder  # noqa: E402
from backend.rag.indexing import QdrantIndex  # noqa: E402
from backend.retrieval.hybrid import HybridRetriever  # noqa: E402
from backend.retrieval.planned import retrieve_for_spec  # noqa: E402
from backend.services.llm.settings import llm_settings  # noqa: E402
from scripts.measure_question_understanding import sample  # noqa: E402

REPORTS = PROJECT_ROOT / "evaluation/reports"

# The same 4-characters-per-token rule the provider adapter paces with. Crude,
# and deliberately the same crudeness: a figure that disagreed with the one the
# limiter uses would describe a system nobody is running.
CHARS_PER_TOKEN = 4


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    args = parser.parse_args()

    settings = llm_settings()
    embedder = Embedder(args.model)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    retriever = HybridRetriever(index=index, embedder=embedder)

    questions = sample(args.limit, args.seed)
    print(f"  {len(questions)} questions · max_tokens={settings.max_tokens} · no API calls\n")

    rows = []
    for q in questions:
        spec = parse_question(q["question"], company=q["company"])
        planned = retrieve_for_spec(
            retriever, spec, top_k=args.top_k, document_id=q.get("document_id")
        )
        blocks = evidence_from_results(planned.results)
        evidence = format_evidence(blocks)
        rows.append(
            {
                "qid": q["qid"],
                "blocks": len(blocks),
                "evidence_tokens": tokens(evidence),
                "natural_prompt_tokens": tokens(
                    NATURAL_CHANNEL_PROMPT.format(evidence=evidence, question=q["question"])
                ),
                "program_prompt_tokens": tokens(
                    PROGRAM_CHANNEL_PROMPT.format(evidence=evidence, question=q["question"])
                ),
            }
        )

    def summarise(key: str) -> dict:
        values = sorted(r[key] for r in rows)
        return {
            "median": statistics.median(values),
            "p90": values[int(len(values) * 0.9) - 1],
            "max": values[-1],
        }

    evidence_stats = summarise("evidence_tokens")
    natural_stats = summarise("natural_prompt_tokens")
    reserve = settings.max_tokens

    print(f"  {'':<22}{'median':>9}{'p90':>9}{'max':>9}")
    print("  " + "-" * 49)
    for label, key in (("evidence blocks", "evidence_tokens"),
                       ("Channel A prompt", "natural_prompt_tokens"),
                       ("Channel B prompt", "program_prompt_tokens")):
        st = summarise(key)
        print(f"  {label:<22}{st['median']:>9,}{st['p90']:>9,}{st['max']:>9,}")
    print(f"  {'completion RESERVED':<22}{reserve:>9,}{reserve:>9,}{reserve:>9,}")
    print("  " + "-" * 49)
    per_call = natural_stats["median"] + reserve
    print(f"  {'per call (median)':<22}{per_call:>9,}")
    print()

    share = reserve / per_call
    print(f"  the reserved completion budget is {share:.0%} of a call.")
    if share > 0.6:
        print("  -> `max_tokens` is the dominant lever, and it is near-linear:")
        for candidate in (4096, 2048, 1024, 512):
            cost = natural_stats["median"] + candidate
            days = (4_350 * cost) / 200_000
            print(f"       max_tokens={candidate:<5} -> {cost:,} tokens/call, "
                  f"full ablation ~{days:.0f} days")
        print()
        print("  Trimming evidence blocks would move the smaller half of the bill,")
        print("  and RX-017 showed the gloss those blocks carry is worth 0.250")
        print("  evidence accuracy - so cutting context is not the cheap lever it")
        print("  looks like.")
    else:
        print("  -> the PROMPT dominates: DEFAULT_MAX_BLOCKS and")
        print("     DEFAULT_MAX_CHARS_PER_BLOCK are the levers, not max_tokens.")

    print()
    print("  Estimated at 4 characters per token - the same crude rule the")
    print("  provider adapter paces with, deliberately, so this figure describes")
    print("  the system that is actually running. A real tokeniser would refine")
    print("  it and would not change which lever dominates.")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "prompt_cost.json"
    out.write_text(
        json.dumps(
            {
                "n": len(rows),
                "max_tokens": reserve,
                "evidence_tokens": evidence_stats,
                "natural_prompt_tokens": natural_stats,
                "program_prompt_tokens": summarise("program_prompt_tokens"),
                "reserve_share_of_call": round(share, 3),
                "chars_per_token": CHARS_PER_TOKEN,
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
