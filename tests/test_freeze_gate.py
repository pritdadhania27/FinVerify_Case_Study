"""The test split cannot be evaluated against a methodology freeze that is void.

D46 wrote a four-step precondition for touching the test split and step 4 was
carried out while steps 1-3 were not, because the precondition lived in a
decision entry and prose blocks nothing (D48). The split is spent, so these
tests protect the *next* freeze rather than that one.

The gate is deliberately the same shape as `preflight_models`: a name recorded
somewhere is not evidence of a state, so go and look. And it fails CLOSED - if
git cannot be asked, the answer is no. An unavailable check must never read as
permission when the resource it guards can only be spent once.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_campaign import live_freeze_tags  # noqa: E402


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _git_returns(monkeypatch, stdout: str) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(stdout))


def _git_raises(monkeypatch, exc: Exception) -> None:
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)


def test_a_freeze_whose_subject_says_void_is_not_live(monkeypatch):
    _git_returns(monkeypatch, "methodology-freeze-v1\tVOID - the model was retired\n")
    assert live_freeze_tags() == []


def test_a_freeze_without_void_is_live(monkeypatch):
    _git_returns(monkeypatch, "methodology-freeze-v2\ttau 0.005, threshold 0.51125\n")
    assert live_freeze_tags() == ["methodology-freeze-v2"]


def test_only_the_live_freeze_is_returned_when_both_exist(monkeypatch):
    _git_returns(
        monkeypatch,
        "methodology-freeze-v1\tVOID - superseded\n"
        "methodology-freeze-v2\tre-frozen on gpt-oss-20b\n",
    )
    assert live_freeze_tags() == ["methodology-freeze-v2"]


def test_void_is_matched_case_insensitively(monkeypatch):
    # The v1 annotation happens to shout it. A future one might not, and a gate
    # that only catches the shouting version is worse than no gate: it reads as
    # a check while passing exactly the case it exists to catch.
    _git_returns(monkeypatch, "methodology-freeze-v3\tvoid, replaced same day\n")
    assert live_freeze_tags() == []


def test_no_freeze_tags_at_all_is_no_live_freeze(monkeypatch):
    _git_returns(monkeypatch, "")
    assert live_freeze_tags() == []


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("git not on PATH"),
        subprocess.CalledProcessError(128, "git"),
        subprocess.TimeoutExpired("git", 15),
    ],
)
def test_the_gate_fails_closed_when_git_cannot_answer(monkeypatch, exc):
    # None, not [] - the caller must be able to tell "no live freeze" from
    # "could not find out", and refuse on both for different stated reasons.
    _git_raises(monkeypatch, exc)
    assert live_freeze_tags() is None


def test_a_multi_line_annotation_does_not_leak_into_the_next_tag(monkeypatch):
    # %(contents:subject) is one line by construction; this pins that the parse
    # keeps working if the format string is ever widened to %(contents), which
    # would silently merge a void tag's body into the following tag's name.
    _git_returns(
        monkeypatch,
        "methodology-freeze-v1\tVOID - retired model\n"
        "\n"
        "Kept as a record that the freeze happened.\n"
        "methodology-freeze-v2\tlive\n",
    )
    assert live_freeze_tags() == ["methodology-freeze-v2"]


def test_the_repository_has_no_live_freeze_right_now():
    """Not a unit test - a statement about this project, and it should fail loudly.

    Every methodology-freeze tag in the repository is annotated VOID (D46), so
    `RUN_TEST_SPLIT.bat` is currently refused. When someone cuts a live v2 this
    test breaks, and breaking is the correct behaviour: it forces a person to
    confirm that re-opening the test split is what they meant, in the one place
    where that decision is cheap to reverse.
    """
    live = live_freeze_tags()
    assert live is not None, "git could not be asked; the gate cannot be verified here"
    assert live == [], (
        f"a live methodology freeze appeared: {live}. The test split is now "
        "runnable again - confirm that is intended, then update this test."
    )
