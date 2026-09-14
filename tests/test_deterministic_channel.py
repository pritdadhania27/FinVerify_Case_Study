"""Tests for the deterministic third opinion (spec Module 10, D6).

This channel is the only component that can overrule two agreeing reasoning
channels, which is what makes it worth having: two channels can only agree or
disagree, and the both-agree-wrong cell (`EVALUATION.md` §5.4) is the method's
blind spot. That also makes a wrong-and-confident deterministic answer the worst
possible failure here - hence the emphasis below on refusing to bind rather than
guessing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.agents.evidence import EvidenceBlock
from backend.agents.question_understanding import parse_question
from backend.core.financial_value import UnitKind
from backend.verification.deterministic_channel import run_deterministic_channel
from backend.verification.operand_binding import bind_operands

PL = EvidenceBlock(
    ref="E1",
    text=(
        "Consolidated Statement of Profit and Loss\n"
        "(All figures in INR crore)\n"
        "| Particulars | Note | 2024 2023 |\n"
        "|---|---|---|\n"
        "| Revenue from operations | 2.18 | 153,670  146,767 |\n"
        "| Profit for the year |  | 26,248  24,108 |\n"
    ),
    citation="p.13, Consolidated Statement of Profit and Loss",
    page=13, scale="crore", currency="INR",
    section="Consolidated Statement of Profit and Loss",
)

BS = EvidenceBlock(
    ref="E2",
    text=(
        "Consolidated Balance Sheet\n"
        "(All figures in INR crore)\n"
        "| Particulars | Note | 2024 2023 |\n"
        "|---|---|---|\n"
        "| Total equity |  | 88,461  75,795 |\n"
        "| Total assets |  | 1,37,814  1,25,816 |\n"
    ),
    citation="p.12, Consolidated Balance Sheet",
    page=12, scale="crore", currency="INR",
    section="Consolidated Balance Sheet",
)


class TestOperandBinding:
    def test_a_figure_binds_with_the_chunk_scale(self):
        """The cell says "26,248"; only the chunk caption says crore. Binding the
        bare numeral would be a 10-million-fold error."""
        result = bind_operands(["profit for the year"], [PL])
        assert result.complete
        operand = result.operands[0]
        assert operand.value.amount == Decimal("26248")
        assert operand.value.canonical() == Decimal("262480000000")
        assert "p.13" in operand.citation

    def test_the_reporting_year_column_is_taken_by_default(self):
        assert bind_operands(["profit for the year"], [PL]).operands[0].value.amount == (
            Decimal("26248")
        )

    def test_the_comparative_column_can_be_selected(self):
        """Taking the wrong year is a plausible-looking wrong answer, so it is an
        explicit parameter rather than a guess."""
        result = bind_operands(["profit for the year"], [PL], column=1)
        assert result.operands[0].value.amount == Decimal("24108")

    def test_an_alias_binds(self):
        assert bind_operands(["net profit"], [PL]).complete

    def test_indian_digit_grouping_binds(self):
        result = bind_operands(["total assets"], [BS])
        assert result.operands[0].value.amount == Decimal("137814")

    def test_a_note_reference_is_not_mistaken_for_a_figure(self):
        """"2.18" is a note number, not a quantity. Binding it would produce a
        confident answer built from nonsense."""
        operand = bind_operands(["revenue from operations"], [PL]).operands[0]
        assert operand.value.amount == Decimal("153670")

    def test_an_absent_metric_is_reported_not_guessed(self):
        result = bind_operands(["goodwill"], [PL])
        assert not result.complete
        assert result.unbound == ("goodwill",)
        assert result.notes

    def test_a_longer_row_label_does_not_match(self):
        """"Profit for the year" must not bind to "Profit for the year
        attributable to non-controlling interests"."""
        block = EvidenceBlock(
            ref="E9",
            text=(
                "(All figures in INR crore)\n"
                "| Profit for the year attributable to non-controlling interests |  | 15 |\n"
            ),
            citation="p.14", scale="crore", currency="INR",
        )
        assert not bind_operands(["profit for the year"], [block]).complete

    def test_disagreeing_matches_refuse_to_choose(self):
        """A figure that differs between two statements is a restatement or a
        misidentification. Picking one silently is the provenance failure the
        HDFC Bank corpus note warns about."""
        other = EvidenceBlock(
            ref="E3",
            text="(All figures in INR crore)\n| Total equity |  | 99,999 |\n",
            citation="p.18", scale="crore", currency="INR",
        )
        result = bind_operands(["total equity"], [BS, other])
        assert not result.complete
        assert "refusing to choose" in result.notes[0]

    def test_agreeing_matches_in_two_places_bind_fine(self):
        duplicate = EvidenceBlock(
            ref="E3",
            text="(All figures in INR crore)\n| Total equity |  | 88,461 |\n",
            citation="p.18", scale="crore", currency="INR",
        )
        assert bind_operands(["total equity"], [BS, duplicate]).complete


class TestDeterministicChannel:
    def test_a_lookup_question_abstains_by_construction(self):
        """Re-reading the figure would be structural corroboration, not an
        independent check, and would inflate apparent agreement."""
        spec = parse_question("What were total assets as at March 31, 2024?")
        result = run_deterministic_channel(spec, [BS])
        assert not result.answer.available
        assert "no arithmetic to verify" in result.answer.failure_reason

    def test_return_on_equity_is_computed_with_no_model_in_the_loop(self):
        spec = parse_question(
            "What was the company's return on equity for the year ended March 31, 2024?"
        )
        result = run_deterministic_channel(spec, [PL, BS])
        assert result.answer.available, result.answer.failure_reason
        # 26,248 / 88,461 = 0.2967 -> 29.67%
        assert result.answer.value.unit_kind is UnitKind.RATIO
        assert result.answer.canonical == pytest.approx(Decimal("0.2967"), abs=Decimal("0.001"))

    def test_a_percentage_result_does_not_lose_a_factor_of_100(self):
        """CalculationResult holds a FRACTION (0.25) and FinancialValue holds a
        PERCENTAGE (25). Passing one straight into the other canonicalises to
        0.0025 - a silent 100x error inside the component meant to catch them."""
        spec = parse_question("What share of total assets did goodwill represent?")
        block = EvidenceBlock(
            ref="E1",
            text=(
                "(All figures in INR crore)\n"
                "| Goodwill |  | 7,303 |\n"
                "| Total assets |  | 1,37,814 |\n"
            ),
            citation="p.11", scale="crore", currency="INR",
        )
        result = run_deterministic_channel(spec, [block])
        assert result.answer.available, result.answer.failure_reason
        # 7,303 / 1,37,814 = 5.30%, i.e. a canonical fraction of 0.0530.
        assert result.answer.canonical == pytest.approx(Decimal("0.053"), abs=Decimal("0.001"))

    def test_an_unbindable_operand_abstains_with_the_reason(self):
        spec = parse_question("What was the company's return on equity?")
        result = run_deterministic_channel(spec, [PL])  # no balance sheet
        assert not result.answer.available
        assert "could not bind" in result.answer.failure_reason
        assert result.binding is not None and result.binding.unbound

    def test_it_never_sees_the_reasoning_channels(self):
        """The same independence constraint as the program channel (D1): a
        verifier that can see the answers it is checking is not a verifier."""
        import inspect

        params = set(inspect.signature(run_deterministic_channel).parameters)
        for forbidden in ("natural", "program", "channel_a", "channel_b", "answers"):
            assert forbidden not in params


class TestTheColumnHazard:
    """`column=0` is the reporting year, and nothing passes a different one.

    `orchestrator.py` calls `run_deterministic_channel(spec, blocks)` with no
    `column`, so the binder always reads the reporting year - while 72% of
    FinVerify-IND asks about a PRIOR year (D36). That would be a serious defect
    in a channel the consistency engine weights highly enough to overrule two
    agreeing channels.

    It is not one today, and the reason is narrow: the channel abstains on
    lookups, and every prior-year question in this dataset is a lookup. The
    blast radius is zero (RX-025), so the proportionate response is a guard
    rather than a fix to code that is currently correct.

    If this test fails, a prior-year question that triggers arithmetic has
    entered the dataset, and `column` must be selected from `spec.fiscal_year`
    against the table's own column headers before the campaign runs.
    """

    def dataset_questions(self):
        import json

        from backend.core.paths import project_path

        path = project_path("datasets/finverify_ind/finverify_ind_v1.json")
        if not path.exists():
            import pytest

            pytest.skip("FinVerify-IND not generated")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload["questions"] if isinstance(payload, dict) else payload

    def test_no_prior_year_question_reaches_the_arithmetic_path(self):
        from backend.agents.question_understanding import parse_question

        offenders = []
        for question in self.dataset_questions():
            spec = parse_question(question["question"], company=question.get("company"))
            if spec.operation == "lookup":
                continue
            year = spec.fiscal_year
            # The corpus is entirely FY2023-24 filings, so any other asked-about
            # year means the answer sits in a comparative column, not column 0.
            if year and year != "2023-24":
                offenders.append(f"{question['qid']} ({spec.operation}, {year})")

        assert not offenders, (
            "these questions trigger the deterministic channel AND ask about a "
            f"prior year, so column=0 reads the wrong column: {offenders[:10]}. "
            "Select the column from spec.fiscal_year against the table's own "
            "headers before running the campaign (RX-025)."
        )

    def test_the_channel_still_abstains_on_lookups(self):
        """The property the guard above depends on. If this changes, the column
        hazard becomes live across 256 questions rather than none."""
        from backend.agents.question_understanding import parse_question
        from backend.verification.deterministic_channel import run_deterministic_channel

        spec = parse_question("What were trade payables as at March 31, 2023?")
        assert spec.operation == "lookup"
        result = run_deterministic_channel(spec, [])
        assert not result.answer.available
        assert not result.answer.applicable, (
            "a lookup must be INAPPLICABLE, not merely unavailable - the "
            "coverage penalty counts achievable corroboration, not absolute"
        )


class TestResolvingTheMetricAQuestionAsksAbout:
    """`metric_in_text` exists for a lookup path that is NOT yet enabled.

    `parse_question` hands the deterministic channel a sub-question metric with
    the boilerplate still attached - "HDFC Bank Limited, reported provisions
    provisions reported consolidated financial statements" - which `_labels_for`
    cannot match against any table row. Resolving the metric against the lexicon
    is the first half of letting this channel check lookups.

    The second half is not built, and RX-041 records why: on the oracle run's
    evidence the binder binds 31 of 45 lookups and agrees with gold on **8**,
    because `column=0` takes the note-reference cell. Enabling lookups on top of
    that would manufacture a confident wrong third opinion, which the consistency
    engine weights highly enough to overrule two agreeing channels.
    """

    def test_it_finds_a_metric_buried_in_boilerplate(self):
        from backend.verification.operand_binding import metric_in_text

        assert metric_in_text(
            "HDFC Bank Limited, reported provisions provisions reported "
            "consolidated financial statements"
        ) == "provisions"

    def test_the_longest_alias_wins(self):
        """"other financial liabilities" and "financial liabilities" are both in
        the lexicon, and the shorter one binds a different row."""
        from backend.verification.operand_binding import metric_in_text

        assert metric_in_text(
            "what were other financial liabilities as reported"
        ) == "other financial liabilities"

    def test_it_returns_the_canonical_name_not_the_spelling_that_matched(self):
        """So `_labels_for` expands to every alias rather than the one form that
        happened to appear in the question."""
        from backend.verification.operand_binding import metric_in_text

        assert metric_in_text("what was the net worth at year end") == "total equity"

    def test_a_question_about_nothing_in_the_lexicon_returns_none(self):
        """None means "cannot check this", never "the evidence is wrong"."""
        from backend.verification.operand_binding import metric_in_text

        assert metric_in_text("what is the weather today") is None
        assert metric_in_text("") is None

    def test_it_does_not_match_inside_a_longer_word(self):
        from backend.verification.operand_binding import metric_in_text

        assert metric_in_text("reinvestments were discussed") != "investments"


class TestLookupsStillAbstain:
    """Pinned deliberately: the channel must NOT check lookups yet.

    RX-041 measured what happens if it does - 23 wrong bindings in 31 - so this
    test fails loudly if someone enables the path without first solving column
    identification.
    """

    def test_a_lookup_is_not_applicable(self):
        from backend.agents.question_understanding import parse_question
        from backend.verification.deterministic_channel import run_deterministic_channel

        spec = parse_question("For Acme Ltd, what were total assets in FY2024?")
        result = run_deterministic_channel(spec, [])
        assert result.answer.available is False
        assert result.answer.applicable is False, (
            "enabling lookups needs the note-reference column solved first - RX-041"
        )
