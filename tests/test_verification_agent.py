"""Tests for the verification agent (spec Module 13).

The arbiter is the one component allowed to see both channel answers, so the
tests here are mostly about the ways that permission could be abused: resolving
when the evidence does not settle it, being told which channel is which, or
having its conclusion counted as channel accuracy.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from backend.agents.evidence import EvidenceBlock
from backend.agents.verification_agent import (
    VERIFICATION_AGENT_PROMPT,
    Resolution,
    parse_verification_response,
    program_goes_first,
    run_verification_agent,
    should_verify,
)
from backend.core.financial_value import Scale, UnitKind, parse_financial_value
from backend.verification.consistency import ChannelAnswer, compare

EVIDENCE = [
    EvidenceBlock(
        ref="E1",
        text="| Particulars | Note | 2024 2023 |\n| Trade payables | 2.14 | 3,956  3,865 |",
        citation="p.12, Consolidated Balance Sheet",
        page=12, scale="crore", currency="INR",
        section="Consolidated Balance Sheet",
    )
]


class StubProvider:
    def __init__(self, text="", error=None):
        self._text, self._error = text, error
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def complete(self, messages, **kwargs):
        self.prompts.append(messages[0].content)
        self.kwargs.append(kwargs)
        if self._error:
            raise self._error
        outer = self

        class R:
            text = outer._text
            usage = None
            model = kwargs.get("model", "stub")

        return R()


def answer(name, text):
    return ChannelAnswer(name, parse_financial_value(text))


class TestTrigger:
    def test_disagreement_triggers_verification(self):
        report = compare(answer("natural", "3956 crore"), answer("program", "3865 crore"))
        assert report.verdict.name == "DISAGREE"
        assert should_verify(report)

    def test_agreement_does_not(self):
        report = compare(answer("natural", "3956 crore"), answer("program", "3956 crore"))
        assert not should_verify(report)

    def test_uncertain_does_not(self):
        """An arbiter cannot adjudicate between an answer and an absence - there
        is no competing claim to weigh, so calling it would spend quota to
        restate the gap."""
        missing = ChannelAnswer("program", None, available=False, failure_reason="crashed")
        report = compare(answer("natural", "3956 crore"), missing)
        assert report.verdict.name == "UNCERTAIN"
        assert not should_verify(report)


class TestParsing:
    def test_supporting_a_candidate(self):
        result = parse_verification_response(
            json.dumps({"resolution": "candidate_1", "answer": 3956, "unit": "INR crore",
                        "reasoning": "E1 row reads 3,956", "evidence_used": ["E1"]})
        )
        assert result.resolution is Resolution.SUPPORTS_A
        assert result.resolved
        assert result.value.amount == Decimal("3956")
        assert result.value.scale is Scale.CRORE

    def test_unresolved_is_not_a_resolution(self):
        """An arbiter that always picks a side manufactures resolution, and a
        manufactured resolution is worse than an open disagreement - the open one
        at least flags the answer as risky."""
        result = parse_verification_response(
            json.dumps({"resolution": "unresolved", "answer": None,
                        "reasoning": "the evidence does not state it"})
        )
        assert result.resolution is Resolution.UNRESOLVED
        assert not result.resolved
        assert result.value is None

    def test_neither_candidate_but_a_figure_is_its_own_outcome(self):
        """"Neither, and here is the figure" resolves; "neither, and I cannot
        say" does not. Collapsing them would overstate resolution accuracy."""
        result = parse_verification_response(
            json.dumps({"resolution": "neither", "answer": 4000, "unit": "INR crore"})
        )
        assert result.resolution is Resolution.OWN_ANSWER
        assert result.resolved

    def test_neither_candidate_with_no_figure_does_not_resolve(self):
        result = parse_verification_response(json.dumps({"resolution": "neither", "answer": None}))
        assert result.resolution is Resolution.SUPPORTS_NEITHER
        assert not result.resolved

    def test_a_percentage_keeps_its_convention(self):
        result = parse_verification_response(
            json.dumps({"resolution": "candidate_2", "answer": 29.77, "unit": "percent"})
        )
        assert result.value.unit_kind is UnitKind.PERCENT
        assert result.value.canonical() == pytest.approx(Decimal("0.2977"))

    def test_a_think_block_does_not_defeat_parsing(self):
        text = '<think>{a} vs {b}</think>{"resolution": "candidate_1", "answer": 3956}'
        assert parse_verification_response(text).resolution is Resolution.SUPPORTS_A

    def test_an_unparseable_reply_abstains(self):
        result = parse_verification_response("I think candidate 1 is right.")
        assert not result.available
        assert not result.resolved

    def test_an_empty_reply_abstains_with_a_diagnosis(self):
        result = parse_verification_response("")
        assert not result.available
        assert "empty reply" in result.failure_reason

    def test_an_unparseable_figure_abstains_rather_than_guessing(self):
        result = parse_verification_response(
            json.dumps({"resolution": "candidate_1", "answer": "about four thousand"})
        )
        assert not result.available
        assert not result.resolved


class TestRun:
    def test_the_prompt_does_not_reveal_which_channel_is_which(self):
        """Telling the arbiter one answer came from executed code invites it to
        defer on authority rather than on evidence, making resolution a function
        of the architecture instead of the filing."""
        provider = StubProvider(json.dumps({"resolution": "candidate_1", "answer": 3956}))
        run_verification_agent(
            "How much were trade payables?", EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        prompt = provider.prompts[0].lower()
        for leak in ("program", "python", "code", "natural language channel",
                     "channel a", "channel b", "sandbox"):
            assert leak not in prompt, f"prompt leaks the channel identity: {leak!r}"

    def test_both_candidate_values_reach_the_prompt(self):
        provider = StubProvider(json.dumps({"resolution": "candidate_1", "answer": 3956}))
        run_verification_agent(
            "q", EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        assert "3956" in provider.prompts[0]
        assert "3865" in provider.prompts[0]

    def test_a_provider_failure_leaves_the_disagreement_standing(self):
        """The safe outcome: the answer stays flagged as risky rather than being
        resolved by a component that did not run."""
        result = run_verification_agent(
            "q", EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=StubProvider(error=RuntimeError("503")), model="m",
        )
        assert not result.available
        assert not result.resolved
        assert result.resolution is Resolution.UNRESOLVED

    def test_an_unavailable_candidate_is_described_not_invented(self):
        provider = StubProvider(json.dumps({"resolution": "unresolved", "answer": None}))
        run_verification_agent(
            "q", EVIDENCE,
            answer("natural", "3956 crore"),
            ChannelAnswer("program", None, available=False, failure_reason="crashed"),
            provider=provider, model="m",
        )
        assert "no answer produced" in provider.prompts[0]

    def test_the_prompt_template_forbids_outside_knowledge(self):
        lowered = VERIFICATION_AGENT_PROMPT.lower()
        assert "only from the evidence" in lowered
        assert "do not use outside knowledge" in lowered


class TestCandidateOrderIsNotChannelIdentity:
    """The prompt hid the channel LABELS and left the POSITIONS fixed (RX-045).

    Channel A was always CANDIDATE 1 and Channel B always CANDIDATE 2, so the
    ordering the prompt takes care not to reveal was revealed by position. Any
    first-position preference in the arbiter then reads as a systematic
    preference for the natural channel - and the arbiter is the one component
    permitted to see both answers, so a bias there is a bias in the resolution
    of every disagreement the system reports.
    """

    def _question_where(self, program_first: bool) -> str:
        for i in range(500):
            q = f"what were trade payables in FY{2000 + i}?"
            if program_goes_first(q) is program_first:
                return q
        raise AssertionError("no question found for that ordering")

    def test_the_assignment_is_deterministic(self):
        """Runs are pinned at temperature 0 (D7a). An arbiter that reordered at
        random would make a campaign irreproducible."""
        q = "what were trade payables as at 31 March 2024?"
        assert program_goes_first(q) == program_goes_first(q) == program_goes_first(q)

    def test_both_orderings_actually_occur(self):
        """A 'randomisation' that always returns the same side is the old bug
        wearing a hash function."""
        seen = {program_goes_first(f"question {i}") for i in range(64)}
        assert seen == {True, False}

    def test_the_program_answer_leads_when_the_question_says_so(self):
        provider = StubProvider(json.dumps({"resolution": "unresolved", "answer": None}))
        q = self._question_where(True)
        run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        prompt = provider.prompts[0]
        assert prompt.index("3865") < prompt.index("3956")

    def test_the_natural_answer_leads_otherwise(self):
        provider = StubProvider(json.dumps({"resolution": "unresolved", "answer": None}))
        q = self._question_where(False)
        run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        prompt = provider.prompts[0]
        assert prompt.index("3956") < prompt.index("3865")

    def test_a_swapped_verdict_is_translated_back_to_the_channel_it_names(self):
        """The important half. Presenting the program answer first and reporting
        'candidate_1' as SUPPORTS_A would credit the natural channel with the
        program channel's answer - silently, and in the artifact."""
        q = self._question_where(True)
        provider = StubProvider(json.dumps({"resolution": "candidate_1", "answer": 3865}))
        result = run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        assert result.resolution is Resolution.SUPPORTS_B

    def test_an_unswapped_verdict_is_left_alone(self):
        q = self._question_where(False)
        provider = StubProvider(json.dumps({"resolution": "candidate_1", "answer": 3956}))
        result = run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        assert result.resolution is Resolution.SUPPORTS_A

    @pytest.mark.parametrize("label", ["neither", "unresolved"])
    def test_verdicts_that_name_no_candidate_are_never_flipped(self, label):
        """`neither` and `unresolved` name no position, so swapping them would be
        a bug introduced by the fix rather than one removed by it."""
        q = self._question_where(True)
        provider = StubProvider(json.dumps({"resolution": label, "answer": None}))
        result = run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        assert result.resolution is {
            "neither": Resolution.SUPPORTS_NEITHER,
            "unresolved": Resolution.UNRESOLVED,
        }[label]

    def test_the_artifact_records_which_order_was_shown(self):
        """Without this the effect could never be measured after the fact - which
        is exactly the position the project was in when RX-045 was written."""
        q = self._question_where(True)
        provider = StubProvider(json.dumps({"resolution": "unresolved", "answer": None}))
        result = run_verification_agent(
            q, EVIDENCE,
            answer("natural", "3956 crore"), answer("program", "3865 crore"),
            provider=provider, model="m",
        )
        assert result.metadata["candidate_1"] == "program"
