"""Source-level guards for defects that are invisible when you read the file.

A literal 0x08 in a regex renders as nothing in an editor, in `Read`, and in a
code review. The pattern simply never matches, and a metric silently drops out
of the lexicon. That happened on 2026-08-27: a shell heredoc interpreted the
two-character sequence backslash-b and wrote six backspace bytes into
`metrics_lexicon.py`, disabling every alias of the debt-to-equity ratio.

Nothing else on this project would have caught it. `ruff` was clean, the module
imported, the tests passed, and the entry was present in the tuple - it just
could never match a question.

These tests are cheap and cover a class rather than an instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", "node_modules", ".git", "dist", "build"}

# Tab and newline are legitimate. Everything else in C0, plus DEL, is not
# something anyone types on purpose into Python source.
FORBIDDEN = {chr(c) for c in range(0x00, 0x20)} - {"\t", "\n", "\r"} | {"\x7f"}


def _sources(*patterns: str) -> list[Path]:
    return sorted(
        p
        for pattern in patterns
        for p in ROOT.rglob(pattern)
        if not SKIP_DIRS.intersection(p.parts)
    )


def python_sources() -> list[Path]:
    return _sources("*.py")


def frontend_sources() -> list[Path]:
    """The same hazard, the other language.

    This glob was missing until 2026-09-11, and a literal NUL had been sitting
    in `Dashboard.tsx` since - written as the separator in a template literal by
    the same heredoc-eats-the-escape mechanism that caused the 0x08 bug above.
    It survived ruff, tsc, the build and a browser pass, because a NUL is a
    perfectly legal JavaScript string character. What it actually broke was
    review: git classifies a file containing a NUL as binary, so `git diff` on
    the largest screen in the app printed "Binary files differ" and grep skipped
    it entirely.
    """
    return _sources("*.ts", "*.tsx", "*.css")


# Tracked separately: the two families have different minimum sizes, and a
# single combined count would let one of them drop to zero unnoticed.
FAMILIES = {"python": python_sources, "frontend": frontend_sources}


@pytest.mark.parametrize("kind,minimum", [("python", 50), ("frontend", 8)])
def test_there_are_sources_to_check(kind, minimum):
    """Guards the guard. A glob that silently matches nothing passes every
    assertion below and verifies exactly nothing."""
    assert len(FAMILIES[kind]()) > minimum


@pytest.mark.parametrize("kind", list(FAMILIES))
def test_no_source_file_contains_a_control_character(kind):
    offenders: list[str] = []
    for path in FAMILIES[kind]():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            found = sorted({ch for ch in line if ch in FORBIDDEN})
            if found:
                names = ", ".join(f"0x{ord(ch):02x}" for ch in found)
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {names}")

    assert not offenders, (
        "control characters in source - these render as nothing and break "
        "regexes silently:\n  " + "\n  ".join(offenders)
    )


def test_every_derived_metric_pattern_can_match_something():
    """A pattern that matches nothing is indistinguishable from an absent entry.

    This is the assertion that would have caught the backspace bug directly: the
    entry was in the tuple, so any test counting entries passed, but no question
    could ever reach it.
    """
    from backend.agents.metrics_lexicon import DERIVED, decompose_derived

    unreachable = [
        d.canonical
        for d in DERIVED
        if decompose_derived(f"what was the {d.canonical} for the year") is None
    ]
    assert not unreachable, (
        f"derived metrics no question can reach: {unreachable}. "
        "The entry exists but its patterns never fire."
    )


def test_every_lexicon_alias_resolves_to_its_entry():
    """Same failure shape on the alias index."""
    from backend.agents.metrics_lexicon import LEXICON, lookup_metric

    broken: list[str] = []
    for entry in LEXICON:
        for name in (entry.canonical, *entry.aliases):
            found = lookup_metric(name)
            if found is None:
                broken.append(f"{name!r} (of {entry.canonical!r}) resolves to nothing")
    assert not broken, "\n  ".join(["unreachable lexicon names:", *broken])
