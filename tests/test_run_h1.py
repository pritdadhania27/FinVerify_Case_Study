"""The H1 runner picks a scope the allowance can actually pay for.

The number that matters is `--limit`, and picking it by hand means either
under-running the budget or discovering at 80% that it does not fit. These tests
pin the arithmetic, because getting it wrong is not visible until a campaign
stops halfway through a two-day window that cannot be repeated before the
deadline.

The direction of every approximation here is deliberate: the plan must
UNDER-estimate what is available. Groq's real window is rolling (RX-024) while
the limiter models a calendar day, so counting only whole untouched days plus
what the limiter says is left today is pessimistic during a day. Pessimistic
finishes early; optimistic stops mid-campaign.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_h1 import (  # noqa: E402
    GROQ_TOKENS_PER_DAY,
    affordable_questions,
    natural_provider,
    tokens_per_question,
    whole_days_before,
)


class TestWholeDaysBeforeTheDeadline:
    def now(self, day: int, hour: int = 12):
        return datetime(2026, 8, day, hour, tzinfo=UTC)

    def deadline(self, day: int):
        return datetime(2026, 9, day, tzinfo=UTC)

    def test_the_remainder_of_today_is_not_counted(self):
        """Today's leftover comes from the limiter's own state instead, because
        the limiter knows what has already been spent and this does not."""
        assert whole_days_before(self.deadline(2), self.now(30)) == 2

    def test_a_deadline_at_the_next_boundary_leaves_no_whole_day(self):
        assert whole_days_before(datetime(2026, 8, 31, tzinfo=UTC), self.now(30)) == 0

    def test_a_deadline_inside_today_leaves_no_whole_day(self):
        assert whole_days_before(datetime(2026, 8, 30, 23, tzinfo=UTC), self.now(30)) == 0

    def test_the_hour_of_day_does_not_change_the_count(self):
        """A whole day is a whole day whether the plan is made at 01:00 or
        23:00 - only the limiter's spent-today figure should move."""
        assert whole_days_before(self.deadline(2), self.now(30, 1)) == (
            whole_days_before(self.deadline(2), self.now(30, 23))
        )


class TestAffordableScope:
    """The cost of a question depends on WHICH arms run.

    Arm H binds both channels to the natural provider, so under a SPLIT-provider
    binding it costs that provider roughly twice what arm A does. A fixed
    calls-per-question constant - which this script originally carried -
    understates the four-arm campaign and would have planned a scope the
    allowance could not pay for. The figure comes from the campaign runner's own
    estimator, so the plan and the run cannot disagree.

    Every test here pins its own bindings. They used to read the ambient `.env`,
    which meant they asserted "Channel A is on Groq today" as much as they
    asserted any arithmetic - and all four broke the moment D44 rebound the
    channel, with no estimator logic changed. A test that fails when a decision
    is taken, rather than when its subject regresses, is measuring the wrong
    thing.
    """

    @staticmethod
    def _split(monkeypatch):
        """The pre-D44 shape: the two channels on two providers."""
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "groq/llama-3.3-70b-versatile")
        monkeypatch.setenv("PROGRAM_CHANNEL_MODEL", "nvidia/nvidia/nemotron-3-ultra-550b-a55b")
        monkeypatch.setenv("VERIFICATION_AGENT_MODEL", "groq/llama-3.3-70b-versatile")
        return "groq"

    @staticmethod
    def _single(monkeypatch):
        """The D44 shape: one provider, two models from two labs."""
        monkeypatch.setenv("NATURAL_CHANNEL_MODEL", "nvidia/openai/gpt-oss-120b")
        monkeypatch.setenv("PROGRAM_CHANNEL_MODEL", "nvidia/nvidia/nemotron-3-ultra-550b-a55b")
        monkeypatch.setenv("VERIFICATION_AGENT_MODEL", "nvidia/openai/gpt-oss-120b")
        return "nvidia"

    def test_a_question_costs_more_when_more_arms_run(self, monkeypatch):
        provider = self._split(monkeypatch)
        assert tokens_per_question(["A", "B5", "G", "H"], 1024, provider) > (
            tokens_per_question(["A", "B5"], 1024, provider)
        )

    def test_the_same_model_arm_is_charged_to_the_natural_provider(self, monkeypatch):
        """Arm H puts the program channel on the natural provider. If the
        estimator missed that, H would look as cheap as a split-provider arm
        and the campaign would run out partway through.
        """
        provider = self._split(monkeypatch)
        with_h = tokens_per_question(["A", "H"], 1024, provider)
        without_h = tokens_per_question(["A"], 1024, provider)
        assert with_h > without_h * 1.5, (
            f"arm H added only {with_h - without_h} {provider} tokens per question; "
            "it should carry BOTH channels"
        )

    def test_a_bigger_completion_budget_costs_more_per_question(self, monkeypatch):
        provider = self._split(monkeypatch)
        assert tokens_per_question(["A", "B5"], 4096, provider) > (
            tokens_per_question(["A", "B5"], 1024, provider)
        )

    def test_the_planned_scope_matches_what_the_campaign_costs(self, monkeypatch):
        """The number this whole script exists to get right.

        Two days of allowance at 1024 on A+B5 is the scope the campaign
        runner independently reported as 2.0 days for 20 questions.
        """
        provider = self._split(monkeypatch)
        per_question = tokens_per_question(["A", "B5"], 1024, provider)
        assert affordable_questions(2 * GROQ_TOKENS_PER_DAY, per_question) == 20

    def test_the_planner_follows_the_binding_rather_than_a_constant(self, monkeypatch):
        """D44: the binding provider is configuration, not a hard-coded name."""
        assert self._split(monkeypatch) == natural_provider()
        assert self._single(monkeypatch) == natural_provider()

    def test_a_provider_serving_none_of_the_scope_costs_nothing(self, monkeypatch):
        """The defect D44 exposed, pinned.

        Under the single-provider binding no call reaches Groq, so its cost is
        zero. The old `max(1, ...)` floor turned that zero into 1 token per
        question and the planner reported an affordable scope of two hundred
        thousand - a tool announcing that everything fits at the moment it
        stopped modelling the pipeline.
        """
        self._single(monkeypatch)
        assert tokens_per_question(["A", "B5", "G", "H"], 4096, "groq") == 0

    def test_an_unused_provider_does_not_bind_the_scope(self, monkeypatch):
        """None, not a number: a provider serving nothing places no limit, and
        that is not the same claim as "unlimited questions"."""
        self._single(monkeypatch)
        per_question = tokens_per_question(["A", "B5"], 4096, "groq")
        assert affordable_questions(2 * GROQ_TOKENS_PER_DAY, per_question) is None

    def test_it_rounds_down_rather_than_up(self):
        """A partial question is not a question. Rounding up plans a
        campaign that stops one question short of finishing.
        """
        assert affordable_questions(299, 100) == 2

    def test_an_empty_budget_affords_nothing(self):
        assert affordable_questions(0, 33_648) == 0


class TestItAnalysesTheRunItJustCreated:
    """RX-033: it analysed a stale run from two days earlier and printed its
    results as the campaign's.

    The old selection was `sorted(glob("*/metrics.json"))[-1]`, which is
    alphabetical, not chronological. `qu-llm_20260829T181529Z` sorts after
    `campaign_20260831T184152Z`, so a completed 180-row campaign was reported
    with another run's empty hypothesis table. Analysing the WRONG run is worse
    than analysing none, because the output is shaped exactly like the right
    answer.
    """

    def test_only_campaign_directories_are_candidates(self, tmp_path, monkeypatch):
        import scripts.run_h1 as run_h1

        runs = tmp_path / "experiments" / "runs"
        for name in ("campaign_20260831T184152Z", "qu-llm_20260829T181529Z",
                     "slice_20260827T115934Z", "budget_20260831T182926Z"):
            (runs / name).mkdir(parents=True)
        monkeypatch.setattr(run_h1, "project_path", lambda _rel: runs)

        assert run_h1.campaign_runs() == {"campaign_20260831T184152Z"}

    def test_a_new_run_is_identified_by_being_new_not_by_sorting(
        self, tmp_path, monkeypatch
    ):
        """The new directory sorts EARLIER than the existing one, so a
        sort-based choice picks the wrong one and this must not."""
        import scripts.run_h1 as run_h1

        runs = tmp_path / "experiments" / "runs"
        (runs / "campaign_20260901T000000Z").mkdir(parents=True)
        monkeypatch.setattr(run_h1, "project_path", lambda _rel: runs)
        before = run_h1.campaign_runs()

        (runs / "campaign_20260831T184152Z").mkdir()
        fresh = run_h1.campaign_runs() - before

        assert fresh == {"campaign_20260831T184152Z"}

    def test_no_new_directory_is_detectable_rather_than_silent(
        self, tmp_path, monkeypatch
    ):
        import scripts.run_h1 as run_h1

        runs = tmp_path / "experiments" / "runs"
        (runs / "campaign_20260831T184152Z").mkdir(parents=True)
        monkeypatch.setattr(run_h1, "project_path", lambda _rel: runs)

        assert run_h1.campaign_runs() - run_h1.campaign_runs() == set()
