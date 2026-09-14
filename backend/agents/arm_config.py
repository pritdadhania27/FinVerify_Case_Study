"""What one experimental condition switches on (spec Module 24).

`ArmConfig` lives here, apart from the orchestrator that consumes it, because
the arm table is *data about the experiment* and several things that are not the
agent runtime need to read it: the campaign runner, the evaluation layer, and
the API. It previously sat in `orchestrator.py`, which imports langgraph at
module scope - so `GET /arms` returned 500 inside the API container, whose image
deliberately carries no agent dependencies. A description of an experiment
should not require the machinery that runs it.

Nothing in this module may import anything heavier than the standard library.
`orchestrator.py` re-exports `ArmConfig`, so every existing import still works.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ArmConfig"]


@dataclass(frozen=True)
class ArmConfig:
    """One experimental condition. Every baseline and ablation arm is one of these."""

    name: str
    description: str = ""
    # "none" is B1's closed-book condition; "semantic" is ablation F.
    # "oracle" hands the question the chunks containing its gold evidence, so
    # every remaining error is a reasoning error by construction. It is a
    # DIAGNOSTIC for H2, not an end-to-end measurement - see
    # evaluation/oracle_evidence.py.
    retrieval: str = "hybrid"
    use_natural: bool = True
    use_program: bool = True
    use_deterministic: bool = True
    use_consistency: bool = True
    use_verification_agent: bool = True
    # B5. n > 0 samples the natural channel n times and scores their dispersion.
    self_consistency_samples: int = 0
    # B4. Extra retrieval rounds seeded by the first round's evidence.
    iterative_retrieval_rounds: int = 0
    # Ablation H. Binds both channels to one model, which is the arm that tests
    # H4 - and the one the slice runner otherwise refuses to run.
    same_model_both_channels: bool = False
    top_k: int = 8
    max_transient_retries: int = 0

    @property
    def provides_detection_score(self) -> bool:
        """Whether this arm can appear in the detection table at all.

        A single-channel arm with no consistency engine and no sampling has
        nothing to rank questions by. Giving it a constant score would put a
        0.5 AUROC in the table that reads as "this baseline detects nothing",
        when the truth is "this baseline is not a detector". The QA table still
        includes it; the detection table does not.
        """
        return self.use_consistency or self.self_consistency_samples > 1

    @property
    def channels_used(self) -> tuple[str, ...]:
        names = []
        if self.use_natural:
            names.append("natural")
        if self.use_program:
            names.append("program")
        if self.use_deterministic:
            names.append("deterministic")
        return tuple(names)

    def validate(self) -> tuple[str, ...]:
        """Configuration errors, returned rather than raised so a campaign can
        report every bad arm at once instead of dying on the first."""
        problems: list[str] = []
        if not (self.use_natural or self.use_program):
            problems.append(f"{self.name}: no reasoning channel is enabled")
        if self.retrieval not in {"none", "semantic", "hybrid", "oracle"}:
            problems.append(f"{self.name}: unknown retrieval mode {self.retrieval!r}")
        # Two ANSWERING channels, counting the deterministic verifier. Ablation
        # B drops the natural channel and still has program + deterministic to
        # cross-check, so requiring both LLM channels here would refuse a valid
        # arm. The deterministic channel abstains on lookups, so arm B will
        # produce UNCERTAIN verdicts on those - that is the arm's real
        # limitation and it belongs in the results, not in a config error.
        if self.use_consistency and len(self.channels_used) < 2:
            problems.append(
                f"{self.name}: the consistency engine cross-checks channels; "
                f"only {len(self.channels_used)} is enabled, so there is nothing "
                "to compare"
            )
        if self.use_verification_agent and not self.use_consistency:
            problems.append(
                f"{self.name}: the arbiter is triggered by a consistency verdict, "
                "so it cannot run without the consistency engine"
            )
        if self.self_consistency_samples == 1:
            problems.append(
                f"{self.name}: one sample measures nothing; self-consistency needs n >= 2"
            )
        return tuple(problems)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "retrieval": self.retrieval,
            "use_natural": self.use_natural,
            "use_program": self.use_program,
            "use_deterministic": self.use_deterministic,
            "use_consistency": self.use_consistency,
            "use_verification_agent": self.use_verification_agent,
            "self_consistency_samples": self.self_consistency_samples,
            "iterative_retrieval_rounds": self.iterative_retrieval_rounds,
            "same_model_both_channels": self.same_model_both_channels,
            "top_k": self.top_k,
            "provides_detection_score": self.provides_detection_score,
        }

