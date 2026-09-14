"""Measure retrieval against gold evidence spans (spec 14, EVALUATION.md §4).

Module 6 left retrieval *running* but not *established*: the rankings looked
weak on a couple of hand-typed queries. This script replaces that impression
with Recall@K, Precision@K, MRR and evidence-retrieval accuracy, so the choices
that follow - embedding model (decision D2), whether reranking is needed, whether
BM25 earns its place - are made against numbers.

Three modes, and the first one is not optional:

    --validate-gold   every anchor must literally appear on the PDF page it
                      claims. A gold set nobody checked is a gold set that can
                      be quietly wrong, and a wrong gold set makes every metric
                      below it meaningless. Run this before trusting any score.

    (default)         score the retriever. `--arms` additionally scores the
                      semantic and keyword legs on their own, which is what
                      makes "hybrid helps" a finding rather than an assumption.

    --diagnose        for every evidence group still missing at the largest K,
                      say WHY: is the evidence in the index but ranked too low
                      (a retrieval failure), or absent from the index entirely
                      (an extraction or chunking failure)? Those two need
                      opposite fixes and are indistinguishable from the score.

Usage:
    python scripts/evaluate_retrieval.py --validate-gold
    python scripts/evaluate_retrieval.py --arms --planned --diagnose
    python scripts/evaluate_retrieval.py --collection finverify_e5 \\
        --model intfloat/e5-base-v2 --arm-label e5-base
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, UTC
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from dotenv import load_dotenv  # noqa: E402

from evaluation.metrics.retrieval import (  # noqa: E402
    GoldQuestion,
    RetrievedItem,
    aggregate,
    normalise,
    score_question,
)

DEFAULT_GOLD = PROJECT_ROOT / "datasets/retrieval_eval/infosys_fy24_v1.json"
REPORT_DIR = PROJECT_ROOT / "evaluation/reports"
K_VALUES = (1, 3, 5, 10)


def load_gold(path: Path) -> tuple[dict, list[GoldQuestion]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = [GoldQuestion.from_dict(q) for q in payload["questions"]]
    seen = [q.qid for q in questions]
    if len(set(seen)) != len(seen):
        raise ValueError("duplicate qid in gold set")
    return payload, questions


def validate_gold(meta: dict, questions: list[GoldQuestion]) -> int:
    """Check every anchor against the source PDF. Returns the failure count."""
    import pymupdf

    pdf_path = Path(meta["source_document"])
    if not pdf_path.exists():
        print(f"FAIL  source document missing: {pdf_path}")
        return 1

    doc = pymupdf.open(pdf_path)
    pages = {i + 1: normalise(doc[i].get_text()) for i in range(doc.page_count)}
    doc.close()

    checked = failures = 0
    for question in questions:
        for group in question.evidence:
            for span in group.any_of:
                checked += 1
                text = pages.get(span.page)
                if text is None:
                    print(f"FAIL  {question.qid}/{group.group_id}: page {span.page} "
                          f"does not exist (document has {len(pages)} pages)")
                    failures += 1
                    continue
                missing = [a for a in span.anchors if normalise(a) not in text]
                if missing:
                    print(f"FAIL  {question.qid}/{group.group_id} p.{span.page}: "
                          f"anchors not on that page: {missing}")
                    failures += 1

    print(f"\n{checked - failures}/{checked} gold spans verified against {pdf_path.name}")

    # Completeness, which is a different failure from correctness and a quieter
    # one. The first version of this set listed only the location picked by hand,
    # so a question about revenue accepted page 13 and rejected the three other
    # pages that state the same figure - the metric was measuring "did retrieval
    # find MY page", and every miss looked like a retrieval defect.
    incomplete = 0
    for question in questions:
        for group in question.evidence:
            listed = {s.page for s in group.any_of}
            elsewhere = {
                page
                for span in group.any_of
                for page, text in pages.items()
                if page not in listed and all(normalise(a) in text for a in span.anchors)
            }
            if elsewhere:
                incomplete += 1
                print(f"INCOMPLETE  {question.qid}/{group.group_id}: lists "
                      f"{sorted(listed)} but the same labelled figure is also on "
                      f"{sorted(elsewhere)}")
    if incomplete:
        print(f"\n{incomplete} group(s) are missing legitimate evidence locations. "
              f"Retrieval will be scored as missing evidence it actually found.")
    else:
        print("every group lists every page that states its figure")

    if failures:
        print(f"{failures} span(s) do not match the source. The gold set is wrong, "
              f"not the retriever - fix the spans before measuring anything.")
    return failures


def to_items(results) -> list[RetrievedItem]:
    return [
        RetrievedItem(
            chunk_id=r.chunk_id, page=int(r.payload.get("page", -1)), text=r.text
        )
        for r in results
    ]


def diagnose(
    index, meta: dict, questions: list[GoldQuestion], arm_results: dict,
    *, arm: str = "hybrid", document_filter: str | None = None,
) -> list[dict]:
    """Separate 'ranked too low' from 'never made it into the index'.

    Scans the whole indexed document once. A group that no chunk anywhere can
    satisfy is an extraction or chunking defect wearing a retrieval defect's
    clothes, and tuning the retriever would never fix it.
    """
    # Scans the same candidate set the retriever drew from. Under --corpus-wide
    # that is every chunk in the index, so "absent from the index" means absent
    # from the whole corpus rather than from one document.
    payloads = index.scroll_all(
        query_filter=index.build_filter(document_id=document_filter)
    )
    corpus = [(int(p.get("page", -1)), p.get("text", ""), p.get("chunk_id", "")) for p in payloads]

    findings: list[dict] = []
    largest = max(K_VALUES)
    for question in questions:
        retrieved = to_items(arm_results[arm][question.qid])[:largest]
        for group in question.evidence:
            if any(group.satisfied_by(i.page, i.text) for i in retrieved):
                continue
            holders = [cid for page, text, cid in corpus if group.satisfied_by(page, text)]
            findings.append(
                {
                    "qid": question.qid,
                    "group_id": group.group_id,
                    "verdict": "ranked_too_low" if holders else "absent_from_index",
                    "chunks_holding_evidence": holders[:5],
                    "pages_allowed": [s.page for s in group.any_of],
                }
            )
    return findings


def main() -> int:
    # BEFORE the parser: argparse fixes `default=os.environ.get(...)` at
    # add_argument() time, so .env has to be loaded first or it is ignored.
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--validate-gold", action="store_true", help="check anchors, then exit")
    parser.add_argument(
        "--arms", action="store_true", help="also score the semantic and keyword legs alone"
    )
    parser.add_argument("--diagnose", action="store_true", help="explain every miss")
    parser.add_argument(
        "--no-company",
        action="store_true",
        help="withhold the company from question understanding - measures what "
             "retrieval does when the issuer is not known, which is what the "
             "campaign did until RX-012",
    )
    parser.add_argument(
        "--corpus-wide",
        action="store_true",
        help="search the whole index instead of filtering to the gold set's document - "
             "these are the conditions the campaign actually runs under",
    )
    parser.add_argument(
        "--planned",
        action="store_true",
        help="score the Module 7 planned-retrieval path (decomposition + statement hints)",
    )
    # Both defaults previously named the pre-D2a configuration - BGE against
    # `finverify_chunks`. That pairing is self-consistent, so measurements taken
    # with it were valid; but it silently measured the superseded index, and
    # combined with .env's model it produced a cross-model pairing that returns
    # ranked, confident, wrong results. The preflight below now refuses that.
    parser.add_argument(
        "--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2")
    )
    parser.add_argument(
        "--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5")
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--device", default=os.environ.get("EMBEDDING_DEVICE", "cpu"))
    parser.add_argument("--arm-label", help="name for this configuration in the report")
    parser.add_argument(
        "--sweep-weights",
        action="store_true",
        help="sweep the RRF semantic weight on validation and report every point",
    )
    parser.add_argument("--semantic-weight", type=float, default=1.0)
    parser.add_argument("--keyword-weight", type=float, default=1.0)
    parser.add_argument("--no-report", action="store_true", help="print only, write nothing")
    args = parser.parse_args()

    meta, questions = load_gold(args.gold)
    print(f"gold set: {meta['set_id']} ({len(questions)} questions, split={meta['split']})")

    # Every retrieval measurement on this project until 2026-08-27 filtered to
    # the gold set's single document. `run_campaign.py` passes document_id=None,
    # so the campaign searches the WHOLE corpus - 22,930 chunks across five
    # filings rather than one document's 906. Those are different tasks: the
    # filtered one asks "can it find the right page in this report", the
    # unfiltered one also asks "can it pick the right report at all".
    # A number measured under the first and quoted about the second overstates.
    document_filter = None if args.corpus_wide else meta["document_id"]
    print(f"scope: {'whole corpus' if document_filter is None else document_filter}")

    if args.validate_gold:
        return 1 if validate_gold(meta, questions) else 0

    if meta["split"] == "test" and os.environ.get("FINVERIFY_ALLOW_TEST") != "1":
        print("refusing to evaluate on the test split without FINVERIFY_ALLOW_TEST=1",
              file=sys.stderr)
        return 2

    from backend.rag.embedding import Embedder
    from backend.rag.indexing import QdrantIndex
    from backend.retrieval.hybrid import HybridRetriever

    from backend.rag.manifest import IndexConfigurationError, preflight

    embedder = Embedder(args.model, device=args.device)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    try:
        manifest = preflight(index, embedding_model=args.model)
    except IndexConfigurationError as exc:
        print(f"\nretrieval preflight FAILED\n{exc}", file=sys.stderr)
        return 1
    indexed = manifest.point_count
    print(f"index: {manifest.collection} - {indexed} chunks, "
          f"{len(manifest.companies)} companies, built with {manifest.embedding_model}")
    retriever = HybridRetriever(
        index=index,
        embedder=embedder,
        semantic_weight=args.semantic_weight,
        keyword_weight=args.keyword_weight,
    )

    label = args.arm_label or f"{args.model.split('/')[-1]}+bm25+rrf"
    print(f"arm: {label}\ncollection: {args.collection} ({indexed} chunks)\n")

    largest = max(K_VALUES)

    if args.sweep_weights:
        # Each question's two legs are computed ONCE and re-fused at every
        # weight, so the whole sweep is scored on identical candidate sets and a
        # difference between rows is the weight and nothing else.
        query_filter = index.build_filter(document_id=document_filter)
        filter_key = repr(("sweep", document_filter))
        candidates = max(largest * retriever.candidate_multiplier, largest)
        legs = {
            q.qid: retriever._legs(q.question, query_filter, filter_key, candidates)
            for q in questions
        }

        print("RRF semantic weight sweep, keyword weight fixed at 1.0")
        print("(w_sem = 0 is BM25 alone; w_sem = 1 is plain RRF)\n")
        print(f"{'w_sem':>6} {'R@5':>7} {'R@10':>7} {'MRR':>7} {'AllEvid@10':>11}")

        sweep = []
        for weight in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
            rows = []
            for question in questions:
                semantic, keyword = legs[question.qid]
                fused = HybridRetriever._fuse(
                    semantic, keyword, semantic_weight=weight, keyword_weight=1.0
                )[:largest]
                rows.append((question, to_items(fused)))
            at5 = aggregate([score_question(q, r, 5) for q, r in rows])
            at10 = aggregate([score_question(q, r, largest) for q, r in rows])
            sweep.append({"semantic_weight": weight, "at_5": at5, "at_10": at10})
            print(f"{weight:>6.2f} {at5['recall_at_k']:>7.3f} {at10['recall_at_k']:>7.3f} "
                  f"{at10['mrr']:>7.3f} {at10['evidence_retrieval_accuracy']:>11.3f}")

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"retrieval_weight_sweep_{args.collection}.json"
        out.write_text(
            json.dumps(
                {
                    "collection": args.collection,
                    "model": args.model,
                    "scope": "corpus" if document_filter is None else document_filter,
        "company_known": not args.no_company,
                    "split": meta["split"],
                    "note": "Tuned on VALIDATION. Any weight chosen here is a "
                            "reported hyperparameter, not a free improvement.",
                    "sweep": sweep,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nsweep report: {out}")
        return 0

    arms: tuple[str, ...] = ("semantic", "keyword", "hybrid") if args.arms else ("hybrid",)
    if args.planned:
        # The configuration `scripts/run_slice.py` actually uses, and the one
        # PROJECT_STATUS reports. It was measured once from a scratch script and
        # was therefore NOT reproducible from the repository - a number that only
        # a deleted script can regenerate is a claim from memory, which is the
        # thing EXPERIMENTS.md exists to prevent.
        arms = (*arms, "planned")
    arm_results: dict[str, dict[str, list]] = {a: {} for a in arms}

    for question in questions:
        if args.arms:
            got = retriever.retrieve_arms(
                question.question, top_k=largest, document_id=document_filter
            )
            for arm in ("semantic", "keyword", "hybrid"):
                arm_results[arm][question.qid] = got[arm]
        else:
            arm_results["hybrid"][question.qid] = retriever.retrieve(
                question.question, top_k=largest, document_id=document_filter
            )

        if args.planned:
            from backend.agents.question_understanding import parse_question
            from backend.retrieval.planned import retrieve_for_spec

            spec = parse_question(
                question.question,
                company=None if args.no_company else meta.get("company"),
            )
            arm_results["planned"][question.qid] = retrieve_for_spec(
                retriever, spec, top_k=largest, document_id=document_filter
            ).results

    report = {
        "set_id": meta["set_id"],
        "split": meta["split"],
        "arm": label,
        "embedding_model": args.model,
        "collection": args.collection,
        "chunks_indexed": indexed,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "k_values": list(K_VALUES),
        "arms": {},
        "per_question": {},
    }

    header = f"{'arm':<10} {'K':>3} {'Recall@K':>9} {'Prec@K':>8} {'MRR':>7} {'AllEvid':>8}"
    print(header)
    print("-" * len(header))
    for arm in arms:
        report["arms"][arm] = {}
        for k in K_VALUES:
            scores = [
                score_question(q, to_items(arm_results[arm][q.qid]), k) for q in questions
            ]
            summary = aggregate(scores)
            report["arms"][arm][str(k)] = summary
            print(f"{arm:<10} {k:>3} {summary['recall_at_k']:>9.3f} "
                  f"{summary['precision_at_k']:>8.3f} {summary['mrr']:>7.3f} "
                  f"{summary['evidence_retrieval_accuracy']:>8.3f}")
            if k == largest:
                report["per_question"][arm] = [
                    {
                        "qid": s.qid,
                        "question_type": q.question_type,
                        "recall": s.recall,
                        "first_relevant_rank": s.first_relevant_rank,
                        "all_evidence_retrieved": s.all_evidence_retrieved,
                        "missed_groups": list(s.missed_groups),
                    }
                    for s, q in zip(scores, questions, strict=True)
                ]
        print()

    headline = "planned" if args.planned else "hybrid"
    failed = [r for r in report["per_question"][headline] if not r["all_evidence_retrieved"]]
    if failed:
        print(f"{len(failed)}/{len(questions)} questions miss evidence at K={largest} "
              f"({headline} arm):")
        for row in failed:
            print(f"  {row['qid']:<5} [{row['question_type']}] missing {row['missed_groups']}")
    else:
        print(f"all {len(questions)} questions have complete evidence at K={largest}")

    if args.diagnose:
        findings = diagnose(
            index, meta, questions, arm_results,
            arm=headline, document_filter=document_filter,
        )
        report["diagnosis"] = findings
        print("\ndiagnosis of misses:")
        if not findings:
            print("  nothing missing to diagnose")
        for f in findings:
            print(f"  {f['qid']:<5} {f['group_id']:<32} {f['verdict']}"
                  + (f" (in {len(f['chunks_holding_evidence'])} chunk(s))"
                     if f["chunks_holding_evidence"] else ""))

    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = report["evaluated_at"].replace(":", "").replace("-", "")
        out = REPORT_DIR / f"retrieval_{label.replace('/', '_')}_{stamp}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
