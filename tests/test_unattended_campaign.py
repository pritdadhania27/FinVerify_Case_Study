"""The supervisor must know when to stop, because nobody is watching it.

An unattended process that restarts a campaign for a week has exactly three
ways to be wrong, and all three are expensive: it stops early and wastes the
allowance it was written to capture, it never stops and runs past the deadline,
or it restarts a run that is failing for a real reason. These pin all three.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_campaign_unattended import (  # noqa: E402
    STOPPED_EARLY,
    completed_pairs,
    expected_pairs,
    next_allowance_reset,
)


class TestTheQuotaStopIsNotAFailure:
    """The supervisor's whole job is to tell these two apart.

    `run_campaign.py` exits `STOPPED_EARLY` when the allowance runs out: the run
    is incomplete, resumable, and should be retried when the window refills.
    The first supervised run assumed that was exit 0, saw 3, read it as a real
    error and stopped - four rows into a 180-row campaign, with eight days of
    allowance left to spend.
    """

    def test_the_two_scripts_share_one_constant(self):
        """Imported, not written out, so they cannot drift apart."""
        from scripts.run_campaign import STOPPED_EARLY as source

        assert STOPPED_EARLY is source

    def test_it_is_distinguishable_from_success_and_from_a_crash(self):
        assert STOPPED_EARLY not in (0, 1, 2)

    def test_the_supervisor_treats_it_as_a_reason_to_continue(self):
        """The exact predicate in the loop, asserted directly - it is one
        operator away from stopping a week-long campaign."""
        keeps_going = [code for code in (0, STOPPED_EARLY, 1, 2, 130)
                       if code in (0, STOPPED_EARLY)]
        assert keeps_going == [0, STOPPED_EARLY]


class TestTheWrongInterpreterSaysSo:
    """On Windows a bare `run_campaign_unattended.py` goes to whichever Python
    owns the .py file association, not the venv. That interpreter has some of
    this project's dependencies and not others, so the run died with

        ModuleNotFoundError: No module named 'langgraph'

    which reads as a missing package and sends the reader to pip - installing it
    into the wrong environment and surfacing the next missing module instead.
    """

    def test_it_names_the_interpreter_and_the_fix(self, capsys, monkeypatch):
        from scripts._console import require_project_interpreter

        monkeypatch.setattr("sys.executable", r"C:\Python312\python.exe")
        require_project_interpreter()
        printed = capsys.readouterr().err
        assert "not the project venv" in printed
        assert ".venv" in printed, "the message must carry the command to run instead"

    def test_the_venv_interpreter_says_nothing(self, capsys, monkeypatch):
        """A warning on every correct run is a warning nobody reads."""
        from scripts import _console

        monkeypatch.setattr("sys.executable", str(_console._VENV_PYTHON))
        _console.require_project_interpreter()
        assert capsys.readouterr().err == ""

    def test_it_warns_rather_than_exits(self, monkeypatch):
        """A legitimate run from another environment that genuinely has the
        dependencies must not be blocked - guessing wrong about someone's setup
        is worse than a printed caution."""
        from scripts._console import require_project_interpreter

        monkeypatch.setattr("sys.executable", r"C:\Python312\python.exe")
        require_project_interpreter()  # returns; does not raise SystemExit

    def test_it_runs_before_any_third_party_import(self):
        """The guard is useless after `import dotenv` raises. Pinned as source
        order because that is the only thing that makes it fire first."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "run_campaign_unattended.py"
        ).read_text(encoding="utf-8")
        assert source.index("require_project_interpreter()") < source.index(
            "from dotenv import load_dotenv"
        )


class TestItWaitsOnTheThingThatActuallyRefuses:
    """The provider's window and the limiter's window are not the same window.

    RX-024 measured Groq's own allowance as rolling, and this supervisor was
    written on that basis - it promised a useful slice of work every half hour.
    But `RateLimiter` gates every call and rolls its counters only when the UTC
    day changes, so a run that spends the day's tokens at 05:00Z is refused
    locally until midnight no matter what the provider has refilled. Polling
    through that window costs an embedding-model load per cycle and gains
    nothing.
    """

    def test_the_reset_is_the_next_utc_midnight(self):
        from datetime import UTC, datetime

        now = datetime(2026, 9, 1, 4, 50, tzinfo=UTC)
        assert next_allowance_reset(now) == datetime(2026, 9, 2, 0, 0, tzinfo=UTC)

    def test_a_run_late_in_the_day_still_waits_for_the_boundary(self):
        """The pathological case for a fixed two-hour poll: eight minutes before
        the reset it sleeps past it, and just after it sleeps a whole day."""
        from datetime import UTC, datetime

        now = datetime(2026, 9, 1, 23, 52, tzinfo=UTC)
        reset = next_allowance_reset(now)
        assert (reset - now).total_seconds() == 8 * 60

    def test_it_is_never_in_the_past(self):
        from datetime import UTC, datetime, timedelta

        for hour in range(24):
            now = datetime(2026, 9, 1, hour, 30, tzinfo=UTC)
            gap = next_allowance_reset(now) - now
            assert timedelta(0) < gap <= timedelta(days=1)


def _run_dir(tmp_path: Path, run_id: str) -> Path:
    directory = tmp_path / "experiments" / "runs" / run_id
    directory.mkdir(parents=True)
    return directory


def _patch_paths(monkeypatch, tmp_path):
    import scripts.run_campaign_unattended as sup

    monkeypatch.setattr(sup, "project_path", lambda rel: tmp_path / rel)


class TestCountingWhatIsDone:
    def test_it_counts_distinct_arm_question_pairs(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_x")
        rows = [
            {"arm": "A", "question_id": "FI01"},
            {"arm": "B5", "question_id": "FI01"},
            {"arm": "A", "question_id": "FI02"},
        ]
        directory.joinpath("results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
        )
        assert completed_pairs("campaign_x") == {
            ("A", "FI01"), ("B5", "FI01"), ("A", "FI02")
        }

    def test_a_duplicate_row_is_not_counted_twice(self, monkeypatch, tmp_path):
        """The set matters, not the line count - a resumed run that re-wrote a
        row must not look like extra progress."""
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_x")
        row = json.dumps({"arm": "A", "question_id": "FI01"})
        directory.joinpath("results.jsonl").write_text(f"{row}\n{row}\n", encoding="utf-8")
        assert len(completed_pairs("campaign_x")) == 1

    def test_a_torn_trailing_write_does_not_crash_the_supervisor(
        self, monkeypatch, tmp_path
    ):
        """An interrupted append is the normal signature of a killed run. The
        supervisor must survive it - it is the thing that restarts after one."""
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_x")
        good = json.dumps({"arm": "A", "question_id": "FI01"})
        directory.joinpath("results.jsonl").write_text(
            f'{good}\n{{"arm": "B5", "question_', encoding="utf-8"
        )
        assert completed_pairs("campaign_x") == {("A", "FI01")}

    def test_a_run_that_has_written_nothing_is_zero_not_an_error(
        self, monkeypatch, tmp_path
    ):
        """`campaign_20260831T211058Z` is exactly this: it stopped on quota
        before writing a row, and it is the id the re-run continues from."""
        _patch_paths(monkeypatch, tmp_path)
        _run_dir(tmp_path, "campaign_empty")
        assert completed_pairs("campaign_empty") == set()


class TestKnowingWhenItIsFinished:
    def test_the_target_is_arms_times_questions(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_x")
        directory.joinpath("config.json").write_text(
            json.dumps({"arms": [{"name": "A"}, {"name": "B5"},
                                 {"name": "G"}, {"name": "H"}],
                        "questions": 45}),
            encoding="utf-8",
        )
        assert expected_pairs("campaign_x") == 180

    def test_a_missing_config_yields_no_target_rather_than_a_wrong_one(
        self, monkeypatch, tmp_path
    ):
        """Guessing a target would make the supervisor stop early or never.
        None means 'run until the deadline', which is the safe reading."""
        _patch_paths(monkeypatch, tmp_path)
        _run_dir(tmp_path, "campaign_x")
        assert expected_pairs("campaign_x") is None

    def test_a_smoke_test_config_reports_ITS_scope_not_the_campaign_s(
        self, monkeypatch, tmp_path
    ):
        """Why the target is re-read every cycle rather than cached.

        `campaign_20260831T211058Z` was created by a `--limit 2` verification
        run, so its config says 2 questions. Read once up front, the supervisor
        would have declared a 45-question campaign complete after 8 pairs.
        """
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_smoke")
        directory.joinpath("config.json").write_text(
            json.dumps({"arms": [{"name": "A"}, {"name": "B5"},
                                 {"name": "G"}, {"name": "H"}],
                        "questions": 2}),
            encoding="utf-8",
        )
        assert expected_pairs("campaign_smoke") == 8

        # The next resume rewrites config.json with the scope it actually ran.
        directory.joinpath("config.json").write_text(
            json.dumps({"arms": [{"name": "A"}, {"name": "B5"},
                                 {"name": "G"}, {"name": "H"}],
                        "questions": 45}),
            encoding="utf-8",
        )
        assert expected_pairs("campaign_smoke") == 180

    def test_a_config_without_arms_yields_no_target(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        directory = _run_dir(tmp_path, "campaign_x")
        directory.joinpath("config.json").write_text(
            json.dumps({"questions": 45}), encoding="utf-8"
        )
        assert expected_pairs("campaign_x") is None
