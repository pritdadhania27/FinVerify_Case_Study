"""Tests for the standalone/consolidated section check (RX-026).

The defect this guards against is the one it was written to find, one level up:
a plausible-looking classification, a clean-looking number, and no error
anywhere. The first version of this classifier keyed on any mention of
"standalone" anywhere in a page's text, which flipped 23 of Reliance's 25
retrieval questions into the wrong section - and would have been reported as a
finding had the pages not been read.

So the rule under test is narrow and deliberate: only a RUNNING HEADER declares
a section, an in-text mention never does, and a page before any marker is
UNKNOWN rather than assigned to either basis.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.documents.statement_basis import classify_header  # noqa: E402
from scripts.check_statement_basis import GROUND_TRUTH, audit_worksheet  # noqa: E402


class TestTheHeaderRule:
    def test_a_consolidated_header_declares_consolidated(self):
        assert classify_header("Consolidated Balance Sheet") == "consolidated"
        assert classify_header("Notes to the Consolidated Financial Statements") == (
            "consolidated"
        )

    def test_a_standalone_header_declares_standalone(self):
        assert classify_header("SCHEDULES TO THE STANDALONE FINANCIAL STATEMENTS") == (
            "standalone"
        )

    def test_a_header_naming_neither_declares_nothing(self):
        """Tata Motors' standalone notes run 145 pages under exactly this
        header. It must not be read as a boundary - it carries no basis, so the
        page inherits the section it is in."""
        assert classify_header("Notes forming part of Financial Statements") is None

    def test_prose_about_the_group_is_not_a_header(self):
        """The bug. Reliance p140 sits under a consolidated header and its body
        discusses the Group; elsewhere the notes cross-reference the standalone
        statements. Reading any mention as a marker put 92% of Reliance's
        questions in the wrong section and produced a confident wrong finding."""
        body = (
            "The Group's activities expose it to a variety of financial risks. "
            "Refer to the standalone financial statements for the parent's own "
            "position."
        )
        assert classify_header(body) in (None, "consolidated"), (
            "a sentence mentioning both bases must not resolve to standalone"
        )

    def test_a_header_naming_both_resolves_nothing(self):
        assert classify_header(
            "Notes to the Standalone and Consolidated Financial Statements"
        ) is None

    def test_a_page_number_alone_declares_nothing(self):
        assert classify_header("214") is None
        assert classify_header("") is None


class TestTheAudit:
    def basis(self):
        return {"Acme Limited": {10: "consolidated", 99: "standalone", 5: "-"}}

    def row(self, qid, page, wording="consolidated"):
        return {
            "qid": qid,
            "company": "Acme Limited",
            "source_page": str(page),
            "question": f"what were borrowings as reported on the {wording} balance sheet?",
        }

    def test_a_consolidated_question_on_a_standalone_page_is_an_offender(self):
        _, offenders = audit_worksheet(self.basis(), [self.row("Q1", 99)])
        assert [row["qid"] for row in offenders] == ["Q1"]

    def test_a_consolidated_question_on_a_consolidated_page_is_fine(self):
        _, offenders = audit_worksheet(self.basis(), [self.row("Q2", 10)])
        assert offenders == []

    def test_a_standalone_question_on_a_standalone_page_is_fine(self):
        """The check is about agreement, not about preferring one basis."""
        _, offenders = audit_worksheet(
            self.basis(), [self.row("Q3", 99, wording="standalone")]
        )
        assert offenders == []

    def test_an_unclassified_page_is_not_counted_against_the_dataset(self):
        """Unknown is not wrong. Counting it as a defect would inflate the
        finding with pages nobody has established anything about."""
        _, offenders = audit_worksheet(self.basis(), [self.row("Q4", 5)])
        assert offenders == []

    def test_a_missing_page_number_does_not_raise(self):
        row = self.row("Q5", "")
        per_company, offenders = audit_worksheet(self.basis(), [row])
        assert offenders == []
        assert per_company["Acme Limited"]["?"] == 1


class TestAgainstTheRealCorpus:
    """The classifier is a heuristic, so it is pinned to pages read by hand.

    `check_statement_basis.py` runs this same check before printing anything
    and refuses to report if it fails; this is the version that runs in CI.
    """

    def test_the_classifier_agrees_with_every_hand_read_page(self):
        from scripts.check_statement_basis import PDFS, RAW_DIR, load_basis, self_check

        missing = [name for name in PDFS.values() if not (RAW_DIR / name).exists()]
        if missing:
            pytest.skip(f"source PDFs not present: {missing[0]}")
        assert self_check(load_basis()) == []

    def test_the_ground_truth_covers_both_bases_and_several_filings(self):
        """A ground truth of five consolidated pages would pass while the
        standalone half of the rule was broken."""
        bases = {expected for _, _, expected in GROUND_TRUTH}
        companies = {company for company, _, _ in GROUND_TRUTH}
        assert bases == {"consolidated", "standalone"}
        assert len(companies) >= 3
