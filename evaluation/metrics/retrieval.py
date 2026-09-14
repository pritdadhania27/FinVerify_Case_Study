"""Retrieval metrics against gold evidence spans (spec §14, EVALUATION.md §4).

Why this module exists at all: after Module 6 the pipeline ran end to end and the
rankings *looked* bad - asking "How much were trade payables?" did not put the
balance sheet at rank 1. "Looked bad" is not a measurement, and tuning against an
impression is how a retrieval component gets optimised into looking good on the
three queries someone happened to type. So the numbers come first.

**Gold spans are located, not quoted.** EVALUATION.md §4 originally defined
overlap as ">= 50% of the gold span's character offsets in the extracted text".
That cannot be computed here: a table chunk is *re-rendered* as a pipe table from
Camelot cells, so it is not a substring of the page text and has no character
offsets into it. Any offset-based rule would therefore have to be computed
against the chunker's own output, which makes the gold labels a function of the
system under test - circular. A span is instead identified by

    (page number in the source PDF, literal anchor strings)

and is satisfied by a retrieved chunk when the chunk comes from that page and
contains every anchor. Both halves are checkable against the PDF independently of
any chunking decision, and `scripts/evaluate_retrieval.py --validate-gold`
verifies every anchor really appears on the page it claims. (Decision D18.)

**Groups, because evidence is not a flat list.** A figure can legitimately appear
in two places - Infosys' trade payables are on the consolidated balance sheet
(p.12) *and* in note 2.14 (p.51) - while a growth question genuinely needs two
different figures. Flattening both into one list of required spans would score a
correct retrieval as a 50% miss in the first case. So each question carries a
list of evidence *groups*: every group must be covered (all-of), and any span
within a group covers it (any-of).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import mean

__all__ = [
    "degroup",
    "normalise",
    "EvidenceSpan",
    "EvidenceGroup",
    "GoldQuestion",
    "RetrievedItem",
    "QuestionScore",
    "score_question",
    "aggregate",
]

# `\s` already covers the non-breaking and thin spaces that appear inside
# Indian-grouped numerals after a line wrap, so only the dashes need mapping:
# Camelot emits a hyphen where the PDF set an en-dash or a true minus sign.
_WHITESPACE = re.compile(r"\s+")
_TRANSLATE = str.maketrans({"–": "-", "—": "-", "−": "-"})


def normalise(text: str) -> str:
    """Casefold and collapse whitespace so anchors survive re-rendering.

    Deliberately does NOT strip commas. `degroup` below is where digit grouping
    is handled, and it is deliberately narrower than "strip every comma".
    """
    return _WHITESPACE.sub(" ", text.translate(_TRANSLATE)).strip().lower()


# A comma sitting inside a run of digits in CANONICAL grouping - Western
# (1,234,567) or Indian (12,34,567), both of which occur in this corpus. The
# lookahead requires the group to be exactly 2 or 3 digits and to end at a
# non-digit, so "1,2232" is left alone: that is mangled grouping, and the whole
# point of not stripping commas blindly is to keep catching it.
_GROUPED_DIGITS = re.compile(r"(?<=\d),(?=\d{2,3}(?:\D|$))")


def degroup(text: str) -> str:
    """Render grouped numerals as bare digits: "12,232" -> "12232".

    Gold anchors store the bare numeral because that is what the answer carries;
    filings print the grouped one. Comparing the two as strings meant a numeric
    anchor could NEVER match, and `EvidenceSpan.satisfied_by` requires ALL
    anchors - so a group whose anchors were ("cost of technical
    sub-contractors", "12232") was unsatisfiable however well retrieval had
    done. On the first complete campaign that put `all_evidence_retrieved` at 1
    of 45 when the true figure was 13, and emptied the reasoning stratum H2 is
    tested on.
    """
    return _GROUPED_DIGITS.sub("", text)


@dataclass(frozen=True)
class EvidenceSpan:
    """One acceptable location for a required piece of evidence."""

    page: int
    anchors: tuple[str, ...]

    def satisfied_by(self, page: int, text: str) -> bool:
        if page != self.page:
            return False
        haystack = normalise(text)
        # Compared twice: once literally, once with canonical digit grouping
        # removed from BOTH sides, so a gold "12232" matches a printed "12,232"
        # without a mangled "1,2232" being accepted for either.
        degrouped = degroup(haystack)
        return all(
            normalise(anchor) in haystack or degroup(normalise(anchor)) in degrouped
            for anchor in self.anchors
        )

    @classmethod
    def from_dict(cls, record: dict) -> EvidenceSpan:
        anchors = tuple(record["anchors"])
        if not anchors:
            raise ValueError(f"evidence span on page {record.get('page')} has no anchors")
        return cls(page=int(record["page"]), anchors=anchors)


@dataclass(frozen=True)
class EvidenceGroup:
    """One thing the question needs to know, plus every place it may be found."""

    group_id: str
    any_of: tuple[EvidenceSpan, ...]
    note: str = ""

    def satisfied_by(self, page: int, text: str) -> bool:
        return any(span.satisfied_by(page, text) for span in self.any_of)

    @classmethod
    def from_dict(cls, record: dict) -> EvidenceGroup:
        spans = tuple(EvidenceSpan.from_dict(s) for s in record["any_of"])
        if not spans:
            raise ValueError(f"evidence group {record.get('group_id')!r} has no spans")
        return cls(group_id=record["group_id"], any_of=spans, note=record.get("note", ""))


@dataclass(frozen=True)
class GoldQuestion:
    qid: str
    question: str
    evidence: tuple[EvidenceGroup, ...]
    question_type: str = "lookup"
    answer_note: str = ""

    @classmethod
    def from_dict(cls, record: dict) -> GoldQuestion:
        groups = tuple(EvidenceGroup.from_dict(g) for g in record["evidence"])
        if not groups:
            raise ValueError(f"question {record.get('qid')!r} has no evidence groups")
        return cls(
            qid=record["qid"],
            question=record["question"],
            evidence=groups,
            question_type=record.get("question_type", "lookup"),
            answer_note=record.get("answer_note", ""),
        )


@dataclass(frozen=True)
class RetrievedItem:
    """The only two things a metric needs from a retrieved chunk, plus its id."""

    chunk_id: str
    page: int
    text: str


@dataclass(frozen=True)
class QuestionScore:
    qid: str
    k: int
    recall: float
    precision: float
    reciprocal_rank: float
    all_evidence_retrieved: bool
    covered_groups: tuple[str, ...]
    missed_groups: tuple[str, ...]
    first_relevant_rank: int | None
    retrieved: int


def score_question(question: GoldQuestion, retrieved: list[RetrievedItem], k: int) -> QuestionScore:
    """Score one question's top-K.

    An empty result list scores zero on everything rather than raising: a
    retriever that returns nothing has failed the question, and dropping it from
    the average would hide exactly that failure.
    """
    top = retrieved[:k]

    covered: list[str] = []
    for group in question.evidence:
        if any(group.satisfied_by(item.page, item.text) for item in top):
            covered.append(group.group_id)
    covered_set = set(covered)
    missed = tuple(g.group_id for g in question.evidence if g.group_id not in covered_set)

    relevant_ranks = [
        rank
        for rank, item in enumerate(top, start=1)
        if any(g.satisfied_by(item.page, item.text) for g in question.evidence)
    ]

    return QuestionScore(
        qid=question.qid,
        k=k,
        recall=len(covered) / len(question.evidence),
        # Denominator is what was actually returned, not k: a retriever that
        # returns 3 perfect chunks when asked for 10 has 100% precision, and
        # dividing by 10 would call that 30%.
        precision=(sum(1 for _ in relevant_ranks) / len(top)) if top else 0.0,
        reciprocal_rank=1.0 / relevant_ranks[0] if relevant_ranks else 0.0,
        all_evidence_retrieved=not missed,
        covered_groups=tuple(covered),
        missed_groups=missed,
        first_relevant_rank=relevant_ranks[0] if relevant_ranks else None,
        retrieved=len(top),
    )


def aggregate(scores: list[QuestionScore]) -> dict:
    """Macro averages over questions.

    Macro, not micro: every question weighs the same regardless of how many
    evidence spans it needs, so a handful of many-span questions cannot dominate
    the headline number.
    """
    if not scores:
        return {
            "questions": 0,
            "recall_at_k": 0.0,
            "precision_at_k": 0.0,
            "mrr": 0.0,
            "evidence_retrieval_accuracy": 0.0,
        }
    return {
        "questions": len(scores),
        "recall_at_k": mean(s.recall for s in scores),
        "precision_at_k": mean(s.precision for s in scores),
        "mrr": mean(s.reciprocal_rank for s in scores),
        "evidence_retrieval_accuracy": mean(
            1.0 if s.all_evidence_retrieved else 0.0 for s in scores
        ),
    }
