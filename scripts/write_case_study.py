"""Write CASE_STUDY.md (spec Module 27).

    python scripts/write_case_study.py                   # corpus only, no results yet
    python scripts/write_case_study.py --run <run_id>     # with results

Separate from `analyse_campaign.py` so the corpus half of Module 27 - selecting
a representative set of Indian companies and reports - can be written and read
before any campaign has run. That selection is finished work, and burying it
inside a script that refuses to run without results would make it look absent.

The corpus table is built from `documents/registry.json` and the dataset, not
typed in. A hand-written page count is a page count nobody re-checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._console import use_utf8  # noqa: E402
from backend.core.paths import project_path  # noqa: E402

use_utf8()

from evaluation.case_study import build_case_study, render_markdown
from evaluation.dataset import DATASET_ROOT, load_dataset
from evaluation.error_analysis import analyse
from experiments.campaign import load_records

REGISTRY = project_path("documents/registry.json")
PROCESSED = project_path("documents/processed")
DATASET_PATH = DATASET_ROOT / "finverify_ind_v1.json"

# Sector labels. Not derived from the filings: classification is a judgement, so
# it is written down where it can be argued with rather than inferred somewhere
# and presented as data.
SECTORS = {
    "Infosys Limited": "IT services",
    "HDFC Bank Limited": "Banking",
    "Reliance Industries Limited": "Energy / conglomerate",
    "Sun Pharmaceutical Industries Limited": "Pharmaceuticals",
    "Tata Motors Limited": "Automotive",
}


def corpus_section() -> str:
    documents = json.loads(REGISTRY.read_text(encoding="utf-8")).get("documents", [])
    records = list(documents.values()) if isinstance(documents, dict) else list(documents)

    candidates: dict[str, int] = {}
    validated: dict[str, int] = {}
    if DATASET_PATH.exists():
        dataset = load_dataset(DATASET_PATH)
        for question in dataset.questions:
            candidates[question.company] = candidates.get(question.company, 0) + 1
            if question.usable_as_gold:
                validated[question.company] = validated.get(question.company, 0) + 1

    rows = []
    extracted = 0
    for record in records:
        company = record.get("company", "")
        chunks = PROCESSED / f"{record['document_id']}.chunks.jsonl"
        is_extracted = chunks.exists()
        extracted += int(is_extracted)
        rows.append(
            f"| {company} | {SECTORS.get(company, '—')} | {record.get('fiscal_year', '')} "
            f"| `{record.get('sha256', '')[:12]}…` | {'yes' if is_extracted else 'NO'} "
            f"| {candidates.get(company, 0)} | {validated.get(company, 0)} |"
        )

    return "\n".join(
        [
            "## The corpus",
            "",
            "Five Indian listed companies across five sectors, FY2023-24, 1,665",
            "pages. Chosen for structural diversity rather than size: a bank's",
            "balance sheet, a conglomerate's segment reporting and a",
            "pharmaceutical company's inventory notes break extraction in",
            "different ways, and five IT companies would measure one document",
            "template five times.",
            "",
            "| Company | Sector | Year | SHA-256 | Extracted | Candidates | Validated |",
            "|---|---|---|---|---|---:|---:|",
            *rows,
            "",
            f"{extracted} of {len(records)} reports extracted and chunked.",
            "",
            "Every report is a public filing obtained from the company's own",
            "investor-relations site; URL, retrieval date and SHA-256 are recorded",
            "in `documents/registry.json`. The PDFs are **not** redistributed —",
            "the hash is what lets a reader confirm the file they fetch from the",
            "publisher is the file this evaluation used.",
            "",
            # Derived, not written down. This paragraph read "Every question is
            # PENDING human validation" for as long as that was true and for a
            # while after it stopped being - a hardcoded claim in a GENERATED
            # document is worse than one in a hand-written file, because it
            # re-asserts itself as fresh on every regeneration.
            (
                "**Candidates are not gold.** "
                f"{sum(validated.values())} of {sum(candidates.values())} "
                "candidate questions have passed human validation (spec 16); "
                "`evaluation.dataset` refuses to serve a PENDING or REJECTED "
                "question to an evaluation. The Validated column is the one "
                "that gates the results below."
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", help="run id to draw results from")
    parser.add_argument("--arm", default="A")
    parser.add_argument("--out", default="CASE_STUDY.md")
    args = parser.parse_args()

    cases: list = []
    questions: dict = {}
    records: list[dict] = []
    if args.run:
        records = load_records(args.run)
        if not records:
            print(f"no records for run {args.run!r}", file=sys.stderr)
            return 1
        dataset = load_dataset(DATASET_PATH)
        questions = {q.qid: q for q in dataset.questions if q.answer is not None}
        cases = analyse(
            [r for r in records if r.get("arm") == args.arm and not r.get("error")],
            questions,
            arm=args.arm,
        ).cases

    study = build_case_study(cases, questions, records, arm=args.arm, sectors=SECTORS)
    Path(args.out).write_text(
        render_markdown(
            study,
            title="Case study: Indian annual reports",
            corpus=corpus_section(),
        ),
        encoding="utf-8",
    )
    print(f"wrote {args.out}")
    if not study.has_data:
        print("  corpus only: no graded questions yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
