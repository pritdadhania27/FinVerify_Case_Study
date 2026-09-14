"""Experiment management (spec Module 28, decision D3).

What makes a quota-bound campaign survivable.

Numbers first, because they set the design. The full evaluation is 150 questions
(D23) across 13 arms. At roughly 2-3 model calls per question per dual-channel
arm, that is several thousand requests against a free-tier allowance measured in
hundreds per day. The campaign therefore *cannot* be a single process that either
finishes or fails: it will be interrupted by quota, by a laptop closing, and by
the vendor question that is still open (D21).

So three properties, each of which is the difference between a recoverable
interruption and a lost day:

1. **Append-only, one row per (arm, question), keyed.** A restart reads what is
   already on disk and skips it. Nothing is recomputed and nothing is
   overwritten - the artifact is the ledger, not a cache.
2. **Budget before spend.** The request count is estimated against the OBSERVED
   daily limits and reported before the first call. A campaign that dies 70%
   through a quota day has wasted more than it spent.
3. **Quota exhaustion is a clean stop, not a crash.** The partial artifact is
   valid, the metrics are computed over what completed, and the resume command
   is printed. A traceback here would lose the run's own record of where it got
   to.

**Arm-major or question-major?** Question-major - every arm answers question 1
before any arm sees question 2. Arm-major would finish B1 completely and leave
the campaign, if interrupted, with one complete arm and nothing to compare it
with. Question-major means an interruption always yields a *balanced* prefix:
fewer questions, every arm, and a publishable (if underpowered) comparison. The
statistical protocol pairs on identical question sets, so balance is worth more
than completeness.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backend.core.paths import project_path
from backend.agents.orchestrator import ArmConfig, PipelineDeps, build_graph, run_question
from backend.services.llm.base import QuotaExhaustedError

__all__ = [
    "RUNS_ROOT",
    "CampaignRecorder",
    "RequestBudget",
    "estimate_requests",
    "CampaignResult",
    "run_campaign",
]

# A resume looks here for prior artifacts. Relative to the working
# directory it finds none, does not fail, and starts the campaign again -
# which on a free tier spends a day of quota.
RUNS_ROOT = project_path("experiments/runs")

# MEASURED, not assumed (RX-023). Prompts are assembled exactly as the channels
# assemble them and counted without being sent: median 1,780 tokens of prompt
# across 40 campaign questions, of which ~1,515 is the evidence blocks.
#
# The completion side is the RESERVED budget, not the reply. A live Groq 429
# reported "Requested 4143" against max_tokens=4096 and a short prompt, which
# says the provider charges the reserve. That makes max_tokens 70% of a call and
# the dominant lever - but a near-linear one, not a magic one: even
# max_tokens=512 leaves a ~2,290-token floor, because the prompt is the floor.
PROMPT_TOKENS_PER_CALL = 1_780


def tokens_per_call(max_tokens: int | None = None) -> int:
    """What one model call costs, prompt plus reserved completion.

    Read from the live setting rather than frozen, so a campaign budgeted after
    lowering LLM_MAX_TOKENS reports the budget it will actually run under.
    """
    if max_tokens is None:
        from backend.services.llm.settings import llm_settings

        max_tokens = llm_settings().max_tokens
    return PROMPT_TOKENS_PER_CALL + max_tokens


def _git_commit() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            or "unknown"
        )
    except Exception:  # noqa: BLE001 - provenance is best-effort, never a gate
        return "unknown"


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": _git_commit(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


@dataclass(frozen=True)
class RequestBudget:
    """What a campaign will cost in requests, per provider, before it runs."""

    per_arm: dict[str, int]
    total: int
    # Requests attributable to the arbiter, which fires only on disagreement.
    # Counted at its ceiling because a budget that assumes the favourable case
    # is not a budget.
    arbiter_upper_bound: int
    questions: int
    # Calls per channel ROLE, so they can be attributed to the provider that
    # actually serves them. Default keeps older callers working.
    per_role: dict = field(default_factory=dict)

    def days_at(self, requests_per_day: int | None) -> float | None:
        if not requests_per_day:
            return None
        return self.total / requests_per_day

    def calls_by_provider(self) -> dict[str, int]:
        """Calls each PROVIDER will serve.

        Requests do not all land in one place, and the limits are per provider.
        Channel B runs on NVIDIA and Channel A on Groq (D21), so a budget that
        pools them charges Groq for calls it never sees - which is the opposite
        error to the one RX-022 fixed and just as misleading.
        """
        from backend.services.llm.registry import resolve_channel

        out: dict[str, int] = {}
        for role, calls in self.per_role.items():
            try:
                provider = resolve_channel(role).provider
            except RuntimeError:
                # An unconfigured role cannot be attributed. Skipped rather than
                # guessed; the total still reports every call.
                continue
            out[provider] = out.get(provider, 0) + calls
        return out

    def tokens_by_provider(self, max_tokens: int | None = None) -> dict[str, int]:
        cost = tokens_per_call(max_tokens)
        return {name: calls * cost for name, calls in self.calls_by_provider().items()}

    @property
    def estimated_tokens(self) -> int:
        """Tokens this campaign will consume, at the ceiling.

        Requests were the only thing counted here until 2026-08-30, and that
        made the budget wrong by two orders of magnitude in the direction that
        matters. Groq's free tier caps **tokens** per day at 200,000; the
        request counter still showed 14,293 of 14,400 free while the provider
        was already refusing (RX-022). A campaign budgeted in requests looked
        like a day's work and was closer to three months of them.
        """
        return self.total * tokens_per_call()

    def days_at_tokens(self, tokens_per_day: int | None) -> float | None:
        if not tokens_per_day:
            return None
        return self.estimated_tokens / tokens_per_day

    def as_dict(self) -> dict:
        return {
            "questions": self.questions,
            "per_arm": self.per_arm,
            "total_requests": self.total,
            "estimated_tokens": self.estimated_tokens,
            "arbiter_upper_bound": self.arbiter_upper_bound,
            "note": (
                "the arbiter fires only on disagreement, so the total is an "
                "upper bound; the realised count is recorded per run"
            ),
        }


def _resume_hint(retry_after: float | None) -> str:
    """How long until the allowance returns, in words an operator can act on."""
    if retry_after is None:
        return (
            "the provider stated no retry delay; its allowance is a rolling "
            "window (RX-024), so re-run rather than waiting for a day boundary"
        )
    if retry_after < 90:
        return f"the provider says the allowance returns in ~{retry_after:.0f}s"
    if retry_after < 5400:
        return f"the provider says the allowance returns in ~{retry_after / 60:.0f} minutes"
    return f"the provider says the allowance returns in ~{retry_after / 3600:.1f} hours"


def estimate_requests(arms: Iterable[ArmConfig], questions: int) -> RequestBudget:
    """Model calls this campaign will make, counted at the ceiling.

    Retrieval and the deterministic verifier cost nothing here: both are local.
    The deterministic channel in particular is fully deterministic (regex plus a
    lexicon, no model), so it consumes no quota at all - which is a large part of
    why it survived the D24 scope cut.
    """
    per_arm: dict[str, int] = {}
    per_role: dict[str, int] = {}
    arbiter = 0
    for config in arms:
        calls = 0
        if config.use_natural:
            n = max(1, config.self_consistency_samples)
            calls += n
            per_role["natural_channel"] = per_role.get("natural_channel", 0) + n * questions
        if config.use_program:
            calls += 1
            # Arm H binds both channels to one model, so its program calls land
            # on the natural channel's provider. Counting them under the program
            # role would understate the provider that actually serves them.
            role = "natural_channel" if config.same_model_both_channels else "program_channel"
            per_role[role] = per_role.get(role, 0) + questions
        if config.use_verification_agent:
            calls += 1
            arbiter += questions
            per_role["verification_agent"] = (
                per_role.get("verification_agent", 0) + questions
            )
        per_arm[config.name] = calls * questions
    return RequestBudget(
        per_arm=per_arm,
        total=sum(per_arm.values()),
        arbiter_upper_bound=arbiter,
        questions=questions,
        per_role=per_role,
    )


class CampaignRecorder:
    """Append-only run artifact (D3), resumable by construction.

    `results.jsonl` is opened in append mode and never truncated. That is not a
    performance choice: a campaign that rewrites its results file can lose two
    days of completed work to one interrupted write, and ENGINEERING_RULES.md makes
    `experiments/runs/` append-only precisely so failed and partial runs survive
    to be reported.
    """

    def __init__(self, run_id: str, *, root: Path = RUNS_ROOT):
        self.run_id = run_id
        self.directory = Path(root) / run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.results_path = self.directory / "results.jsonl"

    def completed(self) -> set[tuple[str, str]]:
        """(arm, question_id) pairs already on disk.

        A malformed trailing line - the signature of an interrupted write - is
        skipped rather than raising. It will simply be recomputed, which is the
        correct outcome: the alternative is a campaign that cannot restart until
        someone hand-edits a JSONL file.
        """
        if not self.results_path.exists():
            return set()
        done: set[tuple[str, str]] = set()
        with self.results_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                arm, qid = record.get("arm"), record.get("question_id")
                if arm and qid:
                    done.add((arm, qid))
        return done

    def append(self, record: dict) -> None:
        with self.results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

    def write_config(self, config: dict) -> None:
        (self.directory / "config.json").write_text(
            json.dumps(config, indent=2, default=str), encoding="utf-8"
        )

    def write_env(self) -> None:
        (self.directory / "env.json").write_text(
            json.dumps(_environment(), indent=2), encoding="utf-8"
        )

    def write_metrics(self, metrics: dict) -> None:
        (self.directory / "metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str), encoding="utf-8"
        )

    def records(self) -> list[dict]:
        if not self.results_path.exists():
            return []
        out = []
        with self.results_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out


@dataclass
class CampaignResult:
    run_id: str
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    stopped_early: bool = False
    stop_reason: str | None = None
    # Seconds until the provider says its allowance returns, when it said so.
    # Persisted because the allowance is a rolling window (RX-024): a resume
    # scheduled for the next day boundary would idle for hours that the provider
    # had already given back.
    resume_after_seconds: float | None = None
    directory: Path | None = None
    errors: list[str] = field(default_factory=list)

    def resume_command(self) -> str:
        return (
            f"python scripts/run_campaign.py --resume {self.run_id}"
            if self.stopped_early
            else ""
        )

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "completed": self.completed,
            "skipped_already_done": self.skipped,
            "failed": self.failed,
            "stopped_early": self.stopped_early,
            "stop_reason": self.stop_reason,
            "resume_after_seconds": self.resume_after_seconds,
            "errors": self.errors[:50],
        }


def run_campaign(
    questions: list[dict],
    arms: list[ArmConfig],
    deps: PipelineDeps,
    *,
    run_id: str | None = None,
    root: Path = RUNS_ROOT,
    on_progress: Callable[[str], None] | None = None,
    extra_config: dict | None = None,
) -> CampaignResult:
    """Run every arm over every question, resumably.

    `questions` are dicts carrying at minimum `id` and `question`. The gold
    answer is deliberately NOT required here: this function produces predictions,
    and grading happens afterwards against the frozen dataset. Keeping them apart
    means a change to the correctness predicate never requires re-spending quota.
    """
    problems = {c.name: c.validate() for c in arms}
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        raise ValueError(
            "misconfigured arms, none run: "
            + "; ".join(f"{k}: {', '.join(v)}" for k, v in problems.items())
        )

    run_id = run_id or f"campaign_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    recorder = CampaignRecorder(run_id, root=root)
    budget = estimate_requests(arms, len(questions))
    already = recorder.completed()

    recorder.write_config(
        {
            "run_id": run_id,
            "arms": [c.as_dict() for c in arms],
            "questions": len(questions),
            "question_ids": [q["id"] for q in questions],
            "budget": budget.as_dict(),
            "natural_model": deps.natural_model,
            "program_model": deps.program_model,
            "verifier_model": deps.verifier_model,
            "temperature": 0.0,
            **(extra_config or {}),
        }
    )
    recorder.write_env()

    result = CampaignResult(run_id=run_id, directory=recorder.directory)
    graph = build_graph()
    report = on_progress or (lambda _: None)

    if already:
        report(f"resuming {run_id}: {len(already)} (arm, question) pairs already done")

    # Question-major: see the module docstring. An interruption leaves a
    # balanced prefix rather than one finished arm.
    # Built once and only when an arm asks for it: resolving gold spans to
    # chunks reads a document's whole chunk cache, and no other arm needs it.
    oracle = None
    if any(config.retrieval == "oracle" for config in arms):
        from evaluation.oracle_evidence import OracleEvidence

        oracle = OracleEvidence()

    for question in questions:
        qid = question["id"]
        oracle_blocks = oracle.blocks_for(question) if oracle is not None else None
        for config in arms:
            if (config.name, qid) in already:
                result.skipped += 1
                continue
            try:
                outcome = run_question(
                    question["question"], config=config, deps=deps, graph=graph,
                    # Per question, from the dataset. A campaign-wide default
                    # cannot carry this: one PipelineDeps serves every question,
                    # so its company was necessarily None and retrieval ran
                    # unscoped across all five filings (RX-012).
                    company=question.get("company"),
                    document_id=question.get("document_id"),
                    oracle_blocks=oracle_blocks,
                )
            except QuotaExhaustedError as exc:
                result.stopped_early = True
                result.stop_reason = f"free-tier quota exhausted: {exc}"
                # WHEN to resume, from the provider's own answer where it gave
                # one. Groq's token allowance is a rolling 24-hour window, not a
                # daily reset (RX-024), so "wait for tomorrow" is wrong advice:
                # the allowance trickles back continuously and a stated delay of
                # a few minutes is normal. An operator told only "resume with"
                # has to guess, and guessing midnight wastes hours of a campaign
                # that is measured in days.
                when = _resume_hint(getattr(exc, "retry_after", None))
                report(
                    f"STOP: {result.stop_reason}\n"
                    f"  {result.completed} rows written to {recorder.directory}\n"
                    f"  {when}\n"
                    f"  resume with: {result.resume_command()}"
                )
                result.resume_after_seconds = getattr(exc, "retry_after", None)
                recorder.write_metrics(result.as_dict())
                return result
            except Exception as exc:  # noqa: BLE001 - one bad question must not end a campaign
                # Recorded as a failed row, not dropped. A question that vanishes
                # from the artifact changes the denominator silently, and a
                # denominator that moves between arms invalidates the pairing.
                result.failed += 1
                result.errors.append(f"{config.name}/{qid}: {type(exc).__name__}: {exc}")
                recorder.append(
                    {
                        "question_id": qid,
                        "arm": config.name,
                        "question": question["question"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "answer": None,
                        "risk_score": None,
                    }
                )
                continue

            record = outcome.as_record()
            record["question_id"] = qid
            for key in ("stratum", "ambiguous", "difficulty", "question_type"):
                if key in question:
                    record[key] = question[key]
            recorder.append(record)
            result.completed += 1
            report(f"{qid} / {config.name}: risk={outcome.risk_score}")

    recorder.write_metrics(result.as_dict())
    return result


def load_records(run_id: str, *, root: Path = RUNS_ROOT) -> list[dict]:
    return CampaignRecorder(run_id, root=root).records()
