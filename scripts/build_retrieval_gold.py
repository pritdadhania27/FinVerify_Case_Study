"""Build retrieval gold sets for the whole corpus (spec Module 6, EVALUATION.md §4).

Retrieval has only ever been measured on Infosys. RX-012 showed why that
matters: the same 22 questions scored 0.909 evidence accuracy filtered to one
document and 0.727 corpus-wide, and Module 7's planned retrieval went from
reading as a +0.068 improvement to a +0.409 one. A bank's balance sheet, a
conglomerate's segment note and a pharmaceutical company's inventory schedule
break retrieval in different ways, and none of them has ever been scored.

**This builds a RETRIEVAL set, not gold answers.** The distinction is what makes
it safe to generate without a human:

    a gold ANSWER asserts "the correct value of X is 1,600,585.9" - a claim about
    what a metric means, which needs a person (spec §16), and which this project
    refuses to generate;

    a gold EVIDENCE SPAN asserts "the string 'Advances' and the string
    '1,600,585.9' both appear on PDF page 213" - a fact about a file, decidable
    by reading the file.

Only the second is produced here, and every one of them is verified against the
PDF's own text layer before it is written. A span that cannot be confirmed is
DROPPED, never guessed at.

Completeness matters as much as correctness. The Infosys set originally listed
only the page picked by hand, so retrieval was scored on "did it find MY page"
rather than "did it find the evidence" - and Infosys states its revenue figure
on four separate pages. Every question here is therefore scanned across the
whole document, and every page carrying the same label-and-figure pair is added
as an alternative location.

    python scripts/build_retrieval_gold.py                 # all companies
    python scripts/build_retrieval_gold.py --company "HDFC Bank Limited"
    python scripts/build_retrieval_gold.py --per-company 25
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.documents.acquisition import DocumentRegistry  # noqa: E402
from evaluation.metrics.retrieval import normalise  # noqa: E402

REGISTRY = PROJECT_ROOT / "documents/registry.json"
DATASET = PROJECT_ROOT / "datasets/finverify_ind/finverify_ind_v1.json"
OUT_DIR = PROJECT_ROOT / "datasets/retrieval_eval"

# Matches the row label the generator recorded, e.g. "row 'Advances', column ...".
_ROW_LABEL = re.compile(r"row '([^']+)'")

# A figure needs enough digits to be a discriminating anchor. "2" appears on
# every page of every annual report; "1,600,585.9" appears on very few.
_MIN_FIGURE_DIGITS = 3


def page_texts(pdf_path: Path) -> dict[int, str]:
    """1-indexed page number -> normalised text, read from the PDF's own layer.

    PyMuPDF rather than the chunk cache on purpose. The chunks are what
    retrieval indexes; grading them against themselves would measure
    self-consistency and call it accuracy.
    """
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        return {i + 1: normalise(doc[i].get_text()) for i in range(doc.page_count)}
    finally:
        doc.close()


def figure_variants(text: str) -> list[str]:
    """The ways a report might print the same figure.

    A generated answer is a canonical decimal ("1600585.9"); the page prints it
    with separators and often without a trailing ".0". Both Indian and western
    grouping appear in this corpus - sometimes in the same table - so the
    variants are searched rather than assumed.
    """
    raw = text.strip().replace(",", "")
    if not raw:
        return []
    negative = raw.startswith("-")
    raw = raw.lstrip("-")
    if not raw.replace(".", "").isdigit():
        return []

    whole, _, frac = raw.partition(".")
    frac = frac.rstrip("0")

    def group_western(s: str) -> str:
        return f"{int(s):,}"

    def group_indian(s: str) -> str:
        if len(s) <= 3:
            return s
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return ",".join(parts) + "," + tail

    bodies = {whole, group_western(whole), group_indian(whole)}
    out: set[str] = set()
    for body in bodies:
        out.add(body)
        if frac:
            out.add(f"{body}.{frac}")
    if negative:
        # Financial statements print negatives in parentheses far more often
        # than with a minus sign.
        out |= {f"({v})" for v in list(out)} | {f"-{v}" for v in list(out)}
    return sorted(out, key=len, reverse=True)


def locate(pages: dict[int, str], label: str, figure: str) -> list[tuple[int, str]]:
    """Every page carrying BOTH the row label and the figure.

    Requiring both on the same page is what stops a bare number matching a
    coincidental occurrence elsewhere in a 585-page filing.
    """
    label_n = normalise(label)
    if not label_n:
        return []
    hits: list[tuple[int, str]] = []
    for page, text in sorted(pages.items()):
        if label_n not in text:
            continue
        for variant in figure_variants(figure):
            if len(re.sub(r"\D", "", variant)) < _MIN_FIGURE_DIGITS:
                continue
            if normalise(variant) in text:
                hits.append((page, variant))
                break
    return hits


def build_for(
    record,
    questions: list[dict],
    *,
    limit: int,
    set_suffix: str = "",
) -> tuple[dict, dict]:
    pages = page_texts(Path(record.path))
    built: list[dict] = []
    stats = {"considered": 0, "no_label": 0, "unverifiable": 0, "kept": 0, "extra_pages": 0}

    for q in questions:
        if len(built) >= limit:
            break
        stats["considered"] += 1
        answer = q.get("answer") or {}
        note = answer.get("source_note", "")
        match = _ROW_LABEL.search(note)
        if not match:
            stats["no_label"] += 1
            continue
        label = match.group(1)
        figure = str(answer.get("text", ""))

        found = locate(pages, label, figure)
        if not found:
            # The anchors cannot be confirmed on any page of the source. That is
            # a fact about this question, not a reason to invent a location.
            stats["unverifiable"] += 1
            continue

        claimed = answer.get("source_page")
        if claimed is not None and not any(p == claimed for p, _ in found):
            # The generator's page and the PDF's text disagree. Recorded as a
            # miss rather than silently corrected - a gold set that quietly
            # relocates its own spans cannot expose an extraction defect.
            stats["unverifiable"] += 1
            continue

        stats["extra_pages"] += max(0, len(found) - 1)
        built.append(
            {
                "qid": f"R{len(built) + 1:02d}",
                "question": q["question"],
                "question_type": q.get("question_type", "lookup_single"),
                "answer_note": (
                    f"candidate figure {figure} {answer.get('unit', '')}".strip()
                    + " - NOT a validated gold answer; this set grades retrieval only"
                ),
                "source_qid": q["qid"],
                # The split this question came from. Available here all along and
                # never recorded, which is why the set-level label could be
                # asserted rather than computed.
                "source_split": q.get("split"),
                "evidence": [
                    {
                        "group_id": f"{(q.get('provenance') or {}).get('metric', 'metric')}_"
                        f"{(q.get('provenance') or {}).get('reported_year', 'year')}",
                        "note": (
                            f"stated on {len(found)} page(s); any is correct evidence"
                        ),
                        "any_of": [
                            {"page": page, "anchors": [label, variant]}
                            for page, variant in found
                        ],
                    }
                ],
            }
        )
        stats["kept"] += 1

    # DERIVED, never asserted. This was hardcoded "validation" while the source
    # questions were drawn from every split, so 43 of the 76 sealed test
    # questions ended up inside sets stamped "validation" and carrying the note
    # "must never be reported as a test result". `evaluate_retrieval.py` gates on
    # this field, so the gate read a label that did not describe the contents and
    # permitted every run. The fix is not a stricter gate - the gate was correct -
    # it is that the label has to be computed from what is actually in the set.
    splits = sorted({q["source_split"] for q in built if q.get("source_split")})
    if not splits:
        # No source question carries a split: a hand-built set, which is what the
        # Infosys set is. Unknown is the honest label, and the gate treats
        # anything that is not plainly "validation" as needing the test flag.
        split_label = "unknown"
    elif splits == ["validation"]:
        split_label = "validation"
    elif "test" in splits:
        # Named so it can never be mistaken for a clean validation set, and so
        # the gate refuses it without FINVERIFY_ALLOW_TEST=1.
        split_label = "test"
    else:
        split_label = "+".join(splits)

    meta = {
        # The split suffix is part of the identity. Without it a validation-only
        # rebuild would overwrite the mixed-split set at the same path, and the
        # only record that the figures came from different gold would be the
        # commit history.
        "set_id": (
            f"retrieval-eval-{record.document_id[:8]}-v1"
            + (f"-{set_suffix}" if set_suffix else "")
        ),
        "split": split_label,
        "split_composition": {
            name: sum(1 for q in built if q.get("source_split") == name)
            for name in splits
        },
        "document_id": record.document_id,
        "company": record.company,
        "fiscal_year": record.fiscal_year,
        "source_document": str(Path(record.path).as_posix()),
        "source_sha256": record.sha256,
        "generated_by": "scripts/build_retrieval_gold.py",
        "notes": [
            (
                "RETRIEVAL set. Grades whether the evidence needed to answer "
                "reaches the top-K. It does NOT grade answers, and the figures "
                "quoted in answer_note are UNVALIDATED FinVerify-IND candidates."
            ),
            (
                "Every span was verified against the PDF text layer at build "
                "time: both the row label and the figure were found on the "
                "stated page. Questions whose anchors could not be confirmed "
                "were DROPPED, and questions whose confirmed page disagreed "
                "with the generator's page were dropped rather than relocated."
            ),
            (
                "`page` is the 1-indexed PDF page, which is what chunk "
                "provenance records - not the number printed on the page."
            ),
            (
                "Evidence groups are COMPLETE: the whole document was scanned "
                "and every page carrying the same label-and-figure pair is "
                "listed as an alternative. Without this the metric measures "
                "'did retrieval find MY page' rather than 'did it find the "
                "evidence'."
            ),
            (
                "Anchors keep the source's own digit grouping. This corpus "
                "mixes Indian and western grouping inside single tables, and an "
                "anchor that normalised separators would hide exactly the "
                "transcription errors this project exists to catch."
            ),
            (
                "The `split` field is DERIVED from the source questions, not "
                "declared. It was hardcoded 'validation' until 2026-09-12 while "
                "source questions were drawn from every split, which put 43 of "
                "the 76 sealed test questions into sets labelled validation and "
                "let `evaluate_retrieval.py`'s seal gate pass on a label that "
                "did not describe the contents. `split_composition` now states "
                "the breakdown so the claim is checkable rather than asserted."
            ),
            (
                "A set whose split is anything other than 'validation' must be "
                "treated as test-bearing: it needs FINVERIFY_ALLOW_TEST=1, the "
                "access is logged, and any figure from it is a one-shot result "
                "that cannot be used to tune anything."
            ),
        ],
        "questions": built,
    }
    return meta, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", help="build for one company only")
    parser.add_argument(
        "--split",
        action="append",
        choices=["train", "validation", "test"],
        help=(
            "restrict to questions from these benchmark splits; repeatable. "
            "Omit to take every split, which is what produced the RX-053 "
            "contamination - the resulting set is then correctly labelled "
            "test-bearing and gated. Use --split validation to build a set that "
            "can be evaluated freely."
        ),
    )
    parser.add_argument("--per-company", type=int, default=25)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    registry = DocumentRegistry(REGISTRY)
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    all_questions = dataset["questions"] if isinstance(dataset, dict) else dataset

    records = [
        r for r in registry.all()
        if args.company is None or r.company == args.company
    ]
    if not records:
        print("no registered documents match", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    total_kept = 0
    for record in records:
        if record.company == "Infosys Limited":
            # Already has a hand-built, hand-completed set (RX-005). Leaving it
            # alone keeps every earlier measurement comparable.
            print(f"{record.company:40} skipped - hand-built set already exists")
            continue

        mine = [q for q in all_questions if q.get("company") == record.company]
        if args.split:
            # Filtered BEFORE the per-company limit, so a validation-only set gets
            # the full limit of validation questions rather than whatever survives
            # after test questions have eaten the quota.
            wanted = set(args.split)
            before = len(mine)
            mine = [q for q in mine if q.get("split") in wanted]
            print(
                f"  {record.company}: {len(mine)} of {before} questions "
                f"match split {sorted(wanted)}"
            )
        # A split-filtered build is a DIFFERENT gold set, not a new version of the
        # same one, so it gets its own id and its own file.
        suffix = "-".join(sorted(args.split)) if args.split else ""
        meta, stats = build_for(
            record, mine, limit=args.per_company, set_suffix=suffix
        )
        if not meta["questions"]:
            print(f"{record.company:40} NO verifiable spans - nothing written")
            continue

        name = f"{record.document_id[:8]}_retrieval_v1"
        if suffix:
            name = f"{name}_{suffix}"
        out = args.out_dir / f"{name}.json"
        out.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        total_kept += stats["kept"]
        print(
            f"{record.company:40} {stats['kept']:3} kept  "
            f"({stats['considered']} considered, {stats['unverifiable']} unverifiable, "
            f"{stats['no_label']} no label, +{stats['extra_pages']} alternative pages)"
            f"  -> {out.name}"
        )

    print(f"\n{total_kept} verified retrieval questions written")
    print("Validate before use:  scripts/evaluate_retrieval.py --gold <file> --validate-gold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
