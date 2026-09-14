"""Sandbox containment tests (spec Modules 9, 29).

These run REAL containers. They are the only evidence that the isolation flags
do what the module claims - asserting containment without executing anything
would be exactly the "never pretend something works" failure spec section 7
forbids.

Skipped (not silently passed) when Docker is unavailable, so a green run on a
machine without Docker never reads as verified containment.
"""

import shutil
import subprocess

import pytest

from backend.services.sandbox import (
    ExecutionStatus,
    SandboxConfig,
    execute_program,
)


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, timeout=20,
            ).returncode
            == 0
        )
    except Exception:
        return False


DOCKER = _docker_up()
requires_docker = pytest.mark.skipif(
    not DOCKER, reason="Docker daemon unavailable - containment cannot be verified"
)

FAST = SandboxConfig(timeout_seconds=8)


@requires_docker
class TestExecution:
    def test_simple_calculation(self):
        r = execute_program(
            "import json\n"
            "result = (125 - 100) / 100 * 100\n"
            "print(json.dumps({'value': result, 'unit': 'percent'}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.OK, r.error
        assert r.value == pytest.approx(25.0)

    def test_decimal_arithmetic_survives_the_sandbox(self):
        r = execute_program(
            "import json\n"
            "from decimal import Decimal\n"
            "v = (Decimal('125') - Decimal('100')) / Decimal('100')\n"
            "print(json.dumps({'value': str(v)}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.OK, r.error
        assert r.value == pytest.approx(0.25)

    def test_payload_carries_extra_fields(self):
        r = execute_program(
            "import json\nprint(json.dumps({'value': 1, 'unit': 'crore', 'steps': ['a']}))",
            config=FAST,
        )
        assert r.payload["unit"] == "crore"

    def test_duration_is_measured(self):
        r = execute_program("import json\nprint(json.dumps({'value': 1}))", config=FAST)
        assert r.duration_seconds > 0


@requires_docker
class TestContainment:
    """Each test verifies one isolation flag actually holds."""

    def test_no_network_access(self):
        """--network none. Exfiltration must be impossible, not just unlikely."""
        r = execute_program(
            "import socket, json\n"
            "socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
            "print(json.dumps({'value': 1}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.RUNTIME_ERROR
        assert r.value is None

    def test_filesystem_is_read_only(self):
        r = execute_program(
            "import json\n"
            "open('/evil.txt', 'w').write('x')\n"
            "print(json.dumps({'value': 1}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.RUNTIME_ERROR

    def test_cannot_write_outside_tmp(self):
        r = execute_program(
            "import json\nopen('/usr/local/x', 'w').write('x')\nprint(json.dumps({'value': 1}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.RUNTIME_ERROR

    def test_runs_as_non_root(self):
        r = execute_program(
            "import os, json\nprint(json.dumps({'value': os.getuid()}))", config=FAST
        )
        assert r.status is ExecutionStatus.OK, r.error
        assert r.value == 65534, "container must not run as root"

    def test_timeout_kills_an_infinite_loop(self):
        r = execute_program(
            "import json\nwhile True:\n    pass\nprint(json.dumps({'value': 1}))",
            config=SandboxConfig(timeout_seconds=4),
        )
        assert r.status is ExecutionStatus.TIMEOUT

    def test_timeout_leaves_no_container_behind(self):
        """--rm does not fire when the CLI is killed; cleanup must be explicit."""
        r = execute_program(
            "while True:\n    pass", config=SandboxConfig(timeout_seconds=4)
        )
        assert r.status is ExecutionStatus.TIMEOUT
        listing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={r.container_id}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.container_id not in listing.stdout

    def test_memory_limit_is_enforced(self):
        r = execute_program(
            "x = bytearray(2_000_000_000)\nprint(x)",
            config=SandboxConfig(timeout_seconds=15, memory_limit="128m"),
        )
        assert r.status in (
            ExecutionStatus.MEMORY_EXCEEDED,
            ExecutionStatus.RUNTIME_ERROR,
        )
        assert r.value is None

    def test_containers_do_not_share_state(self):
        """Each execution is disposable; one run cannot leave data for the next."""
        first = execute_program(
            "import json\nopen('/tmp/leak','w').write('secret')\nprint(json.dumps({'value':1}))",
            config=FAST,
        )
        assert first.status is ExecutionStatus.OK, first.error
        second = execute_program(
            "import json, os\nprint(json.dumps({'value': int(os.path.exists('/tmp/leak'))}))",
            config=FAST,
        )
        assert second.value == 0, "state leaked between sandbox runs"


@requires_docker
class TestOutputProtocol:
    def test_no_output_is_bad_output(self):
        r = execute_program("x = 1", config=FAST)
        assert r.status is ExecutionStatus.BAD_OUTPUT

    def test_non_json_output_rejected(self):
        """Parsing numbers out of prose would reintroduce the ambiguity the
        program channel exists to remove."""
        r = execute_program("print('the answer is 25 percent')", config=FAST)
        assert r.status is ExecutionStatus.BAD_OUTPUT

    def test_json_without_value_key_rejected(self):
        r = execute_program("import json\nprint(json.dumps({'answer': 25}))", config=FAST)
        assert r.status is ExecutionStatus.BAD_OUTPUT
        assert "value" in r.error

    def test_non_numeric_value_rejected(self):
        r = execute_program("import json\nprint(json.dumps({'value': 'lots'}))", config=FAST)
        assert r.status is ExecutionStatus.BAD_OUTPUT

    def test_last_line_is_the_result(self):
        """Programs may log; only the final line is the protocol."""
        r = execute_program(
            "import json\nprint('debug info')\nprint(json.dumps({'value': 42}))",
            config=FAST,
        )
        assert r.status is ExecutionStatus.OK
        assert r.value == 42

    def test_runtime_error_captures_stderr(self):
        r = execute_program("import json\nprint(json.dumps({'value': 1/0}))", config=FAST)
        assert r.status is ExecutionStatus.RUNTIME_ERROR
        assert "ZeroDivisionError" in r.stderr


class TestNoHostFallback:
    """The rule that matters most: never run untrusted code on the host."""

    def test_blocked_when_docker_is_missing(self, monkeypatch):
        monkeypatch.setattr("backend.services.sandbox.shutil.which", lambda _: None)
        r = execute_program("print(1)")
        assert r.status is ExecutionStatus.BLOCKED
        assert r.value is None
        assert "no host-execution fallback" in r.error

    def test_blocked_result_is_not_ok(self, monkeypatch):
        monkeypatch.setattr("backend.services.sandbox.shutil.which", lambda _: None)
        assert not execute_program("print(1)").ok


@requires_docker
class TestNonAsciiSource:
    """Generated programs quote the evidence, and the evidence is Indian filings.

    On Windows `text=True` encodes stdin with the host codepage (cp1252), which
    cannot represent a rupee sign, an en-dash, or the non-breaking hyphen that
    actually appeared in a generated comment. That raised UnicodeEncodeError out
    of the containment layer - a crash from the component whose entire contract
    is to report failure rather than raise it.
    """

    @pytest.mark.parametrize("char", ["\u20b9", "\u2011", "\u2013", "\u2019", "\u00a0"])
    def test_a_non_cp1252_character_in_the_source_still_executes(self, char):
        source = (
            "import json\n"
            f"# figure taken from the evidence {char} note 2.14\n"
            'print(json.dumps({"value": 1}))\n'
        )
        result = execute_program(source)
        assert result.status is ExecutionStatus.OK, result.error
        assert result.value == 1

    def test_non_ascii_in_a_string_literal_round_trips(self):
        source = (
            "import json\n"
            'print(json.dumps({"value": 3956, "unit": "\u20b9 crore"}))\n'
        )
        result = execute_program(source)
        assert result.status is ExecutionStatus.OK, result.error
        assert result.payload["unit"] == "\u20b9 crore"
