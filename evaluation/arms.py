"""The systems compared and the ablation arms (spec Modules 23, 24; EVALUATION.md §7-§8).

Every arm is an `ArmConfig` over the single pipeline in
`backend.agents.orchestrator`. Nothing here contains reasoning logic - if it did,
an arm could differ from the full system in ways its name does not disclose, and
the ablation would stop being an ablation.

**Baselines answer a different question from ablations, and mixing them is a
common way to overstate a result.** A baseline says "here is what a simpler
system achieves"; an ablation says "here is what this component contributes to
*this* system". A table that pools them invites the reader to attribute the
baseline gap to the ablated component.

**Not every arm is a detector.** B1-B4 produce answers and no risk score: there is
nothing in a single-channel RAG pipeline to rank questions by. They appear in the
QA table and are absent from the detection table, rather than appearing there at
AUROC 0.5, which would read as "this baseline detects nothing" when the truth is
"this baseline is not a detector". Only B5 and the dual-channel arms can be
compared on detection, and B5 is the comparator that makes H1 falsifiable.

**Arm H is the one that tests the project's central assumption.** D1 asserts that
channel independence does real work. H binds both channels to one model with the
same two prompts, so the difference between A and H is model diversity with
everything else held constant. HYPOTHESES.md H4 records that a null result here
is the more useful outcome, and that it would narrow the contribution to modality
independence alone.
"""

from __future__ import annotations

# From `arm_config`, not `orchestrator`: this module is a description of the
# experiment, and importing it should not require the runtime that executes it.
# The orchestrator imports langgraph at module scope, so going through it made
# `GET /arms` a 500 inside the API container, whose image carries no agent deps.
from backend.agents.arm_config import ArmConfig

__all__ = [
    "BASELINES",
    "ABLATIONS",
    "DIAGNOSTICS",
    "ALL_ARMS",
    "arm",
    "detection_arms",
    "validate_all",
    "SELF_CONSISTENCY_SAMPLES",
]

# n for B5. Five is chosen to sit near the dual-channel arm's request count -
# the full system spends 2 channel calls plus an arbiter call on disagreement,
# so a 5-sample baseline is the same order of magnitude rather than an order
# cheaper. H1 is stated "at matched API cost", and the cost-matched comparison
# in `efficiency.cost_matched_comparison` is what checks this rather than
# assuming it.
SELF_CONSISTENCY_SAMPLES = 5


BASELINES: tuple[ArmConfig, ...] = (
    ArmConfig(
        name="B1",
        description="LLM only - closed book, no retrieval",
        retrieval="none",
        use_program=False,
        use_deterministic=False,
        use_consistency=False,
        use_verification_agent=False,
    ),
    ArmConfig(
        name="B2",
        description="RAG - hybrid retrieval + natural-language reasoning",
        use_program=False,
        use_deterministic=False,
        use_consistency=False,
        use_verification_agent=False,
    ),
    ArmConfig(
        name="B3",
        description="RAG + programmatic reasoning (PoT-style, single channel)",
        use_natural=False,
        use_deterministic=False,
        use_consistency=False,
        use_verification_agent=False,
    ),
    ArmConfig(
        name="B4",
        description="Agentic RAG - iterative retrieval, single reasoning channel",
        use_program=False,
        use_deterministic=False,
        use_consistency=False,
        use_verification_agent=False,
        iterative_retrieval_rounds=2,
    ),
    ArmConfig(
        name="B5",
        description=(
            "Self-consistency - n samples of one model in one modality; the "
            "SelfCheckGPT-style comparator H1 must beat (D11)"
        ),
        use_program=False,
        use_deterministic=False,
        use_consistency=False,
        use_verification_agent=False,
        self_consistency_samples=SELF_CONSISTENCY_SAMPLES,
    ),
)


ABLATIONS: tuple[ArmConfig, ...] = (
    ArmConfig(
        name="A",
        description="FinVerify-AI, full system - the proposed arm P",
    ),
    ArmConfig(
        name="B",
        description="minus the natural channel (program + deterministic remain)",
        use_natural=False,
    ),
    ArmConfig(
        name="C",
        description="minus the program channel (natural + deterministic remain)",
        use_program=False,
    ),
    ArmConfig(
        name="D",
        description="minus the consistency engine - and therefore the arbiter",
        use_consistency=False,
        use_verification_agent=False,
    ),
    ArmConfig(
        name="E",
        description="minus the verification agent",
        use_verification_agent=False,
    ),
    ArmConfig(
        name="F",
        description="minus hybrid retrieval - semantic leg only",
        retrieval="semantic",
    ),
    ArmConfig(
        name="G",
        description="minus the deterministic verifier (tests H3)",
        use_deterministic=False,
    ),
    ArmConfig(
        name="H",
        description=(
            "same model on both channels - the direct test of D1 and H4. "
            "Independence reduces to prompt and modality difference alone"
        ),
        same_model_both_channels=True,
    ),
)

# NOT ablations. An ablation removes one component from the full system to
# isolate its contribution, and every member of ABLATIONS differs from arm A in
# exactly one field - a property its own test pins. A diagnostic changes the
# CONDITIONS the system runs under instead, to reach a measurement the benchmark
# cannot otherwise supply. Kept separate so "the spec's ablation set" stays
# exactly what the spec says it is.
DIAGNOSTICS: tuple[ArmConfig, ...] = (
    ArmConfig(
        name="O",
        description=(
            "arm A with ORACLE retrieval - handed the chunks containing its "
            "gold evidence, so every error is a reasoning error by "
            "construction. A DIAGNOSTIC for H2 (D47), not an end-to-end result, "
            "and never to be reported beside arm A as though it were one"
        ),
        retrieval="oracle",
    ),
)

ALL_ARMS: dict[str, ArmConfig] = {a.name: a for a in (*BASELINES, *ABLATIONS, *DIAGNOSTICS)}

# "P" is EVALUATION.md §7's name for the proposed system and "A" is §8's name for
# the same configuration. One object under both names, so the two tables cannot
# silently describe different systems.
ALL_ARMS["P"] = ALL_ARMS["A"]


def arm(name: str) -> ArmConfig:
    if name not in ALL_ARMS:
        raise KeyError(f"unknown arm {name!r}; known: {sorted(ALL_ARMS)}")
    return ALL_ARMS[name]


def detection_arms() -> tuple[ArmConfig, ...]:
    """The arms that can legitimately appear in the detection table."""
    seen: set[str] = set()
    out: list[ArmConfig] = []
    for config in (*BASELINES, *ABLATIONS, *DIAGNOSTICS):
        if config.provides_detection_score and config.name not in seen:
            seen.add(config.name)
            out.append(config)
    return tuple(out)


def validate_all() -> dict[str, tuple[str, ...]]:
    """Every misconfigured arm, not just the first.

    Called by the campaign runner before a single API request is spent: a
    campaign that dies on arm G after two days of quota has wasted more than it
    cost to check.
    """
    return {
        config.name: problems
        for config in (*BASELINES, *ABLATIONS, *DIAGNOSTICS)
        if (problems := config.validate())
    }
