"""Sandboxed execution of model-generated programs (spec Modules 9, 29).

The second of two independent defences. `code_validator.py` decides whether a
program is *allowed to run*; this module decides *what it can reach* if the
static check was wrong. Both are required - a static analyser that is bypassed
by some construct nobody anticipated should still land the program in a
container with no network, no writable filesystem and no capabilities.

**There is deliberately no host-execution fallback.** If Docker is unavailable,
execution returns `BLOCKED` and the pipeline records a missing Channel B answer.
Running untrusted generated code on the host because a daemon was down is the
one failure this module exists to make impossible, and a "temporary" fallback is
how that happens. The consistency engine already handles a missing channel as
UNCERTAIN, so refusing to run costs correctness nothing.

Result protocol: the program's final stdout line must be a JSON object with a
`value` key. Parsing a number out of free-form output would reintroduce exactly
the ambiguity the program channel exists to remove.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from enum import Enum

__all__ = ["ExecutionStatus", "ExecutionResult", "SandboxConfig", "execute_program"]


class ExecutionStatus(Enum):
    OK = "ok"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    BAD_OUTPUT = "bad_output"
    BLOCKED = "blocked"  # sandbox unavailable - never a host fallback


@dataclass(frozen=True)
class SandboxConfig:
    image: str = "python:3.12-slim"
    timeout_seconds: int = 10
    memory_limit: str = "256m"
    cpu_limit: str = "1.0"
    pids_limit: int = 64
    tmpfs_size: str = "16m"

    @classmethod
    def from_env(cls) -> SandboxConfig:
        """The configured limits, or these defaults.

        The four SANDBOX_* variables sat in `.env` unread since Module 9. The
        defaults happen to match them exactly, so nothing was running unlimited
        - but an operator who tightened `SANDBOX_MEMORY_LIMIT` for an untrusted
        corpus would have got no tightening and no error, which is the worse
        half of a security control: one that reports being configured.
        """
        import os

        return cls(
            image=os.environ.get("SANDBOX_IMAGE") or cls.image,
            timeout_seconds=int(os.environ.get("SANDBOX_TIMEOUT_SECONDS") or cls.timeout_seconds),
            memory_limit=os.environ.get("SANDBOX_MEMORY_LIMIT") or cls.memory_limit,
            cpu_limit=os.environ.get("SANDBOX_CPU_LIMIT") or cls.cpu_limit,
        )


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    value: float | None = None
    payload: dict | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    error: str | None = None
    container_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.OK


def _docker_available() -> tuple[bool, str]:
    docker = shutil.which("docker")
    if not docker:
        return False, "docker CLI not on PATH"
    try:
        proc = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"docker probe failed: {exc}"
    if proc.returncode != 0:
        return False, f"docker daemon unreachable: {proc.stderr.strip()[:160]}"
    return True, proc.stdout.strip()


def _container_args(name: str, cfg: SandboxConfig) -> list[str]:
    """Isolation flags. Each closes a specific reachable resource."""
    return [
        "run", "--rm", "-i",
        "--name", name,
        # No network at all: exfiltration and remote fetch are impossible, not
        # merely discouraged.
        "--network", "none",
        # memory-swap equal to memory prevents swapping around the memory cap.
        "--memory", cfg.memory_limit,
        "--memory-swap", cfg.memory_limit,
        "--cpus", cfg.cpu_limit,
        # Bounds fork bombs.
        "--pids-limit", str(cfg.pids_limit),
        "--read-only",
        # Python needs somewhere to write; noexec stops a dropped binary running.
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={cfg.tmpfs_size}",
        # nobody:nogroup - no root inside the container.
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "HOME=/tmp",
        cfg.image,
        # -I isolated mode: ignore environment variables and user site-packages.
        # -S skips site initialisation. '-' reads the program from stdin, so the
        # source never becomes a shell argument and cannot be word-split.
        "python", "-I", "-S", "-",
    ]


def execute_program(
    source: str, *, config: SandboxConfig | None = None
) -> ExecutionResult:
    """Run `source` inside a disposable container and parse its JSON result.

    Callers must run `validate_program` first. This function does not re-validate
    - it is the containment layer, not the policy layer - but it is written to be
    safe even for source that slipped past validation.
    """
    # from_env(), not the bare defaults. `SandboxConfig.from_env()` was written for
    # RX-021 and tested, and then nothing on the production path called it: every
    # caller passes config=None, so the SANDBOX_* variables were read by no
    # campaign ever run. Harmless only because the defaults equal .env.example -
    # an operator tightening a limit got no tightening and no error.
    cfg = config or SandboxConfig.from_env()
    docker = shutil.which("docker")
    available, detail = _docker_available()
    if not available:
        return ExecutionResult(
            ExecutionStatus.BLOCKED,
            error=(
                f"sandbox unavailable ({detail}). Program NOT executed - there is "
                "no host-execution fallback by design."
            ),
        )

    name = f"finverify-sbx-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [docker, *_container_args(name, cfg)],
            input=source,
            capture_output=True,
            # UTF-8 explicitly, NOT the host default. On Windows `text=True`
            # encodes stdin as cp1252, and a generated program quoting the
            # evidence routinely contains a rupee sign, an en-dash or a
            # non-breaking hyphen - none of which cp1252 can represent. That
            # raised UnicodeEncodeError out of the containment layer and killed
            # the pipeline, which is both a crash and a lie: the layer whose
            # contract is "always return an ExecutionResult" was raising.
            encoding="utf-8",
            errors="replace",
            timeout=cfg.timeout_seconds + 5,  # daemon overhead beyond the inner limit
            check=False,
        )
    except subprocess.TimeoutExpired:
        # --rm does not fire if the CLI is killed first, so remove it explicitly;
        # otherwise repeated timeouts accumulate live containers.
        subprocess.run([docker, "kill", name], capture_output=True, timeout=30, check=False)
        subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=30, check=False)
        return ExecutionResult(
            ExecutionStatus.TIMEOUT,
            duration_seconds=time.monotonic() - started,
            error=f"execution exceeded {cfg.timeout_seconds}s and was killed",
            container_id=name,
        )
    except OSError as exc:
        return ExecutionResult(ExecutionStatus.BLOCKED, error=f"failed to start sandbox: {exc}")
    except Exception as exc:  # noqa: BLE001 - see below
        # Last-resort guard. This function is the containment layer and its
        # contract is to REPORT failure, never to raise it at the caller: an
        # exception here aborts an evaluation run mid-way and loses every result
        # already computed. Untrusted input reaching an unexpected code path is
        # exactly the case that must degrade quietly and visibly.
        return ExecutionResult(
            ExecutionStatus.BLOCKED,
            duration_seconds=time.monotonic() - started,
            error=f"sandbox invocation failed: {type(exc).__name__}: {exc}",
            container_id=name,
        )

    duration = time.monotonic() - started
    stdout, stderr = proc.stdout or "", proc.stderr or ""

    if proc.returncode != 0:
        # 137 = SIGKILL, which the OOM killer uses when the memory cap is hit.
        status = (
            ExecutionStatus.MEMORY_EXCEEDED
            if proc.returncode == 137
            else ExecutionStatus.RUNTIME_ERROR
        )
        return ExecutionResult(
            status, stdout=stdout, stderr=stderr, duration_seconds=duration,
            error=(stderr.strip() or f"exit code {proc.returncode}")[:2000],
            container_id=name,
        )

    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return ExecutionResult(
            ExecutionStatus.BAD_OUTPUT, stdout=stdout, stderr=stderr,
            duration_seconds=duration, error="program produced no output",
            container_id=name,
        )

    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return ExecutionResult(
            ExecutionStatus.BAD_OUTPUT, stdout=stdout, stderr=stderr,
            duration_seconds=duration,
            error=f"final stdout line is not JSON: {lines[-1][:200]!r}",
            container_id=name,
        )

    if not isinstance(payload, dict) or "value" not in payload:
        return ExecutionResult(
            ExecutionStatus.BAD_OUTPUT, stdout=stdout, stderr=stderr,
            duration_seconds=duration, payload=payload if isinstance(payload, dict) else None,
            error="JSON output must be an object containing a 'value' key",
            container_id=name,
        )

    raw = payload["value"]
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return ExecutionResult(
            ExecutionStatus.BAD_OUTPUT, stdout=stdout, stderr=stderr,
            duration_seconds=duration, payload=payload,
            error=f"'value' is not numeric: {raw!r}", container_id=name,
        )

    return ExecutionResult(
        ExecutionStatus.OK, value=value, payload=payload, stdout=stdout,
        stderr=stderr, duration_seconds=duration, container_id=name,
    )
