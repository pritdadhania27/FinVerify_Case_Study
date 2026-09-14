"""Interactive validation of FinVerify-IND candidates (spec §16, decision D23).

The worksheet is the round-trip format and stays the round-trip format - D23
chose a CSV over a review UI deliberately, and `import_validations` reads it.
This is a front end onto the same file, for the part that a spreadsheet is bad
at: showing one question at a time with the line from the filing beside it.

Run `precheck_gold.py` first and the source line is already in the sheet, so
this needs no PDF open and no page-hunting - which is what made 268 rows feel
unapproachable.

**This tool records a judgment. It never proposes one.** There is no default
answer and Enter does nothing: a keypress that means "yes" when you meant
"next" is how a validation pass becomes a rubber stamp, and a rubber-stamped
gold set measures the generator rather than the system. Every response is an
explicit key.

**It writes after every answer.** Stopping is always safe, the remaining rows
stay PENDING, and re-running resumes where you left off. Partial validation is
the expected mode: ~40 questions is enough for a first result on the validation
split; all 268 is what the frozen test-set evaluation needs.

    .\\.venv\\Scripts\\python.exe scripts\\review_gold.py
    .\\.venv\\Scripts\\python.exe scripts\\review_gold.py --limit 40
    .\\.venv\\Scripts\\python.exe scripts\\review_gold.py --company "Infosys Limited"
    .\\.venv\\Scripts\\python.exe scripts\\review_gold.py --derived
    .\\.venv\\Scripts\\python.exe scripts\\review_gold.py --review-judged

Then commit the verdicts into the dataset:

    .\\.venv\\Scripts\\python.exe scripts\\build_finverify_ind.py import ^
        --csv datasets\\finverify_ind\\worksheet.csv --validator "Your Name"
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

# Resolved against the project root, NOT the working directory. Run from
# anywhere else and a relative default silently pointed at a file that does not
# exist; the script then printed "no worksheet" and exited without touching the
# real one, which is indistinguishable from having reviewed nothing.
WORKSHEET = PROJECT_ROOT / "datasets/finverify_ind/worksheet.csv"

_PRECHECK_NOTE = {
    "on_cited_page": "the figure is printed on the cited page",
    "short_figure": "figure too short to search; the row was found by its label",
    "elsewhere": "NOT on the cited page - found on the pages listed below",
    "not_found": "NOT found anywhere in this filing",
    "no_candidate": "no candidate answer: derived metric, work it out yourself",
    "pdf_missing": "PDF unavailable; nothing was checked",
    "": "not pre-checked - run scripts/precheck_gold.py",
}

_MENU = (
    "  [y] correct      [c] correct, but a different number\n"
    "  [n] wrong        [?] unsure / needs review\n"
    "  [s] skip         [q] save and quit"
)


# A derived-metric row carries no candidate on purpose (D35), so there is
# nothing for "correct" or "wrong" to refer to. Offering them anyway is how a
# blank answer acquires a `validated` verdict from one keystroke - and an empty
# gold label is worse than no label, because it grades as a real one.
_MENU_DERIVED = (
    "  [c] enter the figure you worked out\n"
    "  [?] unsure / needs review\n"
    "  [s] skip         [q] save and quit"
)


def has_candidate(row: dict) -> bool:
    return bool((row.get("candidate_answer") or "").strip())


def width() -> int:
    return min(shutil.get_terminal_size((100, 30)).columns, 100)


def rule(char: str = "─") -> str:
    return char * width()


def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(
        text, width=width(), initial_indent=indent, subsequent_indent=indent
    )


_FIGURE_IN_TEXT = re.compile(r"-?\d[\d,]*\.?\d*")


def anchor_figures(row: dict) -> list[str]:
    """The numerals cited by this row's evidence anchors."""
    text = row.get("evidence_anchors") or ""
    # Strip the page prefix ("p110: ") so the page number is not read as a figure.
    body = re.sub(r"\bp\d+\s*:", " ", text)
    return [m.group(0) for m in _FIGURE_IN_TEXT.finditer(body)]


def answer_contradicts_anchors(row: dict) -> str | None:
    """Report an answer whose own evidence anchors cite a different figure.

    **Usually this means the anchors are stale, not that the answer is wrong.**
    On the eleven rows where it fires today, the validator rejected the
    auto-extracted candidate, supplied a corrected answer with a note, and the
    import wrote the new answer while leaving the anchors pointing at the
    rejected figure. The answer is the human's; the anchors are the machine's
    first guess.

    It still has to be shown, because anything reading the evidence rather than
    the answer - the oracle arm, the retrieval metrics, the H2 stratifier - is
    then pointed at the wrong figure.
    """
    answer = (row.get("corrected_answer") or row.get("candidate_answer") or "").strip()
    if not answer:
        return None
    try:
        target = float(answer.replace(",", ""))
    except ValueError:
        return None

    cited = []
    for figure in anchor_figures(row):
        try:
            cited.append(float(figure.replace(",", "")))
        except ValueError:
            continue
    if not cited or any(abs(c - target) < 1e-9 for c in cited):
        return None

    candidate = (row.get("candidate_answer") or "").strip()
    was_rejected = candidate and any(
        abs(c - float(candidate.replace(",", ""))) < 1e-9
        for c in cited
        if candidate.replace(",", "").replace("-", "").replace(".", "").isdigit()
    )
    detail = (
        f"answer {answer} · anchors cite {', '.join(anchor_figures(row))}"
    )
    if was_rejected:
        detail += (
            f"\nThe anchors still cite {candidate}, which is the candidate this "
            "row's own verdict REJECTED. The answer is almost certainly right and "
            "the anchors are stale."
        )
    return detail


def show(row: dict, position: str) -> None:
    print("\n" * 2 + rule("═"))
    print(f"  {position}   {row['qid']}   {row['company']}")
    print(rule("═"))
    print()
    print(wrap(row["question"]))
    print()
    if row.get("definition"):
        print(wrap(f"definition: {row['definition']}", indent="  "))
    if (row.get("ambiguous") or "").strip().lower() == "yes":
        print(wrap("AMBIGUOUS by design (D22) - scored separately, judge the "
                   "definition as written."))
    print()
    print(rule())

    answer = row["candidate_answer"].strip() or "(none - derived metric)"
    unit = row["candidate_unit"].strip() or "(no unit)"
    print(f"  candidate answer   {answer}   {unit}")
    print(f"  cited page         p{row['source_page']}")
    if row.get("source_note"):
        print(wrap(f"read from           {row['source_note']}", indent="  "))

    anchors = anchor_figures(row)
    if anchors:
        print(f"  evidence anchors   {', '.join(anchors)}")
    conflict = answer_contradicts_anchors(row)
    if conflict:
        print()
        print("  >> THE ANSWER AND ITS OWN EVIDENCE ANCHORS DISAGREE <<")
        print(wrap(conflict, indent="     "))
        print(wrap("The ANSWER is not what needs checking here - it is already "
                   "yours. What is wrong is the anchors, which still point at the "
                   "figure you rejected, so anything that reads the evidence "
                   "instead of the answer is sent to the wrong row.",
                   indent="     "))
    print()

    precheck = row.get("precheck", "")
    print(f"  machine pre-check  {precheck or 'none'} — {_PRECHECK_NOTE.get(precheck, '')}")
    if row.get("also_on_pages"):
        print(f"  also on pages      {row['also_on_pages']}")
    excerpt = row.get("page_excerpt", "").strip()
    if excerpt:
        print()
        print("  from the filing:")
        for part in excerpt.split("//"):
            part = part.strip()
            if part:
                print(wrap(part, indent="      "))
    print()
    print(rule())
    if has_candidate(row):
        print("  Is this the right answer to the question as written?")
        print("  Check the metric, the year's column, the scale and the sign.")
        print(_MENU)
    else:
        print("  DERIVED METRIC - there is no candidate to check. Work the")
        print("  figure out from the filing and enter it, or skip.")
        print(_MENU_DERIVED)


def ask(prompt: str) -> str:
    try:
        # lstrip the BOM: PowerShell prefixes piped stdin with U+FEFF, so the
        # first answer of a scripted session arrives as "\ufeffy" and matches
        # nothing. Interactive input is unaffected, which is why this only
        # showed up when the session was replayed from a file.
        return input(prompt).lstrip("\ufeff").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


_CONCLUSION = re.compile(
    r"Conclusion:.*?is\s+([0-9][0-9,]*\.?[0-9]*)\s*(?:%|percent)",
    re.IGNORECASE | re.DOTALL,
)


def as_figure(text: str) -> str | None:
    """`text` as a bare figure, or None if it is not one.

    A validator working out a derived metric writes out the working, and
    pasting all of it into the answer field is the natural thing to do. The
    tool used to accept it: four gold answers were stored as 400-700
    character paragraphs ending "Conclusion: ... is 1.59%", and an evaluator
    comparing a model's "1.59" against that paragraph would have scored a
    correct answer wrong.

    So a figure is required here, and where the text ends in an explicit
    conclusion the figure is lifted from it rather than the answer being
    refused - the validator did state it, and making them retype it invites
    a transcription error into gold data.
    """
    text = text.strip()
    if not text:
        return None
    bare = text.replace(",", "").rstrip("%").strip()
    try:
        float(bare)
    except ValueError:
        pass
    else:
        return bare
    match = _CONCLUSION.search(text)
    return match.group(1).replace(",", "") if match else None


# A metric that is a quotient of two like quantities. The scale cancels, so
# these answers are dimensionless and any scale word on one is a defect.
_DIMENSIONLESS_METRIC = re.compile(
    r"\b(ratio|margin|return on|yield|coverage|percentage|per\s?cent|percent)\b",
    re.IGNORECASE,
)
_SCALE_WORD = re.compile(r"\b(crores?|lakhs?|millions?|billions?|thousands?|cr)\b", re.IGNORECASE)


def unit_conflicts_with_question(question: str, definition: str, unit: str) -> str | None:
    """Why `unit` cannot be right for this question, or None if it can.

    A debt-to-equity ratio came back as "0.41 crore". The figure was right and
    the validator's own note said "0.41 times", but `crore` multiplies the gold
    by 10^7, so a model answering 0.41 would have been graded WRONG and one
    answering "0.41 crore" - an answer that misunderstands what a ratio is -
    graded correct. The grader was inverted on that question (RX-031).

    Only this direction is checked, because only this direction was observed.
    A scale word on a quotient is unambiguous; the reverse cases are not.
    """
    scale = _SCALE_WORD.search(unit)
    if not scale:
        return None
    if not _DIMENSIONLESS_METRIC.search(f"{question} {definition}"):
        return None
    return (
        f"this question asks for a quotient, so its answer has no scale - "
        f"{scale.group(0)!r} would multiply the gold answer and grade a "
        f"correct model answer wrong. Use % for a percentage, or - for a "
        f"bare ratio."
    )


def judge(row: dict) -> str:
    """Collect one verdict. Returns "next" or "quit"."""
    confused = 0
    derived = not has_candidate(row)
    while True:
        key = ask("  > ").lower()
        if derived and key in {"y", "n"}:
            print("  There is no candidate answer on this row, so there is")
            print("  nothing for that to mean. Enter the figure with c, or s to skip.")
            continue
        if key == "y":
            row["verdict"] = "validated"
            return "next"
        if key == "n":
            row["verdict"] = "rejected"
            row["notes"] = ask("  why is it wrong? ") or row.get("notes", "")
            return "next"
        if key == "c":
            corrected = ask("  the correct figure: ").strip()
            if not corrected:
                print("  nothing entered; not recorded")
                continue
            figure = as_figure(corrected)
            if figure is None:
                print("  that is not a figure, and the answer field has to hold")
                print("  one - a paragraph here becomes the gold answer, and a")
                print("  model replying with the right number would score wrong.")
                print("  Enter just the number; put the working in the note.")
                continue
            if figure != corrected:
                print(f"  reading that as {figure}; the full text goes in the note.")
                row["notes"] = "validator's working, verbatim: " + corrected
            corrected = figure
            row["corrected_answer"] = corrected
            unit_default = row.get("candidate_unit") or ""
            prompt = f"  unit [{unit_default}]: " if unit_default else "  unit: "
            while True:
                unit = ask(prompt).strip() or unit_default
                # An escape hatch, because Enter falls back to the default: if
                # THAT is the conflicting value there would be no way past.
                unit = "" if unit == "-" else unit
                conflict = unit_conflicts_with_question(
                    row.get("question", ""), row.get("definition", ""), unit
                )
                if conflict is None:
                    break
                print(f"  {conflict}")
            row["corrected_unit"] = unit
            row["verdict"] = "validated"
            row["notes"] = ask("  note (optional): ") or row.get("notes", "")
            return "next"
        if key == "?":
            row["verdict"] = "needs_review"
            row["notes"] = ask("  what is unclear? ") or row.get("notes", "")
            return "next"
        if key == "s":
            return "next"
        if key == "q":
            return "quit"
        confused += 1
        if key == "":
            print("  Press a letter, then Enter. Enter on its own does nothing")
            print("  deliberately - a key that means 'yes' when you meant 'next'")
            print("  would turn this into a rubber stamp.")
        else:
            print(f"  {key!r} is not one of the options.")
        if confused >= 3:
            print()
            print("    y = the candidate answer is correct")
            print("    n = it is wrong        ? = unsure       s = skip for now")
            print("    q = save and quit  <- nothing is lost, re-run to resume")
            confused = 0


def save(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Rewrite via a temporary file, so an interrupted write cannot truncate it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", type=Path, default=WORKSHEET)
    parser.add_argument("--company", help="only this company's questions")
    parser.add_argument(
        "--split",
        choices=("train", "validation", "test"),
        help="only questions in this split - validation is the one the campaign "
             "runs on, and validating elsewhere does not feed it",
    )
    parser.add_argument(
        "--derived",
        action="store_true",
        help="only the derived-metric rows that carry no candidate answer "
             "(12 of 268; they gate operand-binding accuracy, RX-025)",
    )
    parser.add_argument(
        "--qid",
        nargs="+",
        help="only these question ids - for re-checking a named set, such as the "
             "rows whose answer contradicts their own evidence anchors (TODO 0b). "
             "Implies --review-judged, since those rows already carry a verdict",
    )
    parser.add_argument("--limit", type=int, help="stop after this many rows")
    parser.add_argument(
        "--review-judged",
        action="store_true",
        help="include rows already carrying a verdict (default: only blanks)",
    )
    args = parser.parse_args()

    if not args.worksheet.exists():
        print(f"no worksheet at {args.worksheet}")
        print("run: scripts\\build_finverify_ind.py export")
        return 1

    with args.worksheet.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    # The worksheet carries no split column - it is a validation artefact, and
    # D23 keeps it human-editable rather than a mirror of the dataset. The split
    # is read from the dataset itself, so filtering cannot drift from it.
    splits: dict[str, str] = {}
    if args.split:
        import json

        from backend.core.paths import project_path

        dataset = project_path("datasets/finverify_ind/finverify_ind_v1.json")
        if not dataset.exists():
            print(f"--split needs the dataset, and {dataset} is missing")
            return 1
        payload = json.loads(dataset.read_text(encoding="utf-8"))
        questions = payload["questions"] if isinstance(payload, dict) else payload
        splits = {q["qid"]: q.get("split") for q in questions}

    if "precheck" not in fieldnames:
        print("  NOTE: this worksheet has not been pre-checked, so no source line")
        print("  will be shown. Run scripts\\precheck_gold.py first - it makes each")
        print("  row readable without opening the PDF.\n")

    # Naming rows explicitly means asking for those rows, and they will already
    # have verdicts - requiring --review-judged as well would only produce an
    # empty queue and a confusing message.
    wanted = set(args.qid or ())
    queue = [
        row
        for row in rows
        if (args.review_judged or wanted or not (row.get("verdict") or "").strip())
        and (not wanted or row["qid"] in wanted)
        and (not args.company or row["company"] == args.company)
        and (not args.derived or not has_candidate(row))
        and (not args.split or splits.get(row["qid"]) == args.split)
    ]
    if wanted:
        missing = wanted - {row["qid"] for row in queue}
        if missing:
            print(f"  not in this worksheet: {', '.join(sorted(missing))}\n")
    if args.limit:
        queue = queue[: args.limit]

    if not queue:
        print("  nothing to review - every matching row already has a verdict.")
        return 0

    done_already = sum(1 for r in rows if (r.get("verdict") or "").strip())
    print(rule("═"))
    print(f"  {len(queue)} to review · {done_already}/{len(rows)} already judged")
    print("  Rows are interleaved by company (D35), so any prefix stays balanced.")
    print("  Quit any time with q - everything answered is saved.")
    print(rule("═"))

    answered = 0
    for index, row in enumerate(queue, start=1):
        show(row, f"[{index}/{len(queue)}]")
        outcome = judge(row)
        save(args.worksheet, rows, fieldnames)
        if outcome == "quit":
            break
        if (row.get("verdict") or "").strip():
            answered += 1

    total_judged = sum(1 for r in rows if (r.get("verdict") or "").strip())
    print("\n" + rule("═"))
    print(f"  {answered} judged this session · {total_judged}/{len(rows)} total")
    print(f"  saved to {args.worksheet}")
    if total_judged == 0:
        print()
        print("  NOTHING WAS RECORDED. The worksheet is unchanged.")
        print("  Every question needs an explicit key - y, n, ?, s or q.")
        print("  If you did answer and see this, that is a bug: say so.")
        print(rule("═"))
        return 0
    print()
    print("  Commit them into the dataset with:")
    print("    .\\.venv\\Scripts\\python.exe scripts\\build_finverify_ind.py import \\")
    print(f"        --csv {args.worksheet} --validator \"Your Name\"")
    print(rule("═"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
