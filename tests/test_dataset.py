"""Tests for FinVerify-IND (spec Module 26, decisions D22 / D23).

Everything empirical rests on these gold answers, and the failure modes here are
shortcuts that look like progress: an unvalidated candidate used as gold, a label
edited after seeing a result, the sealed split opened during development. Each is
prevented structurally, and these tests are what keep the structure in place.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal

import pytest

from evaluation.dataset import (
    Dataset,
    DatasetQuestion,
    GoldAnswer,
    SealedSplitError,
    ValidationRecord,
    ValidationStatus,
    build_split,
    load_dataset,
    manifest,
    stratify,
)
from evaluation.validation import (
    export_for_validation,
    import_validations,
    intra_annotator_agreement,
    select_double_pass_subset,
)


def fill_worksheet(path, qid: str, *, verdict: str, corrected: str = "", notes: str = ""):
    """Edit the worksheet the way a spreadsheet would.

    Through the csv module rather than string replacement: an Indian-grouped
    figure like "3,865" contains the delimiter, and a naive edit silently splits
    it into two fields. That is the same class of bug the parser exists to catch.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0]) if rows else []
    for row in rows:
        if row["qid"] == qid:
            row["verdict"] = verdict
            row["corrected_answer"] = corrected
            row["notes"] = notes
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def question(qid: str, **kw) -> DatasetQuestion:
    base = dict(
        question=f"what were trade payables? ({qid})",
        company="Infosys Limited",
        fiscal_year="2023-24",
        document_id="f356fc75d6d63fa3",
        answer=GoldAnswer(text="3,956", unit="INR crore", source_page=12),
        definition="trade payables as reported on the consolidated balance sheet",
        evidence=({"group_id": "tp", "any_of": [{"page": 12, "anchors": ["Trade payables"]}]},),
    )
    base.update(kw)
    return DatasetQuestion(qid=qid, **base)


def validated(qid: str, **kw) -> DatasetQuestion:
    kw.setdefault(
        "validation",
        ValidationRecord(
            status=ValidationStatus.VALIDATED, validator="owner", validated_on="2026-08-26"
        ),
    )
    return question(qid, **kw)


class TestCandidatesAreNotGold:
    """Spec §16. The single most tempting shortcut in the project."""

    def test_a_pending_question_is_not_usable_as_gold(self):
        assert question("Q1").usable_as_gold is False

    def test_build_split_refuses_pending_questions_by_default(self):
        dataset = Dataset("s", "v", (question("Q1", split="train"),))
        assert build_split(dataset, "train") == ()

    def test_a_validated_question_is_served(self):
        dataset = Dataset("s", "v", (validated("Q1", split="train"),))
        assert len(build_split(dataset, "train")) == 1

    def test_validated_without_a_named_validator_is_a_malformed_record(self):
        bad = question("Q1", validation=ValidationRecord(status=ValidationStatus.VALIDATED))
        assert any("no validator named" in p for p in bad.validation_problems())

    def test_a_rejected_question_never_becomes_gold(self):
        rejected = question(
            "Q1", split="train",
            validation=ValidationRecord(
                status=ValidationStatus.REJECTED, validator="owner", validated_on="2026-08-26"
            ),
        )
        assert build_split(Dataset("s", "v", (rejected,)), "train") == ()


class TestTestSetIsSealed:
    def test_the_test_split_is_refused_without_the_environment_flag(self, monkeypatch):
        monkeypatch.delenv("FINVERIFY_ALLOW_TEST", raising=False)
        dataset = Dataset("s", "v", (validated("Q1", split="test"),))
        with pytest.raises(SealedSplitError):
            build_split(dataset, "test", reason="final evaluation")

    def test_access_requires_a_stated_reason(self, monkeypatch):
        monkeypatch.setenv("FINVERIFY_ALLOW_TEST", "1")
        dataset = Dataset("s", "v", (validated("Q1", split="test"),))
        with pytest.raises(SealedSplitError):
            build_split(dataset, "test")

    def test_the_test_split_may_never_be_run_against_unvalidated_gold(self, monkeypatch):
        monkeypatch.setenv("FINVERIFY_ALLOW_TEST", "1")
        dataset = Dataset("s", "v", (question("Q1", split="test"),))
        with pytest.raises(SealedSplitError):
            build_split(dataset, "test", reason="x", include_pending=True)

    def test_access_is_logged(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FINVERIFY_ALLOW_TEST", "1")
        log = tmp_path / "test_set_access.log"
        monkeypatch.setattr("evaluation.dataset.TEST_ACCESS_LOG", log)
        dataset = Dataset("s", "v", (validated("Q1", split="test"),))
        build_split(dataset, "test", reason="frozen-methodology final run")
        entry = json.loads(log.read_text(encoding="utf-8").strip())
        assert entry["reason"] == "frozen-methodology final run"
        assert entry["questions"] == 1

    def test_the_other_splits_need_no_ceremony(self):
        dataset = Dataset("s", "v", (validated("Q1", split="validation"),))
        assert len(build_split(dataset, "validation")) == 1


class TestAmbiguityIsASubsetNotContamination:
    """D22 / RX-007."""

    def test_ambiguous_questions_are_excluded_by_default(self):
        dataset = Dataset(
            "s", "v",
            (
                validated("Q1", split="train"),
                validated("Q2", split="train", ambiguous=True, definition=""),
            ),
        )
        assert len(build_split(dataset, "train")) == 1
        assert len(build_split(dataset, "train", include_ambiguous=True)) == 2

    def test_a_main_set_question_must_pin_its_definition(self):
        bare = question("Q1", definition="")
        assert any("no pinned definition" in p for p in bare.validation_problems())

    def test_an_ambiguity_question_must_not_pin_one(self):
        """A pinned definition defeats the purpose of the subset."""
        contradictory = question("Q1", ambiguous=True, definition="closing equity")
        assert any("defeats the purpose" in p for p in contradictory.validation_problems())

    def test_the_two_subsets_are_addressable_separately(self):
        dataset = Dataset(
            "s", "v",
            (validated("Q1"), validated("Q2", ambiguous=True, definition="")),
        )
        assert [q.qid for q in dataset.main_set()] == ["Q1"]
        assert [q.qid for q in dataset.ambiguity_subset()] == ["Q2"]


class TestGoldParsesThroughTheSamePathAsPredictions:
    def test_a_crore_gold_answer_canonicalises(self):
        answer = GoldAnswer(text="3,956", unit="INR crore")
        assert answer.canonical() == Decimal("39560000000")

    def test_a_percentage_gold_answer_canonicalises_to_a_fraction(self):
        assert GoldAnswer(text="25", unit="percent").canonical() == Decimal("0.25")

    def test_an_unparseable_gold_answer_is_caught_by_the_audit(self):
        broken = question("Q1", answer=GoldAnswer(text="about a third", unit=""))
        assert any("does not parse" in p for p in broken.validation_problems())

    def test_a_question_with_no_evidence_cannot_be_traced(self):
        problems = question("Q1", evidence=()).validation_problems()
        assert any("no evidence groups" in p for p in problems)


class TestValidationRoundTrip:
    def test_export_then_import_marks_the_question_validated(self, tmp_path):
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)

        text = csv_path.read_text(encoding="utf-8")
        csv_path.write_text(text.replace(",,,,\n", ",validated,,,\n"), encoding="utf-8")

        updated, report = import_validations(dataset, csv_path, validator="owner")
        assert report.validated == 1
        assert updated.questions[0].usable_as_gold is True
        assert updated.questions[0].validation.validator == "owner"

    def test_a_correction_preserves_what_it_replaced(self, tmp_path):
        """ENGINEERING_RULES.md forbids altering gold to improve a score. That rule is only
        auditable if every alteration leaves a record of the previous value."""
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        fill_worksheet(csv_path, "Q1", verdict="validated", corrected="3,865")
        updated, report = import_validations(dataset, csv_path, validator="owner")
        assert updated.questions[0].answer.text == "3,865"
        assert updated.questions[0].provenance["generated_answer"]["text"] == "3,956"
        assert report.corrections[0]["from"] == "3,956"
        assert report.corrections[0]["to"] == "3,865"

    def test_a_correction_invalidates_the_anchors_that_cited_the_old_answer(self, tmp_path):
        """RX-042: the import wrote the corrected answer and left the anchors
        pointing at the figure the validator had just rejected.

        10 of 11 affected rows kept anchors citing their own rejected candidate,
        and nothing noticed for weeks because every metric reads `answer` while
        only the retrieval-side machinery reads `evidence`. It surfaced when the
        oracle arm resolved evidence THROUGH those anchors and handed both
        channels the wrong chunk.
        """
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        assert dataset.questions[0].evidence, "the fixture must start with anchors"

        fill_worksheet(csv_path, "Q1", verdict="validated", corrected="3,865")
        updated, report = import_validations(dataset, csv_path, validator="owner")

        assert updated.questions[0].evidence == (), (
            "anchors that located the rejected figure must not survive the "
            "correction - a stale anchor is the one option that silently "
            "produces a wrong answer"
        )
        assert report.anchors_invalidated == ["Q1"]

    def test_the_superseded_anchors_are_kept_not_deleted(self, tmp_path):
        """Same reason `generated_answer` is kept: an alteration with no record
        of what it replaced cannot be audited, and re-deriving the anchors later
        needs to know where the old ones pointed."""
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        original = list(dataset.questions[0].evidence)

        fill_worksheet(csv_path, "Q1", verdict="validated", corrected="3,865")
        updated, _ = import_validations(dataset, csv_path, validator="owner")

        superseded = updated.questions[0].provenance["superseded_evidence"]
        assert superseded["evidence"] == original
        assert "re-derived" in superseded["reason"]

    def test_an_uncorrected_answer_keeps_its_anchors(self, tmp_path):
        """Only a CORRECTION invalidates them. Confirming the candidate leaves
        the evidence exactly as it was."""
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        original = dataset.questions[0].evidence

        fill_worksheet(csv_path, "Q1", verdict="validated")
        updated, report = import_validations(dataset, csv_path, validator="owner")

        assert updated.questions[0].evidence == original
        assert report.anchors_invalidated == []

    def test_a_blank_verdict_leaves_the_question_pending(self, tmp_path):
        """A partially completed worksheet must be submittable."""
        dataset = Dataset("s", "v", (question("Q1"), question("Q2")))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        updated, report = import_validations(dataset, csv_path, validator="owner")
        assert report.untouched == 2
        assert all(q.validation.status is ValidationStatus.PENDING for q in updated.questions)

    def test_an_unparseable_correction_is_recorded_but_not_trusted(self, tmp_path):
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        # Genuinely unparseable. "roughly 4000" is NOT: the parser recovers 4000
        # from it, which is right, and would have made this test vacuous.
        fill_worksheet(csv_path, "Q1", verdict="validated", corrected="about a third")
        updated, report = import_validations(dataset, csv_path, validator="owner")
        assert updated.questions[0].validation.status is ValidationStatus.NEEDS_REVIEW
        assert any("does not parse" in p for p in report.problems)

    def test_an_unattributed_import_is_refused(self, tmp_path):
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        with pytest.raises(ValueError):
            import_validations(dataset, csv_path, validator="   ")

    def test_rows_for_unknown_questions_are_reported_not_silently_dropped(self, tmp_path):
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation((question("Q1"), question("Q9")), csv_path)
        _, report = import_validations(dataset, csv_path, validator="owner")
        assert report.unknown_qids == ["Q9"]

    def test_an_unrecognised_verdict_does_not_become_a_default(self, tmp_path):
        dataset = Dataset("s", "v", (question("Q1"),))
        csv_path = tmp_path / "w.csv"
        export_for_validation(dataset.questions, csv_path)
        csv_path.write_text(
            csv_path.read_text(encoding="utf-8").replace(",,,,\n", ",looks fine,,,\n"),
            encoding="utf-8",
        )
        updated, report = import_validations(dataset, csv_path, validator="owner")
        assert updated.questions[0].validation.status is ValidationStatus.PENDING
        assert any("unrecognised verdict" in p for p in report.problems)


class TestDoublePass:
    """D23: intra-annotator consistency, and the word it must never be called."""

    def test_the_subset_is_seeded_not_chosen(self):
        questions = tuple(validated(f"Q{i:02d}") for i in range(20))
        a = select_double_pass_subset(questions, fraction=0.2, seed=1)
        b = select_double_pass_subset(questions, fraction=0.2, seed=1)
        c = select_double_pass_subset(questions, fraction=0.2, seed=2)
        assert [q.qid for q in a] == [q.qid for q in b]
        assert [q.qid for q in a] != [q.qid for q in c]

    def test_the_subset_is_the_requested_fraction(self):
        questions = tuple(validated(f"Q{i:02d}") for i in range(20))
        assert len(select_double_pass_subset(questions, fraction=0.2)) == 4

    def test_a_second_pass_records_whether_it_agreed(self, tmp_path):
        dataset = Dataset("s", "v", (validated("Q1"),))
        csv_path = tmp_path / "w2.csv"
        export_for_validation(dataset.questions, csv_path)
        csv_path.write_text(
            csv_path.read_text(encoding="utf-8").replace(",,,,\n", ",validated,,,\n"),
            encoding="utf-8",
        )
        updated, _ = import_validations(dataset, csv_path, validator="owner", pass_number=2)
        assert updated.questions[0].validation.second_pass_agreed is True

    def test_a_second_pass_that_changed_the_answer_records_disagreement(self, tmp_path):
        dataset = Dataset("s", "v", (validated("Q1"),))
        csv_path = tmp_path / "w2.csv"
        export_for_validation(dataset.questions, csv_path)
        csv_path.write_text(
            csv_path.read_text(encoding="utf-8").replace(",,,,\n", ",validated,3,865,,\n"),
            encoding="utf-8",
        )
        updated, _ = import_validations(dataset, csv_path, validator="owner", pass_number=2)
        assert updated.questions[0].validation.second_pass_agreed is False

    def test_the_statistic_refuses_the_words_inter_annotator(self):
        questions = (
            validated(
                "Q1",
                validation=ValidationRecord(
                    status=ValidationStatus.VALIDATED, validator="owner",
                    validated_on="2026-08-26", pass_number=2, second_pass_agreed=True,
                ),
            ),
        )
        report = intra_annotator_agreement(questions)
        assert report["agreement"] == 1.0
        assert "NOT inter-annotator agreement" in report["note"]

    def test_no_second_pass_yields_no_statistic_rather_than_a_flattering_one(self):
        assert intra_annotator_agreement((validated("Q1"),))["agreement"] is None


class TestManifestAndStrata:
    def test_the_manifest_changes_when_a_gold_label_changes(self, tmp_path):
        path = tmp_path / "d.json"
        Dataset("s", "v", (validated("Q1"),)).write(path)
        before = manifest(path)["sha256"]
        Dataset("s", "v", (validated("Q1", answer=GoldAnswer("9,999", "INR crore")),)).write(path)
        assert manifest(path)["sha256"] != before

    def test_strata_are_countable_before_the_campaign_runs(self):
        questions = (
            validated("Q1", question_type="lookup"),
            validated("Q2", question_type="multi_hop", difficulty="hard"),
        )
        counts = stratify(questions)
        assert counts["type:lookup"] == 1
        assert counts["type:multi_hop"] == 1
        assert counts["difficulty:hard"] == 1

    def test_a_dataset_round_trips_through_disk(self, tmp_path):
        path = tmp_path / "d.json"
        original = Dataset("s", "v", (validated("Q1"), question("Q2")))
        original.write(path)
        reloaded = load_dataset(path)
        assert len(reloaded) == 2
        assert reloaded.questions[0].usable_as_gold is True
        assert reloaded.questions[1].usable_as_gold is False
        assert reloaded.questions[0].answer.canonical() == Decimal("39560000000")


class TestFailureCases:
    def test_an_empty_dataset_reports_zero_rather_than_crashing(self):
        empty = Dataset("s", "v", ())
        assert len(empty) == 0
        assert empty.audit() == {}
        assert build_split(empty, "train") == ()

    def test_a_question_with_no_answer_is_flagged_not_scored(self):
        """Derived-metric candidates are generated with no answer on purpose."""
        blank = question("Q1", answer=None)
        assert blank.usable_as_gold is False
        assert any("no gold answer" in p for p in blank.validation_problems())

    def test_a_missing_dataset_file_raises_rather_than_returning_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_dataset(tmp_path / "nope.json")


# --------------------------------------------------------------------------
# Worksheet ordering. Validation is partial by design, so the order a human
# meets the questions in decides which companies a first result covers.
# --------------------------------------------------------------------------


def test_the_worksheet_prefix_is_balanced_across_companies():
    """Grouped by company, the first 40 rows of the real corpus would be 40 HDFC
    Bank questions - 127 of 268 candidates come from one 585-page bank filing.
    A first result from that prefix would describe one bank."""
    from evaluation.validation import interleave_by_company

    questions = [
        question(f"H{i}", company="HDFC Bank Limited") for i in range(60)
    ] + [
        question(f"T{i}", company="Tata Motors Limited") for i in range(8)
    ] + [
        question(f"I{i}", company="Infosys Limited") for i in range(20)
    ]

    prefix = interleave_by_company(questions)[:12]
    companies = {q.company for q in prefix}
    assert companies == {
        "HDFC Bank Limited",
        "Tata Motors Limited",
        "Infosys Limited",
    }, f"a 12-row prefix covered only {companies}"


def test_interleaving_starts_with_the_thinnest_company():
    """A validator who stops early must not starve the smallest cell - it is the
    one already at risk of falling below the case study's reporting floor."""
    from evaluation.validation import interleave_by_company

    questions = [
        question(f"H{i}", company="HDFC Bank Limited") for i in range(50)
    ] + [question("T0", company="Tata Motors Limited")]

    assert interleave_by_company(questions)[0].company == "Tata Motors Limited"


def test_interleaving_loses_no_questions():
    from evaluation.validation import interleave_by_company

    questions = [
        question(f"H{i}", company="HDFC Bank Limited") for i in range(7)
    ] + [question(f"T{i}", company="Tata Motors Limited") for i in range(3)]

    out = interleave_by_company(questions)
    assert len(out) == len(questions)
    assert {q.qid for q in out} == {q.qid for q in questions}
