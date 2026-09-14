"""Pipeline orchestration (spec Module 17) - one code path, every arm a configuration.

`scripts/run_slice.py` wires the components procedurally. That was right for a
vertical slice and is wrong for an evaluation: baselines (Module 23) and ablation
arms (Module 24) differ from the full system only in which components run, and if
each arm were its own script they would drift. A reviewer asking "did B2 use the
same retrieval as P?" would have to diff two files and take the answer on trust.

So there is exactly one pipeline, and an arm is an `ArmConfig`. Two arms that
should differ in one component provably differ in one field. This is a
research-validity property, not a tidiness one: an ablation is only an ablation
if everything else is held constant, and holding it constant by discipline across
a dozen scripts is not something anyone should be asked to believe.

**Why a graph.** The pipeline is not linear - it branches on disagreement, and
the arbiter must run only when the channels actually conflict. LangGraph makes
that branch explicit and gives per-node state, which is what the run artifact
records. The nodes below are pure functions of state; the graph only sequences
them.

**Retry is deliberately off by default.** ENGINEERING_RULES.md is explicit that a retry loop
on top of the adapter's own would burn a day's free-tier quota, and that auth and
model-not-found errors are never retried. `max_transient_retries` therefore
defaults to 0 and, when raised, retries only `ProviderUnavailableError` - never a
rate limit, never an auth failure, never a quota exhaustion.

**Independence is preserved by the same mechanism as before (D1).** The program
node reads `question` and `blocks` from the state and nothing else. It cannot see
the natural node's output because it never asks for it, and a test asserts that
the state keys it touches exclude every Channel-A field.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.evidence import evidence_from_results
from backend.agents.natural_channel import run_natural_channel
from backend.agents.program_channel import run_program_channel
from backend.agents.question_understanding import parse_question
from backend.agents.arm_config import ArmConfig
from backend.agents.verification_agent import run_verification_agent, should_verify
from backend.core.financial_value import FinancialValue
from backend.retrieval.planned import retrieve_for_spec
from backend.services.llm.base import (
    ProviderUnavailableError,
    QuotaExhaustedError,
    usage_record,
)
from backend.verification.confidence import RiskAssessment, assess
from backend.verification.consistency import ChannelAnswer, ConsistencyReport, Verdict, compare
from backend.verification.deterministic_channel import run_deterministic_channel
from backend.verification.explanation import explain

__all__ = [
    "ArmConfig",
    "PipelineDeps",
    "PipelineResult",
    "self_consistency_risk",
    "run_question",
    "build_graph",
]

# Two answers within this relative distance are "the same answer" when clustering
# self-consistency samples. Deliberately the same tolerance the consistency
# engine uses, so "agreement" means one thing across the whole project.
_SAMPLE_TOLERANCE = Decimal("0.005")



@dataclass
class PipelineDeps:
    """Everything the nodes need from outside. Injected so tests need no network."""

    retriever: Any = None
    natural_provider: Any = None
    program_provider: Any = None
    verifier_provider: Any = None
    natural_model: str = ""
    program_model: str = ""
    verifier_model: str = ""
    document_id: str | None = None
    company: str | None = None
    sandbox_config: Any = None


class PipelineState(TypedDict, total=False):
    question: str
    config: ArmConfig
    deps: PipelineDeps
    # Per-question scope, overriding the campaign-wide default in `deps`. One
    # PipelineDeps is built for a whole campaign, so a company set there would
    # apply to every question - which is why it was left None and every question
    # retrieved across all five filings unfiltered.
    company: str | None
    document_id: str | None
    # Supplied by the caller for `retrieval == "oracle"`: the chunks containing
    # this question's gold evidence. Only the campaign runner has the gold, so
    # the orchestrator consumes these rather than resolving them.
    oracle_blocks: list | None
    spec: Any
    planned: Any
    blocks: list
    natural: Any
    samples: list
    program: Any
    deterministic: Any
    report: ConsistencyReport | None
    verification: Any
    risk: RiskAssessment | None
    # Reducers, so a node appends to the record rather than replacing it.
    node_log: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]


def self_consistency_risk(values: Sequence[FinancialValue | None]) -> float | None:
    """Dispersion across n samples of one model - the B5 risk score.

    **Why this is not simply `1 - modal_fraction`.** With n = 5 that expression
    takes five values, so the score is almost entirely ties, and AUROC counts a
    tie as half a concordance. B5 would then lose to the full system partly
    because its score was quantised, which is an artefact of the estimator and
    not a property of self-consistency. H1 would be "supported" for the wrong
    reason.

    So the modal fraction sets the coarse level and the mean relative spread
    among the samples breaks ties continuously within it. Two runs that both
    split 3-2 are then ordered by how far apart their answers actually were,
    which is real information the coarse form throws away.

    Returns None when fewer than two samples produced a value - there is nothing
    to be consistent about, and a fabricated score would enter the detection
    table as if it were a measurement.
    """
    usable = [v for v in values if v is not None]
    if len(usable) < 2:
        return None

    canonicals = [v.canonical() for v in usable]

    # Cluster greedily at the project's agreement tolerance.
    clusters: list[list[Decimal]] = []
    for value in canonicals:
        for cluster in clusters:
            reference = cluster[0]
            scale = max(abs(reference), abs(value))
            close = (
                abs(value - reference) / scale <= _SAMPLE_TOLERANCE if scale else True
            )
            if close:
                cluster.append(value)
                break
        else:
            clusters.append([value])

    modal_fraction = max(len(c) for c in clusters) / len(canonicals)
    coarse = 1.0 - modal_fraction

    # Mean relative distance from the modal cluster's representative, capped so
    # one wild sample cannot saturate the score on its own.
    modal = max(clusters, key=len)[0]
    scale = max(abs(modal), Decimal("1e-9"))
    spread = sum(min(abs(c - modal) / scale, Decimal(1)) for c in canonicals) / len(canonicals)

    # The coarse level dominates; the spread only orders within it.
    return min(1.0, coarse * 0.9 + float(spread) * 0.1)


def _quota_error_names() -> frozenset[str]:
    """Every class name that means "the allowance is spent".

    Derived from the hierarchy rather than listed, so a new quota class is
    covered the moment it exists. Names, because a channel records
    `type(exc).__name__` and the class itself does not survive into the result.

    Computed per call rather than cached at import: `__subclasses__` only sees
    classes already imported, so a module-level snapshot would silently depend
    on import order - the same kind of fragility that caused RX-033.
    """
    def names(cls: type) -> set[str]:
        found = {cls.__name__}
        for sub in cls.__subclasses__():
            found |= names(sub)
        return found

    return frozenset(names(QuotaExhaustedError))


def _raise_if_quota_exhausted(result) -> None:
    """Turn a swallowed quota error back into an exception.

    Both reasoning channels catch provider failures and return an unavailable
    channel rather than raising, which is right: one channel failing is an
    expected event the consistency engine is built to handle, and it should not
    end a run. Quota exhaustion is the exception, and treating it like any other
    channel failure is actively harmful twice over.

    It wastes what remains of the campaign - every subsequent question fails the
    same way, for hours - and, worse, it writes rows in which every channel is
    unavailable. Those are indistinguishable in the artifact from genuine
    abstentions, so they corrupt the abstention rate, the parse-failure rate and
    the detection labels of a run that looks complete.

    Matched on the exception CLASS recorded by the channel, not on message text,
    which is provider wording and changes without notice.

    **Every quota class, not one of them (RX-033).** This matched only
    `QuotaExhaustedError` - the provider refusing mid-call - while the refusal
    that actually fires first is `DailyQuotaExhausted`, the local limiter
    declining to make the call at all. The two are now one hierarchy, and the
    names are derived from it rather than written out, so a third quota class
    cannot be added without this seeing it. The exact failure described above
    then happened anyway: 166 of 180 rows written with Channel A unavailable, in
    a run reporting `completed 180, failed 0`.
    """
    metadata = getattr(result, "metadata", None) or {}
    if metadata.get("error_type") in _quota_error_names():
        raise QuotaExhaustedError(
            f"{result.answer.failure_reason} "
            "(surfaced by the orchestrator: a campaign must stop here rather "
            "than record every remaining question as an abstention)"
        )


def _retry(fn, *, attempts: int, on_error: list):
    """Call `fn`, retrying ONLY genuine provider unavailability.

    Rate limits, quota exhaustion, auth failures and unknown-model errors are
    never retried: the adapter already handles what is retryable, and looping
    over a quota error is how a day's free-tier allowance disappears in a
    minute (ENGINEERING_RULES.md, D14).
    """
    last: Exception | None = None
    for attempt in range(attempts + 1):
        try:
            return fn()
        except ProviderUnavailableError as exc:  # noqa: PERF203 - retry is the point
            last = exc
            on_error.append(f"transient provider failure (attempt {attempt + 1}): {exc}")
    raise last if last is not None else RuntimeError("retry helper called with no attempts")


# --------------------------------------------------------------------------- nodes


def _node_understand(state: PipelineState) -> dict:
    deps = state["deps"]
    # The question's own company wins over the campaign-wide default. It comes
    # from the dataset, which knows it authoritatively, rather than from parsing
    # it back out of the question text.
    spec = parse_question(
        state["question"], company=state.get("company") or deps.company
    )
    return {"spec": spec, "node_log": [{"node": "understand", "operation": spec.operation}]}


@dataclass
class _SemanticOnly:
    """Ablation F, presented through the retriever interface.

    Wrapping rather than adding a flag to `retrieve_for_spec` keeps the planned-
    retrieval logic byte-identical between arms F and A: the ONLY difference is
    which arm of `retrieve_arms` is returned, so a difference in results cannot
    come from a different filter, budget, or interleaving order.
    """

    inner: Any

    def retrieve(self, question: str, *, top_k: int = 10, **filters):
        return self.inner.retrieve_arms(question, top_k=top_k, **filters)["semantic"]


def _node_retrieve(state: PipelineState) -> dict:
    config, deps, spec = state["config"], state["deps"], state["spec"]

    # BEFORE the closed-book branch, which also fires when `deps.retriever is
    # None`. An oracle arm needs no retriever, so testing "none" first would
    # silently turn it into a closed-book arm - the exact substitution the
    # oracle exists to rule out.
    if config.retrieval == "oracle":
        # Every gold group, resolved to the chunk containing it. An empty list
        # means the oracle could NOT be built - a group nothing satisfies - and
        # is recorded as such rather than run as a closed-book question, which
        # would put an unretrievable question into the reasoning stratum and
        # count its failure as a reasoning error.
        blocks = list(state.get("oracle_blocks") or ())
        return {
            "planned": None,
            "blocks": blocks,
            "node_log": [
                {"node": "retrieve", "mode": "oracle", "blocks": len(blocks),
                 "oracle_available": bool(blocks)}
            ],
        }

    if config.retrieval == "none" or deps.retriever is None:
        # B1's closed book. The channels are handed an explicit empty evidence
        # set and the prompt says so, which is the difference between "answer
        # from parametric memory" and a silent retrieval bug.
        return {
            "planned": None,
            "blocks": [],
            "node_log": [{"node": "retrieve", "mode": "none", "blocks": 0}],
        }

    retriever = (
        _SemanticOnly(deps.retriever) if config.retrieval == "semantic" else deps.retriever
    )
    planned = retrieve_for_spec(
        retriever, spec, top_k=config.top_k,
        document_id=state.get("document_id") or deps.document_id,
    )
    results = list(planned.results)
    rounds = 0

    # B4: agentic RAG. A second round is only worth spending if the first left a
    # figure uncovered, and it must be *conditioned* on that - re-issuing the
    # same query would return the same chunks and dress a no-op as agency. The
    # relaxation drops the fiscal-year filter, which is the constraint most
    # likely to have excluded the right page (see `retrieve_for_spec`).
    for _ in range(config.iterative_retrieval_rounds):
        uncovered = [
            sub for sub in (spec.sub_questions or ())
            if not planned.per_sub_question.get(sub.metric)
        ]
        if not uncovered:
            break
        rounds += 1
        seen = {r.chunk_id for r in results}
        for sub in uncovered:
            extra = retriever.retrieve(
                sub.metric, top_k=config.top_k,
                document_id=state.get("document_id") or deps.document_id,
            )
            for hit in extra:
                if hit.chunk_id not in seen:
                    seen.add(hit.chunk_id)
                    results.append(hit)
                    planned.per_sub_question.setdefault(sub.metric, []).append(hit)

    blocks = evidence_from_results(results)
    return {
        "planned": planned,
        "blocks": blocks,
        "node_log": [
            {
                "node": "retrieve",
                "mode": config.retrieval,
                "blocks": len(blocks),
                "extra_rounds": rounds,
            }
        ],
    }


def _node_natural(state: PipelineState) -> dict:
    config, deps = state["config"], state["deps"]
    if not config.use_natural:
        return {"natural": None, "node_log": [{"node": "natural", "skipped": True}]}

    errors: list[str] = []
    blocks = state["blocks"]

    def call(name: str = "natural"):
        return run_natural_channel(
            state["question"],
            blocks,
            provider=deps.natural_provider,
            model=deps.natural_model,
            name=name,
        )

    result = _retry(call, attempts=config.max_transient_retries, on_error=errors)
    _raise_if_quota_exhausted(result)

    samples: list = []
    if config.self_consistency_samples > 1:
        # B5. Note these are samples of ONE model with ONE prompt: the comparator
        # H1 has to beat. The first call is reused as sample 1 rather than
        # spending an extra request on an identical query.
        samples.append(result)
        for i in range(1, config.self_consistency_samples):
            sample = _retry(
                lambda i=i: call(name=f"natural_sample_{i}"),
                attempts=config.max_transient_retries,
                on_error=errors,
            )
            _raise_if_quota_exhausted(sample)
            samples.append(sample)

    return {
        "natural": result,
        "samples": samples,
        "errors": errors,
        "node_log": [
            {
                "node": "natural",
                "available": result.answer.available,
                "samples": len(samples),
            }
        ],
    }


def _node_program(state: PipelineState) -> dict:
    """Channel B.

    Reads `question` and `blocks`. It does NOT read `natural`, and that is the
    whole of decision D1 expressed as code: there is no argument through which
    Channel A's answer could arrive, so agreement between the channels cannot be
    anchoring.
    """
    config, deps = state["config"], state["deps"]
    if not config.use_program:
        return {"program": None, "node_log": [{"node": "program", "skipped": True}]}

    errors: list[str] = []
    model = deps.natural_model if config.same_model_both_channels else deps.program_model
    result = _retry(
        lambda: run_program_channel(
            state["question"],
            state["blocks"],
            provider=(
                deps.natural_provider if config.same_model_both_channels
                else deps.program_provider
            ),
            model=model,
            sandbox_config=deps.sandbox_config,
        ),
        attempts=config.max_transient_retries,
        on_error=errors,
    )
    _raise_if_quota_exhausted(result)
    return {
        "program": result,
        "errors": errors,
        "node_log": [
            {
                "node": "program",
                "available": result.answer.available,
                "executed": result.executed,
                "model": model,
            }
        ],
    }


def _node_deterministic(state: PipelineState) -> dict:
    config = state["config"]
    if not config.use_deterministic:
        return {
            "deterministic": None,
            "node_log": [{"node": "deterministic", "skipped": True}],
        }
    result = run_deterministic_channel(state["spec"], state["blocks"])
    return {
        "deterministic": result,
        "node_log": [
            {"node": "deterministic", "available": result.answer.available,
             "applicable": result.answer.applicable}
        ],
    }


def _unavailable(name: str, reason: str) -> ChannelAnswer:
    return ChannelAnswer(name=name, value=None, available=False, failure_reason=reason)


def _node_consistency(state: PipelineState) -> dict:
    config = state["config"]
    if not config.use_consistency:
        return {"report": None, "node_log": [{"node": "consistency", "skipped": True}]}

    natural = state.get("natural")
    program = state.get("program")
    determ = state.get("deterministic")
    report = compare(
        natural.answer if natural else _unavailable("natural", "channel disabled in this arm"),
        program.answer if program else _unavailable("program", "channel disabled in this arm"),
        determ.answer if determ else None,
    )
    return {
        "report": report,
        "node_log": [
            {"node": "consistency", "verdict": report.verdict.name, "score": report.score}
        ],
    }


def _should_arbitrate(state: PipelineState) -> str:
    config, report = state["config"], state.get("report")
    if not config.use_verification_agent or report is None:
        return "assess"
    return "arbitrate" if should_verify(report) else "assess"


def _node_arbitrate(state: PipelineState) -> dict:
    deps = state["deps"]
    errors: list[str] = []
    natural, program = state.get("natural"), state.get("program")
    result = _retry(
        lambda: run_verification_agent(
            state["question"],
            state["blocks"],
            natural.answer if natural else _unavailable("natural", "disabled"),
            program.answer if program else _unavailable("program", "disabled"),
            provider=deps.verifier_provider,
            model=deps.verifier_model,
        ),
        attempts=state["config"].max_transient_retries,
        on_error=errors,
    )
    _raise_if_quota_exhausted(result)
    return {
        "verification": result,
        "errors": errors,
        "node_log": [
            {"node": "arbitrate", "resolution": result.resolution.value,
             "available": result.available}
        ],
    }


def _node_assess(state: PipelineState) -> dict:
    """Module 15's continuous risk score, or the B5 substitute, or nothing."""
    config = state["config"]
    report = state.get("report")
    verification = state.get("verification")

    if report is not None:
        risk = assess(
            report,
            evidence_blocks=len(state.get("blocks") or []),
            arbiter_abstained=bool(verification is not None and not verification.resolved),
            deterministic_applied=(
                state["deterministic"].answer.applicable
                if state.get("deterministic") is not None
                else False
            ),
        )
        return {"risk": risk, "node_log": [{"node": "assess", "risk": risk.risk_score}]}

    if config.self_consistency_samples > 1:
        score = self_consistency_risk(
            [s.answer.value for s in (state.get("samples") or [])]
        )
        return {
            "risk": None,
            "node_log": [{"node": "assess", "self_consistency_risk": score}],
        }

    # No detector in this arm. Deliberately no score: see
    # ArmConfig.provides_detection_score.
    return {"risk": None, "node_log": [{"node": "assess", "risk": None}]}


def build_graph():
    """Compile the pipeline graph.

    Takes no arm: the shape is identical for every arm, because the nodes
    short-circuit on their own config flags rather than being wired differently.
    One graph to reason about, and a disabled component shows up as `skipped` in
    the node log rather than being silently absent from it - which is what lets a
    reader confirm from the artifact alone that arm C really did run without the
    program channel.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("understand", _node_understand)
    graph.add_node("retrieve", _node_retrieve)
    graph.add_node("natural", _node_natural)
    graph.add_node("program", _node_program)
    graph.add_node("deterministic", _node_deterministic)
    graph.add_node("consistency", _node_consistency)
    graph.add_node("arbitrate", _node_arbitrate)
    graph.add_node("assess", _node_assess)

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "retrieve")
    # Sequential rather than parallel: the free-tier limiter is a shared,
    # process-wide daily counter and concurrent calls would race it. Latency is
    # reported as measured, so a parallel deployment would be faster than the
    # figures here - stated in the write-up rather than implied by them.
    graph.add_edge("retrieve", "natural")
    graph.add_edge("natural", "program")
    graph.add_edge("program", "deterministic")
    graph.add_edge("deterministic", "consistency")
    graph.add_conditional_edges(
        "consistency", _should_arbitrate, {"arbitrate": "arbitrate", "assess": "assess"}
    )
    graph.add_edge("arbitrate", "assess")
    graph.add_edge("assess", END)
    return graph.compile()


@dataclass(frozen=True)
class PipelineResult:
    """One question through one arm, with everything needed to grade and audit it."""

    question: str
    config: ArmConfig
    spec: Any = None
    blocks: tuple = ()
    natural: Any = None
    samples: tuple = ()
    program: Any = None
    deterministic: Any = None
    report: ConsistencyReport | None = None
    verification: Any = None
    risk: RiskAssessment | None = None
    node_log: tuple = ()
    errors: tuple = ()
    latency_seconds: float = 0.0

    @property
    def answer(self) -> FinancialValue | None:
        """The arm's single final answer.

        Priority: an arbiter resolution first - it is the only component that saw
        both channels and the evidence - then the natural channel, then the
        program channel, then the deterministic verifier. The last is included
        because refusing to answer when a fully deterministic computation
        succeeded would discard a correct answer for tidiness.
        """
        if self.verification is not None and self.verification.resolved:
            if self.verification.value is not None:
                return self.verification.value
        for source in (self.natural, self.program, self.deterministic):
            if source is not None and source.answer.available and source.answer.value is not None:
                return source.answer.value
        return None

    @property
    def answer_source(self) -> str | None:
        if self.verification is not None and self.verification.resolved and self.verification.value:
            return "verification_agent"
        for name, source in (
            ("natural", self.natural),
            ("program", self.program),
            ("deterministic", self.deterministic),
        ):
            if source is not None and source.answer.available and source.answer.value is not None:
                return name
        return None

    @property
    def abstained(self) -> bool:
        """The system declined, as distinct from failing to produce a number.

        The natural channel reports `sufficient=False` when it judged the
        evidence inadequate. That is a decision; an unparseable reply is a bug.
        EVALUATION.md §3 counts both as incorrect and reports their rates apart.

        The program channel makes the same distinction and it used to be dropped
        here. `program_channel` already separates a crash from a program that
        ran and deliberately printed `{"value": null}` - its comment calls the
        second "an abstention... recorded as such" - but this property only ever
        consulted the natural channel. On any arm that HAS a natural channel the
        omission is invisible, because that channel abstains on the same
        evidence. On B3, which is the program channel alone, it was not: 2 of the
        first 9 rows were programs that executed cleanly and correctly reported
        the evidence as insufficient, and every one was counted in
        `parse_failure_rate` - a principled refusal reported as a parser bug,
        which is the exact inversion of what `AnswerRecord` warns about.

        `executed` is the discriminator rather than the failure text: it is
        `execution.ok`, so a timeout, a crash or a non-JSON final line all stay
        failures, and only a clean run that chose to return nothing counts here.
        """
        if self.answer is not None:
            return False
        if self.natural is not None and not self.natural.sufficient:
            return True
        if self.program is not None and getattr(self.program, "executed", False):
            return True
        return bool(self.verification is not None and not self.verification.resolved)

    @property
    def risk_score(self) -> float | None:
        """The score `s` the detection metrics rank on - or None if this arm has none."""
        if self.risk is not None:
            return self.risk.risk_score
        if self.config.self_consistency_samples > 1:
            return self_consistency_risk([s.answer.value for s in self.samples])
        return None

    @property
    def agreed(self) -> bool | None:
        return None if self.report is None else self.report.verdict is Verdict.AGREE

    def as_record(self) -> dict:
        """The per-question row written to the run artifact."""
        return {
            "question": self.question,
            "arm": self.config.name,
            "spec": self.spec.as_dict() if self.spec is not None else None,
            "evidence": [
                {
                    "ref": b.ref,
                    "citation": b.citation,
                    "page": b.page,
                    "chunk_id": b.chunk_id,
                }
                for b in self.blocks
            ],
            "answer": str(self.answer.amount) if self.answer is not None else None,
            # The round-trippable form: "INR 3956 crore", "25%". Error analysis
            # re-parses THIS rather than the bare amount, so the correctness
            # label is produced by exactly one code path and a stored canonical
            # value can never disagree with the parser that produced it.
            "answer_text": str(self.answer) if self.answer is not None else None,
            "answer_canonical": (
                str(self.answer.canonical()) if self.answer is not None else None
            ),
            "answer_source": self.answer_source,
            "abstained": self.abstained,
            "risk_score": self.risk_score,
            "agreed": self.agreed,
            "verdict": self.report.verdict.name if self.report is not None else None,
            "consistency_score": self.report.score if self.report is not None else None,
            "natural": _channel_record(self.natural),
            "program": _channel_record(self.program),
            "deterministic": _channel_record(self.deterministic),
            "samples": [_channel_record(s) for s in self.samples],
            # Per question, so a campaign's spend can be summed from its own
            # artifact rather than inferred from a provider dashboard that
            # reports the whole account.
            "tokens_used": _total_tokens(
                [
                    _channel_record(self.natural),
                    _channel_record(self.program),
                    *[_channel_record(s) for s in self.samples],
                ]
            ),
            "verification": (
                {
                    "triggered": True,
                    "available": self.verification.available,
                    "resolution": self.verification.resolution.value,
                    "resolved": self.verification.resolved,
                    "value": (
                        str(self.verification.value.amount)
                        if self.verification.value is not None
                        else None
                    ),
                    # RX-045 records which channel the arbiter saw as CANDIDATE 1
                    # so a position effect stays measurable after the fact - and
                    # this serialiser dropped it, so it reached memory and never
                    # the artifact. The claim "the order shown is recorded" was
                    # true of the object and false of the run, which is the only
                    # place anyone would ever look for it.
                    "metadata": dict(self.verification.metadata or {}),
                }
                if self.verification is not None
                else {"triggered": False}
            ),
            "risk": self.risk.as_dict() if self.risk is not None else None,
            # Module 16's acceptance evidence. `explain` was reachable only from
            # the API's live-QA path, which is off by default (D30), so no
            # campaign row ever carried one and the module could not be shown to
            # work on real results - 787 rows of them, and not an explanation
            # among them. It is derived from this same object (D29), so recording
            # it costs no API call and cannot disagree with the verdict beside it.
            "explanation": explain(self).as_dict(),
            "node_log": list(self.node_log),
            "errors": list(self.errors),
            "latency_seconds": round(self.latency_seconds, 3),
        }


def _channel_record(result) -> dict | None:
    if result is None:
        return None
    answer = result.answer
    return {
        "available": answer.available,
        "applicable": answer.applicable,
        "failure_reason": answer.failure_reason,
        # The exception CLASS, so a dead channel can be told from a real
        # abstention without parsing provider prose (RX-033). The run that
        # exposed this held 166 rows whose only distinguishing mark was the
        # first word of a message - and the reason the codebase matches on
        # classes elsewhere is that message text is provider wording. None when
        # the channel did not fail, or failed without raising.
        "error_type": (getattr(result, "metadata", None) or {}).get("error_type"),
        # Whether the sandbox ran the program cleanly, for the program channel;
        # None for channels that execute nothing. This is the discriminator
        # between a crash and a program that ran and deliberately printed
        # {"value": null}, and it was missing from the artifact - so a run's
        # abstentions could only be recovered by matching on `failure_reason`
        # prose, which is the habit RX-033 was written to break. Recorded so a
        # completed run can be RE-analysed rather than re-run when the
        # abstention rule changes.
        "executed": getattr(result, "executed", None),
        "value": str(answer.value.amount) if answer.value is not None else None,
        "canonical": str(answer.canonical) if answer.canonical is not None else None,
        # Nothing recorded this until 2026-08-30, so evaluation/metrics/
        # efficiency.py - which expects exactly these fields - had no input from
        # any run, and spec §33's cost analysis could not be computed from the
        # artifacts meant to feed it. It is also what turns the campaign's token
        # budget from an extrapolation off a single 429 into a measurement
        # (RX-022). None means no call was made; a zeroed record means a call
        # was made and reported nothing - collapsing the two would make an
        # unmeasured arm look free. The deterministic channel legitimately has
        # no usage: it runs no model, which is a large part of why it survived
        # the D24 scope cut.
        "usage": usage_record(getattr(result, "usage", None)),
    }


def _total_tokens(records) -> int:
    """Tokens across every channel that actually called a model."""
    total = 0
    for record in records:
        usage = (record or {}).get("usage") if isinstance(record, dict) else None
        if usage:
            total += int(usage.get("total_tokens", 0))
    return total


def run_question(
    question: str,
    *,
    config: ArmConfig,
    deps: PipelineDeps,
    graph=None,
    company: str | None = None,
    document_id: str | None = None,
    oracle_blocks: list | None = None,
) -> PipelineResult:
    """Run one question through one arm.

    `graph` is accepted so a campaign compiles once and reuses it across
    questions rather than rebuilding per call.

    `company` scopes retrieval to one issuer and matters more than it looks.
    Without it, retrieval searches all five filings, and measured Recall@10 on
    the gold set falls from 0.932 to 0.523 - most of the loss being the right
    figure retrieved from the wrong company's report (RX-012). The campaign
    passes it per question from the dataset.
    """
    problems = config.validate()
    if problems:
        raise ValueError("; ".join(problems))

    compiled = graph if graph is not None else build_graph()
    started = time.monotonic()
    final = compiled.invoke(
        {
            "question": question,
            "config": config,
            "deps": deps,
            "company": company,
            "document_id": document_id,
            "oracle_blocks": oracle_blocks,
            "node_log": [],
            "errors": [],
        }
    )
    elapsed = time.monotonic() - started

    return PipelineResult(
        question=question,
        config=config,
        spec=final.get("spec"),
        blocks=tuple(final.get("blocks") or ()),
        natural=final.get("natural"),
        samples=tuple(final.get("samples") or ()),
        program=final.get("program"),
        deterministic=final.get("deterministic"),
        report=final.get("report"),
        verification=final.get("verification"),
        risk=final.get("risk"),
        node_log=tuple(final.get("node_log") or ()),
        errors=tuple(final.get("errors") or ()),
        latency_seconds=elapsed,
    )
