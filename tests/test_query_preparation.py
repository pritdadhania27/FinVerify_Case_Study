"""Tests for query preparation (spec Module 6/7 boundary).

Stripping interrogative and reporting-period scaffolding was the largest single
retrieval gain measured in this project (RX-004): evidence-retrieval accuracy
0.636 -> 0.818 with no change to the index, the model or the fusion. These tests
guard the two ways that gain could quietly be lost - stripping too little, and
stripping a domain term that carries meaning.
"""

from __future__ import annotations

import pytest

from backend.retrieval.query import (
    extract_fiscal_year,
    prepare_query,
    strip_question_boilerplate,
)


class TestStripping:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("How much were trade payables as at March 31, 2024?", "trade payables"),
            ("What were total assets as at March 31, 2024?", "total assets"),
            ("What was the finance cost for the year ended March 31, 2024?", "finance cost"),
            (
                "What were employee benefit expenses for the year ended March 31, 2024?",
                "employee benefit expenses",
            ),
        ],
    )
    def test_scaffolding_is_removed(self, question, expected):
        assert strip_question_boilerplate(question) == expected

    @pytest.mark.parametrize(
        "term",
        ["other", "total", "net", "current", "non-current", "gross", "basic", "diluted"],
    )
    def test_domain_qualifiers_survive(self, term):
        """A general English stopword list would drop these. They are exactly the
        words that separate "other equity" from "equity", "total current assets"
        from "total assets", and basic EPS from diluted EPS - so a general list
        would trade one retrieval failure for a worse one."""
        assert term in strip_question_boilerplate(f"What was the {term} equity?")

    def test_a_paraphrased_question_keeps_its_content(self):
        """R02 in the gold set: the question the keyword leg cannot serve. Its
        content words must survive for the dense leg to have anything to work
        with."""
        out = strip_question_boilerplate(
            "How much did the company owe its suppliers at the end of the financial year?"
        )
        assert "owe" in out and "suppliers" in out
        assert "company" not in out

    def test_numbers_in_the_question_survive(self):
        """A verification question may quote the figure it is checking."""
        assert "1,37,814" in strip_question_boilerplate("Is total assets 1,37,814?")

    def test_an_all_stopword_question_falls_back(self):
        """Stripping to nothing would send an empty query, which returns an
        arbitrary ranking rather than an error - worse than not stripping."""
        assert strip_question_boilerplate("What was it?") == "What was it?"

    def test_year_on_year_is_not_eaten_by_the_period_pattern(self):
        assert "year-on-year" in strip_question_boilerplate("What was year-on-year growth?")


class TestFiscalYearExtraction:
    def test_march_31_maps_to_the_indian_fiscal_year(self):
        """March 31 of year Y ends fiscal year (Y-1)-Y, which is the convention
        the corpus registry uses."""
        assert extract_fiscal_year("total assets as at March 31, 2024") == "2023-24"

    def test_explicit_fiscal_year_label(self):
        assert extract_fiscal_year("revenue in FY 2023-24") == "2023-24"

    def test_a_bare_calendar_year_is_not_guessed(self):
        """'revenue in 2024' is genuinely ambiguous between the calendar year and
        either adjacent fiscal year. Filtering on a guess would silently exclude
        the right evidence; not filtering only costs precision."""
        assert extract_fiscal_year("What was revenue in 2024?") is None

    def test_no_date_at_all(self):
        assert extract_fiscal_year("What were trade payables?") is None


class TestPrepareQuery:
    def test_carries_both_halves(self):
        """The date is dropped from the search text but not discarded - it is
        information about WHICH year, which belongs in a metadata filter."""
        spec = prepare_query("What were total assets as at March 31, 2024?")
        assert spec.search_text == "total assets"
        assert spec.fiscal_year == "2023-24"
        assert spec.original.startswith("What were")


class TestContentTerms:
    """`content_terms` has no fallback; `strip_question_boilerplate` does. The
    two callers need opposite things from the empty case, and conflating them let
    a contentless question reach the retriever."""

    def test_a_contentless_question_has_no_content_terms(self):
        from backend.retrieval.query import content_terms

        for question in ("", "?", "What?", "the of and", "   "):
            assert content_terms(question) == [], question

    def test_a_real_question_keeps_its_terms(self):
        from backend.retrieval.query import content_terms

        assert content_terms("What were total assets as at March 31, 2024?") == [
            "total", "assets"
        ]

    def test_stripping_still_falls_back_for_the_retriever(self):
        """Unchanged behaviour: searching the original text beats searching an
        empty string, which returns an arbitrary ranking rather than an error."""
        assert strip_question_boilerplate("What was it?") == "What was it?"
