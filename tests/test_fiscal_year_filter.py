"""Tests for the fiscal-year retrieval filter (RX-015).

A research-validity gate. The defect it pins was total and silent: a chunk's
`fiscal_year` is the year of the DOCUMENT, and the filter matched it against the
year the QUESTION asked about. Every chunk in this corpus carries `2023-24`, so
a question about the year ended March 2023 filtered to **zero chunks** and
retrieval returned an empty list. Both channels then answered from no evidence
at all.

193 of FinVerify-IND's 268 questions ask about a prior year, because an annual
report states its own year beside the previous one. So roughly 72% of the
campaign would have measured the pipeline on an empty context - and the failure
would have been attributed to the method rather than to a filter.

Nothing raised an error. `build_filter` was valid, Qdrant answered normally, and
the empty result set looked exactly like "this report does not contain that".

If one of these fails, the finding is that the guard has been weakened. **The
fix is the code, never the test.**
"""

from __future__ import annotations

from backend.rag.indexing import QdrantIndex
from backend.retrieval.planned import acceptable_document_years


class TestAcceptableDocumentYears:
    def test_a_report_is_a_source_for_its_own_year(self):
        assert "2023-24" in acceptable_document_years("2023-24")

    def test_a_report_is_a_source_for_the_prior_year_it_restates(self):
        """The whole point. FY2022-23 figures live in the FY2023-24 filing."""
        assert "2023-24" in acceptable_document_years("2022-23")

    def test_the_corpus_year_is_reachable_from_every_year_it_actually_reports(self):
        """Concretely: this corpus is all 2023-24, and it reports 2023 and 2024.

        Stated as the real configuration rather than in the abstract, because
        the abstract version passed while the real one returned nothing.
        """
        for asked in ("2022-23", "2023-24"):
            assert "2023-24" in acceptable_document_years(asked), asked

    def test_a_ten_year_summary_makes_a_distant_year_reachable(self):
        """This test previously asserted the OPPOSITE, and was wrong.

        It read: "Comparatives go back one year, not five. Widening further
        would trade a silent miss for a silent wrong-document hit." The premise
        is false in both directions. A report can state any year up to and
        including its own - Indian listed companies publish a ten-year
        highlights summary - and it is an *earlier* report that cannot hold a
        *later* figure, never the reverse.

        Under the old one-year window, 115 of FinVerify-IND's 268 questions
        (43%) filtered to zero chunks and retrieved nothing at all: everything
        asking about FY2013-14 through FY2021-22, which is where HDFC Bank's
        ten-year table and several multi-year comparatives put them (RX-020).
        The test passed the whole time, because it asserted the bug.

        **Do not narrow this back without reading RX-020.**
        """
        assert "2023-24" in acceptable_document_years("2019-20")
        assert "2023-24" in acceptable_document_years("2013-14")

    def test_the_window_is_still_bounded_and_still_one_directional(self):
        """A filter that admits everything is not a filter.

        The direction carries the meaning - never admit a report published
        before the year asked about - and the horizon only bounds it.
        """
        years = acceptable_document_years("2019-20")
        assert "2018-19" not in years, "a report cannot state a year it precedes"
        assert "2023-24" in years
        # Eleven years of margin on a ten-year summary; beyond that, no.
        assert "2023-24" not in acceptable_document_years("2011-12")

    def test_the_century_rolls_over_without_producing_a_100(self):
        years = acceptable_document_years("1999-00")
        assert years[0] == "1999-00"
        assert years[1] == "2000-01"
        assert not any("-100" in y for y in years)

    def test_an_unparseable_year_is_passed_through_unwidened(self):
        """Never guess. A year we cannot parse is filtered on as given."""
        assert acceptable_document_years("FY24") == ("FY24",)


class TestBuildFilterAcceptsSeveralValues:
    def test_a_sequence_becomes_match_any_not_match_all(self):
        """Several ACCEPTABLE values, not several REQUIRED ones.

        A conjunction here would be worse than the original bug: no chunk can
        carry two fiscal years, so every query would return nothing.
        """
        f = QdrantIndex.build_filter(fiscal_year=("2022-23", "2023-24"))
        assert f is not None and len(f.must) == 1
        condition = f.must[0]
        assert condition.key == "fiscal_year"
        assert sorted(condition.match.any) == ["2022-23", "2023-24"]

    def test_a_single_string_still_matches_exactly(self):
        f = QdrantIndex.build_filter(fiscal_year="2023-24")
        assert f.must[0].match.value == "2023-24"

    def test_other_fields_are_unaffected_by_the_change(self):
        f = QdrantIndex.build_filter(
            company="HDFC Bank Limited", fiscal_year=("2022-23", "2023-24")
        )
        keys = {c.key for c in f.must}
        assert keys == {"company", "fiscal_year"}

    def test_no_constraints_still_means_no_filter(self):
        assert QdrantIndex.build_filter() is None
