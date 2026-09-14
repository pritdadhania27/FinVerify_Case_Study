"""Machine pre-check for the FinVerify-IND validation worksheet (spec §16).

This script does NOT validate anything. It cannot: a gold label means a person
read the question, checked the figure against the source page, and signed for
it, and nothing in this repository is allowed to write that word (see
`ValidationStatus` in `evaluation/dataset.py`). Marking candidates validated
from code would grade the detector against answers the generator produced, which
makes every downstream number circular.

What it does is remove the *searching* from the validator's job, leaving only the
*judging*. For each candidate it opens the cited PDF page, looks for the
candidate figure in every grouping the corpus actually uses, and writes the raw
line back into the worksheet. The validator then reads one line of the filing
beside the proposed answer instead of navigating a 585-page document to find it.

The distinction that matters: a machine can establish **that a number is printed
on a page**. It cannot establish that it is **the number the question asks
for** - the right metric, the right year's column, the right consolidation
basis, the right scale. Those are the judgments, and they stay human. So the
`precheck` column is deliberately named for what was checked and never for a
verdict, and rows that fail it are flagged for attention rather than rejected.

Columns added (all read-only context; `import_validations` ignores them):

    precheck        on_cited_page | short_figure | elsewhere | not_found
                    | no_candidate
    page_excerpt    the raw line(s) from the PDF carrying the figure
    source_note     which statement, table and column the generator read
    also_on_pages   other pages carrying the same figure, when relevant

Existing `verdict` / `corrected_*` / `notes` cells and the interleaved row order
(D35) are preserved: this rewrites the sheet in place and a partly-completed
sheet must survive it.

    .\\.venv\\Scripts\\python.exe scripts\\precheck_gold.py
    .\\.venv\\Scripts\\python.exe scripts\\precheck_gold.py --report-only
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from evaluation.metrics.retrieval import normalise  # noqa: E402
from scripts.build_retrieval_gold import figure_variants  # noqa: E402

# Project-root relative, not CWD relative: run from elsewhere and these
# silently resolve to files that do not exist.
REGISTRY = PROJECT_ROOT / "documents/registry.json"
DATASET = PROJECT_ROOT / "datasets/finverify_ind/finverify_ind_v1.json"
WORKSHEET = PROJECT_ROOT / "datasets/finverify_ind/worksheet.csv"
RAW_DIR = PROJECT_ROOT / "documents/raw"

EXTRA_COLUMNS = ("precheck", "page_excerpt", "source_note", "also_on_pages")

# Below this many digits a figure stops being a discriminating anchor: "12"
# appears on every page of every annual report. Same threshold, and the same
# reason, as scripts/build_retrieval_gold.py.
_MIN_FIGURE_DIGITS = 3

# How many pages either side of the cited one to search before calling a figure
# absent. Table extraction occasionally attributes a row to the facing page, and
# reporting that as "not in the document" would send a validator hunting for a
# defect that is one page away.
_NEIGHBOURHOOD = 1


def raw_pages(pdf_path: Path) -> dict[int, list[str]]:
    """1-indexed page number -> the page's own text lines, unnormalised.

    Unnormalised because the validator reads these. `normalise` casefolds and
    collapses whitespace, which is right for matching and wrong for showing a
    person a line from a balance sheet.
    """
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        return {
            i + 1: [line for line in doc[i].get_text().splitlines() if line.strip()]
            for i in range(doc.page_count)
        }
    finally:
        doc.close()


def searchable_variants(figure: str) -> list[str]:
    """The groupings of `figure` that are discriminating enough to search for.

    Empty means the figure cannot be located by search at all, which is a
    different fact from its absence and must not be reported as one. Note this
    is decided on the VARIANTS, not on the raw string: "4.00" looks like three
    digits and reduces to the single digit "4", because a report prints 4.00 as
    "4.00" only sometimes and the trailing zeros carry no information. Gating on
    the raw string flagged five of HDFC Bank's dividend-per-share rows as
    missing from a page they are printed on.
    """
    return [v for v in figure_variants(figure) if len(_digits(v)) >= _MIN_FIGURE_DIGITS]


def find_figure(lines: list[str], figure: str) -> str | None:
    """The first raw line carrying `figure` in any grouping the corpus uses."""
    variants = searchable_variants(figure)
    if not variants:
        return None
    for line in lines:
        haystack = normalise(line)
        for variant in variants:
            if normalise(variant) in haystack:
                return line.strip()
    return None


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def label_window(lines: list[str], label: str, span: int = 2) -> str | None:
    """The label's line plus the next few, for a figure too short to match on.

    A two-digit figure cannot be located by search - "18" is on every page of
    every filing - so for those the row is found by its label instead and the
    validator reads the values themselves. In HDFC Bank's ten-year summary the
    label and its row of figures are extracted as separate lines, which is why
    this returns a window rather than one line.
    """
    label_n = normalise(label)
    if not label_n:
        return None
    for i, line in enumerate(lines):
        if label_n in normalise(line):
            window = [ln.strip() for ln in lines[i : i + 1 + span] if ln.strip()]
            return "  //  ".join(window)
    return None


def _figure_index(lines: list[str], figure: str) -> int | None:
    variants = searchable_variants(figure)
    for i, line in enumerate(lines):
        haystack = normalise(line)
        if any(normalise(v) in haystack for v in variants):
            return i
    return None


def _printable(line: str) -> str:
    """A source line safe to put in a CSV and print to a terminal.

    Sun Pharma's filing carries U+0007 where a bullet glyph should be, and
    PyMuPDF extracts it faithfully. Left in, it reaches the worksheet as a
    control character and makes the reviewer's terminal beep while it shows
    them a balance sheet - a small thing that reads as the tool malfunctioning
    at the exact moment it is asking for careful attention.
    """
    return "".join(" " if ord(c) < 32 else c for c in line).strip()


def excerpt(lines: list[str], figure: str, label: str) -> str:
    """The figure's line, and the label's line when the two are separated.

    In a well-extracted table row they are the same line and one is enough. When
    the extractor split the row, showing both is what lets a validator see that
    the number belongs to the metric being asked about - which is exactly the
    judgment this script is not entitled to make for them.

    The label line is the one NEAREST the figure, not the first on the page. A
    metric name usually appears several times - in a heading, in a footnote, in
    the row itself - and Tata Motors p449 opens with "Total borrowings includes
    all long and short-term borrowings as disclosed in notes 22 and 23", which
    is a definition of the row rather than the row. Taking the first match
    showed the validator a sentence instead of a figure.
    """
    figure_at = _figure_index(lines, figure)
    label_n = normalise(label)
    label_at = None
    if label_n:
        hits = [i for i, line in enumerate(lines) if label_n in normalise(line)]
        if hits and figure_at is not None:
            label_at = min(hits, key=lambda i: abs(i - figure_at))
        elif hits:
            label_at = hits[0]

    if figure_at is not None and label_at == figure_at:
        return _printable(lines[figure_at])
    parts = [_printable(lines[i]) for i in (label_at, figure_at) if i is not None]
    return "  //  ".join(parts)


def label_from_anchors(anchors: str) -> str:
    """The row label out of "p449: Total borrowings | 18872.44"."""
    first = anchors.split(";;")[0]
    _, _, rest = first.partition(":")
    label, _, _figure = rest.partition("|")
    return label.strip()


def classify(
    pages: dict[int, list[str]], cited: int, figure: str, label: str
) -> tuple[str, str, str]:
    """-> (precheck, page_excerpt, also_on_pages)."""
    if not figure.strip():
        # The 12 derived-metric questions carry no candidate answer on purpose:
        # a pre-filled arithmetic result invites a validator to wave it through.
        return "no_candidate", "", ""

    near = [p for p in range(cited - _NEIGHBOURHOOD, cited + _NEIGHBOURHOOD + 1) if p in pages]

    if not searchable_variants(figure):
        # Not "absent" - unsearchable. "18" occurs on every page of every
        # filing, so a hit would mean nothing and a miss even less. Report the
        # row by its label and let the validator read the figure themselves,
        # rather than flagging a defect that is really a limit of the check.
        for page in sorted(near, key=lambda p: abs(p - cited)):
            window = label_window(pages[page], label)
            if window:
                return "short_figure", window, ""
        return "not_found", "", ""

    for page in sorted(near, key=lambda p: abs(p - cited)):
        if find_figure(pages[page], figure):
            note = "" if page == cited else f"found on p{page}, cited as p{cited}"
            return "on_cited_page", excerpt(pages[page], figure, label) or note, note

    elsewhere = [p for p, lines in sorted(pages.items()) if find_figure(lines, figure)]
    if elsewhere:
        shown = ", ".join(str(p) for p in elsewhere[:8])
        if len(elsewhere) > 8:
            shown += f", … ({len(elsewhere)} pages)"
        return "elsewhere", excerpt(pages[elsewhere[0]], figure, label), shown
    return "not_found", "", ""


def load_notes() -> dict[str, str]:
    """qid -> the generator's source note (statement, table, column)."""
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = payload["questions"] if isinstance(payload, dict) else payload
    out = {}
    for q in questions:
        answer = q.get("answer") or {}
        out[q["qid"]] = answer.get("source_note", "")
    return out


def pdf_for(company: str) -> Path | None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for document in registry["documents"]:
        if document.get("company") == company:
            return RAW_DIR / document["filename"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", type=Path, default=WORKSHEET)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print the distribution without rewriting the worksheet",
    )
    args = parser.parse_args()

    if not args.worksheet.exists():
        print(f"no worksheet at {args.worksheet}; run build_finverify_ind.py export first")
        return 1

    with args.worksheet.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        columns = list(reader.fieldnames or [])

    notes = load_notes()
    by_company: dict[str, list[dict]] = {}
    for row in rows:
        by_company.setdefault(row["company"], []).append(row)

    counts: dict[str, int] = {}
    for company, company_rows in sorted(by_company.items()):
        pdf = pdf_for(company)
        if pdf is None or not pdf.exists():
            print(f"  {company}: PDF missing; {len(company_rows)} rows left unchecked")
            for row in company_rows:
                row["precheck"] = "pdf_missing"
                row["page_excerpt"] = ""
                row["source_note"] = notes.get(row["qid"], "")
                row["also_on_pages"] = ""
                counts["pdf_missing"] = counts.get("pdf_missing", 0) + 1
            continue

        print(f"  {company}: reading {pdf.name} …", flush=True)
        pages = raw_pages(pdf)
        for row in company_rows:
            cited = int(row["source_page"] or 0)
            label = label_from_anchors(row.get("evidence_anchors", ""))
            verdict, text, others = classify(pages, cited, row["candidate_answer"], label)
            row["precheck"] = verdict
            row["page_excerpt"] = text
            row["source_note"] = notes.get(row["qid"], "")
            row["also_on_pages"] = others
            counts[verdict] = counts.get(verdict, 0) + 1

    print()
    total = sum(counts.values())
    order = ("on_cited_page", "short_figure", "elsewhere", "not_found",
             "no_candidate", "pdf_missing")
    for name in order:
        if name in counts:
            n = counts[name]
            print(f"  {name:<16} {n:>4}  ({n / total:.1%})")
    print(f"  {'total':<16} {total:>4}")
    print()
    print("  These are MACHINE checks. `on_cited_page` means the figure is printed")
    print("  where the generator said it was - not that it answers the question.")
    print("  The metric, the year's column and the scale are still human judgments.")

    if args.report_only:
        return 0

    fieldnames = columns + [c for c in EXTRA_COLUMNS if c not in columns]
    with args.worksheet.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  wrote {args.worksheet} ({len(rows)} rows, verdicts preserved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
