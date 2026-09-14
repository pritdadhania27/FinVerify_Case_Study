"""Tests for the two reasoning channels (spec Modules 8, 9).

The independence tests in `TestChannelIndependence` are **research-validity
gates**, not ordinary unit tests. If one fails, the finding is that the dual
channel design has been compromised - the fix is the code, never the test.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal

import pytest

from backend.agents import natural_channel, program_channel
from backend.agents.evidence import (
    EvidenceBlock,
    evidence_from_results,
    format_evidence,
)
from backend.agents.natural_channel import (
    NATURAL_CHANNEL_PROMPT,
    parse_natural_response,
    run_natural_channel,
)
from backend.agents.program_channel import (
    PROGRAM_CHANNEL_PROMPT,
    extract_program,
    run_program_channel,
)
from backend.core.financial_value import Scale, UnitKind
from backend.services.sandbox import ExecutionStatus

EVIDENCE = [
    EvidenceBlock(
        ref="E1",
        text="| Particulars | Note | 2024 2023 |\n| Trade payables | 2.14 | 3,956  3,865 |",
        citation="p.12, Consolidated Balance Sheet, table 1",
        page=12,
        scale="crore",
        currency="INR",
        section="Consolidated Balance Sheet",
    )
]


class StubProvider:
    """No network, no quota. Records what it was asked."""

    def __init__(self, text="", error=None, usage=None):
        self._text, self._error, self._usage = text, error, usage
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
            usage = outer._usage
            model = kwargs.get("model", "stub")

        return R()


class TestEvidenceFormatting:
    def test_units_and_citation_travel_with_the_figures(self):
        """A figure without its units and provenance is a number, not evidence."""
        rendered = format_evidence(EVIDENCE)
        assert "p.12" in rendered
        assert "INR crore" in rendered
        assert "3,956" in rendered

    def test_empty_evidence_is_stated_explicitly(self):
        """A model handed an empty string answers from memory, and an answer
        from memory about a specific filing is the hallucination being studied."""
        rendered = format_evidence([])
        assert "NO EVIDENCE" in rendered
        assert "must not answer from prior knowledge" in rendered

    def test_truncation_is_announced(self):
        block = EvidenceBlock("E1", "x" * 5000, "p.1")
        rendered = block.render(max_chars=100)
        assert "[evidence truncated]" in rendered

    def test_results_convert_in_rank_order(self):
        class R:
            def __init__(self, t, page):
                self.text, self.payload = t, {"page": page, "citation": f"p.{page}"}

        blocks = evidence_from_results([R("a", 1), R("b", 2)], max_blocks=5)
        assert [b.ref for b in blocks] == ["E1", "E2"]


class TestNaturalChannelParsing:
    def test_a_clean_answer(self):
        result = parse_natural_response(
            json.dumps({"answer": 3956, "unit": "INR crore", "reasoning": "read E1",
                        "evidence_used": ["E1"], "figures_used": ["3,956"],
                        "sufficient": True})
        )
        assert result.answer.available
        assert result.answer.value.amount == Decimal("3956")
        assert result.answer.value.scale is Scale.CRORE
        assert result.evidence_used == ("E1",)

    def test_the_stated_scale_is_kept(self):
        """Reading "1,37,814 crore" as 137814 is a 10-million-fold error that
        looks entirely plausible. The unit the model stated is carried through."""
        result = parse_natural_response(
            json.dumps({"answer": "1,37,814", "unit": "INR crore"})
        )
        assert result.answer.value.scale is Scale.CRORE
        assert result.answer.value.canonical() == Decimal("1378140000000")

    def test_a_percentage_answer(self):
        result = parse_natural_response(json.dumps({"answer": 29.7, "unit": "percent"}))
        assert result.answer.value.unit_kind is UnitKind.PERCENT
        assert result.answer.value.canonical() == Decimal("0.297")

    def test_json_wrapped_in_prose_is_recovered(self):
        text = 'Here is my answer:\n```json\n{"answer": 470, "unit": "INR crore"}\n```\nDone.'
        assert parse_natural_response(text).answer.value.amount == Decimal("470")

    def test_a_declined_answer_is_unavailable_not_zero(self):
        """null must never become 0. A channel that declined has not agreed with
        anything."""
        result = parse_natural_response(
            json.dumps({"answer": None, "sufficient": False, "reasoning": "not in evidence"})
        )
        assert not result.answer.available
        assert result.answer.value is None
        assert "insufficient" in result.answer.failure_reason

    def test_an_empty_reply_is_a_failure_with_a_diagnosis(self):
        """These models return "" with HTTP 200 when the reasoning budget is
        spent (D17); passing that on as an answer would be worse than the
        failure it masks."""
        result = parse_natural_response("")
        assert not result.answer.available
        assert "empty reply" in result.answer.failure_reason

    def test_unparseable_prose_is_a_failure_not_a_guess(self):
        result = parse_natural_response("I think it was about four thousand crore.")
        assert not result.answer.available

    def test_a_non_numeric_answer_is_rejected(self):
        result = parse_natural_response(json.dumps({"answer": "not available", "unit": None}))
        assert not result.answer.available


class TestNaturalChannelRun:
    def test_a_provider_outage_yields_an_unavailable_channel(self):
        """One channel failing is an expected event the consistency engine
        handles, not a pipeline error."""
        result = run_natural_channel(
            "q", EVIDENCE, provider=StubProvider(error=RuntimeError("503")), model="m"
        )
        assert not result.answer.available
        assert "RuntimeError" in result.answer.failure_reason

    def test_the_prompt_carries_the_evidence_and_the_question(self):
        provider = StubProvider(json.dumps({"answer": 1, "unit": None}))
        run_natural_channel("How much were trade payables?", EVIDENCE,
                            provider=provider, model="m")
        assert "trade payables" in provider.prompts[0].lower()
        assert "3,956" in provider.prompts[0]

    def test_generous_token_budget_by_default(self):
        """A small budget is spent on hidden reasoning and returns nothing (D17)."""
        provider = StubProvider(json.dumps({"answer": 1}))
        run_natural_channel("q", EVIDENCE, provider=provider, model="m")
        assert provider.kwargs[0]["max_tokens"] >= 1024

    def test_temperature_is_pinned_at_zero(self):
        provider = StubProvider(json.dumps({"answer": 1}))
        run_natural_channel("q", EVIDENCE, provider=provider, model="m")
        assert provider.kwargs[0]["temperature"] == 0.0


class TestProgramExtraction:
    def test_a_fenced_program_is_unwrapped(self):
        """Models are told not to fence and fence anyway. Feeding the markers to
        the validator produces a syntax error that reads as a safety refusal."""
        assert extract_program("```python\nprint(1)\n```") == "print(1)"

    def test_a_bare_program_passes_through(self):
        assert extract_program("print(1)") == "print(1)"

    def test_empty_text(self):
        assert extract_program("") == ""


class TestProgramChannelRun:
    PROGRAM = (
        "import json\n"
        "from decimal import Decimal\n"
        "payables = Decimal('3956')  # E1\n"
        'print(json.dumps({"value": str(payables), "unit": "INR crore", "steps": ["read E1"]}))\n'
    )

    def test_a_rejected_program_is_never_executed(self):
        provider = StubProvider("import os\nos.system('ls')\n")
        result = run_program_channel("q", EVIDENCE, provider=provider, model="m")
        assert not result.answer.available
        assert "allowlist" in result.answer.failure_reason
        assert result.execution is None, "validation failed, so nothing should have run"

    def test_an_empty_reply_is_a_failure(self):
        result = run_program_channel("q", EVIDENCE, provider=StubProvider(""), model="m")
        assert not result.answer.available
        assert "no program" in result.answer.failure_reason

    def test_a_provider_outage_yields_an_unavailable_channel(self):
        result = run_program_channel(
            "q", EVIDENCE, provider=StubProvider(error=RuntimeError("boom")), model="m"
        )
        assert not result.answer.available
        assert result.program == ""

    def test_the_prompt_asks_for_code_and_forbids_prose(self):
        provider = StubProvider(self.PROGRAM)
        run_program_channel("q", EVIDENCE, provider=provider, model="m")
        prompt = provider.prompts[0]
        assert "Python program" in prompt
        assert "Decimal" in prompt, "float arithmetic on money is a silent corruption"


def _docker_up() -> bool:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, timeout=20, check=False,
            ).returncode
            == 0
        )
    except Exception:  # noqa: BLE001 - absence of Docker is the thing being detected
        return False


requires_docker = pytest.mark.skipif(
    not _docker_up(),
    reason="Docker unavailable - the program channel cannot be verified, and is "
           "SKIPPED rather than passed so a green run never reads as verified",
)


@requires_docker
class TestProgramChannelEndToEnd:
    """The program channel against a real container. Skipped, never passed, when
    Docker is down - the same discipline as the sandbox suite, because 'the code
    ran safely' is a claim that requires the container to have actually run."""

    def test_a_valid_program_produces_a_scaled_financial_value(self):
        program = (
            "import json\n"
            "from decimal import Decimal\n"
            "payables = Decimal('3956')  # E1, INR crore\n"
            'print(json.dumps({"value": str(payables), "unit": "INR crore",\n'
            '                  "steps": ["took 3,956 from E1"]}))\n'
        )
        result = run_program_channel(
            "How much were trade payables?", EVIDENCE,
            provider=StubProvider(program), model="m",
        )
        assert result.answer.available, result.answer.failure_reason
        assert result.executed
        assert result.answer.value.amount == Decimal("3956")
        assert result.answer.value.scale is Scale.CRORE
        assert result.answer.value.canonical() == Decimal("39560000000")
        assert result.steps == ("took 3,956 from E1",)

    def test_a_program_that_abstains_is_not_an_answer_of_zero(self):
        """Executed-and-declined is materially different from crashed, and both
        are different from answering 0."""
        program = (
            "import json\n"
            'print(json.dumps({"value": None, "unit": None,\n'
            '                  "steps": ["evidence does not contain this figure"]}))\n'
        )
        result = run_program_channel(
            "q", EVIDENCE, provider=StubProvider(program), model="m"
        )
        assert not result.answer.available
        assert result.answer.value is None
        assert result.executed, "it ran; it just declined"
        assert "insufficient" in result.answer.failure_reason

    def test_a_zero_denominator_is_a_channel_failure_not_a_crash(self):
        """Spec section 38 failure case. Chosen over `raise` deliberately: the
        AST allowlist rejects `raise` before execution, so it would test the
        validator rather than the runtime path. Division reaches the container."""
        program = (
            "import json\n"
            "from decimal import Decimal\n"
            "margin = Decimal('100') / Decimal('0')  # E1\n"
            'print(json.dumps({"value": str(margin), "unit": "percent", "steps": []}))\n'
        )
        result = run_program_channel(
            "q", EVIDENCE, provider=StubProvider(program), model="m"
        )
        assert not result.answer.available
        assert result.execution is not None, "it must have reached the container"
        assert not result.execution.ok
        assert result.execution.status is not ExecutionStatus.BLOCKED

    def test_a_percentage_result_canonicalises_as_a_fraction(self):
        """29.7% and 0.297 must compare equal, or the consistency engine reports
        a disagreement that is purely a unit convention."""
        program = (
            "import json\n"
            'print(json.dumps({"value": 29.7, "unit": "percent", "steps": []}))\n'
        )
        result = run_program_channel(
            "q", EVIDENCE, provider=StubProvider(program), model="m"
        )
        assert result.answer.available, result.answer.failure_reason
        assert result.answer.value.unit_kind is UnitKind.PERCENT
        assert result.answer.value.canonical() == Decimal("0.297")


class TestUnitMapping:
    @pytest.mark.parametrize(
        ("unit", "kind", "scale"),
        [
            ("INR crore", UnitKind.CURRENCY, Scale.CRORE),
            ("percent", UnitKind.PERCENT, Scale.UNIT),
            ("ratio", UnitKind.RATIO, Scale.UNIT),
            ("Rs. lakh", UnitKind.CURRENCY, Scale.LAKH),
        ],
    )
    def test_stated_units_map_onto_the_value_model(self, unit, kind, scale):
        got_kind, got_scale, _ = program_channel._unit_kind_for(unit)
        assert got_kind is kind
        assert got_scale is scale


class TestChannelIndependence:
    """RESEARCH-VALIDITY GATES (D1).

    If Channel B can see Channel A's answer it will anchor on it, agreement will
    rise, and that agreement will measure anchoring rather than independent
    corroboration - making the central research question unanswerable while
    appearing to answer it well. A failure here is a bug in the code, never a
    test to update.
    """

    CHANNEL_A_FIELDS = (
        "answer", "reasoning", "evidence_used", "figures_used", "sufficient",
        "confidence", "natural", "channel_a", "channela",
    )

    def test_the_program_channel_cannot_be_given_channel_a_output(self):
        """Enforced by the signature, so it cannot be reintroduced by a caller
        who forgot."""
        params = set(inspect.signature(run_program_channel).parameters)
        for forbidden in ("natural_result", "channel_a", "other_answer", "prior_answer",
                          "natural_answer", "hint"):
            assert forbidden not in params

    def test_the_program_prompt_contains_no_channel_a_scaffold(self):
        lowered = PROGRAM_CHANNEL_PROMPT.lower()
        for term in ("channel a", "the other channel", "previous answer",
                     "another model", "reasoning above", "confidence"):
            assert term not in lowered

    def test_the_two_prompts_are_structurally_disjoint(self):
        """Not merely different wording: different task, different output
        contract. A shared reasoning scaffold would correlate their errors."""
        assert "Python" not in NATURAL_CHANNEL_PROMPT
        assert "code" in NATURAL_CHANNEL_PROMPT.lower()  # only to forbid it
        assert "reasoning" not in PROGRAM_CHANNEL_PROMPT.lower()
        assert "json.dumps" in PROGRAM_CHANNEL_PROMPT

    def test_a_rendered_program_prompt_leaks_nothing_from_a_completed_channel_a(self):
        """The end-to-end version of the gate: run Channel A, then render Channel
        B's prompt, and assert none of Channel A's output appears in it."""
        natural = run_natural_channel(
            "How much were trade payables?",
            EVIDENCE,
            provider=StubProvider(
                json.dumps({"answer": 3956, "unit": "INR crore",
                            "reasoning": "SENTINEL_REASONING_STRING",
                            "evidence_used": ["E1"], "figures_used": ["3,956"]})
            ),
            model="m",
        )
        assert natural.answer.available

        provider = StubProvider(TestProgramChannelRun.PROGRAM)
        run_program_channel("How much were trade payables?", EVIDENCE,
                            provider=provider, model="m")
        rendered = provider.prompts[0]
        assert "SENTINEL_REASONING_STRING" not in rendered
        assert "3956" not in rendered.replace("3,956", ""), (
            "Channel A's answer must not appear; the evidence figure 3,956 may"
        )

    def test_neither_channel_module_imports_the_other(self):
        """A shared import is how coupling gets reintroduced quietly."""
        assert "natural_channel" not in inspect.getsource(program_channel)
        assert "program_channel" not in inspect.getsource(natural_channel)


class TestUnanswerableQuestions:
    """Retrieval must not manufacture evidence for a question that asked nothing.

    Measured against the live index before the fix, the empty question returned
    three arbitrary tables - foreign-currency analysis, segment reporting - which
    would then have been handed to BOTH reasoning channels as evidence. Since the
    channels are instructed to use only the evidence given, that is the same
    failure as answering from memory, one layer down: the system would have
    supplied a plausible context for a question with no content.
    """

    @pytest.mark.parametrize("question", ["", "?", "What?", "the of and", "   "])
    def test_a_contentless_question_yields_no_sub_questions(self, question):
        from backend.agents.question_understanding import parse_question

        spec = parse_question(question)
        assert spec.sub_questions == ()
        assert not spec.is_answerable
        assert spec.ambiguities, "the reason must be recorded, not silent"

    def test_a_real_question_is_answerable(self):
        from backend.agents.question_understanding import parse_question

        spec = parse_question("What were total assets as at March 31, 2024?")
        assert spec.is_answerable
        assert spec.sub_questions

    def test_channels_are_told_to_refuse_when_evidence_is_empty(self):
        """The end of the chain: no sub-questions -> no evidence -> an explicit
        refusal instruction rather than an empty prompt section."""
        rendered = format_evidence([])
        assert "NO EVIDENCE" in rendered
        assert "must not answer from prior knowledge" in rendered


class TestReasoningTraceHandling:
    """Reasoning models externalise their chain of thought in the CONTENT field.

    Qwen on Groq replies "<think>...</think>{json}". Before this was handled,
    binding Qwen to Channel A made EVERY answer come back UNAVAILABLE - which
    reads as "the channel does not work" rather than "the parser cannot read this
    model" - and a <think> block reaching Channel B's AST validator reported a
    syntax error, misattributing a formatting quirk to the security layer.

    Swapping a model binding is not configuration; it changes the output
    contract, and these tests are what catch that.
    """

    def test_channel_a_reads_json_after_a_think_block(self):
        text = (
            "<think>\nLooking at E1 I see {row: Trade payables} and 3,956.\n</think>\n"
            '{"answer": 3956, "unit": "INR crore"}'
        )
        result = parse_natural_response(text)
        assert result.answer.available, result.answer.failure_reason
        assert result.answer.value.amount == Decimal("3956")

    def test_braces_inside_an_unclosed_think_block_do_not_swallow_the_json(self):
        """The original greedy regex matched first-brace-to-last-brace and parsed
        as nothing. This is the exact shape that produced that failure."""
        text = '<think>\nI need {a} / {b} here.\n{"answer": 3956, "unit": "INR crore"}'
        assert parse_natural_response(text).answer.value.amount == Decimal("3956")

    def test_the_last_object_wins(self):
        """A model that revises itself leaves the rejected figure earlier in the
        reply. Preferring it would record a number the model had discarded."""
        text = (
            '{"answer": 111, "unit": "INR crore"} '
            'then actually {"answer": 222, "unit": "INR crore"}'
        )
        assert parse_natural_response(text).answer.value.amount == Decimal("222")

    def test_a_think_block_with_no_answer_is_still_a_failure(self):
        result = parse_natural_response("<think>I am not sure.</think>")
        assert not result.answer.available

    def test_channel_b_strips_the_trace_before_validation(self):
        """Otherwise the AST validator reports 'invalid syntax' and the failure
        looks like the security layer refusing unsafe code."""
        from backend.services.code_validator import validate_program

        text = (
            "<think>\nI should compute it.\n</think>\n"
            'import json\nprint(json.dumps({"value": 1}))'
        )
        program = extract_program(text)
        assert not program.startswith("<think>")
        assert validate_program(program).ok

    def test_braces_inside_json_strings_do_not_break_the_scanner(self):
        from backend.agents.model_output import balanced_objects

        assert balanced_objects('{"note": "a } brace", "answer": 1}') == [
            '{"note": "a } brace", "answer": 1}'
        ]


class TestTruncatedReasoning:
    """A reply cut off mid-thought is full of candidates the model was still
    weighing. Taking the first one hands back a figure the model went on to
    REJECT, and reports it with available=True.

    Measured on a real Qwen reply: the model wrote "First guess: {...3956...}.
    No - the question asks for the consolidated figure, so I should use" and was
    cut off. The channel returned 3956. That converts a genuine channel failure
    into a confident wrong number which then enters the agreement comparison as
    if it were real data - so both the disagreement rate and AUROC would be
    computed partly over fabricated answers.
    """

    TRUNCATED = (
        "<think>\n"
        "E1 shows standalone trade payables 3,956 and E2 shows consolidated 4,102.\n"
        'First guess: {"answer": 3956, "unit": "INR crore"}. No - the question asks\n'
        "for the consolidated figure, so I should use"
    )

    def test_an_abandoned_candidate_is_not_an_answer(self):
        result = parse_natural_response(self.TRUNCATED)
        assert not result.answer.available
        assert result.answer.value is None

    def test_an_answer_at_the_very_end_still_parses(self):
        """The fix must not buy safety by rejecting good replies: an unclosed
        <think> followed by a complete object is still an answer."""
        text = '<think>I need {a}/{b} here.\n{"answer": 3956, "unit": "INR crore"}'
        assert parse_natural_response(text).answer.value.amount == Decimal("3956")

    def test_a_closed_think_block_is_unaffected(self):
        text = '<think>reasoning {x}</think>{"answer": 222, "unit": "INR crore"}'
        assert parse_natural_response(text).answer.value.amount == Decimal("222")

    def test_channel_b_also_refuses_a_truncated_trace(self):
        """extract_program shares the same helper, so the guard covers it too."""
        assert extract_program(self.TRUNCATED).strip() == ""


class TestUnitVocabularyParity:
    """Channel B kept a SECOND copy of the unit words - five long-form labels,
    no abbreviations, no trillion. Channel A parsed the same string through
    parse_financial_value and got the full set, so identical unit text resolved
    to scales up to 10^12 apart and the consistency engine reported
    SCALE_MISMATCH.

    That is a parser gap manufacturing the project's headline error class:
    inflating the disagreement rate, depressing AUROC, and poisoning the exact
    cell of the error taxonomy the research claims to detect.
    """

    @pytest.mark.parametrize(
        "unit",
        ["INR trillion", "Rs cr", "Rs mn", "USD bn", "INR lacs", "Rs. '000",
         "INR crore", "EUR million", "Rs. thousand crore"],
    )
    def test_both_channels_resolve_the_same_scale(self, unit):
        from backend.core.financial_value import parse_financial_value

        _, channel_b_scale, _ = program_channel._unit_kind_for(unit)
        channel_a = parse_financial_value(f"3956 {unit}")
        assert channel_a is not None
        assert channel_b_scale is channel_a.scale, (
            f"{unit!r}: Channel B says {channel_b_scale.label}, "
            f"Channel A says {channel_a.scale.label}"
        )

    def test_percent_and_ratio_are_still_handled_separately(self):
        """parse_financial_value has no RATIO vocabulary, so that branch stays
        hand-written and must keep working."""
        assert program_channel._unit_kind_for("percent")[0] is UnitKind.PERCENT
        assert program_channel._unit_kind_for("ratio")[0] is UnitKind.RATIO
        assert program_channel._unit_kind_for("times")[0] is UnitKind.RATIO

    def test_an_unrecognised_unit_stays_unknown_not_wrong(self):
        """UNKNOWN is compatible with everything in the consistency engine, so an
        unrecognised unit costs precision rather than manufacturing a mismatch."""
        kind, scale, _ = program_channel._unit_kind_for("widgets")
        assert kind is UnitKind.UNKNOWN
        assert scale is Scale.UNIT
