"""Does FinVerify-IND's question *phrasing* cost retrieval? (RX-017)

RX-015 found retrieval does not transfer off Infosys — 0.909 there, 0.375–0.611
on the other four filings — and listed three untested causes: statement hints,
chunk granularity, RRF weighting. It ruled out vocabulary coverage.

There is a fourth it did not consider, and it is not about the documents at all.
The Infosys gold set was hand-written in short natural form:

    "How much were trade payables as at March 31, 2024?"

The four generated sets carry FinVerify-IND's template:

    "For HDFC Bank Limited, as reported for the year ended March 31, 2023, what
     were advances (total advances as reported on the consolidated balance
     sheet)?"

`strip_question_boilerplate` removes dates and stopwords, so the second reduces
to `HDFC Bank Limited, reported advances total advances reported consolidated
balance sheet` — the issuer's name and a restatement of the metric, searched as
though they were content. The two sets differ in **document** and in **question
form**, and RX-015 could not separate those.

Two suspects, measured separately rather than fixed together:

    no_company   the issuer's name is already a metadata filter; repeating it in
                 the query text searches for a phrase on every page of its own
                 annual report
    no_gloss     the parenthesised definition restates the metric and names a
                 statement, which RX-005 measured as net-zero when appended
                 deliberately

`company=` is left untouched in every arm, so the metadata filter is constant
and only the query text varies. No API cost: embeddings are local.

    .\\.venv\\Scripts\\python.exe scripts\\measure_query_formulation.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts._console import use_utf8  # noqa: E402

use_utf8()

import os  # noqa: E402

from backend.agents.question_understanding import parse_question  # noqa: E402
from backend.rag.embedding import Embedder  # noqa: E402
from backend.rag.indexing import QdrantIndex  # noqa: E402
from backend.rag.manifest import IndexConfigurationError, preflight  # noqa: E402
from backend.retrieval.hybrid import HybridRetriever  # noqa: E402
from backend.retrieval.planned import retrieve_for_spec  # noqa: E402
from evaluation.metrics.retrieval import (  # noqa: E402
    GoldQuestion,
    RetrievedItem,
    aggregate,
    score_question,
)
from evaluation.metrics.statistics import paired_bootstrap_difference  # noqa: E402

GOLD_DIR = PROJECT_ROOT / "datasets/retrieval_eval"
GENERATED = ("40f73920", "cca3bdde", "d8e3739d", "db02424e")
TOP_K = 10

_GLOSS = re.compile(r"\s*\([^()]*\)\s*(?=\?*$)")


def without_company(question: str, company: str) -> str:
    """Drop the issuer's name from the query text, leaving the filter alone."""
    out = question
    for name in (company, company.replace(" Limited", "").strip()):
        if name:
            out = re.sub(re.escape(name), " ", out, flags=re.I)
    return re.sub(r"\s{2,}", " ", out).replace(" ,", ",").strip()


def without_gloss(question: str, _company: str) -> str:
    """Drop the trailing parenthesised definition."""
    return _GLOSS.sub("", question).strip()


ARMS = {
    "asis": lambda q, c: q,
    "no_company": without_company,
    "no_gloss": without_gloss,
    "both": lambda q, c: without_gloss(without_company(q, c), c),
}


def load(path: Path) -> tuple[dict, list[GoldQuestion]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, [GoldQuestion.from_dict(q) for q in payload["questions"]]


def to_items(results) -> list[RetrievedItem]:
    return [
        RetrievedItem(chunk_id=r.chunk_id, page=int(r.payload.get("page", -1)), text=r.text)
        for r in results
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--show-examples", type=int, default=2)
    args = parser.parse_args()

    embedder = Embedder(args.model)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    try:
        manifest = preflight(index, embedding_model=args.model)
    except IndexConfigurationError as exc:
        print(f"retrieval preflight FAILED\n{exc}", file=sys.stderr)
        return 1
    print(f"  index: {manifest.collection} - {manifest.point_count} chunks, "
          f"built with {manifest.embedding_model}\n")
    retriever = HybridRetriever(index=index, embedder=embedder)

    sets = [(g, *load(GOLD_DIR / f"{g}_retrieval_recent.json")) for g in GENERATED]

    if args.show_examples:
        example = sets[0][2][0].question
        company = sets[0][1].get("company", "")
        print("  what each arm sends to the retriever:\n")
        for arm, transform in ARMS.items():
            print(f"    {arm:<12} {transform(example, company)[:88]}")
        print()

    table: dict[str, dict[str, float]] = {}
    # qid -> {arm: 1.0 if every evidence group was retrieved else 0.0}
    per_question: dict[str, dict[str, float]] = {}

    for arm, transform in ARMS.items():
        table[arm] = {}
        for set_id, meta, questions in sets:
            scored = []
            for gold in questions:
                spec = parse_question(
                    transform(gold.question, meta.get("company", "")),
                    company=meta.get("company"),
                )
                results = retrieve_for_spec(retriever, spec, top_k=TOP_K).results
                one = score_question(gold, to_items(results), k=TOP_K)
                scored.append(one)
                per_question.setdefault(gold.qid, {})[arm] = float(one.all_evidence_retrieved)
            summary = aggregate(scored)
            accuracy = summary["evidence_retrieval_accuracy"]
            table[arm][set_id] = accuracy
            print(f"  {arm:<12} {meta.get('company','')[:26]:<26} {accuracy:.3f}", flush=True)
        print()

    names = {sid: meta.get("company", sid) for sid, meta, _ in sets}
    counts = {sid: len(qs) for sid, _, qs in sets}
    total = sum(counts.values())

    print("  " + "=" * 78)
    header = "  " + "company".ljust(30) + "".join(a.rjust(12) for a in ARMS)
    print(header)
    print("  " + "-" * 78)
    for sid in GENERATED:
        row = "  " + f"{names[sid][:28]} (n={counts[sid]})".ljust(30)
        row += "".join(f"{table[a][sid]:>12.3f}" for a in ARMS)
        print(row)
    print("  " + "-" * 78)
    weighted = {
        a: sum(table[a][sid] * counts[sid] for sid in GENERATED) / total for a in ARMS
    }
    print("  " + f"weighted mean (n={total})".ljust(30)
          + "".join(f"{weighted[a]:>12.3f}" for a in ARMS))
    print("  " + "=" * 78)
    print()
    best = max(weighted, key=weighted.get)
    delta = weighted[best] - weighted["asis"]
    print(f"  best arm: {best}  ({weighted[best]:.3f}, {delta:+.3f} vs as-is)")
    print()

    # Paired over questions, because every arm answers the SAME 63 questions and
    # an unpaired comparison would throw away that pairing - which is most of the
    # power available at this sample size.
    qids = sorted(per_question)
    stats = {}
    print("  paired bootstrap vs as-is (10,000 resamples, questions resampled):")
    for arm in ARMS:
        if arm == "asis":
            continue
        interval, p_value = paired_bootstrap_difference(
            qids,
            lambda items, a=arm: sum(per_question[q][a] for q in items) / len(items),
            lambda items: sum(per_question[q]["asis"] for q in items) / len(items),
        )
        stats[arm] = {
            "difference": interval.point,
            "ci_low": interval.low,
            "ci_high": interval.high,
            "p_value": p_value,
        }
        crosses = (
            interval.low is not None
            and interval.high is not None
            and interval.low <= 0 <= interval.high
        )
        verdict = "indistinguishable from as-is" if crosses else "differs from as-is"
        print(f"    {arm:<12} {interval.point:+.3f}  "
              f"95% CI [{interval.low:+.3f}, {interval.high:+.3f}]  "
              f"p={p_value:.3f}   {verdict}")
    print()
    print("  Infosys, for reference, is 0.909 on a HAND-WRITTEN short-form gold")
    print("  set (RX-015). These four differ from it in BOTH document and question")
    print("  form; this run separates the second from the first, and nothing here")
    print("  says anything about the first.")

    out = PROJECT_ROOT / "evaluation/reports/query_formulation_arms.json"
    out.write_text(
        json.dumps(
            {
                "arms": {a: {"per_set": table[a], "weighted_mean": weighted[a]} for a in ARMS},
                "paired_bootstrap_vs_asis": stats,
                "question_counts": counts,
                "top_k": TOP_K,
                "collection": args.collection,
                "embedding_model": args.model,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
