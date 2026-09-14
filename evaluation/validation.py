"""The human validation round-trip for FinVerify-IND (spec §16, decision D23).

Spec §16 requires human verification of custom gold data, and no amount of
generation quality substitutes for it. This module is the workflow: candidates
out as a CSV a person can work through against the source PDF, verdicts back in,
every change recorded.

**A CSV, not a web app.** The validator is one person working through 150
questions with a PDF open beside them. A review UI would be a week of frontend
work to replace a spreadsheet that already does the job, and Module 21 is
deferred anyway (D24). The round-trip is the deliverable; the interface is
whatever the validator already has.

**Corrections are recorded, never applied silently.** A validator who finds the
candidate answer wrong writes the right one in `corrected_answer`, and the import
keeps BOTH: the original stays in `provenance.generated_answer` and the change is
listed in the import report. The rule that gold labels are never altered to
improve a score (ENGINEERING_RULES.md) is only auditable if every alteration has a record;
"the gold says 3,956" is worth nothing if nobody can see it once said 3,596.

**The double pass measures intra-annotator consistency, not inter-annotator
agreement** (D23). One validator re-judging a blind ~20% subset tells you how
stable that person's judgments are. It does not tell you whether a second person
would agree, and the write-up must not use the word "inter-annotator". A single
validator is a stated limitation of this dataset, not something to be papered
over with a statistic that sounds like it addresses it.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .dataset import Dataset, DatasetQuestion, GoldAnswer, ValidationRecord, ValidationStatus

__all__ = [
    "VALIDATION_COLUMNS",
    "export_for_validation",
    "import_validations",
    "ImportReport",
    "select_double_pass_subset",
    "intra_annotator_agreement",
]

# The columns a validator fills. `verdict` and `corrected_answer` are the only
# ones they normally touch; the rest are read-only context printed so the check
# can be done without cross-referencing another file.
VALIDATION_COLUMNS = (
    "qid",
    "question",
    "definition",
    "ambiguous",
    "candidate_answer",
    "candidate_unit",
    "source_page",
    "evidence_anchors",
    "company",
    "fiscal_year",
    # Filled by the validator:
    "verdict",            # validated | rejected | needs_review
    "corrected_answer",   # left blank if the candidate answer is right
    "corrected_unit",
    "notes",
)

_VERDICTS = {
    "validated": ValidationStatus.VALIDATED,
    "rejected": ValidationStatus.REJECTED,
    "needs_review": ValidationStatus.NEEDS_REVIEW,
    "": ValidationStatus.PENDING,
}


def _anchors(question: DatasetQuestion) -> str:
    parts: list[str] = []
    for group in question.evidence:
        for span in group.get("any_of", ()):
            page = span.get("printed_page") or span.get("page")
            parts.append(f"p{page}: {' | '.join(span.get('anchors', ()))}")
    return " ;; ".join(parts)


def interleave_by_company(
    questions: tuple[DatasetQuestion, ...] | list[DatasetQuestion],
) -> list[DatasetQuestion]:
    """Round-robin across companies, preserving each company's own order.

    Validation is partial by design - roughly 40 questions is enough for a first
    result - so the ORDER a validator works down the sheet decides which
    companies that first result covers. Grouped by company, the first 40 rows of
    this corpus are 40 HDFC Bank questions, because a 585-page bank filing
    yields 127 of the 268 candidates. The result would then be a statement about
    one bank wearing a five-company label.

    Interleaved, any prefix is approximately balanced. This is the same
    principle as the campaign runner's question-major ordering (D27): make the
    prefix of an interrupted job representative, because the job WILL be
    interrupted.

    It changes nothing about the dataset itself - only the sequence a human
    meets it in.
    """
    by_company: dict[str, list[DatasetQuestion]] = {}
    for question in questions:
        by_company.setdefault(question.company, []).append(question)

    # Smallest company first, so the thinnest cell is never the one starved by a
    # validator who stops early.
    order = sorted(by_company, key=lambda name: (len(by_company[name]), name))
    interleaved: list[DatasetQuestion] = []
    for index in range(max((len(v) for v in by_company.values()), default=0)):
        for name in order:
            bucket = by_company[name]
            if index < len(bucket):
                interleaved.append(bucket[index])
    return interleaved


def export_for_validation(
    questions: tuple[DatasetQuestion, ...] | list[DatasetQuestion],
    path: Path | str,
    *,
    interleave: bool = True,
) -> int:
    """Write the validation worksheet. Returns the row count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = interleave_by_company(questions) if interleave else list(questions)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(VALIDATION_COLUMNS))
        writer.writeheader()
        for question in rows:
            first_page = None
            for group in question.evidence:
                for span in group.get("any_of", ()):
                    first_page = span.get("printed_page") or span.get("page")
                    break
                if first_page is not None:
                    break
            writer.writerow(
                {
                    "qid": question.qid,
                    "question": question.question,
                    "definition": question.definition,
                    "ambiguous": "yes" if question.ambiguous else "no",
                    "candidate_answer": question.answer.text if question.answer else "",
                    "candidate_unit": question.answer.unit if question.answer else "",
                    "source_page": first_page or "",
                    "evidence_anchors": _anchors(question),
                    "company": question.company,
                    "fiscal_year": question.fiscal_year,
                    "verdict": "",
                    "corrected_answer": "",
                    "corrected_unit": "",
                    "notes": "",
                }
            )
    return len(rows)


@dataclass
class ImportReport:
    """What an import changed - the audit trail for every gold label."""

    validated: int = 0
    rejected: int = 0
    needs_review: int = 0
    untouched: int = 0
    corrections: list[dict] = field(default_factory=list)
    unknown_qids: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Rows whose answer was corrected and whose evidence anchors were therefore
    # dropped as stale. They need re-deriving before anything reads evidence for
    # these questions again.
    anchors_invalidated: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "validated": self.validated,
            "rejected": self.rejected,
            "needs_review": self.needs_review,
            "untouched": self.untouched,
            "corrections": self.corrections,
            "unknown_qids": self.unknown_qids,
            "problems": self.problems,
            "anchors_invalidated": self.anchors_invalidated,
        }


def import_validations(
    dataset: Dataset,
    path: Path | str,
    *,
    validator: str,
    pass_number: int = 1,
) -> tuple[Dataset, ImportReport]:
    """Apply a completed worksheet, returning a NEW dataset and an audit report.

    The dataset is rebuilt rather than mutated so the caller decides when the
    change reaches disk, and so an import that turns out to be wrong is a
    discarded object rather than an overwritten file.

    A blank verdict leaves the question exactly as it was. That matters for a
    partially-completed worksheet: a validator who got through 80 rows should be
    able to submit, and the remaining 70 must stay PENDING rather than silently
    becoming anything else.
    """
    if not validator.strip():
        raise ValueError("a validator must be named: an unattributed gold label is not gold")

    rows: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            qid = (row.get("qid") or "").strip()
            if qid:
                rows[qid] = row

    report = ImportReport()
    today = datetime.now(UTC).date().isoformat()
    updated: list[DatasetQuestion] = []

    for question in dataset.questions:
        row = rows.pop(question.qid, None)
        if row is None:
            report.untouched += 1
            updated.append(question)
            continue

        raw_verdict = (row.get("verdict") or "").strip().lower().replace(" ", "_")
        if raw_verdict not in _VERDICTS:
            report.problems.append(
                f"{question.qid}: unrecognised verdict {raw_verdict!r}; left untouched"
            )
            report.untouched += 1
            updated.append(question)
            continue

        status = _VERDICTS[raw_verdict]
        if status is ValidationStatus.PENDING:
            report.untouched += 1
            updated.append(question)
            continue

        answer = question.answer
        provenance = dict(question.provenance)
        evidence = question.evidence
        corrected = (row.get("corrected_answer") or "").strip()
        if corrected:
            # Keep what the generator proposed. A correction with no record of
            # what it replaced is indistinguishable from a label tuned to a
            # result.
            if answer is not None and "generated_answer" not in provenance:
                provenance["generated_answer"] = answer.as_dict()
            new_unit = (row.get("corrected_unit") or "").strip() or (
                answer.unit if answer else ""
            )
            answer = GoldAnswer(
                text=corrected,
                unit=new_unit,
                source_page=answer.source_page if answer else None,
                source_note=answer.source_note if answer else "",
            )
            report.corrections.append(
                {
                    "qid": question.qid,
                    "from": provenance.get("generated_answer", {}).get("text"),
                    "to": corrected,
                    "validator": validator,
                    "on": today,
                    "notes": (row.get("notes") or "").strip(),
                }
            )
            if answer.to_value() is None:
                report.problems.append(
                    f"{question.qid}: corrected answer {corrected!r} does not parse "
                    "as a figure; recorded but NOT usable as gold"
                )
                status = ValidationStatus.NEEDS_REVIEW

            # The anchors located the figure the validator just rejected, so
            # they no longer point at this answer. Dropping them makes every
            # consumer say "undecidable" instead of confidently wrong: the
            # oracle arm refuses to build, `evidence_was_retrieved` returns
            # None, and the H2 stratifier drops the question rather than filing
            # it under the wrong cause. Kept in provenance because a correction
            # with no record of what it replaced cannot be audited.
            if evidence:
                provenance.setdefault(
                    "superseded_evidence",
                    {
                        "evidence": list(evidence),
                        "reason": (
                            "the answer was corrected on import; these anchors "
                            "cite the superseded figure and must be re-derived"
                        ),
                    },
                )
                evidence = ()
                report.anchors_invalidated.append(question.qid)

        second_pass_agreed = question.validation.second_pass_agreed
        if pass_number > 1 and question.validation.status is not ValidationStatus.PENDING:
            # The measurement D23 asks for: did the same person reach the same
            # verdict, and the same number, the second time?
            same_status = question.validation.status is status
            same_answer = (
                (question.answer.text if question.answer else None)
                == (answer.text if answer else None)
            )
            second_pass_agreed = bool(same_status and same_answer)

        counts = {
            ValidationStatus.VALIDATED: "validated",
            ValidationStatus.REJECTED: "rejected",
            ValidationStatus.NEEDS_REVIEW: "needs_review",
        }
        setattr(report, counts[status], getattr(report, counts[status]) + 1)

        updated.append(
            DatasetQuestion(
                qid=question.qid,
                question=question.question,
                company=question.company,
                fiscal_year=question.fiscal_year,
                document_id=question.document_id,
                answer=answer,
                definition=question.definition,
                ambiguous=question.ambiguous,
                question_type=question.question_type,
                difficulty=question.difficulty,
                # The local, not `question.evidence`: a corrected answer empties
                # this, because the anchors located the figure that was rejected.
                evidence=evidence,
                validation=ValidationRecord(
                    status=status,
                    validator=validator,
                    validated_on=today,
                    pass_number=pass_number,
                    notes=(row.get("notes") or "").strip(),
                    second_pass_agreed=second_pass_agreed,
                ),
                split=question.split,
                provenance=provenance,
            )
        )

    report.unknown_qids = sorted(rows)
    if report.unknown_qids:
        report.problems.append(
            f"{len(report.unknown_qids)} row(s) in the worksheet match no question "
            "in the dataset and were ignored"
        )

    return (
        Dataset(
            set_id=dataset.set_id,
            version=dataset.version,
            created_on=dataset.created_on,
            notes=dataset.notes,
            questions=tuple(updated),
        ),
        report,
    )


def select_double_pass_subset(
    questions: tuple[DatasetQuestion, ...] | list[DatasetQuestion],
    *,
    fraction: float = 0.2,
    seed: int = 20260826,
) -> tuple[DatasetQuestion, ...]:
    """A reproducible random subset for the second validation pass (D23).

    Seeded, so the subset is fixed by the seed rather than by whoever ran the
    selection. Choosing the re-check subset after seeing which questions the
    system got wrong would make the consistency estimate meaningless.
    """
    ordered = sorted(questions, key=lambda q: q.qid)
    if not ordered:
        return ()
    count = max(1, round(len(ordered) * fraction))
    return tuple(random.Random(seed).sample(ordered, min(count, len(ordered))))


def intra_annotator_agreement(questions: tuple[DatasetQuestion, ...]) -> dict:
    """Agreement rate over the double-passed subset.

    Deliberately NOT called inter-annotator agreement, and deliberately not
    reported as a kappa: kappa corrects for chance agreement between two
    annotators, and there is only one here. The honest statistic is the raw
    proportion of second-pass judgments that matched the first, reported beside
    the fact that a single validator cannot establish inter-annotator agreement
    at all.
    """
    judged = [q for q in questions if q.validation.second_pass_agreed is not None]
    if not judged:
        return {
            "double_passed": 0,
            "agreement": None,
            "note": "no question has been through a second validation pass",
        }
    agreed = sum(1 for q in judged if q.validation.second_pass_agreed)
    return {
        "double_passed": len(judged),
        "agreed": agreed,
        "agreement": agreed / len(judged),
        "note": (
            "intra-annotator consistency for a single validator (D23). This is "
            "NOT inter-annotator agreement and must not be reported as such."
        ),
    }
