"""Where does the gold chunk actually rank when retrieval misses it?

`evaluate_retrieval.py --diagnose` answers a different and coarser question: is
the evidence anywhere in the index at all? Its two verdicts are
`ranked_too_low` and `absent_from_index`, and everything that exists in the
corpus is filed under the first regardless of whether it came 11th or 3,000th.

That distinction is the whole decision about what to fix:

    rank 11-70    a reranker's job - the right chunk is a candidate and is
                  being out-scored by near-duplicates
    rank 70-300   a recall problem - a larger candidate pool would have to
                  reach it before any reranker could reorder it
    not ranked    the query and the chunk share almost nothing; no amount of
                  reordering helps, and the gold span may itself be wrong

    .\\.venv\\Scripts\\python.exe scripts\\diagnose_retrieval_ranks.py ^
        --gold datasets\\retrieval_eval\\db02424e_retrieval_v1.json

Costs no API quota - retrieval is local. It does load the embedding model and
scan the document, so allow a few minutes per gold set.

**A note on reading `SearchHit`.** `page` lives in `SearchHit.payload`, not as
an attribute of the hit. A first version of this probe read it with
`getattr(hit, "page", None)`, got `None` for every hit, failed every span
comparison, and reported that all 23 Tata misses were unreachable - a confident,
completely wrong conclusion that contradicted RX-028. The conversion here
mirrors `evaluate_retrieval.to_items` on purpose.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from evaluation.metrics.retrieval import GoldQuestion  # noqa: E402

# The bands the fix decision turns on. Upper bound inclusive.
BANDS: tuple[tuple[str, int], ...] = (
    ("top 10 (a hit today)", 10),
    ("11-20", 20),
    ("21-70", 70),
    ("71-300", 300),
)


def rank_of(results, group) -> int | None:
    """1-based rank of the first hit satisfying `group`, or None."""
    for position, hit in enumerate(results, 1):
        if group.satisfied_by(int(hit.payload.get("page", -1)), hit.text):
            return position
    return None


def band_of(rank: int | None) -> str:
    if rank is None:
        return "not ranked"
    for label, upper in BANDS:
        if rank <= upper:
            return label
    return "not ranked"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--deep", type=int, default=300,
                        help="how far down the ranking to look (default 300)")
    parser.add_argument("--model", default="intfloat/e5-base-v2")
    parser.add_argument("--collection", default="finverify_e5")
    parser.add_argument("--corpus-wide", action="store_true",
                        help="search the whole index rather than the gold set's document")
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "write the decomposition as JSON here. Without it this script prints "
            "and forgets, which is why RX-034's figures were prose-only and "
            "outlived the gold they were measured on."
        ),
    )
    args = parser.parse_args()

    payload = json.loads(args.gold.read_text(encoding="utf-8"))
    questions = [GoldQuestion.from_dict(q) for q in payload["questions"]]
    document_id = None if args.corpus_wide else payload["document_id"]

    from backend.rag.embedding import Embedder
    from backend.rag.indexing import QdrantIndex
    from backend.retrieval.hybrid import HybridRetriever

    embedder = Embedder(args.model)
    index = QdrantIndex(collection=args.collection, dimension=embedder.dimension)
    retriever = HybridRetriever(index=index, embedder=embedder)

    print(f"{payload.get('company')} - {args.gold.name}")
    print(f"{len(questions)} questions, looking {args.deep} deep, "
          f"scope={'corpus-wide' if args.corpus_wide else document_id}\n")
    print(f"{'qid':6} {'evidence group':38} {'rank':>6}  band")
    print("-" * 78)

    tally: Counter[str] = Counter()
    # Rows are collected as they are printed, not recomputed afterwards, so the
    # written artifact cannot drift from the table a reader saw.
    rows: list[dict] = []
    for question in questions:
        deep = retriever.retrieve(
            question.question, top_k=args.deep, document_id=document_id
        )
        for group in question.evidence:
            rank = rank_of(deep, group)
            tally[band_of(rank)] += 1
            rows.append(
                {
                    "qid": question.qid,
                    "group_id": group.group_id,
                    "rank": rank,
                    "band": band_of(rank),
                }
            )
            print(f"{question.qid:6} {group.group_id[:38]:38} "
                  f"{str(rank if rank else '-'):>6}  {band_of(rank)}")

    total = sum(tally.values())
    print(f"\n{total} evidence group(s)\n")
    print(f"{'band':24} {'count':>6} {'cumulative recall':>19}")
    print("-" * 52)
    seen = 0
    for label, _upper in BANDS:
        seen += tally[label]
        print(f"{label:24} {tally[label]:>6} {seen / total:>18.3f}")
    print(f"{'not ranked':24} {tally['not ranked']:>6} {'-':>19}")
    print(
        "\nCumulative recall is what evidence accuracy would become if the "
        "\nright chunk could be lifted into the top 10 from that band - the "
        "\nceiling a reranker is working against, not a promise."
    )

    if args.out:
        cumulative = {}
        seen_groups = 0
        for label, _upper in BANDS:
            seen_groups += tally[label]
            cumulative[label] = seen_groups / total if total else None
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "set_id": payload.get("set_id"),
                    "split": payload.get("split"),
                    "split_composition": payload.get("split_composition"),
                    "company": payload.get("company"),
                    "gold_file": args.gold.name,
                    "scope": "corpus-wide" if args.corpus_wide else document_id,
                    "deep": args.deep,
                    "embedding_model": args.model,
                    "collection": args.collection,
                    "evidence_groups": total,
                    "band_counts": dict(tally),
                    "cumulative_recall": cumulative,
                    "per_group": rows,
                    "note": (
                        "Cumulative recall is the ceiling a reranker works "
                        "against, not a promise. RX-035 measured that ceiling and "
                        "the reranker scored BELOW the baseline."
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
