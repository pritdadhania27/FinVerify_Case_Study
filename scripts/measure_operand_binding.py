"""Operand-binding accuracy as its own metric (D6, RX-025).

D6 recorded an honest limitation: the deterministic verifier is exact *given*
`(operation, operands)`, and if operand binding came from an LLM then the
"deterministic" third opinion would be only as deterministic as the model that
fed it. `backend/verification/operand_binding.py` removed that limitation by
matching row labels with regex and the lexicon — no model — and D6 says the
resulting accuracy must be **measured as its own metric rather than folded into
verifier accuracy**. It never has been.

Two numbers, and they answer different questions:

    coverage      on what fraction of questions can it bind at all?
    agreement     when it binds, does it read the same figure the generator read?

**Neither is graded against gold**, because no gold exists yet and inventing it
here would be the circularity this project is built to avoid. Agreement compares
the binder against the *candidate* answer — two independent readings of the same
table, one by the dataset generator from the chunk cache, one by the binder from
the retrieved evidence. Agreement means two methods concur; it does not mean
either is right. Disagreement is printed in full rather than scored, because at
this stage it is a lead, not an error.

Costs nothing: retrieval is local and binding runs no model. That is the point —
this measures the one channel that consumes no quota, which is a large part of
why it survived the D24 scope cut.

    .\\.venv\\Scripts\\python.exe scripts\\measure_operand_binding.py --limit 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.agents.evidence import evidence_from_results  # noqa: E402
from backend.agents.question_understanding import parse_question  # noqa: E402
from backend.core.financial_value import parse_financial_value  # noqa: E402
from backend.rag.embedding import Embedder  # noqa: E402
from backend.rag.indexing import QdrantIndex  # noqa: E402
from backend.retrieval.hybrid import HybridRetriever  # noqa: E402
from backend.retrieval.planned import retrieve_for_spec  # noqa: E402
from backend.verification.operand_binding import bind_operands  # noqa: E402
from scripts.measure_question_understanding import sample  # noqa: E402

REPORTS = PROJECT_ROOT / "evaluation/reports"

# Two readings of the same figure agree within this relative distance. The same
# tolerance the consistency engine uses, so "the same number" means one thing
# across the project.
TOLERANCE = 0.005


def agrees(bound, candidate_text: str, candidate_unit: str) -> bool | None:
    """None when the candidate cannot be parsed - unknown, never 'disagrees'."""
    spoken = f"{candidate_text} {candidate_unit}".strip()
    expected = parse_financial_value(spoken)
    if expected is None:
        return None
    a, b = bound.value.canonical(), expected.canonical()
    if b == 0:
        return a == 0
    return abs((a - b) / b) <= TOLERANCE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--show", type=int, default=8, help="disagreements to print")
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    args = parser.parse_args()

    embedder = Embedder(args.model)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    retriever = HybridRetriever(index=index, embedder=embedder)

    questions = sample(args.limit, args.seed)
    print(f"  {len(questions)} questions · no model calls · deterministic\n")

    rows = []
    for q in questions:
        spec = parse_question(q["question"], company=q["company"])
        blocks = evidence_from_results(
            retrieve_for_spec(
                retriever, spec, top_k=args.top_k, document_id=q.get("document_id")
            ).results
        )
        metrics = [sq.metric for sq in spec.sub_questions]
        # The canonical metric when the lexicon recognised one: the binder
        # matches ROW LABELS, and the rule parser's fallback is the whole
        # stripped question, which no table row is ever going to read like.
        canonical = spec.metadata.get("canonical_metric")
        if canonical:
            metrics = [canonical]
        result = bind_operands(metrics, blocks)

        answer = q.get("answer") or {}
        row = {
            "qid": q["qid"],
            "company": q["company"],
            # The deterministic channel ABSTAINS on lookups - re-reading a
            # figure is structural corroboration, not an independent check - so
            # the binder's behaviour there is never used by the pipeline.
            # Pooling the two strata reports a number for a channel that does
            # not run.
            "operation": spec.operation,
            "engages_channel": spec.operation != "lookup",
            "metrics": metrics,
            "bound": len(result.operands),
            "unbound": list(result.unbound),
            "candidate": answer.get("text"),
            "candidate_unit": answer.get("unit"),
            "agreement": None,
            "bound_value": None,
            "row_label": None,
            "citation": None,
        }
        if result.operands:
            first = result.operands[0]
            row["bound_value"] = str(first.value)
            row["row_label"] = first.row_label
            row["citation"] = first.citation
            if answer.get("text"):
                row["agreement"] = agrees(first, answer["text"], answer.get("unit") or "")
        rows.append(row)

    def stratum(subset: list[dict], label: str) -> dict:
        total = len(subset)
        bound = [r for r in subset if r["bound"]]
        comparable = [r for r in bound if r["agreement"] is not None]
        agreed = [r for r in comparable if r["agreement"]]
        if not total:
            print(f"  {label:<38} (none in this sample)")
            return {"n": 0}
        coverage = len(bound) / total
        line = f"  {label:<38}{len(bound):>4}/{total} bound ({coverage:>5.1%})"
        if comparable:
            rate = len(agreed) / len(comparable)
            line += f" · {len(agreed)}/{len(comparable)} agree ({rate:.0%})"
        else:
            line += " · nothing comparable"
        print(line)
        return {
            "n": total,
            "bound": len(bound),
            "coverage": coverage,
            "comparable": len(comparable),
            "agreed": len(agreed),
            "agreement": (len(agreed) / len(comparable)) if comparable else None,
        }

    n = len(rows)
    engaged = [r for r in rows if r["engages_channel"]]
    lookups = [r for r in rows if not r["engages_channel"]]

    print("  Split by whether the deterministic channel actually runs. Pooling")
    print("  them would report an accuracy for a channel that abstains on 95% of")
    print("  this dataset, which is a number about nothing.\n")
    engaged_stats = stratum(engaged, "WHERE THE CHANNEL ENGAGES (non-lookup)")
    lookup_stats = stratum(lookups, "where it abstains (lookup)")
    print()

    comparable = [r for r in rows if r["bound"] and r["agreement"] is not None]
    disagreed = [r for r in comparable if not r["agreement"]]

    if disagreed:
        print(f"  disagreements across BOTH strata "
              f"(showing {min(args.show, len(disagreed))}):")
        for r in disagreed[: args.show]:
            print(f"    {r['qid']}  binder={r['bound_value']:<22} "
                  f"candidate={r['candidate']} {r['candidate_unit'] or ''}")
            print(f"      row {r['row_label']!r} · {r['citation']}")
        print()

    print("  Neither number is graded against gold, because none exists yet.")
    print("  Agreement means two independent readings of the same table concur -")
    print("  the generator's from the chunk cache, the binder's from retrieved")
    print("  evidence. It does not mean either is right, and a disagreement is a")
    print("  lead rather than an error.")
    print()
    print()
    print("  The lookup stratum is reported for completeness only. The channel")
    print("  abstains there by design, so its binder behaviour on those questions")
    print("  reaches no answer and must never be quoted as the channel's accuracy.")
    print()
    print("  Refusing to bind is CORRECT behaviour, not a miss: the consistency")
    print("  engine weights this channel highly enough to overrule two agreeing")
    print("  channels, so a guessed row would be worse than no opinion.")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "operand_binding.json"
    out.write_text(
        json.dumps(
            {
                "n": n,
                "where_channel_engages": engaged_stats,
                "where_channel_abstains": lookup_stats,
                "disagreements": len(disagreed),
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
