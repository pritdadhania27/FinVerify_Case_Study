"""Tests for evidence-grounded explanation (spec Module 16).

The property under test is the one that makes the module worth having: every
sentence is derived from recorded state, so nothing in an explanation can be
true-sounding and false. A generated explanation would be fluent and
unfalsifiable; a wrong number with a convincing justification is more dangerous
than a wrong number alone, which is the whole reason this project exists.
"""

from __future__ import annotations

import inspect

from backend.agents.evidence import EvidenceBlock
from backend.agents.orchestrator import ArmConfig, PipelineResult
from backend.core.financial_value import parse_financial_value
from backend.verification import explanation as explanation_module
from backend.verification.confidence import assess
from backend.verification.consistency import ChannelAnswer, Verdict, compare
from backend.verification.explanation import explain, render_text


def fv(text: str):
    return parse_financial_value(text)


class FakeChannel:
    def __init__(self, value=None, *, available=True, applicable=True, reason=None,
                 name="chan", sufficient=True):
        self.answer = ChannelAnswer(
            name=name, value=value, available=available,
            failure_reason=reason, applicable=applicable,
        )
        self.sufficient = sufficient


BLOCKS = (
    EvidenceBlock(
        ref="E1",
        text="| Trade payables | 3,956 |",
        citation="p.12, Consolidated Balance Sheet, table 1",
        page=12,
        chunk_id="c1",
    ),
)


def result(**kw) -> PipelineResult:
    base = dict(
        question="How much were trade payables?",
        config=ArmConfig("A"),
        blocks=BLOCKS,
        natural=FakeChannel(fv("3,956 crore"), name="natural"),
        program=FakeChannel(fv("3,956 crore"), name="program"),
        deterministic=FakeChannel(
            None, applicable=False, available=False,
            reason="no arithmetic to check on a lookup", name="deterministic",
        ),
    )
    base.update(kw)
    report = compare(
        base["natural"].answer,
        base["program"].answer,
        base["deterministic"].answer if base.get("deterministic") else None,
    )
    base.setdefault("report", report)
    base.setdefault(
        "risk",
        assess(report, evidence_blocks=len(base["blocks"]), deterministic_applied=False),
    )
    return PipelineResult(**base)


class TestExplanationsAreDerivedNotGenerated:
    """The module's reason for existing, asserted rather than documented."""

    def test_the_module_calls_no_provider(self):
        source = inspect.getsource(explanation_module)
        for forbidden in ("provider", "complete(", "run_natural_channel", "LLMProvider"):
            assert forbidden not in source, (
                f"{forbidden!r} appears in the explanation module: an explanation "
                "produced by a model can be fluent and wrong, which is worse than "
                "no explanation"
            )

    def test_explain_takes_only_the_result(self):
        parameters = list(inspect.signature(explain).parameters)
        assert parameters == ["result"]


class TestTheFourQuestions:
    def test_it_says_what_the_answer_is_and_where_it_came_from(self):
        e = explain(result())
        assert "3956" in (e.answer or "") or "3,956" in (e.answer or "")
        assert e.answer_source == "natural"
        assert any("p.12" in c for c in e.citations)

    def test_it_reports_what_each_channel_said(self):
        e = explain(result())
        joined = " ".join(e.channel_statements)
        assert "Channel A" in joined and "Channel B" in joined

    def test_it_names_the_specific_signal_behind_the_risk(self):
        e = explain(result())
        assert any(r.startswith("agreement:") for r in e.reasons)
        assert any(r.startswith("evidence:") for r in e.reasons)

    def test_it_says_what_would_change_the_verdict(self):
        e = explain(result())
        assert e.what_would_change_it


class TestInapplicableIsNotAGap:
    def test_a_lookup_says_the_deterministic_verifier_does_not_apply(self):
        """Not "the check failed". Conflating the two is what biased the score
        before the coverage penalty was fixed."""
        e = explain(result())
        joined = " ".join(e.reasons)
        assert "does not apply" in joined or "not_applicable" in joined

    def test_a_channel_that_could_not_run_is_distinguished_from_one_that_failed(self):
        e = explain(
            result(program=FakeChannel(None, available=False, reason="model timeout",
                                       name="program"))
        )
        joined = " ".join(e.channel_statements)
        assert "produced no figure" in joined
        assert "model timeout" in joined


class TestRiskDrivesTheProse:
    def test_disagreement_is_named_and_actionable(self):
        e = explain(
            result(
                natural=FakeChannel(fv("3,956 crore"), name="natural"),
                program=FakeChannel(fv("9,999 crore"), name="program"),
            )
        )
        assert e.risk_score > 0.5
        assert any("Resolve the disagreement" in r for r in e.what_would_change_it)

    def test_no_evidence_dominates_every_other_remedy(self):
        """With nothing retrieved, agreement is agreement about nothing."""
        e = explain(result(blocks=()))
        assert len(e.what_would_change_it) == 1
        assert "Retrieve evidence" in e.what_would_change_it[0]
        assert any("No evidence was retrieved" in c for c in e.caveats)

    def test_an_uncertain_verdict_asks_for_a_second_figure(self):
        e = explain(
            result(program=FakeChannel(None, available=False, reason="no program",
                                       name="program"))
        )
        assert e.band != "UNSCORED"
        assert any("second channel" in r for r in e.what_would_change_it)

    def test_an_uncertain_verdict_does_not_claim_the_channels_agree(self):
        """PARTIAL agreement means a channel gave no figure, not a near miss.

        The prose said "the channels broadly agree but not within tolerance" on
        every UNCERTAIN answer - including a live answer whose program channel had
        timed out and produced nothing to agree with.
        """
        e = explain(
            result(program=FakeChannel(None, available=False, reason="timeout",
                                       name="program"))
        )
        agreement = next(r for r in e.reasons if r.startswith("agreement:"))
        evidence = next(r for r in e.reasons if r.startswith("evidence:"))
        assert "PARTIAL" in agreement
        assert "agree" not in agreement.split(" - ", 1)[1]
        assert "fewer than two channels" in agreement
        assert "thin" not in evidence

    def test_an_agreeing_run_still_states_the_residual_risk(self):
        """The blind spot, named in the explanation rather than only in the paper."""
        e = explain(result())
        assert any("both channels and the evidence could be wrong together" in r
                   or "could be wrong about together" in r
                   for r in e.what_would_change_it)


class TestCaveats:
    def test_an_uncalibrated_score_says_so(self):
        e = explain(result())
        assert any("UNCALIBRATED" in c for c in e.caveats)
        assert any("not a probability" in c for c in e.caveats)

    def test_a_deterministic_fallback_answer_is_flagged(self):
        e = explain(
            result(
                natural=FakeChannel(None, available=False, reason="parse failure",
                                    name="natural"),
                program=FakeChannel(None, available=False, reason="execution failed",
                                    name="program"),
                deterministic=FakeChannel(fv("3,956 crore"), name="deterministic"),
            )
        )
        assert e.answer_source == "deterministic"
        assert any("deterministic verifier because neither" in c for c in e.caveats)


class TestUnscoredArms:
    def test_an_arm_with_no_risk_score_makes_no_reliability_claim(self):
        e = explain(
            PipelineResult(
                question="q",
                config=ArmConfig("B2", use_program=False, use_consistency=False,
                                 use_verification_agent=False),
                blocks=BLOCKS,
                natural=FakeChannel(fv("3,956 crore"), name="natural"),
            )
        )
        assert e.band == "UNSCORED"
        assert e.risk_score is None
        assert any("no reliability claim" in r for r in e.reasons)


class TestRendering:
    def test_the_text_rendering_carries_every_section(self):
        text = render_text(explain(result()))
        for heading in ("ANSWER:", "EVIDENCE", "WHAT EACH CHANNEL SAID", "WHY",
                        "WHAT WOULD CHANGE IT"):
            assert heading in text

    def test_an_unanswered_question_renders_without_crashing(self):
        e = explain(
            result(
                natural=FakeChannel(None, available=False, reason="x", name="natural"),
                program=FakeChannel(None, available=False, reason="y", name="program"),
            )
        )
        text = render_text(e)
        assert "no answer produced" in text

    def test_serialisation_round_trips_every_field(self):
        payload = explain(result()).as_dict()
        assert set(payload) == {
            "answer", "answer_source", "risk_score", "band", "citations",
            "channel_statements", "reasons", "what_would_change_it", "caveats",
        }


def test_the_verdict_in_the_explanation_matches_the_report():
    """A rendering that disagreed with the computation would be the exact
    failure this module is built to avoid."""
    outcome = result(
        natural=FakeChannel(fv("3,956 crore"), name="natural"),
        program=FakeChannel(fv("9,999 crore"), name="program"),
    )
    assert outcome.report.verdict is Verdict.DISAGREE
    assert explain(outcome).risk_score == outcome.risk.risk_score
