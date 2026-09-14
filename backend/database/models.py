"""PostgreSQL schema (spec Module 18).

**The database is a projection, not the source of truth.** Decision D4 made run
artifacts files, and they stay canonical: `experiments/runs/` is append-only and
committed, so a result in the paper can be traced to a file in a git history.
Making Postgres authoritative would put the evidence behind a service that has to
be running, on a machine that has to exist, in a state nobody can diff.

So this layer *ingests* artifacts and makes them queryable - "every question
where the channels agreed and both were wrong, across all arms" is one SQL
statement and a painful loop over JSONL. Losing the database costs a re-ingest;
losing the artifacts would cost the results.

That inverts the usual precedence, so the schema is built for it:

- every row that came from an artifact keeps `run_id` and `source_path`, so any
  record can be traced back and re-derived;
- ingestion is idempotent on natural keys, because a partial ingest must be
  safe to repeat;
- no column exists that is not reconstructible from an artifact.

Entities follow spec §26's list. `users` is included because the spec names it,
with the honest note that this project has no multi-user story: it exists so an
API deployment has somewhere to put an identity, not because anything in the
research uses it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "Base",
    "User",
    "Company",
    "Document",
    "Page",
    "Section",
    "TableRecord",
    "FinancialFactRow",
    "Question",
    "Evidence",
    "Experiment",
    "ReasoningRun",
    "ProgramRun",
    "VerificationRun",
    "Answer",
    "HallucinationEvent",
    "EvaluationResult",
]


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Present because spec §26 names it.

    This project has no multi-user story: the research runs as one operator on
    one machine. The table exists so an API deployment has somewhere to put an
    identity, and it is deliberately not wired into any research path - an
    evaluation whose results depended on who was logged in would be a different
    kind of project.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), unique=True)
    sector: Mapped[str | None] = mapped_column(String(120))

    documents: Mapped[list[Document]] = relationship(back_populates="company")


class Document(Base):
    """One source filing.

    `sha256` is unique because it is the identity that matters: two files with
    different names and the same bytes are one document, and the same name with
    different bytes is a different one. Provenance (URL, retrieval date) is
    carried so a reader can obtain the same bytes.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(400))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    fiscal_year: Mapped[str | None] = mapped_column(String(20))
    page_count: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_on: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    company: Mapped[Company | None] = relationship(back_populates="documents")
    pages: Mapped[list[Page]] = relationship(back_populates="document")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "number", name="uq_page"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    number: Mapped[int] = mapped_column(Integer)
    printed_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="pages")


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    title: Mapped[str] = mapped_column(String(400))
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)


class TableRecord(Base):
    """Named `TableRecord` because `Table` is SQLAlchemy's own."""

    __tablename__ = "tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    page: Mapped[int] = mapped_column(Integer)
    table_index: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(120), index=True)
    context_scale: Mapped[str | None] = mapped_column(String(30))
    context_currency: Mapped[str | None] = mapped_column(String(10))
    parsing_accuracy: Mapped[float | None] = mapped_column(Float)


class FinancialFactRow(Base):
    """Module 4's `FinancialFact`, persisted.

    `canonical` is Numeric, never Float. Money that round-trips through a binary
    float acquires errors that look exactly like the small arithmetic mistakes
    this project is trying to detect, and a detector cannot be allowed to trip
    over its own storage layer.

    `year_source` is carried rather than dropped: a figure whose year came from
    the document's fiscal year rather than from the table header is a weaker
    claim, and a query that needs the stronger one must be able to ask.
    """

    __tablename__ = "financial_facts"
    __table_args__ = (
        Index("ix_fact_metric_year", "metric", "year"),
        UniqueConstraint(
            "document_id", "chunk_id", "row_label", "column_index", name="uq_fact_cell"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    metric: Mapped[str] = mapped_column(String(200), index=True)
    amount: Mapped[float] = mapped_column(Numeric(30, 6))
    canonical: Mapped[float] = mapped_column(Numeric(38, 6))
    unit: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str | None] = mapped_column(String(10))
    scale: Mapped[str | None] = mapped_column(String(30))
    year: Mapped[str | None] = mapped_column(String(20))
    year_source: Mapped[str] = mapped_column(String(40), default="unknown")
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(400))
    table_index: Mapped[int | None] = mapped_column(Integer)
    row_label: Mapped[str] = mapped_column(String(400))
    column_label: Mapped[str | None] = mapped_column(String(200))
    column_index: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(120), index=True)
    warnings: Mapped[str | None] = mapped_column(Text)


class Question(Base):
    """A FinVerify-IND question.

    `validation_status` is stored so a query cannot accidentally treat a
    candidate as gold. The application layer refuses the same thing
    (`evaluation.dataset.build_split`); enforcing it in both places means a
    direct SQL user is subject to the same rule as the pipeline.
    """

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    qid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    company: Mapped[str | None] = mapped_column(String(300))
    fiscal_year: Mapped[str | None] = mapped_column(String(20))
    question_type: Mapped[str] = mapped_column(String(40), default="lookup")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    definition: Mapped[str | None] = mapped_column(Text)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    split: Mapped[str] = mapped_column(String(20), default="unassigned", index=True)
    gold_text: Mapped[str | None] = mapped_column(String(120))
    gold_unit: Mapped[str | None] = mapped_column(String(60))
    gold_canonical: Mapped[float | None] = mapped_column(Numeric(38, 6))
    validation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    validator: Mapped[str | None] = mapped_column(String(200))
    validated_on: Mapped[str | None] = mapped_column(String(30))

    evidence: Mapped[list[Evidence]] = relationship(back_populates="question")
    answers: Mapped[list[Answer]] = relationship(back_populates="question")


class Evidence(Base):
    """A gold evidence span. `group_id` preserves the all-of / any-of semantics:
    every group must be covered, any span within a group covers it."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    group_id: Mapped[str] = mapped_column(String(120))
    page: Mapped[int | None] = mapped_column(Integer)
    printed_page: Mapped[int | None] = mapped_column(Integer)
    anchors: Mapped[str] = mapped_column(Text)

    question: Mapped[Question] = relationship(back_populates="evidence")


class Experiment(Base):
    """One campaign run. `source_path` is what makes the row traceable."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source_path: Mapped[str] = mapped_column(Text)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    split: Mapped[str | None] = mapped_column(String(20))
    natural_model: Mapped[str | None] = mapped_column(String(200))
    program_model: Mapped[str | None] = mapped_column(String(200))
    verifier_model: Mapped[str | None] = mapped_column(String(200))
    independence: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    answers: Mapped[list[Answer]] = relationship(back_populates="experiment")


class Answer(Base):
    """One (question, arm) outcome.

    `risk_score` is nullable and must stay so: arms B1-B4 have no detector, and
    a default of 0.5 would enter them into the detection table as though they had
    been measured.
    """

    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("experiment_id", "question_id", "arm", name="uq_answer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    arm: Mapped[str] = mapped_column(String(20), index=True)
    answer_text: Mapped[str | None] = mapped_column(String(200))
    canonical: Mapped[float | None] = mapped_column(Numeric(38, 6))
    answer_source: Mapped[str | None] = mapped_column(String(40))
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    agreed: Mapped[bool | None] = mapped_column(Boolean)
    verdict: Mapped[str | None] = mapped_column(String(20))
    consistency_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    stratum: Mapped[str | None] = mapped_column(String(30), index=True)
    latency_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    # Module 16's evidence-grounded explanation, as the run artifact recorded it.
    # NULL means the run predates RX-045 (the artifact carries no explanation),
    # NOT that the answer was unexplained - the UI has to say which, so this
    # stays nullable rather than defaulting to an empty object.
    explanation: Mapped[dict | None] = mapped_column(JSON)

    experiment: Mapped[Experiment] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="answers")


class ReasoningRun(Base):
    """Channel A's output for one answer."""

    __tablename__ = "reasoning_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    model: Mapped[str | None] = mapped_column(String(200))
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(String(120))
    canonical: Mapped[float | None] = mapped_column(Numeric(38, 6))
    reasoning: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)


class ProgramRun(Base):
    """Channel B's output. `program` is stored so a reader can audit what ran
    inside the sandbox rather than trusting that it was safe."""

    __tablename__ = "program_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    model: Mapped[str | None] = mapped_column(String(200))
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(String(120))
    canonical: Mapped[float | None] = mapped_column(Numeric(38, 6))
    program: Mapped[str | None] = mapped_column(Text)
    execution_status: Mapped[str | None] = mapped_column(String(40))
    validation_violations: Mapped[str | None] = mapped_column(Text)


class VerificationRun(Base):
    """The arbiter, when it was triggered. `triggered=False` rows are kept so
    the trigger RATE is queryable rather than inferred from absence."""

    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(String(40))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[str | None] = mapped_column(String(120))
    reasoning: Mapped[str | None] = mapped_column(Text)


class HallucinationEvent(Base):
    """A labelled error, on both taxonomy axes (Module 14 / D25).

    Two columns rather than one because D12 stratifies by provenance while
    Module 25 analyses by kind, and one enum cannot serve both.
    `label_confidence` is stored because a SUGGESTED label must never be counted
    as a MECHANICAL one in an aggregate.
    """

    __tablename__ = "hallucination_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    provenance: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    label_confidence: Mapped[str] = mapped_column(String(30))
    counts_as_hallucination: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence: Mapped[str | None] = mapped_column(Text)


class EvaluationResult(Base):
    """One metric value from one report.

    Long and thin rather than a wide table with a column per metric: the metric
    set will grow, and a schema migration per new metric is how a reporting
    table stops being updated. `value` is nullable because "undefined" is a real
    outcome - an AUROC on a stratum with no errors is not zero.
    """

    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("experiment_id", "arm", "metric", "stratum", name="uq_result"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"))
    arm: Mapped[str] = mapped_column(String(20), index=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    stratum: Mapped[str] = mapped_column(String(40), default="all")
    value: Mapped[float | None] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    n: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
