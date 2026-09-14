"""Tests for the gold-validation tooling (spec §16, decisions D22 / D23 / D35).

These guard a boundary rather than a feature. `precheck_gold.py` may locate a
figure; it may never conclude that the figure answers the question, because that
conclusion is what makes a gold label gold. If a change here starts writing
verdicts from code, every downstream metric silently becomes a measurement of
the generator rather than of the system.

The other property under test is that the reviewer has **no default answer**.
A tool where Enter means "yes" turns a validation pass into a rubber stamp, and
a rubber-stamped gold set is indistinguishable from an unvalidated one in the
data but not in what it claims.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.precheck_gold import (  # noqa: E402
    EXTRA_COLUMNS,
    classify,
    excerpt,
    label_from_anchors,
    searchable_variants,
)
from scripts.review_gold import (  # noqa: E402
    as_figure,
    has_candidate,
    judge,
    save,
    unit_conflicts_with_question,
)

BALANCE_SHEET = [
    "Consolidated Balance Sheet",
    "Total borrowings includes all long and short-term borrowings, see note 22.",
    "Non-current liabilities",
    "Total borrowings",
    "18,872.44",
    "16,101.20",
]


class TestSearchableVariants:
    def test_a_long_figure_is_searchable(self):
        assert "18,872.44" in searchable_variants("18872.44")

    def test_indian_and_western_grouping_are_both_offered(self):
        variants = searchable_variants("135702")
        assert "1,35,702" in variants and "135,702" in variants

    def test_trailing_zeros_do_not_make_a_figure_searchable(self):
        """"4.00" looks like three digits and reduces to the single digit "4".

        Gating on the raw string flagged five of HDFC Bank's dividend-per-share
        rows as missing from a page they are printed on. A one-digit needle
        matches every page of every filing, so the honest answer is "cannot
        check", not "absent".
        """
        assert searchable_variants("4.00") == []
        assert searchable_variants("18.0") == []


class TestClassify:
    def pages(self):
        return {448: ["something else"], 449: BALANCE_SHEET, 450: ["more text"]}

    def test_a_figure_on_its_cited_page(self):
        verdict, text, _ = classify(self.pages(), 449, "18872.44", "Total borrowings")
        assert verdict == "on_cited_page"
        assert "18,872.44" in text

    def test_a_blank_candidate_is_not_a_failure(self):
        """The 12 derived-metric questions carry no candidate answer on purpose."""
        assert classify(self.pages(), 449, "", "x")[0] == "no_candidate"

    def test_an_unsearchable_figure_is_reported_as_such_not_as_absent(self):
        pages = {12: ["Dividend per share (H)1", "4.00", "4.75"]}
        verdict, text, _ = classify(pages, 12, "4.00", "Dividend per share (H)1")
        assert verdict == "short_figure"
        assert "4.00" in text, "the validator must still see the row"

    def test_a_figure_on_a_different_page_is_flagged_not_accepted(self):
        pages = {10: ["noise"], 11: ["noise"], 12: ["noise"], 449: BALANCE_SHEET}
        verdict, _, others = classify(pages, 11, "18872.44", "Total borrowings")
        assert verdict == "elsewhere"
        assert "449" in others

    def test_the_facing_page_is_accepted_because_extraction_slips_by_one(self):
        verdict, _, note = classify(self.pages(), 448, "18872.44", "Total borrowings")
        assert verdict == "on_cited_page"
        assert "449" in note, "the discrepancy is recorded, not swallowed"

    def test_a_genuinely_absent_figure(self):
        assert classify(self.pages(), 449, "99999.99", "Total borrowings")[0] == "not_found"


class TestExcerpt:
    def test_the_label_line_nearest_the_figure_wins(self):
        """Tata Motors p449 opens with a sentence defining the row.

        Taking the first label match showed the validator "Total borrowings
        includes all long and short-term borrowings…" - a definition, not a
        figure - and buried the number it was meant to display.
        """
        text = excerpt(BALANCE_SHEET, "18872.44", "Total borrowings")
        assert "includes all long" not in text
        assert text == "Total borrowings  //  18,872.44"

    def test_a_row_extracted_as_one_line_is_not_duplicated(self):
        lines = ["Trade payables    3,956    3,590"]
        assert excerpt(lines, "3956", "Trade payables") == lines[0]


class TestAnchorParsing:
    def test_the_row_label_is_recovered(self):
        assert label_from_anchors("p449: Total borrowings | 18872.44") == "Total borrowings"

    def test_only_the_first_span_is_used(self):
        anchors = "p140: Borrowings | 135702 ;; p140: Borrowings | 12029"
        assert label_from_anchors(anchors) == "Borrowings"

    def test_an_empty_anchor_string_does_not_raise(self):
        assert label_from_anchors("") == ""


class TestReviewerHasNoDefault:
    def row(self):
        return {
            "qid": "FI0001",
            "candidate_answer": "288",
            "candidate_unit": "INR crore",
            "verdict": "",
            "corrected_answer": "",
            "corrected_unit": "",
            "notes": "",
        }

    def answers(self, monkeypatch, *keys):
        supplied = iter(keys)
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(supplied))

    def test_enter_alone_records_nothing(self, monkeypatch):
        """The property this whole tool is judged on.

        If Enter accepted the candidate, the cheapest way through 268 rows would
        be to hold it down, and the resulting gold set would be a measurement of
        the generator wearing a validator's name. Enter must re-prompt.
        """
        row = self.row()
        self.answers(monkeypatch, "", "", "y")
        assert judge(row) == "next"
        assert row["verdict"] == "validated", "only the explicit key was accepted"

    def test_y_validates(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "y")
        judge(row)
        assert row["verdict"] == "validated"

    def test_n_rejects_and_keeps_the_reason(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "n", "wrong year's column")
        judge(row)
        assert row["verdict"] == "rejected"
        assert row["notes"] == "wrong year's column"

    def test_a_correction_is_recorded_beside_the_candidate(self, monkeypatch):
        """The candidate is never overwritten here.

        `import_validations` keeps the generated answer in
        `provenance.generated_answer`; "the gold says 3,956" is worth nothing if
        nobody can see it once said 3,596.
        """
        row = self.row()
        self.answers(monkeypatch, "c", "3956", "", "misread column")
        judge(row)
        assert row["corrected_answer"] == "3956"
        assert row["corrected_unit"] == "INR crore", "blank keeps the candidate unit"
        assert row["candidate_answer"] == "288", "the candidate must survive"
        assert row["verdict"] == "validated"

    def test_an_empty_correction_is_refused_rather_than_stored(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "c", "", "s")
        assert judge(row) == "next"
        assert row["corrected_answer"] == ""
        assert row["verdict"] == ""

    def test_unsure_is_a_first_class_answer(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "?", "two figures on the page")
        judge(row)
        assert row["verdict"] == "needs_review"

    def test_skip_leaves_the_row_pending(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "s")
        judge(row)
        assert row["verdict"] == ""

    def test_quitting_is_always_available(self, monkeypatch):
        self.answers(monkeypatch, "q")
        assert judge(self.row()) == "quit"

    def test_an_interrupted_session_quits_rather_than_crashing(self, monkeypatch):
        def interrupt(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", interrupt)
        assert judge(self.row()) == "quit"


class TestSaving:
    def test_a_save_preserves_every_column(self, tmp_path):
        path = tmp_path / "worksheet.csv"
        fieldnames = ["qid", "verdict", *EXTRA_COLUMNS]
        rows = [
            {"qid": "A", "verdict": "validated", **dict.fromkeys(EXTRA_COLUMNS, "")},
            {"qid": "B", "verdict": "", **dict.fromkeys(EXTRA_COLUMNS, "")},
        ]
        save(path, rows, fieldnames)
        back = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
        assert [r["qid"] for r in back] == ["A", "B"]
        assert back[0]["verdict"] == "validated"
        assert back[1]["verdict"] == "", "an unjudged row stays PENDING"

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        path = tmp_path / "worksheet.csv"
        save(path, [{"qid": "A"}], ["qid"])
        assert list(tmp_path.iterdir()) == [path]


class TestTheBoundary:
    def test_no_module_here_can_write_a_validated_status(self):
        """The rule this suite exists to enforce.

        `ValidationStatus.VALIDATED` means a person signed for the figure.
        `precheck_gold.py` reports what is printed on a page and stops there; if
        it ever gains the ability to set a status, the dataset becomes a record
        of the generator agreeing with itself.
        """
        import ast

        tree = ast.parse(Path("scripts/precheck_gold.py").read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        # The AST, not the text: this file's own docstring names the class it
        # must not use, and a substring check would forbid explaining the rule.
        assert "ValidationStatus" not in imported
        assert "import_validations" not in imported
        modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(m.startswith("evaluation.validation") for m in modules)

    @pytest.mark.parametrize("column", EXTRA_COLUMNS)
    def test_precheck_columns_are_ignored_by_the_importer(self, column):
        """Extra context columns must not collide with the round-trip contract."""
        from evaluation.validation import VALIDATION_COLUMNS

        assert column not in VALIDATION_COLUMNS


class TestTheToolActuallyReachesItsFile:
    """Two ways a validation session silently recorded nothing, both mine.

    Neither raised, neither printed anything alarming, and both left the
    worksheet byte-identical - which is indistinguishable from a validator who
    reviewed nothing. Failures that look like a clean run are the worst kind in
    a workflow whose whole output is a file that changed.
    """

    def test_the_default_worksheet_does_not_depend_on_the_working_directory(self):
        """Run from anywhere but the repo root and the relative default resolved
        to a file that does not exist. The script then printed "no worksheet"
        and exited 1 without touching the real one."""
        from scripts.review_gold import WORKSHEET

        assert WORKSHEET.is_absolute()
        assert WORKSHEET.name == "worksheet.csv"

    def test_precheck_paths_are_also_absolute(self):
        from scripts import precheck_gold

        for name in ("REGISTRY", "DATASET", "WORKSHEET", "RAW_DIR"):
            assert getattr(precheck_gold, name).is_absolute(), name

    def test_a_byte_order_mark_does_not_swallow_the_first_answer(self, monkeypatch):
        """PowerShell prefixes piped stdin with U+FEFF.

        The first answer of a scripted session arrived as "\ufeffy", matched no
        branch, and re-prompted - so a replayed session recorded one fewer
        verdict than it was given, starting with the first.
        """
        from scripts.review_gold import judge

        supplied = iter(["\ufeffy"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(supplied))
        row = {"qid": "Q", "candidate_answer": "1", "candidate_unit": "u",
               "verdict": "", "corrected_answer": "", "corrected_unit": "", "notes": ""}
        assert judge(row) == "next"
        assert row["verdict"] == "validated"

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        from scripts.review_gold import judge

        supplied = iter(["  Y  "])
        monkeypatch.setattr("builtins.input", lambda _p="": next(supplied))
        row = {"qid": "Q", "candidate_answer": "1", "candidate_unit": "u",
               "verdict": "", "corrected_answer": "", "corrected_unit": "", "notes": ""}
        judge(row)
        assert row["verdict"] == "validated"


class TestADerivedRowCannotBeRubberStamped:
    """The 12 derived-metric rows carry no candidate answer on purpose (D35).

    On those rows `y` has nothing to affirm. Accepting it anyway would write
    `verdict=validated` beside a blank answer - a gold label with no gold in it,
    produced by exactly the single keystroke the rest of this tool refuses. It
    is worse than an unvalidated row, because an unvalidated row is visibly
    PENDING while this one grades as real.

    These questions are also the only ones the deterministic channel engages on
    (RX-025), so they are the stratum operand-binding accuracy waits for. A
    silently empty verdict here would block that measurement while looking like
    it had unblocked it.
    """

    def row(self):
        return {
            "qid": "FI0044",
            "candidate_answer": "",
            "candidate_unit": "",
            "verdict": "",
            "corrected_answer": "",
            "corrected_unit": "",
            "notes": "",
        }

    def answers(self, monkeypatch, *keys):
        supplied = iter(keys)
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(supplied))

    def test_has_candidate_is_what_separates_the_two_kinds_of_row(self):
        assert not has_candidate(self.row())
        assert not has_candidate({"candidate_answer": "   "}), "whitespace is blank"
        assert has_candidate({"candidate_answer": "288"})

    def test_y_is_refused_rather_than_recorded(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "y", "s")
        assert judge(row) == "next"
        assert row["verdict"] == "", "a blank answer must never become validated"

    def test_n_is_refused_too(self, monkeypatch):
        """There is no candidate to call wrong; a rejection here would record a
        judgment about a figure nobody ever wrote down."""
        row = self.row()
        self.answers(monkeypatch, "n", "s")
        assert judge(row) == "next"
        assert row["verdict"] == ""

    def test_the_figure_is_entered_with_c(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "c", "12.4", "percent", "operating margin, p.212")
        judge(row)
        assert row["corrected_answer"] == "12.4"
        assert row["corrected_unit"] == "percent"
        assert row["verdict"] == "validated"

    def test_a_blank_unit_stays_blank_rather_than_inheriting_one(self, monkeypatch):
        """The candidate unit is the default elsewhere. Here there is none, so
        an empty answer must leave it empty rather than borrow a unit - a
        derived ratio and a crore figure are not interchangeable."""
        row = self.row()
        self.answers(monkeypatch, "c", "12.4", "", "")
        judge(row)
        assert row["corrected_unit"] == ""

    def test_unsure_and_skip_still_work(self, monkeypatch):
        row = self.row()
        self.answers(monkeypatch, "?", "cannot find the denominator")
        judge(row)
        assert row["verdict"] == "needs_review"

    def test_the_blank_rows_are_exactly_the_derived_questions(self):
        """An invariant, not a snapshot.

        This used to assert 268 rows and 12 blanks, and broke when the
        dataset was rebuilt for correctness (D42). Pinning a count tests
        the size of the dataset; what actually has to hold is that the
        worksheet covers every question and that the rows carrying no
        candidate answer are exactly the derived-metric ones - which is the
        property `--derived` selects on and the reason those rows refuse a
        rubber stamp.
        """
        import csv as _csv
        import json

        from backend.core.paths import project_path

        sheet = project_path("datasets/finverify_ind/worksheet.csv")
        dataset = project_path("datasets/finverify_ind/finverify_ind_v1.json")
        if not sheet.exists() or not dataset.exists():
            pytest.skip("dataset or worksheet not built")

        with sheet.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(_csv.DictReader(handle))
        payload = json.loads(dataset.read_text(encoding="utf-8"))
        questions = payload["questions"] if isinstance(payload, dict) else payload

        assert len(rows) == len(questions), "the worksheet must cover every question"
        blank = {r["qid"] for r in rows if not has_candidate(r)}
        # Keyed on question TYPE, not on whether an answer exists yet. Once a
        # validator computes a derived metric the question HAS a gold answer
        # while its worksheet candidate stays blank by design - so `answer is
        # None` stops tracking "is this derived" the moment validation starts,
        # which is exactly when this guard needs to still work.
        derived = {
            q["qid"] for q in questions if q["question_type"] in ("multi_hop", "computed")
        }
        assert blank == derived, (
            "a question with no candidate answer must be a derived-metric "
            "question and vice versa"
        )
        assert blank, "the derived stratum must not be empty"


class TestTheAnswerFieldHoldsAFigure:
    """Four gold answers were stored as 400-700 character paragraphs.

    A validator working out a derived metric writes out the working, and pasting
    all of it into the answer field is the natural thing to do - the tool used to
    accept it. The stored answer then read "Metric: Return on Assets... Conclusion:
    the derived return on assets is 1.59%", and an evaluator comparing a model's
    "1.59" against that would score a correct answer wrong. The gold would have
    been unusable in a way that looks like the system failing.
    """

    def test_a_plain_figure_passes_through(self):
        assert as_figure("1.59") == "1.59"

    def test_separators_and_a_percent_sign_are_stripped(self):
        assert as_figure("1,234.5") == "1234.5"
        assert as_figure("7.26%") == "7.26"

    def test_a_negative_figure_is_still_a_figure(self):
        assert as_figure("-1150.69") == "-1150.69"

    def test_an_explicit_conclusion_is_read_rather_than_refused(self):
        """The validator did state the figure. Making them retype it invites a
        transcription error into gold data."""
        working = (
            "Metric: Return on Assets (derived metric). Formula: (Profit / Total "
            "Assets) * 100. Extraction: Profit = 64,060 crore, Total Assets = "
            "4,030,194.26 crore. Calculation: (64,060 / 4,030,194.26) * 100 = "
            "1.589% Conclusion: The derived return on assets is 1.59%."
        )
        assert as_figure(working) == "1.59"

    def test_prose_with_no_figure_is_refused(self):
        assert as_figure("about eight percent") is None
        assert as_figure("see the note") is None
        assert as_figure("") is None

    def test_a_paragraph_is_not_recorded_as_the_answer(self, monkeypatch):
        """End to end through judge(): the paragraph must not reach the field."""
        row = {
            "qid": "FI0001", "candidate_answer": "288", "candidate_unit": "INR crore",
            "verdict": "", "corrected_answer": "", "corrected_unit": "", "notes": "",
        }
        working = "Formula: x/y. Conclusion: The derived return on assets is 8.58%."
        supplied = iter(["c", working, "percent", ""])
        monkeypatch.setattr("builtins.input", lambda _p="": next(supplied))
        judge(row)
        assert row["corrected_answer"] == "8.58"
        assert working in row["notes"], "the working is preserved, not discarded"

    def test_unreadable_prose_re_prompts_instead_of_storing_it(self, monkeypatch):
        row = {
            "qid": "FI0001", "candidate_answer": "288", "candidate_unit": "INR crore",
            "verdict": "", "corrected_answer": "", "corrected_unit": "", "notes": "",
        }
        supplied = iter(["c", "roughly eight percent", "s"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(supplied))
        assert judge(row) == "next"
        assert row["corrected_answer"] == ""
        assert row["verdict"] == ""


class TestAQuotientHasNoScale:
    """A debt-to-equity ratio was validated as "0.41 crore" (RX-031).

    The figure was right - the validator's own note said "0.41 times" - but
    `crore` multiplies the gold by 10^7. A model answering 0.41 would have been
    graded WRONG, and one answering "0.41 crore" graded correct. The grader was
    inverted on that question, and nothing in the tool objected.
    """

    RATIO_Q = (
        "For Reliance Industries Limited, in the 2023-24 consolidated financial "
        "statements, what was debt to equity ratio?"
    )

    def test_a_scale_word_on_a_ratio_is_refused(self):
        assert unit_conflicts_with_question(self.RATIO_Q, "", "crore") is not None

    def test_every_scale_word_is_caught_not_just_crore(self):
        for unit in ("lakh", "INR million", "billions", "thousand", "cr"):
            assert unit_conflicts_with_question(self.RATIO_Q, "", unit) is not None, unit

    def test_percent_and_blank_are_allowed(self):
        assert unit_conflicts_with_question(self.RATIO_Q, "", "%") is None
        assert unit_conflicts_with_question(self.RATIO_Q, "", "") is None

    def test_a_currency_metric_keeps_its_scale(self):
        """The guard must not fire on the 96 questions that DO want crore."""
        borrowings = "what were total borrowings?"
        assert unit_conflicts_with_question(borrowings, "", "INR crore") is None

    def test_the_definition_is_read_as_well_as_the_question(self):
        """The metric name is sometimes only in the definition column."""
        conflict = unit_conflicts_with_question(
            "what was the figure for FY2024?", "borrowings divided by total equity ratio", "crore"
        )
        assert conflict is not None

    def test_judge_re_prompts_rather_than_storing_the_bad_unit(self, monkeypatch):
        row = {
            "qid": "FI9e9260be", "question": self.RATIO_Q, "definition": "",
            "candidate_answer": "", "candidate_unit": "",
            "verdict": "", "corrected_answer": "", "corrected_unit": "", "notes": "",
        }
        supplied = iter(["c", "0.41", "crore", "-", ""])
        monkeypatch.setattr("builtins.input", lambda _p="": next(supplied))
        judge(row)
        assert row["corrected_answer"] == "0.41"
        assert row["corrected_unit"] == "", "the scale word must not survive"
        assert row["verdict"] == "validated"
