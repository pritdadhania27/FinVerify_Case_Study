"""Tests for experiment management and the arm catalogue (spec Modules 23, 24, 28).

The campaign runner's failure modes are all silent and all expensive. A resume
that re-runs finished work burns quota the campaign does not have. A resume that
skips *unfinished* work leaves a hole in the artifact that no metric will notice,
because a missing row simply shrinks the denominator. And a question that dies
mid-campaign must leave a recorded failure rather than vanishing, or the paired
comparison silently stops being paired.
"""

from __future__ import annotations

import json

import pytest

from backend.agents.orchestrator import ArmConfig, PipelineDeps
from backend.services.llm.base import QuotaExhaustedError
from evaluation.arms import (
    ABLATIONS,
    ALL_ARMS,
    BASELINES,
    arm,
    detection_arms,
    validate_all,
)
from experiments.campaign import (
    CampaignRecorder,
    estimate_requests,
    run_campaign,
)


class StubProvider:
    def __init__(self, text: str, *, fail_after: int | None = None):
        self._text = text
        self._fail_after = fail_after
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self._fail_after is not None and self.calls > self._fail_after:
            raise QuotaExhaustedError("groq: daily free-tier allowance is spent")
        outer = self

        class R:
            text = outer._text
            usage = None
            model = "stub"

        return R()


class StubRetriever:
    def retrieve(self, question, *, top_k=10, **filters):
        return []

    def retrieve_arms(self, question, *, top_k=10, **filters):
        return {"semantic": [], "keyword": [], "fused": []}


NATURAL_JSON = (
    '{"answer": 100, "unit": "INR crore", "reasoning": "r", "evidence_used": [], '
    '"figures_used": [], "sufficient": true}'
)

QUESTIONS = [
    {"id": "Q1", "question": "How much were trade payables?", "stratum": "lookup"},
    {"id": "Q2", "question": "What was the revenue growth?", "stratum": "computed"},
]

SIMPLE_ARM = ArmConfig(
    "B2",
    use_program=False,
    use_deterministic=False,
    use_consistency=False,
    use_verification_agent=False,
)


def deps(**kw) -> PipelineDeps:
    base = dict(
        retriever=StubRetriever(),
        natural_provider=StubProvider(NATURAL_JSON),
        program_provider=StubProvider("```python\nprint(100)\n```"),
        verifier_provider=StubProvider('{"resolution": "abstain", "answer": null}'),
        natural_model="a",
        program_model="b",
        verifier_model="c",
    )
    base.update(kw)
    return PipelineDeps(**base)


class TestArmCatalogue:
    def test_every_arm_is_a_valid_configuration(self):
        """Caught before a campaign spends anything, not on day two."""
        assert validate_all() == {}

    def test_the_spec_baselines_are_all_present(self):
        assert {c.name for c in BASELINES} == {"B1", "B2", "B3", "B4", "B5"}

    def test_the_spec_ablation_arms_are_all_present(self):
        assert {c.name for c in ABLATIONS} == set("ABCDEFGH")

    def test_p_and_a_are_the_same_object(self):
        """EVALUATION.md §7 calls it P and §8 calls it A. Two names, one system."""
        assert arm("P") is arm("A")

    def test_arms_differ_from_the_full_system_in_exactly_one_field(self):
        """An ablation that changed two things would not isolate either."""
        full = arm("A")
        for name in "BCEFGH":
            changed = [
                key
                for key, value in arm(name).as_dict().items()
                if key not in {"name", "description", "provides_detection_score"}
                and value != full.as_dict()[key]
            ]
            assert len(changed) == 1, f"arm {name} changed {changed}"

    def test_removing_consistency_also_removes_the_arbiter(self):
        """Arm D changes two fields, and legitimately: the arbiter is triggered
        BY the consistency verdict, so it cannot survive its removal."""
        d = arm("D")
        assert d.use_consistency is False
        assert d.use_verification_agent is False

    def test_non_detector_baselines_stay_out_of_the_detection_table(self):
        names = {c.name for c in detection_arms()}
        assert names & {"B1", "B2", "B3", "B4"} == set()
        assert "B5" in names and "A" in names

    def test_unknown_arm_names_are_an_error_not_a_default(self):
        with pytest.raises(KeyError):
            arm("Z")


class TestBudgetBeforeSpend:
    def test_the_full_campaign_is_costed_in_requests(self):
        budget = estimate_requests(list(ALL_ARMS[n] for n in "ABCDEFGH"), 150)
        assert budget.total > 0
        assert budget.questions == 150

    def test_self_consistency_costs_n_times_more_than_one_sample(self):
        single = estimate_requests([arm("B2")], 100).total
        sampled = estimate_requests([arm("B5")], 100).total
        assert sampled == single * arm("B5").self_consistency_samples

    def test_the_arbiter_is_budgeted_at_its_ceiling(self):
        """It fires only on disagreement, but a budget assuming the good case
        is not a budget."""
        budget = estimate_requests([arm("A")], 100)
        assert budget.arbiter_upper_bound == 100

    def test_local_components_cost_no_quota(self):
        """The deterministic verifier uses no model at all."""
        with_determ = estimate_requests([arm("A")], 50).total
        without = estimate_requests([arm("G")], 50).total
        assert with_determ == without

    def test_days_at_a_daily_limit(self):
        budget = estimate_requests([arm("A")], 150)
        assert budget.days_at(100) == pytest.approx(4.5)
        assert budget.days_at(None) is None


class TestResumability:
    def test_a_completed_pair_is_skipped_on_resume(self, tmp_path):
        provider = StubProvider(NATURAL_JSON)
        first = run_campaign(
            QUESTIONS, [SIMPLE_ARM], deps(natural_provider=provider),
            run_id="r1", root=tmp_path,
        )
        assert first.completed == 2
        calls_after_first = provider.calls

        second = run_campaign(
            QUESTIONS, [SIMPLE_ARM], deps(natural_provider=provider),
            run_id="r1", root=tmp_path,
        )
        assert second.completed == 0
        assert second.skipped == 2
        assert provider.calls == calls_after_first, "resume re-spent quota"

    def test_a_partially_written_line_is_recomputed_not_fatal(self, tmp_path):
        recorder = CampaignRecorder("r2", root=tmp_path)
        recorder.append({"arm": "B2", "question_id": "Q1"})
        recorder.results_path.write_text(
            recorder.results_path.read_text(encoding="utf-8") + '{"arm": "B2", "quest',
            encoding="utf-8",
        )
        assert recorder.completed() == {("B2", "Q1")}

    def test_the_results_file_is_never_truncated(self, tmp_path):
        run_campaign(QUESTIONS, [SIMPLE_ARM], deps(), run_id="r3", root=tmp_path)
        before = (tmp_path / "r3" / "results.jsonl").read_text(encoding="utf-8")
        run_campaign(QUESTIONS, [SIMPLE_ARM], deps(), run_id="r3", root=tmp_path)
        after = (tmp_path / "r3" / "results.jsonl").read_text(encoding="utf-8")
        assert after.startswith(before)


class TestQuotaIsACleanStop:
    def test_exhaustion_writes_what_completed_and_names_the_resume(self, tmp_path):
        provider = StubProvider(NATURAL_JSON, fail_after=1)
        result = run_campaign(
            QUESTIONS, [SIMPLE_ARM], deps(natural_provider=provider),
            run_id="r4", root=tmp_path,
        )
        assert result.stopped_early is True
        assert "quota" in result.stop_reason
        assert result.completed == 1
        assert "r4" in result.resume_command()
        assert (tmp_path / "r4" / "results.jsonl").exists()

    def test_the_partial_run_resumes_where_it_stopped(self, tmp_path):
        run_campaign(
            QUESTIONS, [SIMPLE_ARM],
            deps(natural_provider=StubProvider(NATURAL_JSON, fail_after=1)),
            run_id="r5", root=tmp_path,
        )
        resumed = run_campaign(
            QUESTIONS, [SIMPLE_ARM], deps(), run_id="r5", root=tmp_path
        )
        assert resumed.skipped == 1
        assert resumed.completed == 1


class TestFailuresAreRecordedNotDropped:
    def test_a_failing_question_leaves_a_row(self, tmp_path):
        """A vanished question shrinks the denominator silently and breaks the
        pairing the statistical protocol depends on."""

        class Exploding:
            def retrieve(self, *a, **k):
                raise RuntimeError("index unreachable")

            def retrieve_arms(self, *a, **k):
                raise RuntimeError("index unreachable")

        result = run_campaign(
            QUESTIONS, [SIMPLE_ARM], deps(retriever=Exploding()),
            run_id="r6", root=tmp_path,
        )
        assert result.failed == 2
        rows = [
            json.loads(line)
            for line in (tmp_path / "r6" / "results.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert len(rows) == 2
        assert all(row["error"] for row in rows)
        assert all(row["answer"] is None for row in rows)


class TestQuestionMajorOrdering:
    def test_an_interruption_leaves_a_balanced_prefix(self, tmp_path):
        """Every arm sees question 1 before any arm sees question 2, so an
        interrupted campaign still supports a paired comparison."""
        arms = [
            SIMPLE_ARM,
            ArmConfig("B1", retrieval="none", use_program=False,
                      use_deterministic=False, use_consistency=False,
                      use_verification_agent=False),
        ]
        run_campaign(QUESTIONS, arms, deps(), run_id="r7", root=tmp_path)
        rows = [
            json.loads(line)
            for line in (tmp_path / "r7" / "results.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert [r["question_id"] for r in rows] == ["Q1", "Q1", "Q2", "Q2"]


class TestArtifactCompleteness:
    def test_config_env_and_metrics_are_all_written(self, tmp_path):
        run_campaign(QUESTIONS, [SIMPLE_ARM], deps(), run_id="r8", root=tmp_path)
        directory = tmp_path / "r8"
        for name in ("config.json", "env.json", "metrics.json", "results.jsonl"):
            assert (directory / name).exists(), name

    def test_the_config_records_the_budget_and_the_bindings(self, tmp_path):
        run_campaign(QUESTIONS, [SIMPLE_ARM], deps(), run_id="r9", root=tmp_path)
        config = json.loads((tmp_path / "r9" / "config.json").read_text(encoding="utf-8"))
        assert config["budget"]["total_requests"] == 2
        assert config["natural_model"] == "a"
        assert config["question_ids"] == ["Q1", "Q2"]

    def test_question_metadata_travels_into_the_row(self, tmp_path):
        """Stratum and the ambiguity flag are needed at analysis time; recovering
        them by re-joining against the dataset invites a silent mismatch."""
        run_campaign(QUESTIONS, [SIMPLE_ARM], deps(), run_id="r10", root=tmp_path)
        rows = [
            json.loads(line)
            for line in (tmp_path / "r10" / "results.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert rows[0]["stratum"] == "lookup"


class TestFailureCases:
    def test_a_misconfigured_arm_stops_the_campaign_before_any_request(self, tmp_path):
        provider = StubProvider(NATURAL_JSON)
        bad = ArmConfig("bad", use_program=False, use_deterministic=False)
        with pytest.raises(ValueError):
            run_campaign(
                QUESTIONS, [bad], deps(natural_provider=provider),
                run_id="r11", root=tmp_path,
            )
        assert provider.calls == 0

    def test_an_empty_question_set_is_not_an_error(self, tmp_path):
        result = run_campaign([], [SIMPLE_ARM], deps(), run_id="r12", root=tmp_path)
        assert result.completed == 0

    def test_a_fresh_run_id_has_nothing_to_skip(self, tmp_path):
        assert CampaignRecorder("brand-new", root=tmp_path).completed() == set()


class TestTheStopTellsYouWhenToResume:
    """A campaign measured in days must not be resumed by guesswork.

    Groq's token allowance is a rolling 24-hour window, not a daily reset
    (RX-024), so the allowance trickles back continuously and a stated delay of
    a few minutes is normal. An operator told only "resume with: ..." has to
    guess, and guessing the next day boundary idles hours the provider had
    already given back.
    """

    def test_a_stated_delay_is_reported_in_minutes(self):
        from experiments.campaign import _resume_hint

        assert "24 minutes" in _resume_hint(1431.0)

    def test_a_short_delay_stays_in_seconds(self):
        from experiments.campaign import _resume_hint

        assert "45s" in _resume_hint(45.0)

    def test_a_long_delay_becomes_hours_rather_than_1440_minutes(self):
        from experiments.campaign import _resume_hint

        hint = _resume_hint(86_400.0)
        assert "hours" in hint and "1440" not in hint

    def test_no_stated_delay_says_re_run_rather_than_wait_for_midnight(self):
        """The wrong default is the expensive one: waiting for a boundary that
        does not exist costs hours per stop, across a campaign of days."""
        from experiments.campaign import _resume_hint

        hint = _resume_hint(None)
        assert "rolling" in hint
        assert "re-run" in hint

    def test_the_delay_is_persisted_on_the_result(self):
        """It goes into metrics.json, so a scheduler can read it rather than a
        human having to catch it in a log line."""
        from experiments.campaign import CampaignResult

        result = CampaignResult(run_id="r", stopped_early=True, resume_after_seconds=1431.0)
        assert result.as_dict()["resume_after_seconds"] == 1431.0

    def test_a_completed_run_carries_no_resume_delay(self):
        from experiments.campaign import CampaignResult

        assert CampaignResult(run_id="r").as_dict()["resume_after_seconds"] is None
