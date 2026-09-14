"""Tests for retrieval metrics (spec §14, EVALUATION.md §4).

These run without Qdrant: the metric arithmetic is separable from the retriever
and must be checkable on its own. If Recall@K is wrong, every retrieval decision
downstream is wrong in a way no amount of Qdrant uptime would reveal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.metrics.retrieval import (
    EvidenceGroup,
    degroup,
    EvidenceSpan,
    GoldQuestion,
    RetrievedItem,
    aggregate,
    normalise,
    score_question,
)

GOLD_FILE = Path("datasets/retrieval_eval/infosys_fy24_v1.json")


@pytest.fixture(scope="module")
def gold() -> dict:
    return json.loads(GOLD_FILE.read_text(encoding="utf-8"))


def span(page: int, *anchors: str) -> EvidenceSpan:
    return EvidenceSpan(page=page, anchors=anchors)


def group(gid: str, *spans: EvidenceSpan) -> EvidenceGroup:
    return EvidenceGroup(group_id=gid, any_of=spans)


def question(qid: str, *groups: EvidenceGroup) -> GoldQuestion:
    return GoldQuestion(qid=qid, question="q", evidence=groups)


def item(chunk_id: str, page: int, text: str) -> RetrievedItem:
    return RetrievedItem(chunk_id=chunk_id, page=page, text=text)


class TestNormalise:
    def test_collapses_whitespace_from_table_rendering(self):
        """A pipe table renders '| Trade payables | 3,956 |'; the PDF text has
        newlines. Both must satisfy the same anchor."""
        assert normalise("Trade\n  payables") == "trade payables"

    def test_unicode_spaces_collapse(self):
        """PDF text wraps Indian-grouped numerals across a non-breaking or thin
        space, which an anchor typed with an ordinary space must still match."""
        assert normalise("1,37, 814") == normalise("1,37, 814")
        assert normalise("total assets") == "total assets"

    def test_commas_are_not_stripped(self):
        """'3,956' and '3956' are different tokens in the source. An anchor that
        matched either would accept a chunk whose digit grouping was mangled -
        exactly the corruption this project exists to catch."""
        assert normalise("3956") != normalise("3,956")

    def test_en_dash_folds_to_hyphen(self):
        assert normalise("2023–24") == "2023-24"


class TestEvidenceSpan:
    def test_requires_the_right_page(self):
        assert not span(12, "3,956").satisfied_by(51, "Trade payables 3,956")

    def test_requires_every_anchor(self):
        s = span(13, "Revenue from operations", "153,670")
        assert s.satisfied_by(13, "| Revenue from operations | 2.18 | 153,670 | 146,767 |")
        assert not s.satisfied_by(13, "| Revenue from operations | 2.18 | 146,767 |")

    def test_from_dict_rejects_an_anchorless_span(self):
        """A span with no anchors is satisfied by any chunk on the page, which
        would silently inflate recall."""
        with pytest.raises(ValueError, match="no anchors"):
            EvidenceSpan.from_dict({"page": 12, "anchors": []})


class TestAGoldNumeralMeetsAPrintedOne:
    """A gold anchor of "12232" could never match a filing that prints "12,232".

    `satisfied_by` requires ALL anchors, so a group whose anchors were
    ("cost of technical sub-contractors", "12232") was unsatisfiable however
    well retrieval had done - the label matched, the numeral could not. On the
    first complete campaign that reported `all_evidence_retrieved` on 1 question
    of 45 where the true figure was 13, and emptied the reasoning stratum that
    H2 is tested on. Every error was then attributed to retrieval by
    construction.
    """

    def test_western_grouping_is_collapsed(self):
        assert degroup("1,234,567") == "1234567"

    def test_indian_grouping_is_collapsed(self):
        """The corpus is Indian filings: 12,34,567 is the same number."""
        assert degroup("12,34,567") == "1234567"
        assert degroup("rs 1,00,000 crore") == "rs 100000 crore"

    def test_a_bare_numeral_is_unchanged(self):
        assert degroup("12232") == "12232"

    def test_mangled_grouping_is_left_alone(self):
        """The reason `normalise` never stripped commas blindly, kept intact.

        "1,2232" is not canonical grouping in either convention, so it must not
        be silently read as 12232.
        """
        assert degroup("1,2232") == "1,2232"

    def test_the_span_matches_across_the_two_renderings(self):
        span = EvidenceSpan(page=13, anchors=("Cost of technical sub-contractors", "12232"))
        printed = "Cost of technical sub-contractors | | 12,232 14,062 |"
        assert span.satisfied_by(13, printed)

    def test_the_span_still_needs_every_anchor(self):
        """Loosening the numeral must not loosen the conjunction."""
        span = EvidenceSpan(page=13, anchors=("Cost of technical sub-contractors", "12232"))
        assert not span.satisfied_by(13, "Travel expenses | | 12,232 |")

    def test_the_span_still_needs_the_right_page(self):
        span = EvidenceSpan(page=13, anchors=("12232",))
        assert not span.satisfied_by(14, "| 12,232 |")

    def test_a_mangled_numeral_does_not_satisfy_a_clean_anchor(self):
        span = EvidenceSpan(page=13, anchors=("12232",))
        assert not span.satisfied_by(13, "| 1,2232 |")


class TestEvidenceGroup:
    def test_any_location_satisfies(self):
        """Infosys' trade payables are on the balance sheet AND in note 2.14.
        Retrieving either is a correct retrieval."""
        g = group("tp", span(12, "3,956"), span(51, "3,956"))
        assert g.satisfied_by(51, "Trade payables 3,956")
        assert g.satisfied_by(12, "Trade payables 3,956")

    def test_neither_location_fails(self):
        g = group("tp", span(12, "3,956"), span(51, "3,956"))
        assert not g.satisfied_by(13, "Trade payables 3,956")


class TestScoring:
    def test_perfect_single_group(self):
        q = question("R01", group("tp", span(12, "Trade payables", "3,956")))
        s = score_question(q, [item("c1", 12, "Trade payables 3,956")], k=10)
        assert (s.recall, s.precision, s.reciprocal_rank) == (1.0, 1.0, 1.0)
        assert s.all_evidence_retrieved

    def test_recall_counts_groups_not_spans(self):
        """The two-location case must score 1.0, not 0.5. Flattening alternative
        locations into required spans would call a correct retrieval a half-miss."""
        q = question("R01", group("tp", span(12, "3,956"), span(51, "3,956")))
        s = score_question(q, [item("c1", 51, "Trade payables 3,956")], k=10)
        assert s.recall == 1.0

    def test_multi_group_partial_is_not_success(self):
        """Return on equity needs profit AND total equity. Retrieving one gives a
        confident, wrong answer - so it must score 0.5, and must not count as
        evidence-retrieved."""
        q = question(
            "R14",
            group("profit", span(13, "26,248")),
            group("equity", span(12, "88,461")),
        )
        s = score_question(q, [item("c1", 13, "Profit for the year 26,248")], k=10)
        assert s.recall == 0.5
        assert not s.all_evidence_retrieved
        assert s.missed_groups == ("equity",)

    def test_reciprocal_rank_is_the_first_relevant_position(self):
        q = question("R", group("g", span(12, "3,956")))
        results = [item("a", 1, "noise"), item("b", 2, "noise"), item("c", 12, "3,956")]
        s = score_question(q, results, k=10)
        assert s.reciprocal_rank == pytest.approx(1 / 3)
        assert s.first_relevant_rank == 3

    def test_k_truncates(self):
        q = question("R", group("g", span(12, "3,956")))
        results = [item("a", 1, "noise"), item("b", 2, "noise"), item("c", 12, "3,956")]
        assert score_question(q, results, k=2).recall == 0.0
        assert score_question(q, results, k=3).recall == 1.0

    def test_precision_denominator_is_what_was_returned(self):
        """Three perfect chunks when ten were asked for is 100% precision, not
        30%. Dividing by k would punish a retriever for the corpus being small."""
        q = question("R", group("g", span(12, "3,956")))
        s = score_question(q, [item("c", 12, "Trade payables 3,956")], k=10)
        assert s.precision == 1.0

    def test_precision_falls_with_irrelevant_neighbours(self):
        q = question("R", group("g", span(12, "3,956")))
        results = [item("c", 12, "Trade payables 3,956"), item("d", 1, "noise")]
        assert score_question(q, results, k=2).precision == 0.5

    def test_empty_retrieval_scores_zero_rather_than_raising(self):
        """A retriever that returns nothing has failed the question. Dropping it
        from the average would hide exactly that failure."""
        q = question("R", group("g", span(12, "3,956")))
        s = score_question(q, [], k=10)
        assert (s.recall, s.precision, s.reciprocal_rank) == (0.0, 0.0, 0.0)
        assert not s.all_evidence_retrieved


class TestAggregate:
    def test_macro_average_over_questions(self):
        q1 = question("A", group("g", span(1, "x")))
        q2 = question(
            "B", group("g1", span(1, "x")), group("g2", span(2, "y")), group("g3", span(3, "z"))
        )
        s1 = score_question(q1, [item("c", 1, "x")], k=10)
        s2 = score_question(q2, [item("c", 1, "x")], k=10)
        # Macro: (1.0 + 1/3) / 2. Micro over spans would give 2/4 = 0.5 and let
        # many-span questions dominate the headline.
        assert aggregate([s1, s2])["recall_at_k"] == pytest.approx((1.0 + 1 / 3) / 2)

    def test_empty_is_zero_not_an_error(self):
        assert aggregate([])["questions"] == 0


class TestTheRealGoldSet:
    """Schema checks on the shipped gold set. Loading is the cheapest place to
    catch a malformed span; --validate-gold checks the anchors against the PDF."""

    def test_loads(self, gold):
        questions = [GoldQuestion.from_dict(q) for q in gold["questions"]]
        assert len(questions) >= 20

    def test_qids_are_unique(self, gold):
        qids = [q["qid"] for q in gold["questions"]]
        assert len(set(qids)) == len(qids)

    def test_is_not_the_test_split(self, gold):
        """This set tunes the embedding choice (D2). If it were ever relabelled
        'test', that tuning would have contaminated the sealed split."""
        assert gold["split"] == "validation"

    def test_every_question_has_evidence(self, gold):
        for record in gold["questions"]:
            parsed = GoldQuestion.from_dict(record)
            assert parsed.evidence
            for grp in parsed.evidence:
                assert grp.any_of
                for sp in grp.any_of:
                    assert sp.anchors and sp.page > 0

    def test_multi_hop_questions_exist(self, gold):
        """A set of only single-lookup questions would measure the easy case and
        report it as retrieval quality."""
        types = {q["question_type"] for q in gold["questions"]}
        assert {"cross_statement_multi_hop", "paraphrase"} <= types
