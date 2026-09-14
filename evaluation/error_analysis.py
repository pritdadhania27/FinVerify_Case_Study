"""Error analysis (spec Module 25, decisions D12 / D19 / D22 / D25).

Two jobs, and the first is not qualitative at all.

**1. The H2 stratification, which is a load-bearing measurement.** H2 predicts
that detection works far better on reasoning-caused errors than on
retrieval-caused ones, because two channels reading the same wrong evidence agree
about it. Assigning each error to a stratum therefore decides whether the
project's causal story is testable. The assignment is made from gold evidence
spans, not from the system's own opinion of what it retrieved - a system that
graded its own retrieval would be marking its own homework in the one place where
that most matters.

**2. The qualitative contribution.** Errors are labelled on both taxonomy axes
(Module 14): where the error entered, and what it looks like. Cases are grouped
so a reader can look at them, because the table of counts is not the finding -
the finding is what the cases show.

**Both-agree-wrong is reported first, not buried.** It is the method's blind
spot: the questions where the detector was confident and wrong. A paper that
reports AUROC 0.85 and does not show this cell has described how well the
detector ranks the errors it can see, which is a different claim.

**Ambiguity gets its own number** (D22). "How often does a consistency-based
detector mistake definitional ambiguity for a numerical error?" is a false-
positive mode that is unquantified in the literature, and it is measurable here
precisely because the ambiguity subset was built to measure it.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.paths import project_path
from backend.verification.taxonomy import (
    ErrorKind,
    ErrorLabel,
    ErrorProvenance,
    classify,
)
from evaluation.dataset import DatasetQuestion
from evaluation.metrics.correctness import TAU_PRIMARY, judge
from evaluation.metrics.retrieval import EvidenceGroup

__all__ = [
    "PROCESSED",
    "ChunkTextIndex",
    "evidence_was_retrieved",
    "extraction_delivered_the_figure",
    "question_was_parsed_correctly",
    "AnalysedCase",
    "ErrorAnalysis",
    "analyse",
]

PROCESSED = project_path("documents/processed")


class ChunkTextIndex:
    """Chunk text by id, read back from the processed cache.

    Run artifacts record chunk ids rather than chunk text: 8 blocks of ~1.2 KB
    across 150 questions and 13 arms is around 20 MB of duplicated text in a
    committed repository. Reading it back by id is exact - it is the same text
    the channels saw, not a truncated copy - and costs one pass over a file that
    already exists.
    """

    def __init__(self, root: Path = PROCESSED):
        self._root = Path(root)
        self._text: dict[str, tuple[int | None, str]] = {}
        self._loaded: set[Path] = set()

    def _load(self, path: Path) -> None:
        if path in self._loaded or not path.exists():
            self._loaded.add(path)
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk_id = record.get("chunk_id")
            if chunk_id:
                self._text[chunk_id] = (record.get("page"), record.get("text") or "")
        self._loaded.add(path)

    def get(self, chunk_id: str, *, document_id: str | None = None) -> tuple[int | None, str]:
        if chunk_id not in self._text:
            if document_id:
                self._load(self._root / f"{document_id}.chunks.jsonl")
            else:
                for path in sorted(self._root.glob("*.chunks.jsonl")):
                    self._load(path)
        return self._text.get(chunk_id, (None, ""))


def evidence_was_retrieved(
    question: DatasetQuestion, record: dict, index: ChunkTextIndex
) -> bool | None:
    """Did the retrieved set contain every gold evidence group?

    This is the H2 stratifier, and it uses the same group semantics as the
    retrieval metrics: ALL groups must be covered, ANY span within a group
    covers it. A figure legitimately stated on four pages must not penalise a
    retriever that found it on the second.

    Returns None - not False - when it cannot be decided: no gold evidence, or
    chunk text that is no longer on disk. A missing chunk cache silently
    answering "no" would reclassify every reasoning error as retrieval-caused,
    which is exactly the direction that would make H2 look supported when it is
    not.
    """
    if not question.evidence:
        return None
    blocks = record.get("evidence") or []
    if not blocks:
        return False

    seen: list[tuple[int | None, str]] = []
    resolved = 0
    for block in blocks:
        chunk_id = block.get("chunk_id")
        if not chunk_id:
            continue
        page, text = index.get(chunk_id, document_id=question.document_id)
        if text:
            resolved += 1
            seen.append((page if page is not None else block.get("page"), text))
    if not seen:
        return None
    if resolved < len(blocks):
        # Some chunks resolved and some did not. Deciding on a partial view can
        # only produce a false "not retrieved", so refuse.
        return None

    for group_record in question.evidence:
        group = EvidenceGroup.from_dict(group_record)
        if not any(group.satisfied_by(page, text) for page, text in seen):
            return False
    return True


def _retrieved_text(record: dict, question: DatasetQuestion, index: ChunkTextIndex) -> str:
    parts = []
    for block in record.get("evidence") or []:
        chunk_id = block.get("chunk_id")
        if chunk_id:
            _, text = index.get(chunk_id, document_id=question.document_id)
            if text:
                parts.append(text)
    return "\n".join(parts)


def extraction_delivered_the_figure(question: DatasetQuestion, evidence_text: str) -> bool | None:
    """Did the gold figure survive extraction into the text the channels read?

    A mechanical answer to one third of the provenance question. If the gold
    number is literally present in the retrieved chunk text, extraction did its
    job for that figure and the error entered later - which is what turns an
    UNDETERMINED label into a REASONING one without a human having to look.

    Only ever returns True or None. Absence of the figure does NOT prove
    extraction was faulty: a computed answer has no printed figure to find, and
    a multi-hop question needs several. Returning False on that basis would
    reattribute reasoning errors to extraction, and the whole point of Module
    14's tri-state is that a check nobody ran must not masquerade as a check
    that passed.
    """
    if question.answer is None or not evidence_text:
        return None
    needle = question.answer.text.strip()
    if not needle:
        return None
    haystack = evidence_text.replace(" ", "")
    if needle.replace(" ", "") in haystack:
        return False  # `extraction_faulty=False`: the figure was there
    return None


def question_was_parsed_correctly(question: DatasetQuestion, record: dict) -> bool | None:
    """Did question understanding find the metric the question asks about?

    D19 makes this a common-mode failure path: one QuestionSpec feeds both
    channels, so a mis-parse makes both wrong identically and their agreement
    proves nothing. The generator records the metric each question was built
    from, which makes the check mechanical for generated questions and `None`
    for hand-written ones - honest in both cases.
    """
    metric = (question.provenance or {}).get("metric")
    spec = record.get("spec")
    if not metric or not isinstance(spec, dict):
        return None
    searched = " ".join(
        str(sub.get("metric", "")) for sub in (spec.get("sub_questions") or ())
    ).lower()
    if not searched:
        return None
    return metric.lower() in searched


@dataclass(frozen=True)
class AnalysedCase:
    """One graded question for one arm, with its error label if it is wrong."""

    qid: str
    arm: str
    question: str
    correct: bool
    risk_score: float | None
    agreed: bool | None
    ambiguous: bool
    all_evidence_retrieved: bool | None
    label: ErrorLabel | None = None
    predicted: str | None = None
    gold: str | None = None
    answer_source: str | None = None

    @property
    def stratum(self) -> str:
        """The H2 split. `unknown` is a real category, not a bucket to hide in."""
        if self.all_evidence_retrieved is None:
            return "unknown"
        return "retrieval_caused" if not self.all_evidence_retrieved else "reasoning_caused"

    @property
    def both_agree_wrong(self) -> bool:
        return bool(self.agreed) and not self.correct

    def as_dict(self) -> dict:
        return {
            "qid": self.qid,
            "arm": self.arm,
            "question": self.question,
            "correct": self.correct,
            "risk_score": self.risk_score,
            "agreed": self.agreed,
            "ambiguous": self.ambiguous,
            "stratum": self.stratum,
            "predicted": self.predicted,
            "gold": self.gold,
            "answer_source": self.answer_source,
            "label": self.label.as_dict() if self.label else None,
        }


@dataclass
class ErrorAnalysis:
    arm: str
    cases: list[AnalysedCase] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[AnalysedCase]:
        return [c for c in self.cases if not c.correct]

    def by_provenance(self) -> dict[str, int]:
        return dict(
            Counter(
                c.label.provenance.value if c.label else ErrorProvenance.UNDETERMINED.value
                for c in self.errors
            )
        )

    def by_kind(self) -> dict[str, int]:
        return dict(
            Counter(
                c.label.kind.value if c.label else ErrorKind.UNCLASSIFIED.value
                for c in self.errors
            )
        )

    def cross_tabulate(self) -> dict[str, dict[str, int]]:
        """Provenance x kind. The reason Module 14 has two axes at all."""
        table: dict[str, dict[str, int]] = defaultdict(dict)
        for case in self.errors:
            if case.label is None:
                continue
            row = table[case.label.provenance.value]
            row[case.label.kind.value] = row.get(case.label.kind.value, 0) + 1
        return {k: dict(sorted(v.items())) for k, v in sorted(table.items())}

    def strata(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for name in ("retrieval_caused", "reasoning_caused", "unknown"):
            group = [c for c in self.cases if c.stratum == name]
            out[name] = {
                "questions": len(group),
                "errors": sum(1 for c in group if not c.correct),
            }
        return out

    def blind_spot(self) -> dict:
        """Both-agree-wrong: the questions the detector was confident about and
        got wrong. Reported first because it is the honest measure of what the
        method cannot see."""
        cases = [c for c in self.cases if c.both_agree_wrong]
        agreed = [c for c in self.cases if c.agreed]
        return {
            "both_agree_wrong": len(cases),
            "of_agreed": len(agreed),
            "rate_among_agreed": (len(cases) / len(agreed)) if agreed else None,
            "qids": [c.qid for c in cases],
        }

    def ambiguity_false_positives(self, threshold: float) -> dict:
        """D22's own finding: how often ambiguity is mistaken for an error.

        Counted only over ambiguous questions the system answered CORRECTLY -
        a correct answer flagged as risky is unambiguously a false positive. An
        ambiguous question answered wrongly is a different event and is not
        evidence about this failure mode.
        """
        ambiguous = [c for c in self.cases if c.ambiguous and c.risk_score is not None]
        correct = [c for c in ambiguous if c.correct]
        flagged = [c for c in correct if c.risk_score >= threshold]
        return {
            "ambiguous_questions": len(ambiguous),
            "answered_correctly": len(correct),
            "flagged_as_risky": len(flagged),
            "false_positive_rate": (len(flagged) / len(correct)) if correct else None,
            "threshold": threshold,
            "note": (
                "the detector cannot distinguish a defensible alternative "
                "definition from a numerical error; this is the size of that "
                "confusion, measured rather than assumed (D22)"
            ),
        }

    def as_dict(self, *, threshold: float = 0.5, examples: int = 10) -> dict:
        return {
            "arm": self.arm,
            "questions": len(self.cases),
            "errors": len(self.errors),
            "blind_spot": self.blind_spot(),
            "strata": self.strata(),
            "by_provenance": self.by_provenance(),
            "by_kind": self.by_kind(),
            "provenance_by_kind": self.cross_tabulate(),
            "ambiguity_false_positives": self.ambiguity_false_positives(threshold),
            "needs_human_labelling": sum(
                1 for c in self.errors
                if c.label is not None and c.label.confidence.value == "needs_human"
            ),
            "examples": [c.as_dict() for c in self.errors[:examples]],
            "notes": list(self.notes),
        }


def analyse(
    records: list[dict],
    questions: dict[str, DatasetQuestion],
    *,
    arm: str,
    index: ChunkTextIndex | None = None,
    tau=TAU_PRIMARY,
) -> ErrorAnalysis:
    """Grade and label one arm's records.

    `records` are run-artifact rows; `questions` is the gold set keyed by qid.
    A record whose qid has no gold entry is skipped with a note rather than
    counted as an error - a question the dataset does not contain cannot be
    answered wrongly, and counting it would inflate the error rate with the
    dataset's own gaps.
    """
    index = index or ChunkTextIndex()
    analysis = ErrorAnalysis(arm=arm)
    missing = 0

    for record in records:
        if record.get("arm") != arm:
            continue
        qid = record.get("question_id")
        question = questions.get(qid)
        if question is None or question.answer is None:
            missing += 1
            continue

        gold = question.gold_value()
        predicted = _predicted_value(record)
        verdict = judge(predicted, gold, tau=tau)
        retrieved = evidence_was_retrieved(question, record, index)

        label = None
        if not verdict.correct:
            evidence_text = _retrieved_text(record, question, index)
            label = classify(
                predicted,
                gold,
                is_correct=False,
                # Tri-state all the way down. `None` means nobody checked, and
                # Module 14 returns UNDETERMINED rather than defaulting to
                # REASONING - which would quietly inflate the exact stratum H2
                # is tested on. The two helpers above answer mechanically where
                # they can and return None where they cannot.
                all_evidence_retrieved=retrieved,
                extraction_faulty=extraction_delivered_the_figure(question, evidence_text),
                question_parsed_correctly=question_was_parsed_correctly(question, record),
                definition_ambiguous=question.ambiguous,
            )

        analysis.cases.append(
            AnalysedCase(
                qid=qid,
                arm=arm,
                question=question.question,
                correct=verdict.correct,
                risk_score=record.get("risk_score"),
                agreed=record.get("agreed"),
                ambiguous=question.ambiguous,
                all_evidence_retrieved=retrieved,
                label=label,
                predicted=record.get("answer"),
                gold=question.answer.text,
                answer_source=record.get("answer_source"),
            )
        )

    if missing:
        analysis.notes.append(
            f"{missing} record(s) had no gold answer and were excluded from the "
            "error analysis; a question the dataset does not contain cannot be "
            "answered wrongly"
        )
    unknown = sum(1 for c in analysis.cases if c.stratum == "unknown")
    if unknown:
        analysis.notes.append(
            f"{unknown} question(s) could not be assigned to a retrieval/reasoning "
            "stratum (no gold evidence spans, or chunk text absent from the "
            "processed cache). Reported as `unknown`, never folded into "
            "reasoning-caused - that direction would make H2 look supported"
        )
    return analysis


def _predicted_value(record: dict):
    """Re-parse the arm's answer through the project's parser.

    Reads `answer_text` - the round-trippable form "INR 3956 crore" - rather
    than a stored canonical number. Two reasons: the correctness label is then
    produced by exactly one code path, and a parser fix re-grades the whole
    archive instead of leaving two generations of labels side by side.
    """
    from backend.core.financial_value import parse_financial_value

    text = record.get("answer_text") or record.get("answer")
    return parse_financial_value(text) if text else None
