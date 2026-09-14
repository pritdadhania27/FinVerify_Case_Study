"""Does each gold question cite a page on the basis it asks about? (RX-026)

An Indian annual report contains the same metric twice: once in the STANDALONE
statements (the parent company alone) and once in the CONSOLIDATED statements
(the group, subsidiaries included). The two differ by however much the
subsidiaries are worth - for Tata Motors, whose consolidated figures carry
Jaguar Land Rover, total borrowings are 13,771.04 crore standalone against
98,500.09 crore consolidated. Seven times.

FinVerify-IND's questions name the basis explicitly ("as reported on the
consolidated balance sheet"). Nothing checked that the cited page was on that
basis, and `precheck_gold.py` cannot: it establishes that a figure is PRINTED
on a page, and both figures are printed, on different pages. RX-016 said so in
as many words, and this is the hole it named.

The generator prefers the standalone page for a structural reason worth
understanding. Under Ind AS the consolidated balance sheet splits borrowings
into current and non-current with no total line, while the standalone capital
management note prints a single row labelled `Total borrowings`. A binder
matching row labels finds the exact string in the wrong section and no string
at all in the right one, so the defect is not random - it is what label
matching does to this document structure.

    .\\.venv\\Scripts\\python.exe scripts\\check_statement_basis.py
    .\\.venv\\Scripts\\python.exe scripts\\check_statement_basis.py --show 20
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.documents.statement_basis import page_basis  # noqa: E402
from scripts.precheck_gold import RAW_DIR  # noqa: E402

WORKSHEET = PROJECT_ROOT / "datasets/finverify_ind/worksheet.csv"
RETRIEVAL = PROJECT_ROOT / "datasets/retrieval_eval"
REPORT = PROJECT_ROOT / "evaluation/reports/statement_basis.json"

PDFS = {
    "Tata Motors Limited": "tata_motors_iar_2023-24.pdf",
    "Reliance Industries Limited": "reliance_iar_2023-24.pdf",
    "Sun Pharmaceutical Industries Limited": "sun_pharma_ar_2023-24.pdf",
    "HDFC Bank Limited": "hdfc_bank_iar_2023-24.pdf",
    "Infosys Limited": "infosys_ar_2023-24_consolidated.pdf",
}

# The classifier itself lives in backend/documents/statement_basis.py, because
# the generator needs the same rule and two copies of a rule this subtle would
# drift apart. This script is the audit built on top of it.


def load_basis() -> dict[str, dict[int, str]]:
    return {company: page_basis(RAW_DIR / name) for company, name in PDFS.items()}


# Pages whose basis was established by reading them. The classifier is checked
# against these on every run: it is a heuristic over running headers, and a
# heuristic that silently drifts would produce exactly the confident wrong
# number this script exists to find.
GROUND_TRUTH = (
    ("Tata Motors Limited", 280, "consolidated"),
    ("Tata Motors Limited", 449, "standalone"),
    ("Sun Pharmaceutical Industries Limited", 182, "standalone"),
    ("HDFC Bank Limited", 316, "standalone"),
    ("Reliance Industries Limited", 140, "consolidated"),
)


def self_check(basis: dict[str, dict[int, str]]) -> list[str]:
    failures = []
    for company, page, expected in GROUND_TRUTH:
        got = basis[company].get(page, "?")
        if got != expected:
            failures.append(f"{company} p{page}: expected {expected}, got {got}")
    return failures


def audit_worksheet(basis, rows):
    per_company: dict[str, Counter] = defaultdict(Counter)
    offenders = []
    for row in rows:
        page = (row.get("source_page") or "").strip()
        where = basis.get(row["company"], {}).get(int(page), "?") if page.isdigit() else "?"
        per_company[row["company"]][where] += 1
        if where == "standalone" and "consolidated" in row["question"].lower():
            offenders.append(row)
    return per_company, offenders


def asked_basis(question_text: str) -> str:
    """The basis the QUESTION names. Questions now say which one they mean.

    Without this the audit counted "the evidence is standalone" as a defect
    even for a question that asks for the standalone figure - which after
    the D42 regeneration is 15 of Sun Pharma's 29 questions, and reported a
    52% error rate for a set that was entirely correct.
    """
    lowered = question_text.lower()
    if "standalone" in lowered:
        return "standalone"
    if "consolidated" in lowered:
        return "consolidated"
    return "?"


def audit_retrieval(basis):
    sets = []
    for path in sorted(glob.glob(str(RETRIEVAL / "*_retrieval_v1.json"))):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        pages_of = basis.get(payload["company"], {})
        counts: Counter = Counter()
        for question in payload["questions"]:
            where = set()
            for group in question["evidence"]:
                for alternative in group.get("any_of", [group]):
                    where.add(pages_of.get(alternative["page"], "?"))
            where -= {"-", "?"}
            wanted = asked_basis(question.get("question", ""))
            # A defect only when EVERY acceptable page is on a basis the
            # question did not ask for. Standalone evidence for a standalone
            # question is correct, not a miss.
            if len(where) == 1 and wanted != "?" and wanted not in where:
                counts["wrong_basis"] += 1
            elif where == {wanted}:
                counts["right_basis"] += 1
            else:
                counts["mixed_or_unknown"] += 1
        sets.append({"company": payload["company"], "n": len(payload["questions"]), **counts})
    return sets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=10, help="offending qids to print")
    args = parser.parse_args()

    basis = load_basis()
    failures = self_check(basis)
    if failures:
        print("  the section classifier FAILED its own ground truth:")
        for line in failures:
            print(f"    {line}")
        print("  refusing to report numbers derived from it.")
        return 1
    print(f"  section classifier agrees with all {len(GROUND_TRUTH)} hand-read pages\n")

    with WORKSHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    per_company, offenders = audit_worksheet(basis, rows)

    print("  FinVerify-IND - the basis of the page each candidate was read from")
    print(f"  {'company':<38}{'n':>4}{'consol.':>9}{'standal.':>10}{'unknown':>9}")
    for company in sorted(per_company):
        counts = per_company[company]
        total = sum(counts.values())
        unknown = counts["-"] + counts["?"]
        print(f"  {company[:36]:<38}{total:>4}{counts['consolidated']:>9}"
              f"{counts['standalone']:>10}{unknown:>9}")
    share = len(offenders) / len(rows)
    unknown_total = sum(c["-"] + c["?"] for c in per_company.values())
    print(f"\n  ASKS CONSOLIDATED, CITES A STANDALONE PAGE: "
          f"{len(offenders)}/{len(rows)} ({share:.1%})")
    print(f"  cited page could not be placed in either section: {unknown_total}"
          f"/{len(rows)} ({unknown_total / len(rows):.1%})")
    print("  Unknown is not wrong - it is unverified, and it is why the figure")
    print("  above is a lower bound rather than a total. A page outside every")
    print("  financial-statements section (an MD&A summary table, say) is a")
    print("  DIFFERENT defect from the wrong basis, and needs a person.")

    if offenders:
        print(f"\n  first {min(args.show, len(offenders))}:")
        for row in offenders[: args.show]:
            print(f"    {row['qid']}  p{row['source_page']:<5} {row['company'][:26]:<28}"
                  f"{row['candidate_answer']} {row['candidate_unit']}")

    retrieval = audit_retrieval(basis)
    total = sum(entry["n"] for entry in retrieval)
    wrong = sum(entry.get("wrong_basis", 0) for entry in retrieval)

    print("\n  Retrieval gold - evidence on a basis the question did not ask for")
    print(f"  {'company':<38}{'n':>4}{'wrong basis':>14}")
    for entry in retrieval:
        bad = entry.get("wrong_basis", 0)
        print(f"  {entry['company'][:36]:<38}{entry['n']:>4}{bad:>10}"
              f" ({bad / entry['n']:>4.0%})")
    print(f"\n  EVIDENCE ENTIRELY ON THE UNASKED BASIS: {wrong}/{total} "
          f"({wrong / total:.1%})")
    print("  On these a retriever that returns the page the question actually")
    print("  names is scored as a miss. Standalone evidence for a standalone")
    print("  question is correct and is NOT counted here - an earlier version")
    print("  of this check ignored the question wording and reported 52% for a")
    print("  Sun Pharma set that was entirely right.")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "finverify_ind": {
                    "n": len(rows),
                    "offenders": [row["qid"] for row in offenders],
                    "share": share,
                    "per_company": {c: dict(v) for c, v in per_company.items()},
                },
                "retrieval_gold": {"n": total, "wrong_basis": wrong, "sets": retrieval},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
