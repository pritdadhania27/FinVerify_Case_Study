"""Tests for pipeline orchestration (spec Module 17).

The orchestrator is where an evaluation goes wrong quietly. Every arm runs
through it, so a fault here does not break one arm - it shifts every arm by the
same unknown amount and leaves the comparison looking clean. These tests pin the
properties the ablation's validity depends on: that arms differ only in what
their config says, that the disagreement branch fires only on disagreement, and
that Channel B still cannot see Channel A.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from backend.agents import orchestrator
from backend.agents.orchestrator import (
    ArmConfig,
    PipelineDeps,
    PipelineResult,
    build_graph,
    run_question,
    self_consistency_risk,
)
from backend.core.financial_value import parse_financial_value
from backend.services.llm.base import (
    AuthError,
    ProviderUnavailableError,
    QuotaExhaustedError,
)
from backend.verification.consistency import ChannelAnswer, compare


def fv(text: str):
    return parse_financial_value(text)


class FakeResult:
    """Stands in for a channel result: only `.answer` and `.sufficient` are read."""

    def __init__(self, value=None, *, available=True, applicable=True, sufficient=True,
                 reason=None, name="chan"):
        self.answer = ChannelAnswer(
            name=name, value=value, available=available,
            failure_reason=reason, applicable=applicable,
        )
        self.sufficient = sufficient
        self.executed = available


class StubProvider:
    """Returns a fixed JSON reply and records every prompt it was shown."""

    def __init__(self, text: str, error: Exception | None = None):
        self._text, self._error = text, error
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(" ".join(m.content for m in messages))
        if self._error is not None:
            raise self._error
        outer = self

        class R:
            text = outer._text
            usage = None
            model = kwargs.get("model", "stub")

        return R()


class StubRetriever:
    """Returns nothing. Retrieval quality is measured in its own module."""

    def retrieve(self, question, *, top_k=10, **filters):
        return []

    def retrieve_arms(self, question, *, top_k=10, **filters):
        return {"semantic": [], "keyword": [], "fused": []}


NATURAL_JSON = (
    '{"answer": 3956, "unit": "INR crore", "reasoning": "read from the balance '
    'sheet", "evidence_used": ["E1"], "figures_used": ["3,956"], "sufficient": true}'
)


def deps(**kw):
    base = dict(
        retriever=StubRetriever(),
        natural_provider=StubProvider(NATURAL_JSON),
        program_provider=StubProvider("```python\nprint(3956)\n```"),
        verifier_provider=StubProvider('{"resolution": "abstain", "answer": null}'),
        natural_model="model-a",
        program_model="model-b",
        verifier_model="model-c",
    )
    base.update(kw)
    return PipelineDeps(**base)


class TestArmsAreConfigurationsNotForks:
    """The reason this module exists: an ablation is only an ablation if
    everything except the ablated component is held constant."""

    def test_the_graph_shape_does_not_depend_on_the_arm(self):
        assert inspect.signature(build_graph).parameters == {}

    def test_a_disabled_component_is_visibly_skipped_not_absent(self):
        """A reader must be able to confirm from the artifact that arm C really
        ran without the program channel, rather than trusting the arm's name."""
        config = ArmConfig("C", use_program=False, use_consistency=False,
                           use_verification_agent=False)
        result = run_question("How much were trade payables?", config=config, deps=deps())
        skipped = {entry["node"] for entry in result.node_log if entry.get("skipped")}
        assert "program" in skipped
        assert {e["node"] for e in result.node_log} >= {
            "understand", "retrieve", "natural", "program", "deterministic",
            "consistency", "assess",
        }


class TestChannelIndependenceSurvivesTheGraph:
    """D1 as a research-validity gate. If this fails, fix the code, not the test."""

    def test_the_program_node_never_sees_channel_a(self):
        config = ArmConfig("P")
        program_provider = StubProvider("```python\nprint(3956)\n```")
        d = deps(program_provider=program_provider)
        run_question("How much were trade payables?", config=config, deps=d)
        combined = " ".join(program_provider.prompts).lower()
        # Markers of Channel A's payload specifically. Not generic words: the
        # program prompt legitimately contains "insufficient" as its own
        # abstention instruction, and matching that would fail a correct system.
        # The evidence set is empty here, so 3956 could only have come from
        # Channel A's answer.
        for leaked in ("3956", "read from the balance sheet", "channel a",
                       "figures_used", "evidence_used"):
            assert leaked not in combined, f"Channel A content {leaked!r} reached Channel B"

    def test_the_program_channel_signature_still_admits_no_channel_a_argument(self):
        from backend.agents.program_channel import run_program_channel

        names = set(inspect.signature(run_program_channel).parameters)
        assert not (names & {"natural", "channel_a", "other_answer", "peer_answer"})


class TestBranchOnDisagreement:
    """The one conditional edge in the graph, driven both ways.

    Channel B is substituted with a fixed result rather than run through the
    sandbox, so the verdict is decided by the test and not by whether Docker is up
    or whether a stubbed program happens to print valid JSON.
    """

    def _program_says(self, monkeypatch, text):
        monkeypatch.setattr(
            orchestrator,
            "run_program_channel",
            lambda *args, **kwargs: FakeResult(fv(text), name="program"),
        )

    def test_the_arbiter_does_not_run_when_the_channels_agree(self, monkeypatch):
        """This used to pass for the wrong reason. It stubbed Channel B with
        `print(3956)`, which the sandbox rejects - its final line must be a JSON
        object - so Channel B produced nothing, the verdict was UNCERTAIN, and the
        arbiter was skipped because a channel was missing. The channels never
        agreed. The verdict is now asserted, so the test cannot pass that way."""
        self._program_says(monkeypatch, "INR 3956 crore")
        verifier = StubProvider('{"resolution": "abstain", "answer": null}')
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig("P"),
            deps=deps(verifier_provider=verifier),
        )
        assert result.report.verdict.name == "AGREE"
        assert verifier.calls == 0

    def test_a_disagreement_reaches_the_arbiter(self, monkeypatch):
        """The branch nothing exercised. Every graph-level arbiter test asserted
        `verifier.calls == 0`; the edge from DISAGREE to the arbiter was covered
        only by the campaigns happening to produce seventeen disagreements."""
        self._program_says(monkeypatch, "INR 4100 crore")
        verifier = StubProvider('{"resolution": "abstain", "answer": null}')
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig("P"),
            deps=deps(verifier_provider=verifier),
        )
        assert result.report.verdict.name == "DISAGREE"
        assert verifier.calls == 1
        assert result.verification is not None
        # The arbiter sees both answers - D1 permits exactly this component to.
        combined = " ".join(verifier.prompts)
        assert "3956" in combined and "4100" in combined

    def test_an_uncertain_verdict_does_not_spend_an_arbiter_call(self, monkeypatch):
        """UNCERTAIN means a channel produced nothing: there is no competing claim
        to weigh, so arbitrating would spend quota to restate the gap."""
        monkeypatch.setattr(
            orchestrator,
            "run_program_channel",
            lambda *args, **kwargs: FakeResult(None, available=False, reason="no figure",
                                               name="program"),
        )
        verifier = StubProvider('{"resolution": "abstain", "answer": null}')
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig("P"),
            deps=deps(verifier_provider=verifier),
        )
        assert result.report.verdict.name == "UNCERTAIN"
        assert verifier.calls == 0

    def test_the_arbiter_is_skipped_entirely_when_the_arm_disables_it(self):
        verifier = StubProvider('{"resolution": "abstain", "answer": null}')
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig("E", use_verification_agent=False),
            deps=deps(verifier_provider=verifier),
        )
        assert verifier.calls == 0
        assert result.verification is None


class TestConfigValidation:
    def test_an_arm_with_no_reasoning_channel_is_refused(self):
        problems = ArmConfig("bad", use_natural=False, use_program=False).validate()
        assert any("no reasoning channel" in p for p in problems)

    def test_consistency_with_only_one_channel_is_refused(self):
        problems = ArmConfig("bad", use_program=False, use_deterministic=False).validate()
        assert any("nothing to compare" in p for p in problems)

    def test_dropping_the_natural_channel_still_leaves_a_cross_check(self):
        """Ablation B: program + deterministic is two channels, not one.

        Requiring both LLM channels here would refuse a valid ablation arm.
        """
        assert ArmConfig("B", use_natural=False).validate() == ()

    def test_the_arbiter_cannot_run_without_the_consistency_engine(self):
        problems = ArmConfig("bad", use_consistency=False).validate()
        assert any("consistency engine" in p for p in problems)

    def test_one_self_consistency_sample_measures_nothing(self):
        problems = ArmConfig(
            "bad", use_program=False, use_consistency=False,
            use_verification_agent=False, self_consistency_samples=1,
        ).validate()
        assert any("n >= 2" in p for p in problems)

    def test_running_an_invalid_arm_raises_rather_than_producing_a_row(self):
        with pytest.raises(ValueError):
            run_question(
                "q",
                config=ArmConfig("bad", use_program=False, use_deterministic=False),
                deps=deps(),
            )

    def test_every_validation_problem_is_reported_at_once(self):
        """A campaign should list every bad arm, not die on the first."""
        problems = ArmConfig("bad", retrieval="telepathy", use_consistency=False).validate()
        assert len(problems) >= 2


class TestDetectionEligibility:
    """A baseline with no detector must not enter the detection table at 0.5."""

    def test_a_single_channel_rag_arm_provides_no_detection_score(self):
        assert ArmConfig(
            "B2", use_program=False, use_consistency=False, use_verification_agent=False
        ).provides_detection_score is False

    def test_self_consistency_does_provide_one(self):
        assert ArmConfig(
            "B5", use_program=False, use_consistency=False,
            use_verification_agent=False, self_consistency_samples=5,
        ).provides_detection_score is True

    def test_the_full_system_provides_one(self):
        assert ArmConfig("P").provides_detection_score is True


class TestSelfConsistencyRisk:
    """B5 is the comparator H1 must beat, so its score must not be handicapped."""

    def test_unanimous_samples_are_zero_risk(self):
        assert self_consistency_risk([fv("100"), fv("100"), fv("100")]) == pytest.approx(0.0)

    def test_a_split_raises_the_risk(self):
        split = self_consistency_risk([fv("100"), fv("100"), fv("250")])
        unanimous = self_consistency_risk([fv("100"), fv("100"), fv("100")])
        assert split > unanimous

    def test_the_score_is_not_quantised_to_n_levels(self):
        """The confound this estimator exists to avoid.

        `1 - modal_fraction` alone takes only n values, so AUROC would count
        most B5 pairs as ties worth half a concordance - and the full system
        would win partly because the baseline was rounded off. Two runs with the
        same 2-1 split must still be ordered by how far apart their answers were.
        """
        near = self_consistency_risk([fv("100"), fv("100"), fv("110")])
        far = self_consistency_risk([fv("100"), fv("100"), fv("100000")])
        assert near != far
        assert far > near

    def test_fewer_than_two_samples_is_none_not_zero(self):
        assert self_consistency_risk([fv("100")]) is None
        assert self_consistency_risk([None, None]) is None

    def test_unusable_samples_do_not_count_as_agreement(self):
        """A model that failed to answer twice has not agreed with itself."""
        assert self_consistency_risk([fv("100"), None, None]) is None

    def test_the_score_stays_in_range(self):
        assert 0.0 <= self_consistency_risk([fv("1"), fv("-1000000")]) <= 1.0


class TestAnswerSelection:
    def _result(self, **kw) -> PipelineResult:
        return PipelineResult(question="q", config=ArmConfig("P"), **kw)

    def test_the_natural_channel_answers_by_default(self):
        r = self._result(natural=FakeResult(fv("100")), program=FakeResult(fv("200")))
        assert r.answer_source == "natural"

    def test_the_program_channel_answers_when_channel_a_failed(self):
        r = self._result(
            natural=FakeResult(None, available=False), program=FakeResult(fv("200"))
        )
        assert r.answer_source == "program"

    def test_the_deterministic_verifier_is_a_last_resort_not_a_discard(self):
        r = self._result(
            natural=FakeResult(None, available=False),
            program=FakeResult(None, available=False),
            deterministic=FakeResult(fv("300")),
        )
        assert r.answer_source == "deterministic"

    def test_no_channel_produced_a_number(self):
        r = self._result(natural=FakeResult(None, available=False))
        assert r.answer is None
        assert r.answer_source is None

    def test_a_declined_answer_is_an_abstention_not_a_parse_failure(self):
        r = self._result(natural=FakeResult(None, available=False, sufficient=False))
        assert r.abstained is True

    def test_an_unparseable_reply_is_not_an_abstention(self):
        r = self._result(natural=FakeResult(None, available=False, sufficient=True))
        assert r.abstained is False

    def test_a_program_that_ran_and_returned_null_is_an_abstention(self):
        """The program channel makes the same decision the natural channel does -
        it prints {"value": null} when the evidence does not support an answer -
        and this property used to ignore it. Invisible on any arm that also has a
        natural channel; on B3, which is the program channel alone, it filed 2 of
        the first 9 rows as parse failures when they were clean runs that
        correctly declined."""

        class Executed:
            answer = FakeResult(None, available=False).answer
            executed = True

        r = self._result(natural=None, program=Executed())
        assert r.abstained is True

    def test_a_program_that_crashed_is_not_an_abstention(self):
        """`executed` is `execution.ok`, so a timeout, a crash or a non-JSON
        final line stays a failure. Only a clean run that chose to return nothing
        is a decision."""

        class Crashed:
            answer = FakeResult(None, available=False).answer
            executed = False

        r = self._result(natural=None, program=Crashed())
        assert r.abstained is False

    def test_a_program_that_answered_is_not_an_abstention(self):
        r = self._result(natural=None, program=FakeResult(fv("42")))
        assert r.abstained is False


class TestRetryDoesNotBurnQuota:
    """ENGINEERING_RULES.md: a retry loop over the adapter's own is how a day disappears."""

    def test_a_quota_error_is_never_retried(self):
        provider = StubProvider("", error=QuotaExhaustedError("groq: quota exhausted"))
        errors: list[str] = []
        with pytest.raises(QuotaExhaustedError):
            orchestrator._retry(
                lambda: provider.complete([]), attempts=3, on_error=errors
            )
        assert provider.calls == 1

    def test_an_auth_error_is_never_retried(self):
        provider = StubProvider("", error=AuthError("bad key"))
        with pytest.raises(AuthError):
            orchestrator._retry(lambda: provider.complete([]), attempts=3, on_error=[])
        assert provider.calls == 1

    def test_transient_unavailability_is_retried_up_to_the_limit(self):
        provider = StubProvider("", error=ProviderUnavailableError("503"))
        errors: list[str] = []
        with pytest.raises(ProviderUnavailableError):
            orchestrator._retry(
                lambda: provider.complete([]), attempts=2, on_error=errors
            )
        assert provider.calls == 3
        assert len(errors) == 3

    def test_retries_default_to_off(self):
        assert ArmConfig("P").max_transient_retries == 0


class TestQuotaExhaustionIsNotAnAbstention:
    """The defect this guards was silent and expensive.

    Both channels catch provider failures and return an unavailable channel,
    which is correct - one channel failing is an expected event. But a quota
    exhaustion caught that way lets a campaign run for hours writing rows in
    which every channel is unavailable, and those rows are indistinguishable in
    the artifact from genuine abstentions. They corrupt the abstention rate, the
    parse-failure rate, and the detection labels of a run that looks complete.
    """

    def test_the_channel_records_the_exception_class_not_just_its_message(self):
        from backend.agents.natural_channel import run_natural_channel

        result = run_natural_channel(
            "q", [],
            provider=StubProvider("", error=QuotaExhaustedError("spent")),
            model="m",
        )
        assert result.metadata["error_type"] == "QuotaExhaustedError"

    def test_the_orchestrator_re_raises_it(self):
        with pytest.raises(QuotaExhaustedError):
            run_question(
                "How much were trade payables?",
                config=ArmConfig("B2", use_program=False, use_deterministic=False,
                                 use_consistency=False, use_verification_agent=False),
                deps=deps(
                    natural_provider=StubProvider("", error=QuotaExhaustedError("spent"))
                ),
            )

    def test_the_LOCAL_limiter_refusal_stops_the_run_too(self):
        """RX-033: the one that actually fires, and the one that was missed.

        `QuotaExhaustedError` is the provider refusing mid-call. The refusal
        that comes first in practice is `DailyQuotaExhausted` - the local
        limiter declining to make the call at all - and while the two were
        unrelated classes this guard saw only the former. A campaign then ran
        to "completion": 180 rows, `failed 0`, and Channel A unavailable on 166
        of them.
        """
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        with pytest.raises(QuotaExhaustedError):
            run_question(
                "How much were trade payables?",
                config=ArmConfig("B2", use_program=False, use_deterministic=False,
                                 use_consistency=False, use_verification_agent=False),
                deps=deps(
                    natural_provider=StubProvider(
                        "", error=DailyQuotaExhausted("groq: daily token allowance spent")
                    )
                ),
            )

    def test_every_quota_class_is_covered_not_just_the_two_known_today(self):
        """The names come from the hierarchy, so a third class is covered on
        creation rather than on someone remembering to add it here."""
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        # Named through the classes, not as string literals: `__subclasses__`
        # only sees what has been imported, so referencing the class here is
        # what makes this test exercise the real condition.
        covered = orchestrator._quota_error_names()
        assert {QuotaExhaustedError.__name__, DailyQuotaExhausted.__name__} <= covered

    def test_a_local_refusal_is_a_kind_of_quota_error(self):
        """Asserted at the type level: every `except QuotaExhaustedError` in the
        codebase - campaign.py's stop handler included - depends on this."""
        from backend.services.llm.ratelimit import DailyQuotaExhausted

        assert issubclass(DailyQuotaExhausted, QuotaExhaustedError)

    def test_an_ordinary_channel_failure_still_does_not_stop_the_run(self):
        """Only quota ends a campaign. A model that returned nonsense is data."""
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig("P"),
            deps=deps(natural_provider=StubProvider("not json at all")),
        )
        assert result.natural.answer.available is False
        assert result.as_record()["arm"] == "P"


class TestClosedBook:
    def test_b1_retrieves_nothing_by_configuration_not_by_failure(self):
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig(
                "B1", retrieval="none", use_program=False,
                use_consistency=False, use_verification_agent=False,
            ),
            deps=deps(),
        )
        entry = next(e for e in result.node_log if e["node"] == "retrieve")
        assert entry["mode"] == "none"
        assert entry["blocks"] == 0

    def test_risk_is_none_when_the_arm_has_no_detector(self):
        result = run_question(
            "How much were trade payables?",
            config=ArmConfig(
                "B1", retrieval="none", use_program=False,
                use_consistency=False, use_verification_agent=False,
            ),
            deps=deps(),
        )
        assert result.risk_score is None


class TestRecordIsAuditable:
    def test_the_record_carries_the_arm_and_the_node_log(self):
        result = run_question("How much were trade payables?", config=ArmConfig("P"),
                              deps=deps())
        record = result.as_record()
        assert record["arm"] == "P"
        assert record["node_log"]
        assert "risk_score" in record

    def test_no_evidence_still_produces_a_gradable_row(self):
        """A retrieval failure must not lose the question from the denominator."""
        result = run_question("How much were trade payables?", config=ArmConfig("P"),
                              deps=deps())
        assert result.as_record()["evidence"] == []

    def test_every_row_carries_its_explanation(self):
        """Module 16's acceptance evidence, and it was missing from 787 rows.

        `explain` was reachable only through the API's live-QA path, which D30
        turns off by default, so three complete campaigns produced no explanation
        at all and the module could not be shown to work on anything real. It is
        derived from the result object (D29), so recording it costs no API call.
        """
        record = run_question("How much were trade payables?", config=ArmConfig("P"),
                              deps=deps()).as_record()
        assert "explanation" in record
        # Not merely present: the risk band is the field a reader looks at first,
        # and an explanation that agrees with nothing is worse than none.
        assert record["explanation"]["band"]
        assert record["explanation"]["risk_score"] == record["risk_score"]
        assert record["explanation"]["answer_source"] == record["answer_source"]

    def test_the_arbiter_row_records_which_channel_it_saw_first(self):
        """RX-045's audit trail has to reach the ARTIFACT, not just the object.

        `run_verification_agent` records `candidate_1` so a position effect stays
        measurable after the fact - and this serialiser dropped it, so the field
        existed in memory and never in a run. The 225-row ablation campaign was
        written that way. A claim that something "is recorded" is about the file
        someone will read, not the dataclass that briefly held it.
        """
        from backend.agents.verification_agent import Resolution, VerificationResult

        result = PipelineResult(
            question="q",
            config=ArmConfig("P"),
            verification=VerificationResult(
                resolution=Resolution.SUPPORTS_B,
                metadata={"candidate_1": "program", "model": "m"},
            ),
        )
        recorded = result.as_record()["verification"]
        assert recorded["triggered"] is True
        assert recorded["metadata"]["candidate_1"] == "program"

    def test_a_row_with_no_arbiter_carries_no_metadata_key(self):
        """`triggered: False` stays a bare marker - inventing an empty metadata
        block would make "the arbiter did not run" and "it ran and recorded
        nothing" indistinguishable in the artifact."""
        record = run_question("How much were trade payables?", config=ArmConfig("P"),
                              deps=deps()).as_record()
        assert record["verification"] == {"triggered": False}


class TestRiskDirectionMatchesTheMetrics:
    def test_disagreement_scores_riskier_than_agreement(self):
        """The whole detection result inverts if this ever flips."""
        from backend.verification.confidence import assess

        agree = compare(
            ChannelAnswer("a", fv("100")), ChannelAnswer("b", fv("100")),
        )
        disagree = compare(
            ChannelAnswer("a", fv("100")), ChannelAnswer("b", fv("250")),
        )
        assert assess(agree, evidence_blocks=3).risk_score < assess(
            disagree, evidence_blocks=3
        ).risk_score


class TestFailureCases:
    def test_an_unknown_retrieval_mode_is_caught_before_any_api_call(self):
        provider = StubProvider(NATURAL_JSON)
        with pytest.raises(ValueError):
            run_question(
                "q",
                config=ArmConfig("bad", retrieval="telepathy"),
                deps=deps(natural_provider=provider),
            )
        assert provider.calls == 0

    def test_a_question_with_no_searchable_terms_does_not_crash(self):
        result = run_question("???", config=ArmConfig("P"), deps=deps())
        assert result.as_record()["arm"] == "P"

    def test_zero_evidence_blocks_raise_the_risk_score(self):
        result = run_question("How much were trade payables?", config=ArmConfig("P"),
                              deps=deps())
        assert result.risk is not None
        assert result.risk.risk_score >= 0.9


def test_sample_tolerance_matches_the_consistency_engine():
    """One definition of 'the same answer' across the project."""
    from backend.verification.consistency import DEFAULT_TOLERANCE

    assert orchestrator._SAMPLE_TOLERANCE == DEFAULT_TOLERANCE == Decimal("0.005")


# --------------------------------------------------------------------------
# Retrieval scope (RX-012). A research-validity gate: without the company,
# retrieval searches all five filings and evidence accuracy falls 0.909 -> 0.727
# on the gold set, most of the loss being the right figure taken from the wrong
# company's report. If one of these fails, the fix is the code.
# --------------------------------------------------------------------------


class ScopeRecordingRetriever(StubRetriever):
    """Captures the filters retrieval was actually asked for."""

    def __init__(self):
        self.calls: list[dict] = []

    def retrieve(self, question, *, top_k=10, **filters):
        self.calls.append(dict(filters))
        return []

    def retrieve_arms(self, question, *, top_k=10, **filters):
        self.calls.append(dict(filters))
        return {"semantic": [], "keyword": [], "fused": []}


def test_the_questions_company_reaches_retrieval():
    """One PipelineDeps serves a whole campaign, so its company is necessarily
    None. The company must therefore travel with the question."""
    retriever = ScopeRecordingRetriever()
    run_question(
        "For HDFC Bank Limited, what were total deposits?",
        config=ArmConfig("A"),
        deps=deps(retriever=retriever, company=None),
        company="HDFC Bank Limited",
    )
    assert retriever.calls, "retrieval was never called"
    assert any(
        call.get("company") == "HDFC Bank Limited" for call in retriever.calls
    ), f"company never reached the retriever; filters seen: {retriever.calls}"


def test_a_per_question_company_overrides_the_campaign_default():
    retriever = ScopeRecordingRetriever()
    run_question(
        "For Tata Motors Limited, what was revenue?",
        config=ArmConfig("A"),
        deps=deps(retriever=retriever, company="Infosys Limited"),
        company="Tata Motors Limited",
    )
    assert all(
        call.get("company") != "Infosys Limited" for call in retriever.calls
    ), "the campaign-wide default won over the question's own company"


def test_without_a_company_retrieval_is_unscoped_rather_than_wrongly_scoped():
    """The failure mode must stay honest: no company means no filter, never a
    guessed one. A wrong filter would return the wrong company's evidence with
    no signal that anything was assumed."""
    retriever = ScopeRecordingRetriever()
    run_question(
        "what were total deposits?",
        config=ArmConfig("A"),
        deps=deps(retriever=retriever, company=None),
    )
    assert all(not call.get("company") for call in retriever.calls)
