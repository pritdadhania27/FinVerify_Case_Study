"""Load artifacts into PostgreSQL (spec Module 18).

    python scripts/ingest_to_database.py --all
    python scripts/ingest_to_database.py --run campaign_20260826T120000Z

One direction only: files -> database. Run artifacts stay canonical (D4), so a
number in the paper traces to a committed file rather than to a service that has
to be running. This makes the awkward cross-cutting questions cheap:

    SELECT q.qid, a.arm, a.answer_text, q.gold_text
    FROM answers a JOIN questions q ON q.id = a.question_id
    WHERE a.agreed AND a.correct IS FALSE;      -- the blind spot, every arm

Re-running is safe. Every upsert is keyed on something the artifact itself
determines, so an ingest interrupted halfway is repeated rather than repaired.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The repository's .env, not one relative to the working directory.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from dotenv import load_dotenv

from backend.core.paths import project_path  # noqa: E402
from backend.database.ingest import (
    _get_or_create,
    ingest_dataset,
    ingest_facts,
    ingest_registry,
    ingest_report,
    ingest_run,
    ingest_structure,
    prune_facts,
)
from backend.database.session import build_engine, database_url, session_scope
from backend.documents.facts import facts_from_chunks
from evaluation.dataset import DATASET_ROOT, load_dataset
from evaluation.error_analysis import ChunkTextIndex, analyse
from experiments.campaign import RUNS_ROOT, CampaignRecorder

REGISTRY = project_path("documents/registry.json")
PROCESSED = project_path("documents/processed")
DATASET_PATH = DATASET_ROOT / "finverify_ind_v1.json"
REPORTS = project_path("evaluation/reports")


def _load_chunks(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("chunk_id"):
            out.append(record)
    return out


def _ingest_pooled_reports(session) -> int:
    """Reports whose analysis unit is several runs pooled, e.g. `<a>+<b>`.

    An ablation arm only means something against the arm A that shares its
    binding, and on this project those landed in two campaigns - so the report
    that carries the Module 24 contrasts is named for the POOL, which is not a
    directory under experiments/runs/. The per-run loop above therefore skipped
    it silently, and the project's one supported result never reached the
    database or the UI.

    The pooled experiment row is created from its components, which
    `analyse_campaign.py` has already refused to pool unless their split and
    both channel bindings match exactly (RX-047). Components that are not
    themselves ingested are skipped rather than stubbed: a pooled row whose
    parts are missing would claim an analysis nobody can trace.
    """
    from backend.database.models import Experiment
    from sqlalchemy import select

    count = 0
    for path in sorted(REPORTS.glob("results_*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        run_id = report.get("run_id") or ""
        if "+" not in run_id:
            continue

        parts = run_id.split("+")
        rows = [
            session.execute(select(Experiment).filter_by(run_id=part)).scalar_one_or_none()
            for part in parts
        ]
        if any(row is None for row in rows):
            print(f"skipping pooled report {path.name}: a component run is not ingested")
            continue

        first = rows[0]
        _get_or_create(
            session,
            Experiment,
            defaults={
                "source_path": str(path),
                "split": first.split,
                "natural_model": first.natural_model,
                "program_model": first.program_model,
                "verifier_model": first.verifier_model,
                "independence": first.independence,
            },
            run_id=run_id,
        )
        count += ingest_report(session, run_id, report)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="registry, facts, dataset, every run")
    parser.add_argument("--registry", action="store_true")
    parser.add_argument("--facts", action="store_true")
    parser.add_argument("--dataset", action="store_true")
    parser.add_argument("--structure", action="store_true",
                        help="pages, sections and tables from the chunk cache")
    parser.add_argument("--run", action="append", help="run id (repeatable)")
    parser.add_argument("--report", help="report JSON to load metric values from")
    args = parser.parse_args()

    load_dotenv(_ENV_FILE)
    try:
        url = database_url()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    engine = build_engine(url)
    totals: dict[str, int] = {}

    with session_scope(engine) as session:
        if (args.all or args.registry) and REGISTRY.exists():
            totals["documents"] = ingest_registry(session, REGISTRY)

        if args.all or args.structure:
            # After the registry: every page, section and table hangs off a
            # document row that must already exist.
            totals.update(
                ingest_structure(session, sorted(PROCESSED.glob("*.chunks.jsonl")))
            )

        if args.all or args.facts:
            count = 0
            # Accumulated across every file, then pruned once. Per-file pruning
            # would delete every fact belonging to a file not yet reached.
            live_facts: set[tuple[str, str, str, int]] = set()
            for path in sorted(PROCESSED.glob("*.chunks.jsonl")):
                batch = list(facts_from_chunks(_load_chunks(path)))
                count += ingest_facts(session, batch)
                live_facts.update(
                    (f.document_id, f.chunk_id, f.row_label, f.column_index) for f in batch
                )
            totals["financial_facts"] = count
            totals["facts_pruned"] = prune_facts(session, live_facts)

        dataset = None
        if (args.all or args.dataset or args.run) and DATASET_PATH.exists():
            dataset = load_dataset(DATASET_PATH)
            totals["questions"] = ingest_dataset(session, dataset)

        run_ids = list(args.run or [])
        if args.all:
            run_ids = sorted(
                {p.parent.name for p in RUNS_ROOT.glob("*/results.jsonl")} | set(run_ids)
            )

        index = ChunkTextIndex()
        answers = 0
        for run_id in run_ids:
            recorder = CampaignRecorder(run_id)
            records = recorder.records()
            if not records:
                continue
            config_path = recorder.directory / "config.json"
            config = (
                json.loads(config_path.read_text(encoding="utf-8"))
                if config_path.exists()
                else {}
            )

            # Grade once, here, and copy the verdict in. Recomputing correctness
            # inside the database layer would let it disagree with the report
            # about which answers were wrong, and two graders is one too many.
            graded: dict[tuple[str, str], dict] = {}
            if dataset is not None:
                questions = {q.qid: q for q in dataset.questions if q.answer is not None}
                for arm in sorted({r.get("arm") for r in records if r.get("arm")}):
                    for case in analyse(records, questions, arm=arm, index=index).cases:
                        graded[(arm, case.qid)] = case.as_dict()

            answers += ingest_run(
                session,
                run_id,
                records,
                config=config,
                source_path=str(recorder.results_path),
                analysis_by_qid=graded,
            )

            report_path = (
                Path(args.report) if args.report else REPORTS / f"results_{run_id}.json"
            )
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                totals["evaluation_results"] = totals.get(
                    "evaluation_results", 0
                ) + ingest_report(session, run_id, report)
        if run_ids:
            totals["answers"] = answers

        if args.all:
            totals["pooled_reports"] = _ingest_pooled_reports(session)

    for name, count in sorted(totals.items()):
        print(f"{name}: {count}")
    if not totals:
        print("nothing to ingest; pass --all or --run <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
