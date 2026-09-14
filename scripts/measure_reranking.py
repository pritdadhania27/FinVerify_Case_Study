"""Does cross-encoder reranking actually lift evidence into the top 10?

RX-034 established the headroom: over 100 evidence groups the gold chunk is in
the top 10 for 0.280 of them, within rank 20 for 0.440, and within rank 70 for
0.720. That says a reranker *could* help. It does not say one does, and the
difference between those two sentences is this script.

    .\\.venv\\Scripts\\python.exe scripts\\measure_reranking.py ^
        --gold datasets\\retrieval_eval\\db02424e_retrieval_v1.json

**Paired by construction.** The candidate pool is retrieved once per question
and both arms are scored against that same pool - baseline is its first `k`,
reranked is the cross-encoder's first `k` over the identical list. So the only
thing that differs between the two numbers is the ordering function. Retrieving
twice would let a different pool depth or a cache state leak into the
comparison and turn a pool effect into a reranker result.

Reports the paired movement, not just the two totals. Two arms that both score
0.400 can be the same 40 groups or two disjoint sets of 40, and only one of
those is "the reranker did nothing". `promoted` and `demoted` are the honest
summary; a reranker that lifts eight groups and drops eight is a wash whose net
zero hides real churn.

Costs no API quota. The cross-encoder is a 22M-parameter model on CPU.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.retrieval.query import strip_question_boilerplate  # noqa: E402
from evaluation.metrics.retrieval import GoldQuestion  # noqa: E402
from scripts.diagnose_retrieval_ranks import rank_of  # noqa: E402


def recall_at(ranks: list[int | None], k: int) -> float:
    hit = sum(1 for rank in ranks if rank is not None and rank <= k)
    return hit / len(ranks) if ranks else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, action="append", required=True,
                        help="gold set (repeatable - totals pool across all of them)")
    parser.add_argument("--pool", type=int, default=100,
                        help="candidates the reranker reorders (default 100)")
    parser.add_argument("--k", type=int, default=10, help="cutoff scored (default 10)")
    parser.add_argument("--model", default="intfloat/e5-base-v2")
    parser.add_argument("--collection", default="finverify_e5")
    parser.add_argument("--rerank-model", default=None,
                        help="cross-encoder id (default: the module default)")
    parser.add_argument("--corpus-wide", action="store_true",
                        help="search the whole index rather than the gold set's document")
    parser.add_argument("--strip-question", action="store_true",
                        help="give the reranker the boilerplate-stripped query the "
                             "legs use, rather than the full question")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the per-group table as JSON, e.g. "
                             "evaluation/reports/rerank_tata_v1.json - NOT under "
                             "experiments/runs/, which D3 reserves for run "
                             "directories rather than loose report files")
    args = parser.parse_args()

    from backend.rag.embedding import Embedder
    from backend.rag.indexing import QdrantIndex
    from backend.retrieval.hybrid import HybridRetriever
    from backend.retrieval.rerank import DEFAULT_RERANK_MODEL, Reranker

    rerank_model = args.rerank_model or DEFAULT_RERANK_MODEL
    embedder = Embedder(args.model)
    index = QdrantIndex(collection=args.collection, dimension=embedder.dimension)
    retriever = HybridRetriever(index=index, embedder=embedder)
    reranker = Reranker(model_name=rerank_model)

    print(f"baseline : hybrid fusion, top {args.k} of a {args.pool}-candidate pool")
    print(f"reranked : {rerank_model} over that same pool, top {args.k}")
    print(f"gold     : {', '.join(g.name for g in args.gold)}\n")

    rows: list[dict] = []
    base_ranks: list[int | None] = []
    rerank_ranks: list[int | None] = []
    seconds = 0.0

    for gold_path in args.gold:
        payload = json.loads(gold_path.read_text(encoding="utf-8"))
        questions = [GoldQuestion.from_dict(q) for q in payload["questions"]]
        document_id = None if args.corpus_wide else payload["document_id"]

        for question in questions:
            pool = retriever.retrieve(
                question.question, top_k=args.pool, document_id=document_id
            )
            # These questions are ~80% scaffolding - "For Tata Motors Limited,
            # as reported for the year ended March 31, 2024, what were X (X as
            # reported in the consolidated financial statements)?" - and every
            # word of that appears in every chunk of the document. Whether the
            # cross-encoder wants the natural question or the stripped keywords
            # is an empirical question, so it is a flag rather than a decision.
            query = (
                strip_question_boilerplate(question.question)
                if args.strip_question
                else question.question
            )
            started = time.perf_counter()
            reordered = reranker.rerank(query, pool)
            seconds += time.perf_counter() - started

            for group in question.evidence:
                before = rank_of(pool, group)
                after = rank_of(reordered, group)
                base_ranks.append(before)
                rerank_ranks.append(after)
                rows.append({
                    "company": payload.get("company"),
                    "qid": question.qid,
                    "group_id": group.group_id,
                    "rank_before": before,
                    "rank_after": after,
                })

    total = len(rows)
    if not total:
        print("no evidence groups found - nothing to measure")
        return 1

    print(f"{'company':26} {'qid':8} {'before':>7} {'after':>7}  movement")
    print("-" * 72)
    for row in rows:
        before, after = row["rank_before"], row["rank_after"]
        if before is None and after is None:
            movement = "never in pool"
        elif before is None or after is None:
            movement = "pool boundary"
        elif after < before:
            movement = f"up {before - after}"
        elif after > before:
            movement = f"down {after - before}"
        else:
            movement = "-"
        print(f"{(row['company'] or '')[:26]:26} {row['qid']:8} "
              f"{str(before or '-'):>7} {str(after or '-'):>7}  {movement}")

    # Paired movement across the cutoff - the number that decides adoption.
    entered = sum(
        1 for r in rows
        if (r["rank_after"] is not None and r["rank_after"] <= args.k)
        and not (r["rank_before"] is not None and r["rank_before"] <= args.k)
    )
    left = sum(
        1 for r in rows
        if (r["rank_before"] is not None and r["rank_before"] <= args.k)
        and not (r["rank_after"] is not None and r["rank_after"] <= args.k)
    )
    base = recall_at(base_ranks, args.k)
    after_recall = recall_at(rerank_ranks, args.k)

    print(f"\n{total} evidence group(s) over {len(args.gold)} gold set(s)\n")
    print(f"{'arm':22} {f'recall@{args.k}':>12}")
    print("-" * 36)
    print(f"{'hybrid (baseline)':22} {base:>12.3f}")
    print(f"{'hybrid + reranking':22} {after_recall:>12.3f}")
    print(f"{'difference':22} {after_recall - base:>+12.3f}")
    print(f"\ngroups lifted INTO the top {args.k}: {entered}")
    print(f"groups pushed OUT of the top {args.k}: {left}")
    print(f"net: {entered - left:+d} of {total}")
    print(f"\nreranking latency: {seconds / total:.2f}s per evidence group "
          f"({seconds:.1f}s total, {args.pool} pairs per question)")
    print(
        f"\nCeiling check: reranking reorders the {args.pool}-candidate pool and "
        f"\ncannot reach anything outside it. Groups marked 'never in pool' are a "
        f"\nrecall problem, not an ordering one, and no reranker addresses them."
    )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({
            "baseline_recall_at_k": base,
            "reranked_recall_at_k": after_recall,
            "k": args.k,
            "pool": args.pool,
            "rerank_model": rerank_model,
            "embedding_model": args.model,
            "collection": args.collection,
            "corpus_wide": args.corpus_wide,
            "entered_top_k": entered,
            "left_top_k": left,
            "groups": total,
            "seconds_per_group": seconds / total,
            "rows": rows,
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
