"""A retrieval gold set's declared split must match the questions inside it.

RX-053. The four generated retrieval sets each declared `"split": "validation"`
and carried the note *"VALIDATION split. Must never be reported as a test
result."* while **43 of the benchmark's 76 sealed test questions (57%)** appeared
among their source questions, verbatim.

`scripts/evaluate_retrieval.py` gates the test split on exactly that field:

    if meta["split"] == "test" and os.environ.get("FINVERIFY_ALLOW_TEST") != "1":
        refuse

The gate was correct. The *label* was not: `build_retrieval_gold.py` hardcoded
"validation" instead of deriving it from the source questions, so every retrieval
evaluation ran on test-bearing gold without the flag, without an entry in
`experiments/test_set_access.log`, and while asserting the opposite in its own
notes.

What leaked is evidence-span locations and retrieval performance on those
questions, not gold answers - the answer-level campaign used the sealed loader,
which does log. It is still a seal breach, and RX-035's decision not to adopt a
reranker rests partly on it.

These tests are the guard that did not exist. The lesson they encode: **a seal
that reads a self-declared label is only as good as whoever wrote the label**, so
the label has to be computed from the contents and then checked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = ROOT / "datasets" / "retrieval_eval"
BENCHMARK = ROOT / "datasets" / "finverify_ind" / "finverify_ind_v1.json"

# A set declaring this exact value is asserting it is safe to evaluate freely.
# Anything else must route through the test-set flag and the access log.
FREELY_EVALUABLE = "validation"


def _gold_sets() -> list[tuple[str, dict]]:
    if not GOLD_DIR.exists():
        return []
    out = []
    for path in sorted(GOLD_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "questions" in data:
            out.append((path.name, data))
    return out


def _benchmark_splits() -> dict[str, str]:
    if not BENCHMARK.exists():
        return {}
    data = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    return {q["qid"]: q.get("split") for q in data.get("questions", [])}


def _source_splits(gold: dict, splits: dict[str, str]) -> dict[str, int]:
    """What splits this set's questions actually came from."""
    counts: dict[str, int] = {}
    for item in gold.get("questions", []):
        src = item.get("source_qid")
        if not src:
            continue
        name = splits.get(src)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


@pytest.mark.parametrize("name,gold", _gold_sets(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_set_claiming_validation_contains_only_validation_questions(name, gold):
    """The assertion that let RX-053 happen, now checked.

    A set may legitimately contain test questions - the benchmark is the source of
    truth for splits and retrieval evaluation is a reasonable thing to want on it.
    What it may not do is contain them while telling the seal gate it is
    validation, because then nothing asks for the flag and nothing is logged.
    """
    splits = _benchmark_splits()
    if not splits:
        pytest.skip("the benchmark is not present")

    actual = _source_splits(gold, splits)
    if not actual:
        # A hand-built set with no source_qid mapping. It cannot be checked this
        # way, and it must therefore not claim to be freely evaluable either.
        return

    declared = gold.get("split")
    contaminating = {k: v for k, v in actual.items() if k != FREELY_EVALUABLE}
    if declared == FREELY_EVALUABLE:
        assert not contaminating, (
            f"{name} declares split={declared!r} but its source questions include "
            f"{contaminating}. evaluate_retrieval.py gates on this field, so this "
            f"label lets a test-bearing set be evaluated with no "
            f"FINVERIFY_ALLOW_TEST=1 and no access-log entry. Derive the label "
            f"from the contents (build_retrieval_gold.py) rather than asserting it."
        )


@pytest.mark.parametrize("name,gold", _gold_sets(), ids=lambda v: v if isinstance(v, str) else "")
def test_no_set_claims_in_prose_what_its_split_field_denies(name, gold):
    """The notes must not contradict the split field.

    Every affected set carried "VALIDATION split. Must never be reported as a test
    result." A reader checking the prose instead of the data got the wrong answer,
    and the prose is what a reader checks.
    """
    declared = (gold.get("split") or "").lower()
    notes = " ".join(gold.get("notes") or []).lower()
    if "validation split. must never be reported as a test result" in notes:
        assert declared == FREELY_EVALUABLE, (
            f"{name} asserts in prose that it is the validation split while its "
            f"split field says {declared!r}"
        )


def test_the_sealed_test_split_is_not_silently_reachable_through_retrieval_gold():
    """The project-level invariant, stated once.

    Not per-set: the question is how much of the sealed split is reachable through
    a door that does not log. At the time RX-053 was found the answer was 57%.
    """
    splits = _benchmark_splits()
    if not splits:
        pytest.skip("the benchmark is not present")

    test_qids = {qid for qid, name in splits.items() if name == "test"}
    if not test_qids:
        pytest.skip("the benchmark declares no test split")

    reachable: set[str] = set()
    for _name, gold in _gold_sets():
        if gold.get("split") != FREELY_EVALUABLE:
            continue  # this set will be gated, so it is not a silent door
        for item in gold.get("questions", []):
            src = item.get("source_qid")
            if src in test_qids:
                reachable.add(src)

    assert not reachable, (
        f"{len(reachable)} of {len(test_qids)} sealed test questions "
        f"({len(reachable) / len(test_qids):.0%}) are reachable through retrieval "
        f"gold labelled split={FREELY_EVALUABLE!r}, which bypasses both "
        f"FINVERIFY_ALLOW_TEST=1 and experiments/test_set_access.log"
    )


def test_the_builder_derives_the_split_rather_than_hardcoding_it():
    """Guards the fix itself.

    The one-line regression that caused RX-053 was a literal in a dict. A future
    edit could restore it and every test above would still pass on the *current*
    files while the next generated set carried the same untruth.
    """
    builder = ROOT / "scripts" / "build_retrieval_gold.py"
    if not builder.exists():
        pytest.skip("the builder is not present")
    source = builder.read_text(encoding="utf-8")
    assert '"split": "validation"' not in source, (
        "build_retrieval_gold.py hardcodes the split label again; it must be "
        "derived from the source questions' own splits"
    )
    assert "source_split" in source, (
        "the builder no longer records each question's source split, so the "
        "set-level label cannot be derived from the contents"
    )
