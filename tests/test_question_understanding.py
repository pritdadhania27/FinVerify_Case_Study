"""Tests for question understanding (spec Module 7).

Module 7 is on the critical path for *retrieval* quality, not only for parsing:
decomposing "return on equity" into its two components is what let question R14
be retrieved at all, after it had failed at every K in four consecutive
measurements (RX-005). These tests guard the mechanisms that produced that gain
and the failure modes that would silently undo it.
"""

from __future__ import annotations

import json

import pytest

from backend.agents.metrics_lexicon import decompose_derived, lookup_metric, statement_hint
from backend.agents.question_understanding import (
    QuestionSpec,
    SubQuestion,
    parse_question,
    understand_question,
)
from backend.retrieval.planned import retrieve_for_spec


class TestLexicon:
    def test_longest_match_wins(self):
        """"total current assets" must not be read as "total assets", and
        "profit before tax" must not be read as "profit for the year" - each pair
        differs by a large number in the same document."""
        assert lookup_metric("total current assets").canonical == "total current assets"
        assert lookup_metric("profit before tax").canonical == "profit before tax"

    def test_aliases_resolve(self):
        assert lookup_metric("what was net profit").canonical == "profit for the year"
        assert lookup_metric("accounts payable").canonical == "trade payables"

    def test_unknown_metric_returns_nothing_rather_than_guessing(self):
        assert lookup_metric("number of patents filed") is None

    def test_statement_hint(self):
        assert "balance sheet" in statement_hint("total assets")
        assert "profit and loss" in statement_hint("revenue from operations")

    def test_derived_metrics_are_recognised(self):
        assert decompose_derived("what was return on equity").canonical == "return on equity"
        assert decompose_derived("the ROE for the year").canonical == "return on equity"
        assert decompose_derived("what were trade payables") is None


class TestDecomposition:
    def test_a_ratio_becomes_its_components(self):
        """The failure this fixes: "return on equity" is a line item in no
        document, so one query ranks nothing useful and the question failed at
        every K for four measurements."""
        spec = parse_question("What was the return on equity for the year ended March 31, 2024?")
        assert spec.is_multi_hop
        metrics = {sq.metric for sq in spec.sub_questions}
        assert metrics == {"profit for the year", "total equity"}
        assert spec.derived_metric == "return on equity"
        assert spec.expected_unit == "percent"

    def test_components_carry_their_statement(self):
        """Component names come out of the lexicon by construction, so their
        statement is known rather than inferred - which is why the hint is
        trusted here and not on the reported-metric path."""
        spec = parse_question("What was the return on equity?")
        by_metric = {sq.metric: sq for sq in spec.sub_questions}
        assert "profit and loss" in by_metric["profit for the year"].search_text
        assert "balance sheet" in by_metric["total equity"].search_text

    def test_a_reported_figure_is_not_decomposed(self):
        spec = parse_question("What were trade payables as at March 31, 2024?")
        assert not spec.is_multi_hop
        assert spec.derived_metric is None


class TestStatementHintRule:
    def test_an_exact_line_item_gets_its_statement(self):
        """"total assets" has no selective term of its own - `total` is in 16% of
        chunks and `assets` in 25% - so it needs the hint to be findable."""
        spec = parse_question("What were total assets as at March 31, 2024?")
        assert "balance sheet" in spec.sub_questions[0].search_text

    def test_a_question_that_merely_contains_a_line_item_does_not(self):
        """"total other financial liabilities" contains "other financial
        liabilities" but is a different figure, reported in a note rather than on
        the balance sheet. Attaching the hint lost this question; the exact-match
        rule is what stopped it regressing."""
        spec = parse_question("What were total other financial liabilities?")
        assert spec.sub_questions[0].search_text == "total other financial liabilities"

    def test_an_unknown_metric_is_flagged_not_guessed(self):
        spec = parse_question("How many patents were filed?")
        assert spec.ambiguities
        assert any("lexicon" in a for a in spec.ambiguities)


class TestFiscalYear:
    def test_march_31_maps_to_the_indian_fiscal_year(self):
        assert parse_question("total assets as at March 31, 2024").fiscal_year == "2023-24"

    def test_two_years_means_no_filter(self):
        """Filtering to either year excludes half the evidence. Taking the first
        match filtered a single-year corpus to a year it does not contain and
        returned nothing at all, silently."""
        spec = parse_question(
            "By how much did revenue grow between the year ended March 31, 2023 "
            "and March 31, 2024?"
        )
        assert spec.fiscal_year is None
        # Both years are still recorded - the verifier needs to know a comparison
        # was asked for, even though neither may be used as a filter.
        assert spec.metadata.get("fiscal_years_mentioned") == ["2022-23", "2023-24"]

    def test_a_missing_year_is_recorded_as_an_ambiguity(self):
        spec = parse_question("What were trade payables?")
        assert spec.fiscal_year is None
        assert any("fiscal year" in a for a in spec.ambiguities)


class TestOperationDetection:
    @pytest.mark.parametrize(
        ("question", "operation"),
        [
            ("What were trade payables?", "lookup"),
            ("By how much did revenue grow?", "percentage_change"),
            ("What was the net profit margin?", "margin"),
            ("What was the current ratio?", "ratio"),
            ("What was the CAGR of revenue?", "cagr"),
            ("What share of total assets was goodwill?", "percentage_of"),
        ],
    )
    def test_operation(self, question, operation):
        assert parse_question(question).operation == operation

    def test_a_derived_metric_sets_a_percentage_unit(self):
        """A unit mismatch downstream is only detectable if the expectation was
        recorded: return on equity is a percentage, not crore."""
        assert parse_question("What was return on equity?").expected_unit == "percent"


class StubProvider:
    """Minimal provider stand-in; no network, no quota."""

    def __init__(self, text=None, error=None):
        self._text, self._error = text, error
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self._error:
            raise self._error

        class R:
            text = self._text

        return R()


class TestLLMPath:
    def test_no_provider_means_the_deterministic_parse(self):
        spec = understand_question("What were trade payables?")
        assert spec.source == "deterministic"

    def test_a_valid_payload_is_used(self):
        payload = json.dumps(
            {
                "operation": "ratio",
                "metrics": ["profit for the year", "total equity"],
                "expected_unit": "percent",
                "fiscal_year": "2023-24",
                "ambiguities": [],
            }
        )
        spec = understand_question(
            "How profitable was the equity base?", provider=StubProvider(payload), model="m"
        )
        assert spec.source == "llm"
        assert {sq.metric for sq in spec.sub_questions} == {
            "profit for the year",
            "total equity",
        }

    def test_a_provider_outage_degrades_rather_than_raising(self):
        """Question understanding is upstream of everything. Failing closed here
        would stop the pipeline for a component that has a working fallback."""
        spec = understand_question(
            "What were trade payables as at March 31, 2024?",
            provider=StubProvider(error=RuntimeError("503")),
            model="m",
        )
        assert spec.sub_questions
        assert spec.fiscal_year == "2023-24"
        assert any("unavailable" in a for a in spec.ambiguities)

    def test_malformed_json_falls_back(self):
        spec = understand_question(
            "What were trade payables?", provider=StubProvider("not json at all"), model="m"
        )
        assert spec.source == "deterministic"
        assert spec.sub_questions

    def test_an_empty_metric_list_is_not_accepted(self):
        """Retrieving on nothing is worse than retrieving on the rule-based
        parse, so an empty list is a rejected answer rather than an instruction."""
        payload = json.dumps({"operation": "lookup", "metrics": [], "ambiguities": ["unclear"]})
        spec = understand_question(
            "What were trade payables?", provider=StubProvider(payload), model="m"
        )
        assert spec.source == "deterministic"
        assert spec.sub_questions


class FakeRetriever:
    """Returns distinct, identifiable hits per query so interleaving is visible."""

    candidate_multiplier = 5

    def __init__(self):
        self.queries = []

    def retrieve(self, text, *, top_k=10, **filters):
        self.queries.append((text, filters))

        class Hit:
            def __init__(self, cid):
                self.chunk_id = cid
                self.text = cid
                self.payload = {"page": 1}

        return [Hit(f"{text[:12]}#{i}") for i in range(top_k)]


class TestPlannedRetrieval:
    def test_every_sub_question_is_retrieved_for(self):
        spec = parse_question("What was the return on equity?")
        r = FakeRetriever()
        planned = retrieve_for_spec(r, spec, top_k=10)
        assert len(r.queries) == 2
        assert set(planned.coverage) == {"profit for the year", "total equity"}

    def test_results_are_interleaved_not_score_ordered(self):
        """Taking the global top-K lets the sub-question with the stronger signal
        fill every slot, which produces a confident answer from half the evidence
        - the exact failure mode multi-hop questions have."""
        spec = parse_question("What was the return on equity?")
        planned = retrieve_for_spec(FakeRetriever(), spec, top_k=4)
        origins = [r.chunk_id.split("#")[0] for r in planned.results]
        assert len(set(origins)) == 2, "both sub-questions must occupy slots"

    def test_top_k_is_respected(self):
        spec = parse_question("What was the return on equity?")
        assert len(retrieve_for_spec(FakeRetriever(), spec, top_k=5).results) == 5

    def test_the_fiscal_year_becomes_a_filter(self):
        """The filter admits the asked-for year AND the report that restates it.

        This test previously asserted an exact match, `== "2023-24"`, and passed
        for days while the behaviour it pinned was a total failure: a chunk's
        `fiscal_year` is the DOCUMENT's year, so a question about the prior year
        matched zero chunks and retrieval returned an empty list. 72% of
        FinVerify-IND asks about a prior year (RX-015, D36).

        It passed because every question in this file asks about the current
        year, which the exact match happens to serve. **Widened deliberately —
        do not tighten this back to an equality without reading RX-015.**
        """
        spec = parse_question("What were trade payables as at March 31, 2024?")
        r = FakeRetriever()
        retrieve_for_spec(r, spec, top_k=5, document_id="doc1")
        assert "2023-24" in r.queries[0][1]["fiscal_year"]
        assert r.queries[0][1]["document_id"] == "doc1"

    def test_a_prior_year_question_still_reaches_the_report_that_restates_it(self):
        """The case the old assertion missed entirely.

        A FY2022-23 figure exists only inside the FY2023-24 filing, as a
        comparative column. If this filter excludes that document, both channels
        answer from an empty evidence set.
        """
        spec = parse_question("What were trade payables as at March 31, 2023?")
        r = FakeRetriever()
        retrieve_for_spec(r, spec, top_k=5)
        assert "2023-24" in r.queries[0][1]["fiscal_year"], (
            "the FY2023-24 report is the only source for a FY2023 comparative"
        )

    def test_two_years_does_not_produce_a_year_filter(self):
        spec = parse_question(
            "By how much did revenue grow between March 31, 2023 and March 31, 2024?"
        )
        r = FakeRetriever()
        retrieve_for_spec(r, spec, top_k=5)
        assert "fiscal_year" not in r.queries[0][1]

    def test_a_spec_with_no_sub_questions_returns_empty(self):
        spec = QuestionSpec(original="?", sub_questions=())
        assert retrieve_for_spec(FakeRetriever(), spec, top_k=5).results == []

    def test_duplicate_chunks_are_not_repeated(self):
        """Two sub-questions often retrieve the same table; a duplicate wastes a
        slot that another figure needs."""
        spec = QuestionSpec(
            original="q",
            sub_questions=(SubQuestion("a", "same"), SubQuestion("b", "same")),
        )
        planned = retrieve_for_spec(FakeRetriever(), spec, top_k=6)
        assert len({r.chunk_id for r in planned.results}) == len(planned.results)
