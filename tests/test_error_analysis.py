"""Tests for error analysis and the results report (spec Modules 22, 25).

The H2 stratifier is the piece with the most dangerous failure mode in the whole
evaluation. It decides which errors count as retrieval-caused, and every way it
can be wrong pushes in the same direction: misclassify reasoning errors as
retrieval-caused and the reasoning stratum gets cleaner, which is exactly the
result H2 predicts. A stratifier that fails safe has to fail towards `unknown`,
never towards either stratum, and that is what these tests pin.
"""

from __future__ import annotations

import json

import pytest

from evaluation.dataset import (
    Dataset,
    DatasetQuestion,
    GoldAnswer,
    ValidationRecord,
    ValidationStatus,
)
from evaluation.error_analysis import ChunkTextIndex, analyse, evidence_was_retrieved
from evaluation.report import MIN_STRATUM_FOR_A_CLAIM, build_report, choose_operating_threshold


def gold(qid: str, **kw) -> DatasetQuestion:
    base = dict(
        question=f"what were trade payables? ({qid})",
        company="Infosys Limited",
        fiscal_year="2023-24",
        document_id="doc1",
        answer=GoldAnswer(text="3,956", unit="INR crore", source_page=12),
        definition="as reported on the consolidated balance sheet",
        evidence=(
            {"group_id": "tp", "any_of": [{"page": 12, "anchors": ["Trade payables", "3,956"]}]},
        ),
        validation=ValidationRecord(
            status=ValidationStatus.VALIDATED, validator="owner", validated_on="2026-08-26"
        ),
    )
    base.update(kw)
    return DatasetQuestion(qid=qid, **base)


def chunk_cache(tmp_path, entries):
    path = tmp_path / "doc1.chunks.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for chunk_id, page, text in entries:
            fh.write(json.dumps({"chunk_id": chunk_id, "page": page, "text": text}) + "\n")
    return ChunkTextIndex(tmp_path)


def row(qid: str, arm: str = "A", **kw) -> dict:
    base = {
        "question_id": qid,
        "arm": arm,
        "question": "q",
        "answer": "3956",
        "answer_text": "INR 3956 crore",
        "answer_canonical": "39560000000",
        "answer_source": "natural",
        "abstained": False,
        "risk_score": 0.2,
        "agreed": True,
        "evidence": [{"ref": "E1", "page": 12, "chunk_id": "c1", "citation": "p.12"}],
        "natural": {"available": True},
        "program": {"available": True},
        "latency_seconds": 1.0,
        "verification": {"triggered": False},
    }
    base.update(kw)
    return base


class TestTheStratifierFailsToUnknown:
    """Every wrong answer here would make H2 look better than it is."""

    def test_evidence_present_is_reasoning_caused(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        assert evidence_was_retrieved(gold("Q1"), row("Q1"), index) is True

    def test_evidence_absent_is_retrieval_caused(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Goodwill | 7,248 |")])
        assert evidence_was_retrieved(gold("Q1"), row("Q1"), index) is False

    def test_no_evidence_at_all_is_retrieval_caused(self, tmp_path):
        index = chunk_cache(tmp_path, [])
        assert evidence_was_retrieved(gold("Q1"), row("Q1", evidence=[]), index) is False

    def test_a_missing_chunk_cache_is_unknown_not_a_retrieval_failure(self, tmp_path):
        """The dangerous case. A cache that is simply absent would otherwise
        reclassify every reasoning error as retrieval-caused, cleaning up the
        exact stratum H2 predicts should be clean."""
        index = chunk_cache(tmp_path, [])
        assert evidence_was_retrieved(gold("Q1"), row("Q1"), index) is None

    def test_a_partially_resolved_evidence_set_is_unknown(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Goodwill | 7,248 |")])
        record = row("Q1", evidence=[
            {"ref": "E1", "page": 12, "chunk_id": "c1"},
            {"ref": "E2", "page": 51, "chunk_id": "missing"},
        ])
        assert evidence_was_retrieved(gold("Q1"), record, index) is None

    def test_a_question_with_no_gold_spans_is_unknown(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "anything")])
        assert evidence_was_retrieved(gold("Q1", evidence=()), row("Q1"), index) is None

    def test_any_of_semantics_accept_the_second_location(self, tmp_path):
        """A figure printed on four pages must not penalise finding it on one."""
        question = gold("Q1", evidence=({
            "group_id": "tp",
            "any_of": [
                {"page": 12, "anchors": ["Trade payables", "3,956"]},
                {"page": 51, "anchors": ["Trade payables", "3,956"]},
            ],
        },))
        index = chunk_cache(tmp_path, [("c1", 51, "| Trade payables | 3,956 |")])
        record = row("Q1", evidence=[{"ref": "E1", "page": 51, "chunk_id": "c1"}])
        assert evidence_was_retrieved(question, record, index) is True

    def test_all_of_semantics_require_every_group(self, tmp_path):
        question = gold("Q1", evidence=(
            {"group_id": "a", "any_of": [{"page": 12, "anchors": ["Trade payables"]}]},
            {"group_id": "b", "any_of": [{"page": 20, "anchors": ["Total equity"]}]},
        ))
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        assert evidence_was_retrieved(question, row("Q1"), index) is False


class TestAnalysis:
    def test_a_correct_answer_carries_no_error_label(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        analysis = analyse([row("Q1")], {"Q1": gold("Q1")}, arm="A", index=index)
        assert analysis.cases[0].correct is True
        assert analysis.cases[0].label is None

    def test_a_scale_error_is_labelled_on_both_axes(self, tmp_path):
        """Provenance reaches `reasoning` only because the checks are mechanical.

        The gold figure "3,956" is literally present in the retrieved text, so
        extraction demonstrably delivered it, and the recorded metric matches
        what question understanding searched for. Without either check this
        would - correctly - stay UNDETERMINED.
        """
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        question = gold("Q1", provenance={"metric": "trade payables"})
        record = row(
            "Q1",
            answer_text="INR 3956 million",
            spec={"sub_questions": [{"metric": "trade payables"}]},
        )
        label = analyse([record], {"Q1": question}, arm="A", index=index).cases[0].label
        assert label is not None
        assert label.kind.value == "wrong_scale"
        assert label.provenance.value == "reasoning"

    def test_provenance_stays_undetermined_when_nothing_was_checked(self, tmp_path):
        """A hand-written question records no metric, so the parse check cannot
        run. UNDETERMINED is the honest label; REASONING would be a guess in the
        one direction that flatters H2."""
        index = chunk_cache(tmp_path, [("c1", 12, "| Goodwill | 7,248 |")])
        question = gold("Q1", evidence=(
            {"group_id": "g", "any_of": [{"page": 12, "anchors": ["Goodwill"]}]},
        ))
        record = row("Q1", answer_text="INR 9999 crore")
        label = analyse([record], {"Q1": question}, arm="A", index=index).cases[0].label
        assert label.provenance.value == "undetermined"
        assert label.confidence.value == "needs_human"

    def test_an_unresolvable_stratum_never_becomes_reasoning(self, tmp_path):
        """Module 14's tri-state, all the way through to the report.

        With no chunk cache the H2 stratum is `unknown`. The taxonomy is handed
        `all_evidence_retrieved=None` and must not resolve it to REASONING - the
        one direction that would make H2 look supported.
        """
        index = chunk_cache(tmp_path, [])
        record = row("Q1", answer_text="INR 9999 crore")
        analysis = analyse([record], {"Q1": gold("Q1")}, arm="A", index=index)
        assert analysis.cases[0].stratum == "unknown"
        assert analysis.cases[0].label.provenance.value != "reasoning"

    def test_records_with_no_gold_are_excluded_with_a_note(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "x")])
        analysis = analyse([row("Q9")], {"Q1": gold("Q1")}, arm="A", index=index)
        assert analysis.cases == []
        assert any("no gold answer" in n for n in analysis.notes)

    def test_the_blind_spot_is_counted(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        records = [
            row("Q1", answer_text="INR 9999 crore", agreed=True),
            row("Q2", answer_text="INR 3956 crore", agreed=True),
        ]
        questions = {"Q1": gold("Q1"), "Q2": gold("Q2")}
        blind = analyse(records, questions, arm="A", index=index).blind_spot()
        assert blind["both_agree_wrong"] == 1
        assert blind["of_agreed"] == 2
        assert blind["rate_among_agreed"] == pytest.approx(0.5)

    def test_ambiguity_false_positives_count_only_correct_answers(self, tmp_path):
        """A wrong answer to an ambiguous question is a different event."""
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        questions = {
            "Q1": gold("Q1", ambiguous=True, definition=""),
            "Q2": gold("Q2", ambiguous=True, definition=""),
        }
        records = [
            row("Q1", risk_score=0.9),                                # correct, flagged
            row("Q2", risk_score=0.9, answer_text="INR 1 crore"),     # wrong, flagged
        ]
        result = analyse(records, questions, arm="A", index=index).ambiguity_false_positives(0.5)
        assert result["ambiguous_questions"] == 2
        assert result["answered_correctly"] == 1
        assert result["false_positive_rate"] == pytest.approx(1.0)

    def test_the_two_axes_cross_tabulate(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        record = row("Q1", answer_text="INR 3956 million")
        question = gold("Q1", provenance={"metric": "trade payables"})
        record = row(
            "Q1",
            answer_text="INR 3956 million",
            spec={"sub_questions": [{"metric": "trade payables"}]},
        )
        table = analyse([record], {"Q1": question}, arm="A", index=index).cross_tabulate()
        assert table == {"reasoning": {"wrong_scale": 1}}


class TestReport:
    def _corpus(self, tmp_path, n: int = 24):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        questions = {f"Q{i:02d}": gold(f"Q{i:02d}") for i in range(n)}
        records = []
        for i in range(n):
            wrong = i % 3 == 0
            records.append(
                row(
                    f"Q{i:02d}",
                    arm="A",
                    answer_text="INR 9999 crore" if wrong else "INR 3956 crore",
                    risk_score=0.9 if wrong else 0.1,
                    agreed=not wrong,
                )
            )
            records.append(
                row(
                    f"Q{i:02d}",
                    arm="B5",
                    answer_text="INR 9999 crore" if wrong else "INR 3956 crore",
                    risk_score=0.5,
                    agreed=None,
                )
            )
        return records, questions, index

    def test_the_report_produces_qa_and_detection_for_a_detector_arm(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        report = build_report(records, questions, resamples=200, chunk_index=index)
        arm = report["arms"]["A"]
        assert arm["qa"]["n"] == 24
        assert arm["detection"]["auroc"] == 1.0
        assert arm["detection"]["auroc_ci"]["ci_low"] is not None

    def test_a_non_detector_arm_is_absent_from_the_detection_table(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        for record in records:
            if record["arm"] == "B5":
                record["arm"] = "B2"
        report = build_report(records, questions, resamples=100, chunk_index=index)
        assert report["arms"]["B2"]["detection"] is None
        assert any("QA baseline only" in n for n in report["arms"]["B2"]["notes"])

    def test_the_operating_threshold_row_is_omitted_when_none_was_frozen(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        report = build_report(records, questions, resamples=100, chunk_index=index)
        assert report["arms"]["A"]["detection"]["at_threshold"] is None

    def test_h1_is_tested_paired_and_reports_an_interval(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        report = build_report(records, questions, resamples=300, chunk_index=index)
        h1 = report["hypotheses"]["verdicts"]["H1"]
        assert h1["testable"] is True
        assert h1["comparison"] == "AUROC(A) - AUROC(B5)"
        assert h1["interval"]["point"] > 0

    def test_an_untestable_hypothesis_says_so_rather_than_vanishing(self, tmp_path):
        records, questions, index = self._corpus(tmp_path, n=4)
        report = build_report(records, questions, resamples=100, chunk_index=index)
        h1 = report["hypotheses"]["verdicts"]["H1"]
        assert h1["testable"] is False
        assert "not enough" in h1["reason"]

    def test_a_thin_stratum_is_labelled_not_quietly_reported(self, tmp_path):
        records, questions, index = self._corpus(tmp_path, n=6)
        report = build_report(records, questions, resamples=100, chunk_index=index)
        detection = report["arms"]["A"]["detection"]
        assert detection["positives"] < MIN_STRATUM_FOR_A_CLAIM
        assert any("too few" in n for n in detection["notes"])

    def test_failed_rows_are_excluded_from_metrics_but_counted(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        records.append({"question_id": "Q00", "arm": "A", "error": "boom", "answer": None})
        report = build_report(records, questions, resamples=100, chunk_index=index)
        assert any("failed with an error" in n for n in report["arms"]["A"]["notes"])

    def test_the_contingency_table_is_reported(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        report = build_report(records, questions, resamples=100, chunk_index=index)
        cells = report["arms"]["A"]["agreement_x_correctness"]
        assert sum(cells.values()) == 24

    def test_holm_correction_covers_every_tested_hypothesis(self, tmp_path):
        records, questions, index = self._corpus(tmp_path)
        report = build_report(records, questions, resamples=300, chunk_index=index)
        correction = report["hypotheses"]["family_wise_correction"]
        tested = {
            name for name, v in report["hypotheses"]["verdicts"].items()
            if v.get("testable") and "p_value" in v
        }
        assert set(correction) == tested


class TestThresholdSelectionDiscipline:
    def test_a_threshold_can_be_chosen_from_validation_records(self, tmp_path):
        records, questions, index = self._records(tmp_path)
        threshold = choose_operating_threshold(records, questions)
        assert 0.0 <= threshold <= 1.0

    def test_selection_from_an_arm_with_no_scores_is_an_error(self, tmp_path):
        records, questions, _ = self._records(tmp_path)
        for record in records:
            record["risk_score"] = None
        with pytest.raises(ValueError):
            choose_operating_threshold(records, questions)

    def _records(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        questions = {f"Q{i}": gold(f"Q{i}") for i in range(6)}
        records = [
            row(
                f"Q{i}",
                answer_text="INR 9999 crore" if i % 2 else "INR 3956 crore",
                risk_score=0.8 if i % 2 else 0.2,
            )
            for i in range(6)
        ]
        return records, questions, index


class TestFailureCases:
    def test_an_empty_campaign_produces_an_empty_report(self):
        report = build_report([], {}, resamples=10)
        assert report["arms"] == {}

    def test_a_dataset_with_no_questions_does_not_crash(self, tmp_path):
        index = chunk_cache(tmp_path, [])
        report = build_report([row("Q1")], {}, resamples=10, chunk_index=index)
        assert report["arms"]["A"]["n"] == 0

    def test_an_abstention_is_wrong_and_labelled_unsupported(self, tmp_path):
        index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
        record = row("Q1", answer=None, answer_text=None, abstained=True)
        analysis = analyse([record], {"Q1": gold("Q1")}, arm="A", index=index)
        assert analysis.cases[0].correct is False
        assert analysis.cases[0].label.kind.value == "unsupported_claim"


def test_dataset_round_trip_feeds_the_analysis(tmp_path):
    """The dataset and the analysis agree on what a gold answer is."""
    path = tmp_path / "d.json"
    Dataset("s", "v", (gold("Q1"),)).write(path)
    from evaluation.dataset import load_dataset

    reloaded = load_dataset(path)
    index = chunk_cache(tmp_path, [("c1", 12, "| Trade payables | 3,956 |")])
    analysis = analyse([row("Q1")], {"Q1": reloaded.questions[0]}, arm="A", index=index)
    assert analysis.cases[0].correct is True
