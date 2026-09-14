"""API contracts (spec Module 20).

Pydantic models rather than bare dicts, because the contract is the documentation:
FastAPI generates the OpenAPI schema from these, so a field that is not declared
here does not exist as far as a client is concerned.

Two conventions carried from the research layer rather than invented here:

- **A number travels with its unit and its canonical form.** `3956`, `INR 3956
  crore` and `39560000000` are the same answer, and a client that sees only the
  first is one scale error from a hundredfold mistake.
- **`risk_score` is optional and nullable.** An arm with no detector has no
  score, and the API must be able to say so. Substituting 0.5 would present a
  placeholder as a measurement.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ArmOut",
    "DocumentOut",
    "EvidenceOut",
    "ChannelOut",
    "VerificationOut",
    "AskRequest",
    "AnswerOut",
    "ExperimentOut",
    "EvaluationResultOut",
    "HealthOut",
]


class DocumentOut(BaseModel):
    document_id: str
    filename: str
    company: str | None = None
    fiscal_year: str | None = None
    page_count: int | None = None
    sha256: str
    source_url: str | None = None
    retrieved_on: str | None = None


class EvidenceOut(BaseModel):
    ref: str
    citation: str
    page: int | None = None
    chunk_id: str | None = None
    text: str | None = None
    # Which filing. A page number alone is ambiguous across a five-filing corpus,
    # and spec section 24 asks the response to name its source document.
    document_id: str | None = None
    document: str | None = None


class ArmOut(BaseModel):
    """What a configuration actually switches on (spec Module 24).

    Served because the UI cannot otherwise tell "this arm has no arbiter" from
    "the arbiter did not fire", and it was reporting the second for both. Every
    arm is arm A with exactly one field changed, so these booleans ARE the
    ablation - reading them is how a viewer knows what a row is evidence of.
    """

    name: str
    description: str
    uses_natural: bool
    uses_program: bool
    uses_deterministic: bool
    uses_consistency: bool
    uses_arbiter: bool
    same_model_both_channels: bool
    retrieval: str | None = None
    self_consistency_samples: int | None = None
    # What this arm removes relative to arm A, in words, or None for arm A.
    removes: str | None = None


class ChannelOut(BaseModel):
    name: str
    available: bool
    applicable: bool = True
    value: str | None = None
    canonical: str | None = None
    failure_reason: str | None = None


class VerificationOut(BaseModel):
    triggered: bool
    available: bool = False
    resolution: str | None = None
    resolved: bool = False
    value: str | None = None
    reasoning: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    company: str | None = None
    document_id: str | None = None
    arm: str = Field(
        default="A",
        description=(
            "Which configuration to run. Defaults to the full system. Any arm "
            "name from evaluation.arms is accepted so a client can reproduce a "
            "baseline through the same code path the campaign uses."
        ),
    )
    top_k: int = Field(default=8, ge=1, le=25)


class AnswerOut(BaseModel):
    question: str
    arm: str
    # Set when the answer is stored and can be reopened. A live answer that could
    # not be persisted is still returned, with these null (D51).
    answer_id: int | None = None
    question_id: str | None = None
    answer: str | None = None
    answer_text: str | None = None
    canonical: str | None = None
    answer_source: str | None = None
    abstained: bool = False
    verdict: str | None = None
    agreed: bool | None = None
    # Nullable on purpose: an arm with no detector has no score, and a default
    # would present a placeholder as a measurement.
    risk_score: float | None = None
    band: str = "UNSCORED"
    calibrated: bool = False
    channels: list[ChannelOut] = Field(default_factory=list)
    # What this configuration switches on. Without it the UI cannot distinguish
    # "this arm has no arbiter" from "the arbiter did not fire" - and it was
    # asserting the second for arms that structurally have no arbiter at all.
    arm_config: ArmOut | None = None
    evidence: list[EvidenceOut] = Field(default_factory=list)
    verification: VerificationOut | None = None
    explanation: dict | None = None
    latency_seconds: float | None = None
    quota_note: str | None = None


class ExperimentOut(BaseModel):
    run_id: str
    split: str | None = None
    natural_model: str | None = None
    program_model: str | None = None
    verifier_model: str | None = None
    independence: str | None = None
    answers: int = 0
    # Metric rows. A picker on the metrics screen needs this, not `answers`: the
    # pooled ablation report carries metrics and no answers of its own, and the
    # development runs carry neither.
    results: int = 0
    source_path: str | None = None


class EvaluationResultOut(BaseModel):
    run_id: str
    arm: str
    metric: str
    stratum: str = "all"
    value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    n: int | None = None
    note: str | None = None


class AnswerSummaryOut(BaseModel):
    """One recorded answer, thin enough to list.

    Exists because the verification screen previously asked the operator to type
    an integer primary key, which nobody outside the database knows. A recorded
    answer is only inspectable if it is first findable.

    `correct` and `risk_score` are deliberately nullable and must stay so.
    `correct` is None for an answer that has been produced but not graded, and
    `risk_score` is None for an arm with no detector (B1-B4) - rendering either
    as 0 would put a measurement where the truth is "not measured".
    """

    answer_id: int
    run_id: str
    arm: str
    question_id: str
    question: str
    answer_text: str | None = None
    abstained: bool = False
    verdict: str | None = None
    agreed: bool | None = None
    risk_score: float | None = None
    # Served rather than derived in the client. The band is a function of the
    # score and its thresholds, and a UI that reimplemented them would drift
    # from the API the first time either moved - so the thresholds live in
    # exactly one place (`confidence.band_for`) and both screens read this.
    band: str = "UNSCORED"
    correct: bool | None = None
    latency_seconds: float | None = None


class CorpusStatsOut(BaseModel):
    """What the system has actually ingested and measured.

    Every field is a COUNT of rows that exist, never a target or a plan. The
    dashboard renders this directly, and a dashboard that shows a number the
    database cannot produce is the failure mode this schema exists to prevent:
    there is deliberately no field here for a metric nobody has computed yet.
    Absent measurements are absent, and the UI says so in words.
    """

    documents: int
    pages: int
    sections: int
    tables: int
    financial_facts: int

    # Gold data, split by validation status. `validated` is the only one of
    # these that may be scored against; the split is exposed so a reader can see
    # how much of the set that is rather than taking 115 on trust.
    questions_total: int
    questions_validated: int
    questions_rejected: int
    questions_pending: int
    evidence_spans: int

    # Experimental progress. `answers` counts (arm, question) outcomes across
    # every ingested run, so it is rows measured - not questions covered.
    runs: int
    answers: int
    answers_graded: int
    arms: list[str] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: str
    database: bool
    vector_index: bool
    live_questions_enabled: bool
    # Which build is answering. A container keeps serving whatever code it was
    # built with, and every other field here reports on the SERVICES it talks
    # to - so all of them can be green while the process itself is weeks stale.
    # That is the same shape as D-1: a healthy-looking report about the wrong
    # thing. "unknown" when the image was built without a ref, never a guess.
    build_ref: str
    note: str
