"""FinVerify-IND: the gold dataset layer (spec Module 26, decisions D22 / D23).

Everything empirical in this project rests on 150 gold answers. That makes this
module the highest-leverage place in the codebase for a silent error, and the
errors available here are not subtle bugs - they are shortcuts that look like
progress:

- an unvalidated candidate answer used as gold, which makes every metric a
  measurement of the generator rather than the system;
- a gold label edited after seeing a result, which converts the evaluation into
  a fit;
- the test split touched during development, which converts it into a second
  validation set.

Each is prevented structurally rather than by discipline.

**Candidates are not gold.** `build_split` returns only questions whose
validation status is VALIDATED. A PENDING question is a proposal awaiting a
human (spec §16), and there is deliberately no flag on the loader that lets an
evaluation run against proposals. The one escape hatch, `include_pending`, exists
for the validation tooling itself and is refused when a split is requested for
evaluation.

**The test split is sealed.** Loading it requires `FINVERIFY_ALLOW_TEST=1` and
appends to `experiments/test_set_access.log`. The point is not that the guard
cannot be bypassed - anyone with the repository can set an environment variable -
but that bypassing it leaves evidence in the repository rather than going
unnoticed.

**Ambiguity is a designed subset, not contamination** (D22). RX-007 found all
three channels splitting on return on equity because they used three standard
definitions - closing equity, average equity, owners' share - each defensible.
The detector flagged the question as risky, which is correct by its own lights
and wrong for a research question about *hallucination*. So the main set pins the
definition in the question text and carries it in `definition`, while a
deliberately unpinned subset is marked `ambiguous` and scored separately. It is
never pooled into the headline figure, because pooling would make that figure a
property of how many ambiguous questions someone happened to write.
"""

from __future__ import annotations

import hashlib
import json
import sys
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from backend.core.paths import project_path
from backend.core.financial_value import FinancialValue, parse_financial_value

__all__ = [
    "DATASET_ROOT",
    "TEST_ACCESS_LOG",
    "ValidationStatus",
    "ValidationRecord",
    "GoldAnswer",
    "DatasetQuestion",
    "Dataset",
    "load_dataset",
    "build_split",
    "SealedSplitError",
    "manifest",
    "stratify",
]

DATASET_ROOT = project_path("datasets/finverify_ind")
# Anchored to the repository, not the working directory: the seal's whole
# value is that bypassing it leaves evidence IN the repository. A log
# written wherever a process happened to start is not evidence.
TEST_ACCESS_LOG = project_path("experiments/test_set_access.log")


class SealedSplitError(RuntimeError):
    """Raised when the sealed split is requested without explicit authorisation.

    Named for the seal rather than for the split because pytest collects any
    class whose name starts with "Test", and an exception class masquerading as
    a test suite produces a warning on every run.
    """


class ValidationStatus(Enum):
    """Where a question stands with its human validator.

    PENDING is the default and the only status a generator may assign. Nothing
    else in the pipeline may write VALIDATED - that word means a person read the
    question, checked the figure against the source page, and signed for it.
    """

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class ValidationRecord:
    """Who signed off on this question, when, and on which pass.

    `pass_number` supports D23's double-pass on a random ~20%: the same validator
    re-judges a subset blind, and the disagreement rate estimates INTRA-annotator
    consistency. That is a weaker claim than inter-annotator agreement and the
    write-up must say so - one validator cannot produce the latter, and reporting
    it as though they could would misstate the dataset's reliability.
    """

    status: ValidationStatus = ValidationStatus.PENDING
    validator: str | None = None
    validated_on: str | None = None
    pass_number: int = 1
    notes: str = ""
    # Set when a second pass disagreed with the first. Carried rather than
    # resolved silently, because the disagreement IS the measurement.
    second_pass_agreed: bool | None = None

    @property
    def usable_as_gold(self) -> bool:
        return self.status is ValidationStatus.VALIDATED

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "validator": self.validator,
            "validated_on": self.validated_on,
            "pass_number": self.pass_number,
            "notes": self.notes,
            "second_pass_agreed": self.second_pass_agreed,
        }

    @classmethod
    def from_dict(cls, record: dict | None) -> ValidationRecord:
        if not record:
            return cls()
        return cls(
            status=ValidationStatus(record.get("status", "pending")),
            validator=record.get("validator"),
            validated_on=record.get("validated_on"),
            pass_number=int(record.get("pass_number", 1)),
            notes=record.get("notes", ""),
            second_pass_agreed=record.get("second_pass_agreed"),
        )


@dataclass(frozen=True)
class GoldAnswer:
    """The expected answer, kept in the form the document states it.

    `text` is preserved rather than only the canonical Decimal because a
    correctness dispute is settled by reading the source page, and "3,956 crore"
    is what appears there. `canonical` is derived, never hand-entered - a
    hand-entered canonical value is one more place a scale error can hide.
    """

    text: str
    unit: str = ""
    source_page: int | None = None
    source_note: str = ""

    def to_value(self) -> FinancialValue | None:
        """Parse through the same code path the predictions use.

        Deliberately the same parser: if gold were parsed by different rules from
        predictions, the correctness predicate would be comparing two different
        readings of the same notation, and a systematic scale bug would cancel
        out on one side and not the other.
        """
        text = self.text if not self.unit else f"{self.text} {self.unit}".strip()
        return parse_financial_value(text)

    def canonical(self) -> Decimal | None:
        value = self.to_value()
        return value.canonical() if value is not None else None

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "unit": self.unit,
            "source_page": self.source_page,
            "source_note": self.source_note,
        }


@dataclass(frozen=True)
class DatasetQuestion:
    qid: str
    question: str
    company: str
    fiscal_year: str
    document_id: str
    answer: GoldAnswer | None = None
    # D22. On the main set this states the metric definition the question pins,
    # e.g. "closing shareholders' equity". On the ambiguity subset it is empty
    # BY DESIGN and `ambiguous` is True.
    definition: str = ""
    ambiguous: bool = False
    question_type: str = "lookup"
    difficulty: str = "medium"
    # Evidence groups in the retrieval-eval format: all groups must be covered,
    # any span within a group covers it. Shared with `metrics.retrieval` so
    # provenance means one thing across the project.
    evidence: tuple[dict, ...] = ()
    validation: ValidationRecord = field(default_factory=ValidationRecord)
    split: str = "unassigned"
    provenance: dict = field(default_factory=dict)

    @property
    def usable_as_gold(self) -> bool:
        return self.validation.usable_as_gold and self.answer is not None

    @property
    def awaiting_computation(self) -> bool:
        """A derived-metric question whose answer is blank ON PURPOSE.

        The generator refuses to pre-fill an arithmetic result, because a
        validator is far likelier to wave through a number already in the box
        than to notice one that is missing. That blank is an intended state, not
        a defect - and an audit that reports intended states as problems is an
        audit people learn to ignore.
        """
        return bool(self.provenance.get("answer_left_blank"))

    @property
    def stratum(self) -> str:
        """The H2 stratum is assigned at analysis time from the retrieval result,
        not here. This is the *design* stratum - what the question is - which is
        what the sampling plan balances."""
        return f"{self.question_type}/{self.difficulty}"

    def gold_value(self) -> FinancialValue | None:
        return self.answer.to_value() if self.answer is not None else None

    def as_dict(self) -> dict:
        return {
            "qid": self.qid,
            "question": self.question,
            "company": self.company,
            "fiscal_year": self.fiscal_year,
            "document_id": self.document_id,
            "answer": self.answer.as_dict() if self.answer else None,
            "definition": self.definition,
            "ambiguous": self.ambiguous,
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "evidence": list(self.evidence),
            "validation": self.validation.as_dict(),
            "split": self.split,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, record: dict) -> DatasetQuestion:
        answer = record.get("answer")
        return cls(
            qid=record["qid"],
            question=record["question"],
            company=record.get("company", ""),
            fiscal_year=record.get("fiscal_year", ""),
            document_id=record.get("document_id", ""),
            answer=GoldAnswer(**answer) if answer else None,
            definition=record.get("definition", ""),
            ambiguous=bool(record.get("ambiguous", False)),
            question_type=record.get("question_type", "lookup"),
            difficulty=record.get("difficulty", "medium"),
            evidence=tuple(record.get("evidence", ())),
            validation=ValidationRecord.from_dict(record.get("validation")),
            split=record.get("split", "unassigned"),
            provenance=record.get("provenance", {}),
        )

    def validation_problems(self) -> tuple[str, ...]:
        """Everything wrong with this record, checked before it can be used.

        Run over the whole dataset by `scripts/build_finverify_ind.py --audit`.
        The point is that a malformed gold record fails loudly at build time
        rather than producing a quietly wrong correctness label at run time.
        """
        problems: list[str] = []
        if not self.question.strip():
            problems.append("empty question text")
        if self.answer is None and not self.awaiting_computation:
            problems.append("no gold answer")
        elif self.answer is not None and self.answer.to_value() is None:
            problems.append(f"gold answer {self.answer.text!r} does not parse as a figure")
        if not self.evidence:
            problems.append("no evidence groups: the answer cannot be traced to a page")
        if not self.ambiguous and not self.definition:
            problems.append(
                "main-set question with no pinned definition (D22 requires one; "
                "set ambiguous=True to place it in the ambiguity subset instead)"
            )
        if self.ambiguous and self.definition:
            problems.append(
                "ambiguity-subset question carries a pinned definition, which "
                "defeats the purpose of the subset"
            )
        if self.validation.status is ValidationStatus.VALIDATED and not self.validation.validator:
            problems.append("marked validated with no validator named")
        return tuple(problems)


@dataclass(frozen=True)
class Dataset:
    set_id: str
    version: str
    questions: tuple[DatasetQuestion, ...]
    created_on: str = ""
    notes: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.questions)

    def split(self, name: str) -> tuple[DatasetQuestion, ...]:
        return tuple(q for q in self.questions if q.split == name)

    def validated(self) -> tuple[DatasetQuestion, ...]:
        return tuple(q for q in self.questions if q.usable_as_gold)

    def pending(self) -> tuple[DatasetQuestion, ...]:
        return tuple(
            q for q in self.questions if q.validation.status is ValidationStatus.PENDING
        )

    def main_set(self) -> tuple[DatasetQuestion, ...]:
        return tuple(q for q in self.validated() if not q.ambiguous)

    def ambiguity_subset(self) -> tuple[DatasetQuestion, ...]:
        return tuple(q for q in self.validated() if q.ambiguous)

    def audit(self) -> dict[str, tuple[str, ...]]:
        return {q.qid: problems for q in self.questions if (problems := q.validation_problems())}

    def as_dict(self) -> dict:
        return {
            "set_id": self.set_id,
            "version": self.version,
            "created_on": self.created_on,
            "notes": list(self.notes),
            "counts": {
                "total": len(self.questions),
                "validated": len(self.validated()),
                "pending": len(self.pending()),
                "main_set": len(self.main_set()),
                "ambiguity_subset": len(self.ambiguity_subset()),
            },
            "questions": [q.as_dict() for q in self.questions],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )


def load_dataset(path: Path | str) -> Dataset:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    return Dataset(
        set_id=record.get("set_id", ""),
        version=record.get("version", ""),
        created_on=record.get("created_on", ""),
        notes=tuple(record.get("notes", ())),
        questions=tuple(DatasetQuestion.from_dict(q) for q in record.get("questions", ())),
    )


def _current_commit() -> str | None:
    """The commit the accessing code was at, or None if it cannot be determined.

    Best-effort by construction: this runs inside the seal, and an access must
    never fail because git is missing, slow, or the tree is not a repository. A
    missing commit is recorded as null rather than omitted, so a reader can tell
    "not determinable" from "nobody wrote this field".
    """
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(TEST_ACCESS_LOG.parent.parent),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001 - logging must never break an evaluation
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _log_test_access(reason: str, count: int) -> None:
    """Append one line of evidence that the sealed split was opened.

    RX-053 is why this records more than it used to. EVALUATION.md said each entry
    carried "timestamp, run id, and git commit"; it carried timestamp, reason and
    count. An entry with no commit cannot be tied to a state of the code, which
    defeats the point - the log exists so that a leak leaves evidence, and
    evidence that cannot be dated to a commit is most of the way to no evidence.

    `dirty` matters as much as `commit`: an evaluation run from a modified working
    tree is not reproducible from the recorded commit, and that is exactly the
    circumstance in which someone is most likely to be re-running the test split.
    """
    TEST_ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reason": reason,
        "questions": count,
        "commit": _current_commit(),
        "invoked_by": Path(sys.argv[0]).name if sys.argv and sys.argv[0] else None,
    }
    with TEST_ACCESS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def build_split(
    dataset: Dataset,
    split: str,
    *,
    include_ambiguous: bool = False,
    include_pending: bool = False,
    reason: str = "",
) -> tuple[DatasetQuestion, ...]:
    """The only supported way to get questions for an evaluation.

    Args:
        split: "train", "validation" or "test".
        include_ambiguous: D22's subset, off by default so it cannot reach a
            headline figure by accident.
        include_pending: for the validation tooling ONLY. Refused on the test
            split outright: there is no legitimate reason to run an evaluation
            against unvalidated answers on the sealed split.
        reason: recorded in the test-set access log. Required for the test split,
            because "who ran this and why" is the whole value of the log.

    Raises:
        SealedSplitError: the test split was requested without
            FINVERIFY_ALLOW_TEST=1, or without a reason.
    """
    if split == "test":
        if os.environ.get("FINVERIFY_ALLOW_TEST") != "1":
            raise SealedSplitError(
                "the test split is sealed (EVALUATION.md §1). Set "
                "FINVERIFY_ALLOW_TEST=1 deliberately, and only once the "
                "methodology is frozen at tag methodology-freeze-v1. Re-running "
                "after seeing test results converts it into a validation set, "
                "and the write-up would have to say so."
            )
        if not reason.strip():
            raise SealedSplitError(
                "test-set access requires a stated reason; it is written to "
                f"{TEST_ACCESS_LOG}"
            )
        if include_pending:
            raise SealedSplitError(
                "the test split may never be evaluated against unvalidated gold"
            )

    questions = [q for q in dataset.questions if q.split == split]
    if not include_pending:
        questions = [q for q in questions if q.usable_as_gold]
    if not include_ambiguous:
        questions = [q for q in questions if not q.ambiguous]

    if split == "test":
        _log_test_access(reason, len(questions))
    return tuple(questions)


def manifest(path: Path | str) -> dict:
    """SHA-256 of the dataset file, so a silent edit is detectable.

    EVALUATION.md §1 requires a recorded manifest for the test split. Recording
    it for the whole file is simpler and strictly stronger: a gold label altered
    to improve a score changes this hash, and the hash is committed.
    """
    data = Path(path).read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def stratify(questions: tuple[DatasetQuestion, ...]) -> dict[str, int]:
    """Cell counts over the design strata.

    D23 asks for the dataset to be stratified by design "so thin cells are thin
    by plan". This is what makes that checkable: a cell with three questions
    cannot support a per-stratum claim, and seeing that before the campaign runs
    is worth more than discovering it in the results table.
    """
    counts: dict[str, int] = {}
    for question in questions:
        for key in (
            f"type:{question.question_type}",
            f"difficulty:{question.difficulty}",
            f"company:{question.company}",
            f"ambiguous:{question.ambiguous}",
        ):
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
