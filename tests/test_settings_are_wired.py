"""Every declared setting must actually do something (RX-021).

Nine of the ten `LLM_*` and `SANDBOX_*` variables in `.env` were read by
nothing. They had been there since Module 8, one annotated `# reasoning models
need headroom`, and every call ran on whatever default its own function
signature carried.

That was not cosmetic. `LLM_MAX_TOKENS=4096` lost to Channel A's own
`max_tokens=2048`, which truncates a reasoning model mid-`<think>`; the channel
then returned "no JSON object in the reply" on **35% of questions** (RX-019),
which a campaign would have recorded as the method failing.

A configuration surface that does not exist is worse than none, because it
reports being configured. The last test here is the one that matters: it walks
`.env.example` and fails if a variable is declared and consumed nowhere.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.core.paths import PROJECT_ROOT
from backend.services.llm.settings import llm_settings
from backend.services.sandbox import SandboxConfig

LLM_VARS = ("LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_MAX_RETRIES", "LLM_TIMEOUT_SECONDS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*LLM_VARS, "SANDBOX_IMAGE", "SANDBOX_TIMEOUT_SECONDS",
                 "SANDBOX_MEMORY_LIMIT", "SANDBOX_CPU_LIMIT"):
        monkeypatch.delenv(name, raising=False)


class TestLLMSettings:
    def test_the_documented_defaults_apply_when_nothing_is_set(self):
        """A missing .env must behave like the shipped one, not differently."""
        s = llm_settings()
        assert (s.temperature, s.max_tokens, s.max_retries, s.timeout_seconds) == (
            0.0, 4096, 3, 60.0
        )

    def test_the_environment_wins(self, monkeypatch):
        monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
        s = llm_settings()
        assert s.max_tokens == 8192
        assert s.temperature == 0.7

    def test_it_reads_at_call_time_not_at_import(self, monkeypatch):
        """The trap that produced the bug in the first place.

        A module-level `os.environ.get` is evaluated when the module is first
        imported - before `load_dotenv` in most entry points, and before any
        fixture can redirect it. Same shape as D33's argparse defaults and the
        API's upload directory.
        """
        first = llm_settings().max_tokens
        monkeypatch.setenv("LLM_MAX_TOKENS", str(first + 1024))
        assert llm_settings().max_tokens == first + 1024

    @pytest.mark.parametrize("value", ["oops", "", "  ", "4096.5"])
    def test_a_malformed_budget_is_loud(self, monkeypatch, value):
        """Silently reverting to a default makes a run not be the run its config
        claims - which is unrecoverable after the fact."""
        monkeypatch.setenv("LLM_MAX_TOKENS", value)
        if value.strip() == "":
            assert llm_settings().max_tokens == 4096  # blank means "unset"
        else:
            with pytest.raises(RuntimeError, match="LLM_MAX_TOKENS"):
                llm_settings()


class TestChannelsUseTheSetting:
    """The channels are where the setting has to arrive, not just be readable."""

    class Recorder:
        def __init__(self):
            self.kwargs = {}

        def complete(self, messages, **kwargs):
            self.kwargs = kwargs

            class R:
                text = '{"answer": 1, "unit": "INR crore", "reasoning": "r"}'
                usage = None

            return R()

    def test_channel_a_takes_the_configured_budget(self, monkeypatch):
        from backend.agents.natural_channel import run_natural_channel

        monkeypatch.setenv("LLM_MAX_TOKENS", "9001")
        rec = self.Recorder()
        run_natural_channel("q", [], provider=rec, model="m")
        assert rec.kwargs["max_tokens"] == 9001, (
            "Channel A ran on its own hard-coded default for weeks; 2048 truncates "
            "a reasoning model mid-think (RX-019)"
        )

    def test_an_explicit_argument_still_wins(self, monkeypatch):
        from backend.agents.natural_channel import run_natural_channel

        monkeypatch.setenv("LLM_MAX_TOKENS", "9001")
        rec = self.Recorder()
        run_natural_channel("q", [], provider=rec, model="m", max_tokens=128)
        assert rec.kwargs["max_tokens"] == 128

    def test_temperature_defaults_to_zero_because_d7a_pins_it(self):
        from backend.agents.natural_channel import run_natural_channel

        rec = self.Recorder()
        run_natural_channel("q", [], provider=rec, model="m")
        assert rec.kwargs["temperature"] == 0.0


class TestSandboxSettings:
    def test_defaults_match_the_dataclass(self):
        assert SandboxConfig.from_env() == SandboxConfig()

    def test_a_tightened_limit_is_honoured(self, monkeypatch):
        """The worse half of a security control is one that reports being
        configured. An operator tightening memory for an untrusted corpus got
        no tightening and no error."""
        monkeypatch.setenv("SANDBOX_MEMORY_LIMIT", "64m")
        monkeypatch.setenv("SANDBOX_TIMEOUT_SECONDS", "3")
        config = SandboxConfig.from_env()
        assert config.memory_limit == "64m"
        assert config.timeout_seconds == 3

    def test_execute_program_applies_the_configured_limit_when_no_config_is_passed(
        self, monkeypatch
    ):
        """The test above proved `from_env()` reads the variables. It did not prove
        anything on the production path CALLED it - and nothing did. Every caller
        passes config=None, and `execute_program` fell back to the bare dataclass,
        so the two tests above passed for a setting no campaign ever honoured.

        This one asserts the limit reaches the docker command line itself, which
        is where containment actually happens. No container is started.
        """
        import backend.services.sandbox as sandbox

        monkeypatch.setenv("SANDBOX_MEMORY_LIMIT", "64m")
        monkeypatch.setattr(sandbox, "_docker_available", lambda: (True, ""))
        monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "docker")
        captured: list[list[str]] = []

        def fake_run(args, **_kwargs):
            captured.append(list(args))
            raise RuntimeError("stop before a container is started")

        monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
        result = sandbox.execute_program("print(1)")

        assert captured, "execute_program never reached the docker invocation"
        assert "64m" in captured[0], (
            f"SANDBOX_MEMORY_LIMIT=64m did not reach the container arguments: {captured[0]}"
        )
        # The containment layer reports rather than raises, even here.
        assert result.status is sandbox.ExecutionStatus.BLOCKED


class TestEveryDeclaredSettingIsConsumed:
    """The guard against this recurring.

    A variable may be consumed by Python or by a compose file. It may not be
    consumed by nothing.
    """

    # Read through `resolve_channel(role)`, which builds the name as
    # f"{role.upper()}_MODEL" - so a literal search cannot find them.
    DYNAMIC = {
        "NATURAL_CHANNEL_MODEL",
        "PROGRAM_CHANNEL_MODEL",
        "VERIFICATION_AGENT_MODEL",
        "QUESTION_UNDERSTANDING_MODEL",
    }

    def sources(self) -> str:
        parts = []
        for pattern in ("backend/**/*.py", "evaluation/**/*.py",
                        "experiments/*.py", "scripts/*.py"):
            for path in PROJECT_ROOT.glob(pattern):
                parts.append(path.read_text(encoding="utf-8"))
        for name in ("docker-compose.yml", "docker-compose.app.yml"):
            parts.append((PROJECT_ROOT / name).read_text(encoding="utf-8"))
        for path in (PROJECT_ROOT / "docker").glob("*.Dockerfile"):
            parts.append(path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def declared(self) -> list[str]:
        text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        return sorted({m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]+)=", text, re.M)})

    def test_no_declared_variable_is_read_by_nothing(self):
        haystack = self.sources()
        orphans = [
            name
            for name in self.declared()
            if name not in self.DYNAMIC and name not in haystack
        ]
        assert not orphans, (
            f"declared in .env.example and consumed nowhere: {orphans}. "
            f"Either wire it up or delete it - a setting nothing reads is worse "
            f"than no setting, because it reports being configured."
        )

    def test_the_dynamic_channel_variables_really_are_dynamic(self):
        """Guards the allowlist above from becoming a place to hide orphans."""
        registry = (PROJECT_ROOT / "backend/services/llm/registry.py").read_text(
            encoding="utf-8"
        )
        assert 'f"{role.upper()}_MODEL"' in registry

    def test_the_vestigial_ablation_variable_stayed_deleted(self):
        """ABLATION_SAME_MODEL was unread, and wiring it up would have been the
        wrong repair: arms are ArmConfig objects (D26) and a second source of
        truth could disagree with the arm actually running."""
        text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        assert not re.search(r"^ABLATION_SAME_MODEL=", text, re.M)


class TestPathsAreAnchored:
    """The other half of the same class: a path relative to the working
    directory that resolves somewhere harmless-looking and empty."""

    def test_no_module_declares_a_cwd_relative_path_constant(self):
        offenders = []
        for pattern in ("backend/**/*.py", "evaluation/**/*.py",
                        "experiments/*.py", "scripts/*.py"):
            for path in PROJECT_ROOT.glob(pattern):
                for m in re.finditer(r'^([A-Z_]+) = Path\("[^"]*/',
                                     path.read_text(encoding="utf-8"), re.M):
                    offenders.append(f"{path.name}:{m.group(1)}")
        assert not offenders, (
            f"CWD-relative path constants: {offenders}. Run the script from "
            f"anywhere else and these resolve to files that do not exist - which "
            f"cost a validation session, and which is where TEST_ACCESS_LOG's "
            f"audit trail would have gone."
        )

    def test_no_entry_point_loads_dotenv_from_the_working_directory(self):
        offenders = []
        for path in Path(PROJECT_ROOT / "scripts").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r'load_dotenv\(\)|load_dotenv\("\.env"\)', text):
                offenders.append(path.name)
        assert not offenders, (
            f"{offenders} load .env relative to the working directory; run from "
            f"elsewhere and every API key silently goes missing, which reads as "
            f"'no providers configured' rather than as a path problem."
        )


class TestTokenUsageReachesTheArtifact:
    """Two finished modules with no wire between them (RX-022).

    `evaluation/metrics/efficiency.py` expects `prompt_tokens`,
    `completion_tokens` and `reasoning_tokens`; nothing recorded them, so spec
    §33's cost analysis could not be computed from the artifacts meant to feed
    it, and the campaign's token budget rested on a single 429 rather than on a
    measurement.
    """

    def usage(self, **kwargs):
        from backend.services.llm.base import Usage

        return Usage(**kwargs)

    def test_a_usage_serialises_every_field_efficiency_asks_for(self):
        record = self.usage(
            prompt_tokens=120, completion_tokens=40, reasoning_tokens=200,
            reported_total_tokens=380, latency_seconds=1.2345,
        ).as_dict()
        for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                      "total_tokens", "equivalent_cost_usd"):
            assert field in record, field
        assert record["total_tokens"] == 380

    def test_the_provider_total_wins_over_the_derived_sum(self):
        """An observed Gemini call reported prompt=29, completion=2, total=223.
        Deriving the total would have understated it roughly sevenfold."""
        record = self.usage(
            prompt_tokens=29, completion_tokens=2, reported_total_tokens=223
        ).as_dict()
        assert record["total_tokens"] == 223

    def test_no_call_and_a_zero_call_are_different_records(self):
        """Collapsing them would make an unmeasured arm look free."""
        from backend.services.llm.base import usage_record

        assert usage_record(None) is None
        assert usage_record(self.usage())["total_tokens"] == 0

    def test_a_channel_record_carries_its_usage(self):
        from backend.agents.orchestrator import _channel_record
        from backend.verification.consistency import ChannelAnswer

        class Result:
            answer = ChannelAnswer(name="natural", value=None, available=False)
            usage = None

        result = Result()
        result.usage = self.usage(prompt_tokens=10, completion_tokens=5)
        record = _channel_record(result)
        assert record["usage"]["total_tokens"] == 15

    def test_the_deterministic_channel_legitimately_has_none(self):
        """It runs no model. That is a large part of why it survived D24."""
        from backend.agents.orchestrator import _channel_record
        from backend.verification.consistency import ChannelAnswer

        class Result:
            answer = ChannelAnswer(name="deterministic", value=None, available=False)

        assert _channel_record(Result())["usage"] is None

    def test_a_question_row_totals_its_channels(self):
        from backend.agents.orchestrator import _total_tokens

        rows = [
            {"usage": {"total_tokens": 100}},
            {"usage": {"total_tokens": 250}},
            {"usage": None},
            None,
        ]
        assert _total_tokens(rows) == 350
