"""The arm table must be readable without the agent runtime.

`GET /arms` returned 500 in the deployed API container because
`evaluation.arms` imported `ArmConfig` from `orchestrator`, which imports
langgraph at module scope - and the API image deliberately carries no agent
dependencies. A description of an experiment should not require the machinery
that runs it.

These tests pin that separation. They fail if anyone re-couples the arm table to
the runtime, which is easy to do by accident and invisible until the container
is rebuilt.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Imported by the API container, which has none of these installed.
HEAVY = ("langgraph", "torch", "sentence_transformers", "qdrant_client", "camelot", "fitz")


def _import_in_a_fresh_interpreter(statement: str) -> set[str]:
    """Modules loaded as a side effect of `statement`, in a clean process.

    A fresh interpreter is the whole point: inside the test session something
    else has almost certainly imported the orchestrator already, so checking
    `sys.modules` in-process would pass no matter how the imports are wired.
    """
    code = (
        f"import sys; sys.path.insert(0, r'{ROOT}')\n"
        f"{statement}\n"
        "print('\\n'.join(sorted(m for m in sys.modules if '.' not in m)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


class TestTheArmTableIsDependencyLight:
    @pytest.mark.parametrize("module", HEAVY)
    def test_importing_the_arm_table_does_not_load(self, module):
        loaded = _import_in_a_fresh_interpreter("import evaluation.arms")
        assert module not in loaded, (
            f"importing evaluation.arms pulled in {module!r}. The API container "
            "has no agent dependencies, so this makes /arms a 500 there."
        )

    def test_arm_config_imports_on_its_own(self):
        loaded = _import_in_a_fresh_interpreter(
            "from backend.agents.arm_config import ArmConfig"
        )
        assert not (set(HEAVY) & loaded)

    def test_the_orchestrator_still_re_exports_it(self):
        """Every existing `from backend.agents.orchestrator import ArmConfig`
        must keep working - the move is meant to be invisible to callers."""
        from backend.agents.arm_config import ArmConfig
        from backend.agents.orchestrator import ArmConfig as ReExported

        assert ReExported is ArmConfig

    def test_the_api_can_describe_every_arm(self):
        """`arm_out` is what the arbiter panel reads to tell "no arbiter in this
        arm" from "the arbiter did not fire"."""
        from backend.api.main import arm_out
        from evaluation.arms import ALL_ARMS

        for key in ALL_ARMS:
            described = arm_out(key)
            assert described is not None, key
            # Keyed by label but named by configuration: "P" resolves to arm A.
            assert described.name == ALL_ARMS[key].name

        assert arm_out("NOT_AN_ARM") is None

    def test_the_listing_does_not_show_one_arm_twice_under_its_alias(self):
        """"P" (EVALUATION.md §7) and "A" (§8) are the same configuration, so a
        listing keyed on the dict would report fourteen arms as fifteen."""
        from fastapi.testclient import TestClient

        from backend.api.main import create_app

        with TestClient(create_app()) as client:
            names = [row["name"] for row in client.get("/arms").json()]

        assert names == sorted(set(names)), f"duplicate arm rows: {names}"
        assert "A" in names and len(names) == len(set(names))

    def test_removes_is_empty_for_the_baseline_and_names_one_field_for_an_ablation(self):
        """Every arm is "A minus exactly one field", and the label is computed
        by diffing against A rather than written down twice."""
        from backend.api.main import arm_out

        assert arm_out("A").removes is None
        assert "executed-program" in (arm_out("C").removes or "")
        assert "natural-language" in (arm_out("B").removes or "")
        assert arm_out("C").uses_program is False
        assert arm_out("D").uses_arbiter is False
