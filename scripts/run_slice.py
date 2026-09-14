"""Run one question end to end through the dual-channel pipeline (Milestone 2).

    question -> understanding -> planned retrieval -> evidence
             -> Channel A (natural language)   -.
             -> Channel B (program -> sandbox) -'-> consistency -> verdict + score

This is the first point at which the project's research question is exercised
rather than described: two independent channels answer the same question from the
same evidence, and their agreement is measured.

**Real API calls.** Every run consumes free-tier quota, which is the binding
constraint on the whole evaluation (D14), so the default is a single question.
The rate limiter's persisted daily counter is shared with every other run.

**Run artifacts are written per D3** to `experiments/runs/<run_id>/`, containing
the config, the per-question record, and the environment - everything spec 36
requires to reproduce the run, including the *resolved* model ids rather than the
requested ones, so a silent checkpoint substitution is visible afterwards.

Usage:
    python scripts/run_slice.py --question "How much were trade payables?"
    python scripts/run_slice.py --gold-set --limit 3
    python scripts/run_slice.py --dry-run          # no API calls; prints the plan
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The repository's .env, not one relative to wherever the process started:
# run from elsewhere and every API key silently goes missing.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from dotenv import load_dotenv

from backend.core.paths import project_path  # noqa: E402
from backend.agents.evidence import evidence_from_results, format_evidence
from backend.services.llm.base import usage_record
from backend.agents.natural_channel import run_natural_channel
from backend.agents.program_channel import run_program_channel
from backend.agents.question_understanding import parse_question
from backend.rag.embedding import Embedder
from backend.rag.indexing import QdrantIndex
from backend.rag.manifest import IndexConfigurationError, preflight
from backend.retrieval.hybrid import HybridRetriever
from backend.retrieval.planned import retrieve_for_spec
from backend.services.llm.registry import build_provider, channels_are_independent, resolve_channel
from backend.agents.verification_agent import (
    run_verification_agent,
    should_verify,
)
from backend.verification.consistency import compare
from backend.verification.deterministic_channel import run_deterministic_channel

GOLD = project_path("datasets/retrieval_eval/infosys_fy24_v1.json")
RUNS = project_path("experiments/runs")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - provenance is best-effort, never a gate
        return "unknown"


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": _git_commit(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def main() -> int:
    # Bindings and keys live in .env (gitignored), and so do QDRANT_COLLECTION
    # and EMBEDDING_MODEL. This must run BEFORE the parser: argparse evaluates
    # `default=os.environ.get(...)` at add_argument() time, so loading .env
    # afterwards means the file is read but its retrieval settings are ignored.
    load_dotenv(_ENV_FILE)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", action="append", help="ask this (repeatable)")
    parser.add_argument("--gold-set", action="store_true", help="use the retrieval gold questions")
    parser.add_argument("--limit", type=int, default=1, help="how many gold questions")
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"))
    parser.add_argument("--top-k", type=int, default=8)
    # This script was written when the corpus was one document, and it filtered
    # retrieval to that document's id unconditionally. With five filings
    # indexed, that silently answered a question about any other company from
    # Infosys's pages - the filter did its job, on the wrong document.
    parser.add_argument(
        "--document-id",
        help="restrict retrieval to this document; 'any' searches the whole corpus "
             "(default: the gold set's document)",
    )
    parser.add_argument("--company", help="company for question understanding")
    parser.add_argument("--dry-run", action="store_true", help="no API calls")
    parser.add_argument("--run-id", help="override the generated run id")
    args = parser.parse_args()

    meta = json.loads(GOLD.read_text(encoding="utf-8"))
    if args.gold_set:
        questions = [q["question"] for q in meta["questions"][: args.limit]]
    elif args.question:
        questions = args.question
    else:
        questions = [meta["questions"][0]["question"]]

    independent, note = channels_are_independent()
    print(f"channel independence: {'OK' if independent else 'VIOLATED'} - {note}\n")
    if not independent:
        print("Refusing to run: the two channels are bound to the same model, so "
              "their agreement would measure sampling noise rather than "
              "independent corroboration (D1). Set different bindings, or run "
              "this deliberately as the same-model ABLATION arm.", file=sys.stderr)
        return 2

    natural_binding = resolve_channel("natural_channel")
    program_binding = resolve_channel("program_channel")
    verifier_binding = resolve_channel("verification_agent")
    print(f"Channel A: {natural_binding.label}")
    print(f"Channel B: {program_binding.label}")
    print(f"Arbiter:   {verifier_binding.label}"
          f" (Module 13, only on DISAGREE)\n")

    embedder = Embedder(args.model, device=os.environ.get("EMBEDDING_DEVICE", "cpu"))
    index = QdrantIndex(collection=args.collection, dimension=embedder.dimension)
    try:
        manifest = preflight(index, embedding_model=args.model)
    except IndexConfigurationError as exc:
        print(f"\nretrieval preflight FAILED\n{exc}", file=sys.stderr)
        return 1
    requested = args.document_id or meta["document_id"]
    document_filter = None if requested == "any" else requested
    print(f"Index:     {manifest.collection} - {manifest.point_count} chunks "
          f"({manifest.embedding_model})")
    print(f"Scope:     {'whole corpus' if document_filter is None else document_filter}\n")
    retriever = HybridRetriever(index=index, embedder=embedder)

    natural_provider = None if args.dry_run else build_provider(natural_binding.provider)
    program_provider = None if args.dry_run else build_provider(program_binding.provider)
    verifier_provider = None if args.dry_run else build_provider(verifier_binding.provider)

    run_id = args.run_id or f"slice_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    records: list[dict] = []

    for question in questions:
        print("=" * 78)
        print(question)
        started = time.monotonic()

        spec = parse_question(question, company=args.company or meta.get("company"))
        planned = retrieve_for_spec(
            retriever, spec, top_k=args.top_k, document_id=document_filter
        )
        blocks = evidence_from_results(planned.results)
        print(f"  understanding: op={spec.operation} unit={spec.expected_unit} "
              f"sub-questions={[s.search_text for s in spec.sub_questions]}")
        if spec.ambiguities:
            for a in spec.ambiguities:
                print(f"  ambiguity: {a}")
        print(f"  retrieved {len(blocks)} evidence blocks; coverage={planned.coverage}")

        if args.dry_run:
            print("\n--- evidence that WOULD be sent ---")
            print(format_evidence(blocks)[:1500])
            records.append({"question": question, "spec": spec.as_dict(),
                            "evidence_refs": [b.ref for b in blocks], "dry_run": True})
            continue

        natural = run_natural_channel(
            question, blocks, provider=natural_provider, model=natural_binding.model
        )
        program = run_program_channel(
            question, blocks, provider=program_provider, model=program_binding.model
        )

        # The third opinion, and the only one that can overrule two agreeing
        # channels - the both-agree-wrong cell EVALUATION.md 5.4 calls the
        # method's blind spot. It sees the question and the evidence, never
        # the channels: a verifier that can see the answers it is checking is
        # not a verifier (D1).
        determ = run_deterministic_channel(spec, blocks)

        report = compare(natural.answer, program.answer, determ.answer)
        elapsed = time.monotonic() - started

        def describe(answer) -> str:
            if not answer.available:
                return f"UNAVAILABLE ({answer.failure_reason})"
            return (
                f"{answer.value.raw_text or answer.value.amount} "
                f"(canonical {answer.value.canonical()})"
            )

        print(f"\n  Channel A: {describe(natural.answer)}")
        if natural.reasoning:
            print(f"    reasoning: {natural.reasoning[:200]}")
        print(f"  Channel B: {describe(program.answer)}")
        if program.validation_violations:
            print(f"    rejected: {program.validation_violations[:2]}")
        if program.execution is not None:
            print(f"    sandbox: {program.execution.status.name} "
                  f"in {program.execution.duration_seconds:.1f}s")

        if determ.answer.available:
            print(f"  Deterministic: {determ.answer.value.raw_text} "
                  f"(canonical {determ.answer.canonical})")
            for step in determ.steps[:3]:
                print(f"    {step}")
        else:
            print(f"  Deterministic: n/a "
                  f"({(determ.answer.failure_reason or '')[:110]})")

        print(f"\n  VERDICT: {report.verdict.name}  score={report.score:.3f}  "
              f"band={report.band()}  ({elapsed:.1f}s)")

        # Module 13. Triggered ONLY on DISAGREE - an arbiter cannot
        # adjudicate between an answer and an absence, so UNCERTAIN is left
        # standing. Its result is recorded separately and never folded into
        # channel accuracy (EVALUATION.md 5.4).
        verification = None
        if should_verify(report):
            verification = run_verification_agent(
                question, blocks, natural.answer, program.answer,
                provider=verifier_provider, model=verifier_binding.model,
            )
            if verification.available:
                shown = (
                    verification.value.raw_text or verification.value.amount
                    if verification.value is not None
                    else 'no figure'
                )
                print(f"  ARBITER: {verification.resolution.value} -> {shown}")
                if verification.reasoning:
                    print(f"    {verification.reasoning[:180]}")
            else:
                print(f"  ARBITER: unavailable "
                      f"({(verification.failure_reason or '')[:100]})")
        for pair in report.pairs:
            if pair.disagreement.name != "NONE":
                print(f"    {pair.left} vs {pair.right}: {pair.disagreement.name} {pair.note}")

        records.append(
            {
                "question": question,
                "spec": spec.as_dict(),
                "evidence": [
                    {"ref": b.ref, "citation": b.citation, "page": b.page} for b in blocks
                ],
                "retrieval_coverage": planned.coverage,
                "natural_channel": {
                    "available": natural.answer.available,
                    "failure_reason": natural.answer.failure_reason,
                    "value": (
                        str(natural.answer.value.amount) if natural.answer.value else None
                    ),
                    "canonical": (
                        str(natural.answer.canonical)
                        if natural.answer.canonical is not None
                        else None
                    ),
                    "unit": natural.stated_unit,
                    "reasoning": natural.reasoning,
                    "evidence_used": list(natural.evidence_used),
                    "figures_used": list(natural.figures_used),
                    "metadata": natural.metadata,
                    "usage": usage_record(getattr(natural, "usage", None)),
                    # What the channel actually said, kept ONLY when it could not
                    # be parsed. Without it a parse failure cannot be diagnosed
                    # after the run, and "no JSON object in the reply" hides
                    # whether the model abstained in prose or produced nonsense -
                    # two different events for the abstention-rate metric.
                    "raw_text": (
                        natural.raw_text[:2000] if not natural.answer.available else None
                    ),
                },
                "program_channel": {
                    "available": program.answer.available,
                    "failure_reason": program.answer.failure_reason,
                    "value": (
                        str(program.answer.value.amount) if program.answer.value else None
                    ),
                    "canonical": (
                        str(program.answer.canonical)
                        if program.answer.canonical is not None
                        else None
                    ),
                    "unit": program.stated_unit,
                    "program": program.program,
                    "validation_violations": list(program.validation_violations),
                    "execution_status": (
                        program.execution.status.name if program.execution else None
                    ),
                    "steps": list(program.steps),
                    "metadata": program.metadata,
                    "usage": usage_record(getattr(program, "usage", None)),
                    "raw_text": (
                        program.raw_text[:2000] if not program.answer.available else None
                    ),
                },
                "deterministic_channel": {
                    "available": determ.answer.available,
                    "failure_reason": determ.answer.failure_reason,
                    "operation": determ.operation,
                    "value": (
                        str(determ.answer.value.amount) if determ.answer.value else None
                    ),
                    "canonical": (
                        str(determ.answer.canonical)
                        if determ.answer.canonical is not None
                        else None
                    ),
                    "steps": list(determ.steps),
                    "operands": (determ.metadata or {}).get("operands", []),
                    "unbound": list(determ.binding.unbound) if determ.binding else [],
                },
                "verification_agent": (
                    {
                        "triggered": True,
                        "available": verification.available,
                        "failure_reason": verification.failure_reason,
                        "resolution": verification.resolution.value,
                        "resolved": verification.resolved,
                        "value": (
                            str(verification.value.amount)
                            if verification.value is not None
                            else None
                        ),
                        "reasoning": verification.reasoning,
                        "evidence_used": list(verification.evidence_used),
                        "metadata": verification.metadata,
                    "usage": usage_record(getattr(verification, "usage", None)),
                    }
                    if verification is not None
                    else {"triggered": False}
                ),
                "consistency": {
                    "verdict": report.verdict.name,
                    "score": report.score,
                    "band": report.band(),
                    "available_channels": report.available_channels,
                    "pairs": [
                        {"left": p.left, "right": p.right, "score": p.score,
                         "type": p.disagreement.name, "note": p.note}
                        for p in report.pairs
                    ],
                    "notes": list(report.notes),
                },
                "latency_seconds": round(elapsed, 3),
            }
        )

    out = RUNS / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "natural_channel": natural_binding.label,
                "program_channel": program_binding.label,
                "embedding_model": args.model,
                "collection": args.collection,
                "top_k": args.top_k,
                "temperature": 0.0,
                "dry_run": args.dry_run,
                "document_id": meta["document_id"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "env.json").write_text(json.dumps(_environment(), indent=2), encoding="utf-8")
    with (out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("=" * 78)
    print(f"run artifact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
