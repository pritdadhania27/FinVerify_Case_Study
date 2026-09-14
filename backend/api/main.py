"""FastAPI application (spec Module 20).

Read endpoints serve the database projection. `POST /questions/ask` is the only
one that spends anything, and it is the one that needed the most care.

**Asking a question costs free-tier quota, and the quota is the project's binding
constraint (D14).** An open endpoint that runs the full pipeline is a way to
lose a day of campaign budget to a crawler, a refresh loop, or a demo. So:

- it is **disabled by default** and requires `FINVERIFY_ENABLE_LIVE_QA=1`;
- when disabled it returns 503 with the reason, not a stub answer;
- the response carries how much quota remains, so a caller can see the cost;
- it can never reach the test split, because it does not read the dataset at all
  - it answers free-text questions against the index.

**Uploads do not silently join the corpus.** `POST /documents/upload` registers
a file with its SHA-256 and returns; it does not extract, chunk, embed or index.
Those take minutes to hours on this hardware (a 585-page filing is a real wait),
and a request that appeared to succeed while the document was not actually
searchable would be worse than one that says what remains to be done.

**Nothing here writes to `experiments/runs/`.** The artifacts are the research
record (D4) and an API request is not an experiment. An ad-hoc question answered
through this endpoint is a demonstration, and mixing demonstrations into the run
artifacts would put unlabelled, unplanned rows in the evidence base.

**Live answers ARE stored in the database, and only there (D51).** A question
asked through the dashboard is persisted under run `live-qa` and split `live`
so it can be reopened in the verification view after the user navigates away.
It is never graded, never enters `/stats`, and is not a run artifact - a
database rebuilt from the artifacts simply does not contain it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi import Path as PathParam  # `Path` here is pathlib's
from sqlalchemy import func, select, true
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.api.schemas import (
    AnswerOut,
    AnswerSummaryOut,
    ArmOut,
    AskRequest,
    ChannelOut,
    CorpusStatsOut,
    DocumentOut,
    EvaluationResultOut,
    EvidenceOut,
    ExperimentOut,
    HealthOut,
    VerificationOut,
)
from backend.database.models import (
    Answer,
    Company,
    Document,
    EvaluationResult,
    Evidence,
    Experiment,
    FinancialFactRow,
    Page,
    ProgramRun,
    Question,
    ReasoningRun,
    Section,
    TableRecord,
    VerificationRun,
)
from backend.database.session import build_engine, database_url, session_scope
from backend.verification.confidence import band_for

__all__ = ["app", "create_app"]

def build_ref() -> str:
    """Which build of this code is running.

    Baked into the image at build time. `/health` reports on the database and
    the vector index - both external - and can be entirely green while the
    process itself runs code from a fortnight ago. This is the one field that
    describes the *server*, and it is why a stale container can now say so.
    """
    return os.environ.get("FINVERIFY_BUILD_REF", "unknown")


def upload_dir() -> Path:
    """Where an uploaded PDF is stored.

    Read per call, not at import: a module-level `os.environ.get` is evaluated
    once when the module is first imported, which in the test suite is before
    any fixture has had a chance to redirect it. That is how the API's own tests
    came to write `test.pdf` and `a.pdf` into the real corpus directory beside
    five genuine annual reports.
    """
    return Path(os.environ.get("FINVERIFY_UPLOAD_DIR", "documents/raw"))

LIVE_QA_DISABLED = (
    "Live question answering is disabled. Each request runs both reasoning "
    "channels and can trigger the arbiter, spending free-tier quota that the "
    "evaluation campaign depends on (D14). Set FINVERIFY_ENABLE_LIVE_QA=1 to "
    "enable it deliberately."
)

LIVE_QA_ENGINE_MISSING = (
    "Live question answering needs the research engine - LangGraph, the "
    "sentence-transformers embedding model, the LLM client and Docker for the "
    "program sandbox - and this API process does not have it ({reason}). The API "
    "container image is deliberately web-only. Run the API from the project's "
    "virtual environment instead: RUN_LIVE_DEMO.bat starts it with live QA enabled "
    "and points the dashboard at it."
)

# Live answers are demonstrations, not experiments (D51). Stored so a result
# survives navigating away, under a run id and split that no research count reads.
LIVE_RUN_ID = "live-qa"
LIVE_SPLIT = "live"

log = logging.getLogger("finverify.api")


@lru_cache(maxsize=1)
def _live_pipeline_parts() -> dict:
    """The expensive, request-independent half of a live question, built once.

    `ask` constructed the embedding model, the vector-index client, the retriever
    and three LLM clients on EVERY request. The first live question measured 365 s
    of wall time against 167 s inside the pipeline - the difference was loading,
    and it was past nginx's proxy timeout, so a question asked from the dashboard
    would have done all of its work and then failed with a 504. It also discarded
    the retriever's BM25 cache between questions.
    """
    from backend.rag.embedding import Embedder
    from backend.rag.indexing import QdrantIndex
    from backend.retrieval.hybrid import HybridRetriever
    from backend.services.llm.registry import build_provider, resolve_channel

    model_name = os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2")
    embedder = Embedder(model_name, device=os.environ.get("EMBEDDING_DEVICE", "cpu"))
    index = QdrantIndex(
        # QDRANT_URL, for the same reason /health spells out: inside a container
        # "localhost" is the container itself.
        url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        collection=os.environ.get("QDRANT_COLLECTION", "finverify_e5"),
        dimension=embedder.dimension,
    )
    channels = {
        name: resolve_channel(name)
        for name in ("natural_channel", "program_channel", "verification_agent")
    }
    return {
        "model_name": model_name,
        "index": index,
        "retriever": HybridRetriever(index=index, embedder=embedder),
        "channels": channels,
        "providers": {name: build_provider(b.provider) for name, b in channels.items()},
    }


def _warm_live_pipeline() -> None:
    """Load the embedding model at startup, not inside the first user's question."""
    try:
        from backend.agents.orchestrator import PipelineDeps  # noqa: F401 - import cost only

        _live_pipeline_parts()
        log.info("live QA pipeline loaded")
    except Exception as exc:  # noqa: BLE001 - the request path reports it properly
        log.warning("live QA warm-up failed: %s: %s", type(exc).__name__, exc)


def _document_of_chunk(chunk_id: str | None) -> str | None:
    """The filing a retrieved chunk came from; chunk ids are `<document_id>:p<page>:...`."""
    if not chunk_id or ":" not in chunk_id:
        return None
    return chunk_id.split(":", 1)[0]


def _document_labels(session: Session, document_ids: set[str]) -> dict[str, str]:
    """document_id -> the company that filed it, or the filename if no company is known.

    A page number alone does not identify evidence in a five-filing corpus, and
    spec section 24 asks the response to name its source document.
    """
    if not document_ids:
        return {}
    rows = session.execute(
        select(Document.document_id, Company.name, Document.filename)
        .outerjoin(Company, Company.id == Document.company_id)
        .where(Document.document_id.in_(document_ids))
    ).all()
    return {doc: (company or filename) for doc, company, filename in rows}


def _company_of_document(session: Session, document_id: str) -> str | None:
    return session.execute(
        select(Company.name)
        .join(Document, Document.company_id == Company.id)
        .where(Document.document_id == document_id)
    ).scalar_one_or_none()


def _persist_live_answer(
    session: Session,
    *,
    question: str,
    company: str | None,
    document_id: str | None,
    record: dict,
    models: dict[str, str | None],
) -> tuple[int | None, str]:
    """Store one live answer where the verification view can open it (D51).

    Through `ingest_run`, the code that loads campaign rows, so a live answer and
    a recorded one cannot diverge in shape. A fresh question id per ask: ingest
    upserts by (run, question, arm) and keeps the FIRST row's answer fields, so
    reusing an id for a repeated question would show a stale answer beside new
    channel runs. `correct` stays NULL - a demonstration is not graded.
    """
    from backend.database.ingest import ingest_run

    qid = f"LIVE-{uuid.uuid4().hex[:12]}"
    document = (
        session.execute(select(Document).filter_by(document_id=document_id)).scalar_one_or_none()
        if document_id
        else None
    )
    row = Question(
        qid=qid,
        text=question,
        company=company,
        document_id=document.id if document is not None else None,
        split=LIVE_SPLIT,
        validation_status=LIVE_SPLIT,
    )
    session.add(row)
    session.flush()
    ingest_run(
        session,
        LIVE_RUN_ID,
        [{**record, "question_id": qid}],
        config={
            "split": LIVE_SPLIT,
            **models,
            "independence": "live demonstration - not an experiment",
        },
    )
    session.flush()
    answer_id = session.execute(
        select(Answer.id).where(Answer.question_id == row.id)
    ).scalar_one_or_none()
    return answer_id, qid


def live_qa_enabled() -> bool:
    return os.environ.get("FINVERIFY_ENABLE_LIVE_QA") == "1"


# Module level, not nested inside create_app, so a test can substitute it with
# `app.dependency_overrides[get_session] = ...`. A dependency defined inside the
# factory has no importable identity, and overriding it means walking the route
# table looking for a function by name - which works until it silently does not.
_ENGINE: dict[str, object] = {"engine": None}


def get_engine():
    if _ENGINE["engine"] is None:
        _ENGINE["engine"] = build_engine(database_url())
    return _ENGINE["engine"]


def get_session():
    with session_scope(get_engine()) as session:
        yield session


Db = Annotated[Session, Depends(get_session)]

# `answers.id` is a Postgres int4. An id past its range never reaches a row - it
# reaches psycopg, which raises NumericValueOutOfRange, which surfaced as a 500.
# The verification screen has a free-text "look one up by id" box, so a typo
# turned into a server error. Bounding the parameter at the column's own limit
# makes an impossible id a 422 with a reason, which is what it actually is.
PG_INT4_MAX = 2_147_483_647
AnswerId = Annotated[int, PathParam(ge=1, le=PG_INT4_MAX)]


def arm_out(name: str):
    """One arm's configuration, or None for a name no arm defines.

    Read from `evaluation.arms` rather than restated here: the arm table is the
    ablation's definition, and a second copy of it in the API layer would be a
    second thing to keep true. `removes` is computed by diffing against arm A,
    which is what "arm A minus exactly one field" means operationally - so the
    label cannot drift from the flags beside it.
    """
    from evaluation.arms import ALL_ARMS

    config = ALL_ARMS.get(name)
    if config is None:
        return None

    baseline = ALL_ARMS.get("A")
    differences: list[str] = []
    if baseline is not None and name != "A":
        for field, label in (
            ("use_natural", "the natural-language channel"),
            ("use_program", "the executed-program channel"),
            ("use_deterministic", "the deterministic verifier"),
            ("use_consistency", "the consistency engine"),
            ("use_verification_agent", "the arbiter"),
        ):
            if getattr(baseline, field) and not getattr(config, field):
                differences.append(label)
        if config.retrieval != baseline.retrieval:
            differences.append(f"hybrid retrieval ({config.retrieval} only)")
        if config.same_model_both_channels and not baseline.same_model_both_channels:
            differences.append("cross-model channels (one model on both)")

    return ArmOut(
        name=config.name,
        description=config.description,
        uses_natural=config.use_natural,
        uses_program=config.use_program,
        uses_deterministic=config.use_deterministic,
        uses_consistency=config.use_consistency,
        uses_arbiter=config.use_verification_agent,
        same_model_both_channels=config.same_model_both_channels,
        retrieval=config.retrieval,
        self_consistency_samples=config.self_consistency_samples or None,
        removes=", ".join(differences) or None,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinVerify-AI",
        version="0.1.0",
        description=(
            "Dual-channel numerical QA over financial reports, with consistency "
            "verification. This API is a thin layer over the research engine: "
            "read endpoints serve a projection of the run artifacts, which "
            "remain the source of truth (decision D4)."
        ),
    )

    if live_qa_enabled():
        # In the background, so startup and /health are not held up by a model
        # load - but finished, in practice, before anyone has typed a question.
        threading.Thread(target=_warm_live_pipeline, name="live-qa-warmup", daemon=True).start()

    @app.exception_handler(OperationalError)
    async def database_unreachable(_request: Request, exc: OperationalError):
        # A stopped database surfaced as a bare 500 - and before the connect
        # timeout, as a request that never returned, which left every screen on
        # "Loading" with no error at all. The service is fine; one of its
        # dependencies is not, and that is a 503 with the reason.
        log.warning("database unreachable: %s", exc.orig if exc.orig is not None else exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": (
                    "The database is unreachable, so this data cannot be read right "
                    "now. Check that the finverify-postgres container is running "
                    "(docker compose up -d)."
                )
            },
        )

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health() -> HealthOut:
        """Liveness plus the two facts a caller actually needs.

        Reports what is REACHABLE rather than what is configured. A healthy
        response with an unreachable database would be the same mistake that
        cost an afternoon on this project: `docker compose ps` reported Postgres
        healthy while every connection from the host reached a different server.
        """
        database_ok = False
        try:
            engine = build_engine(database_url())
            with session_scope(engine) as session:
                session.execute(select(func.count()).select_from(Document))
            database_ok = True
        except Exception:  # noqa: BLE001 - health must report, never raise
            database_ok = False

        index_ok = False
        index_problem: str | None = None
        try:
            from backend.rag.indexing import QdrantIndex
            from backend.rag.manifest import IndexConfigurationError, preflight

            # QDRANT_URL, not the class default of localhost: inside a container
            # "localhost" is the container itself, and the check would report a
            # perfectly healthy index as unreachable.
            probe = QdrantIndex(
                url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
                collection=os.environ.get("QDRANT_COLLECTION", "finverify_e5"),
                dimension=768,
            )
            # A reachable, non-empty index is not a usable one. This endpoint
            # reported `vector_index: true` for a full day while the configured
            # collection held one company's chunks embedded by a different
            # model - true, and useless. Health now means "can answer", not
            # "responds".
            try:
                preflight(
                    probe,
                    embedding_model=os.environ.get(
                        "EMBEDDING_MODEL", "intfloat/e5-base-v2"
                    ),
                )
                index_ok = True
            except IndexConfigurationError as exc:
                index_problem = str(exc).splitlines()[0]
        except Exception as exc:  # noqa: BLE001 - health must report, never raise
            # WHY it failed, not just that it did. An earlier version swallowed
            # every exception into `False`, so a missing Python dependency in
            # the image was reported as "the vector index is unreachable" - a
            # deployment problem misattributed to a service that was fine.
            index_problem = f"{type(exc).__name__}: {exc}"

        note = (
            "measured, not declared: each field was checked by making the call, "
            "not by reading an environment variable. vector_index additionally "
            "requires that the collection was built by the embedding model now "
            "configured to query it - it reported true for a full day while "
            "those disagreed, which is reachable but useless"
        )
        if index_problem:
            note = f"{note}. Vector index check failed with {index_problem}"

        return HealthOut(
            status="ok",
            database=database_ok,
            vector_index=index_ok,
            live_questions_enabled=live_qa_enabled(),
            build_ref=build_ref(),
            note=note,
        )

    @app.get("/stats", response_model=CorpusStatsOut, tags=["meta"])
    def corpus_stats(session: Db):
        """Counts of what has been ingested and measured.

        Counted at request time rather than cached. These are cheap aggregate
        queries, and a cached corpus figure that survives a re-ingest is exactly
        the kind of confidently-stale number this project keeps finding in its
        own documentation - the database is the only thing that knows.
        """

        def count(model) -> int:
            return session.execute(select(func.count()).select_from(model)).scalar_one()

        def questions_where(status: str) -> int:
            return session.execute(
                select(func.count())
                .select_from(Question)
                .where(Question.validation_status == status)
            ).scalar_one()

        # Live demonstrations (D51) are excluded from every figure below. They
        # are real system outputs, but a dashboard reporting "193 questions" after
        # one demo would be counting a user's typing as benchmark growth.
        live_run = session.execute(
            select(Experiment.id).where(Experiment.run_id == LIVE_RUN_ID)
        ).scalar_one_or_none()
        not_live = Answer.experiment_id != live_run if live_run is not None else true()

        arms = session.execute(
            select(Answer.arm).where(not_live).distinct().order_by(Answer.arm)
        )
        return CorpusStatsOut(
            documents=count(Document),
            pages=count(Page),
            sections=count(Section),
            tables=count(TableRecord),
            financial_facts=count(FinancialFactRow),
            questions_total=session.execute(
                select(func.count()).select_from(Question).where(Question.split != LIVE_SPLIT)
            ).scalar_one(),
            questions_validated=questions_where("validated"),
            questions_rejected=questions_where("rejected"),
            questions_pending=questions_where("pending"),
            evidence_spans=count(Evidence),
            runs=session.execute(
                select(func.count())
                .select_from(Experiment)
                .where(Experiment.run_id != LIVE_RUN_ID)
            ).scalar_one(),
            answers=session.execute(
                select(func.count()).select_from(Answer).where(not_live)
            ).scalar_one(),
            # An answer with `correct` still NULL has been produced but not
            # graded. Counting it as measured would report a campaign as further
            # along than it is, which is the number a reader most wants to trust.
            answers_graded=session.execute(
                select(func.count())
                .select_from(Answer)
                .where(Answer.correct.isnot(None), not_live)
            ).scalar_one(),
            arms=[row[0] for row in arms],
        )

    # ---------------------------------------------------------------- documents

    @app.get("/documents", response_model=list[DocumentOut], tags=["documents"])
    def list_documents(session: Db, limit: int = Query(default=50, ge=1, le=500)):
        rows = session.execute(
            select(Document, Company.name)
            .outerjoin(Company, Company.id == Document.company_id)
            # A LIMIT with no ORDER BY lets the database return any `limit` rows
            # it likes, and it is free to choose differently on the next call.
            # At the default of 50 that silently changes which filings "the
            # registered corpus" contains between two reloads.
            .order_by(Document.document_id)
            .limit(limit)
        ).all()
        return [
            DocumentOut(
                document_id=doc.document_id,
                filename=doc.filename,
                company=company,
                fiscal_year=doc.fiscal_year,
                page_count=doc.page_count,
                sha256=doc.sha256,
                source_url=doc.source_url,
                retrieved_on=doc.retrieved_on,
            )
            for doc, company in rows
        ]

    @app.get("/documents/{document_id}", response_model=DocumentOut, tags=["documents"])
    def get_document(document_id: str, session: Db):
        row = session.execute(
            select(Document, Company.name)
            .outerjoin(Company, Company.id == Document.company_id)
            .where(Document.document_id == document_id)
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no document {document_id!r}")
        doc, company = row
        return DocumentOut(
            document_id=doc.document_id,
            filename=doc.filename,
            company=company,
            fiscal_year=doc.fiscal_year,
            page_count=doc.page_count,
            sha256=doc.sha256,
            source_url=doc.source_url,
            retrieved_on=doc.retrieved_on,
        )

    @app.post("/documents/upload", tags=["documents"], status_code=status.HTTP_202_ACCEPTED)
    async def upload_document(file: UploadFile, session: Db):
        """Register a PDF. Deliberately 202, not 201.

        The file is stored and hashed; it is NOT extracted, chunked, embedded or
        indexed. Those take minutes to hours on this hardware, and returning 201
        would tell a client the document is searchable when it is not. The
        response says what still has to be run.
        """
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF files are accepted"
            )
        payload = await file.read()
        if not payload:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")

        digest = hashlib.sha256(payload).hexdigest()
        existing = session.execute(
            select(Document).where(Document.sha256 == digest)
        ).scalar_one_or_none()
        if existing is not None:
            # Identity is the bytes, not the name. Two uploads of the same file
            # under different names are one document.
            return {
                "document_id": existing.document_id,
                "sha256": digest,
                "duplicate": True,
                "indexed": False,
                "next_step": "already registered; check whether it has been indexed",
            }

        document_id = digest[:16]
        directory = upload_dir()
        # A deployment problem must not surface as a bare 500. The upload
        # directory defaulted into the corpus mount, which compose mounts
        # read-only on purpose, so every upload died on an OSError with no
        # indication of why - the button simply failed. Reported as 507 with the
        # path, because the caller cannot fix it and the operator can.
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                f"The upload directory {directory} is not writable ({exc.strerror}). "
                "Set FINVERIFY_UPLOAD_DIR to a writable location; the corpus "
                "mount is deliberately read-only.",
            ) from exc
        # Prefixed with the content hash. Two different files may legitimately
        # share a name - `annual-report.pdf` is what half these companies call
        # their filing - and the bytes are already known to differ, because an
        # identical sha256 returned above. Writing to the bare name would
        # overwrite the earlier document while the registry still claims its
        # original hash, which severs provenance from the file it describes.
        destination = directory / f"{document_id}_{Path(file.filename).name}"
        try:
            destination.write_bytes(payload)
        except OSError as exc:
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                f"Could not write the upload to {destination} ({exc.strerror}).",
            ) from exc
        session.add(
            Document(
                document_id=document_id,
                sha256=digest,
                filename=destination.name,
                fiscal_year=None,
            )
        )
        return {
            "document_id": document_id,
            "sha256": digest,
            "duplicate": False,
            "indexed": False,
            "next_step": (
                "run scripts/index_corpus.py to extract, chunk, embed and index. "
                "Until then this document is registered but not searchable."
            ),
        }

    # ---------------------------------------------------------------- questions

    @app.post("/questions/ask", response_model=AnswerOut, tags=["questions"])
    def ask(request: AskRequest, session: Db):
        """Run one question through the pipeline. Spends free-tier quota."""
        if not live_qa_enabled():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LIVE_QA_DISABLED)

        from backend.verification.explanation import explain
        from evaluation.arms import ALL_ARMS

        config = ALL_ARMS.get(request.arm)
        if config is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown arm {request.arm!r}; known: {sorted(ALL_ARMS)}",
            )

        try:
            from backend.agents.orchestrator import PipelineDeps, run_question

            parts = _live_pipeline_parts()
        except ImportError as exc:
            # The web-only API container lands here. This was an unhandled
            # ModuleNotFoundError - a bare 500 with no reason, on the one screen
            # whose purpose is the live pipeline, and invisible for as long as
            # live QA stayed switched off.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                LIVE_QA_ENGINE_MISSING.format(reason=str(exc)),
            ) from exc

        from backend.rag.manifest import IndexConfigurationError, preflight

        try:
            preflight(parts["index"], embedding_model=parts["model_name"])
        except IndexConfigurationError as exc:
            # 503, not 500: the service is fine, its retrieval configuration is
            # not, and the operator needs the specific mismatch to fix it.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - an unreachable index is a 503 with its cause
            # A stopped vector index raises a connection error here, which is not
            # an IndexConfigurationError - so asking returned a bare 500, and did
            # so before any quota was spent, which is the moment to say why.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"The vector index is unreachable ({type(exc).__name__}), so no "
                "evidence can be retrieved. Check that the finverify-qdrant "
                "container is running.",
            ) from exc

        company = request.company
        if request.document_id and not company:
            # Retrieval is scoped by company (RX-012). A client naming only the
            # filing must not silently fall through to all filings at once.
            company = _company_of_document(session, request.document_id)

        channels = parts["channels"]
        providers = parts["providers"]
        deps = PipelineDeps(
            retriever=parts["retriever"],
            natural_provider=providers["natural_channel"],
            program_provider=providers["program_channel"],
            verifier_provider=providers["verification_agent"],
            natural_model=channels["natural_channel"].model,
            program_model=channels["program_channel"].model,
            verifier_model=channels["verification_agent"].model,
            document_id=request.document_id,
            company=company,
        )

        # `replace` rather than mutation: ArmConfig is frozen so the catalogue
        # entry cannot be altered by a request. A client changing top_k must not
        # change what the next caller - or the campaign - runs.
        result = run_question(
            request.question,
            config=replace(config, top_k=request.top_k),
            deps=deps,
        )
        explanation = explain(result)

        answer_id: int | None = None
        question_id: str | None = None
        saved_note = ""
        try:
            answer_id, question_id = _persist_live_answer(
                session,
                question=request.question,
                company=company,
                document_id=request.document_id,
                record=result.as_record(),
                models={
                    "natural_model": channels["natural_channel"].model,
                    "program_model": channels["program_channel"].model,
                    "verifier_model": channels["verification_agent"].model,
                },
            )
        except Exception as exc:  # noqa: BLE001 - never lose an answer already paid for
            # The quota is spent and the answer exists; failing the request now
            # would throw it away. It is returned, and says it was not saved.
            log.exception("live answer could not be persisted")
            session.rollback()
            saved_note = (
                f" The answer was NOT saved ({type(exc).__name__}), so it cannot be "
                "reopened from the verification view."
            )

        try:
            labels = _document_labels(
                session,
                {d for d in (_document_of_chunk(b.chunk_id) for b in result.blocks) if d},
            )
        except Exception:  # noqa: BLE001 - labels are presentation; the answer stands
            session.rollback()
            labels = {}

        return AnswerOut(
            question=request.question,
            arm=config.name,
            answer_id=answer_id,
            question_id=question_id,
            answer=str(result.answer.amount) if result.answer is not None else None,
            answer_text=str(result.answer) if result.answer is not None else None,
            canonical=(
                str(result.answer.canonical()) if result.answer is not None else None
            ),
            answer_source=result.answer_source,
            abstained=result.abstained,
            verdict=result.report.verdict.name if result.report else None,
            agreed=result.agreed,
            risk_score=result.risk_score,
            band=explanation.band,
            calibrated=bool(result.risk.calibrated) if result.risk else False,
            arm_config=arm_out(config.name),
            channels=[
                ChannelOut(
                    name=name,
                    available=source.answer.available,
                    applicable=source.answer.applicable,
                    value=(
                        str(source.answer.value.amount)
                        if source.answer.value is not None
                        else None
                    ),
                    canonical=(
                        str(source.answer.canonical)
                        if source.answer.canonical is not None
                        else None
                    ),
                    failure_reason=source.answer.failure_reason,
                )
                for name, source in (
                    ("natural", result.natural),
                    ("program", result.program),
                    ("deterministic", result.deterministic),
                )
                if source is not None
            ],
            evidence=[
                EvidenceOut(
                    ref=block.ref,
                    citation=block.citation,
                    page=block.page,
                    chunk_id=block.chunk_id,
                    document_id=_document_of_chunk(block.chunk_id),
                    document=labels.get(_document_of_chunk(block.chunk_id) or ""),
                )
                for block in result.blocks
            ],
            verification=(
                VerificationOut(
                    triggered=True,
                    available=result.verification.available,
                    resolution=result.verification.resolution.value,
                    resolved=result.verification.resolved,
                    value=(
                        str(result.verification.value.amount)
                        if result.verification.value is not None
                        else None
                    ),
                    reasoning=result.verification.reasoning,
                )
                if result.verification is not None
                else VerificationOut(triggered=False)
            ),
            explanation=explanation.as_dict(),
            latency_seconds=round(result.latency_seconds, 3),
            quota_note=(
                "This request consumed free-tier LLM quota; see "
                "scripts/verify_llm_providers.py --quota." + saved_note
            ),
        )

    @app.get("/arms", response_model=list[ArmOut], tags=["meta"])
    def list_arms():
        """Every configuration the campaign can run, and what each one removes.

        The ablation is the research design, and until now it existed only in
        `evaluation/arms.py` and the write-up - a reader of the UI saw arm names
        with no way to learn what "arm C" is. Served from the same table the
        campaign runs from, so the two cannot disagree.
        """
        from evaluation.arms import ALL_ARMS

        # Distinct by the config's own name, not by key: "P" is EVALUATION.md
        # §7's name for the proposed system and "A" is §8's name for the same
        # arm, so keying the listing would show one configuration twice under
        # two labels and read as fifteen arms where there are fourteen.
        seen: set[str] = set()
        described = []
        for key in sorted(ALL_ARMS):
            out = arm_out(key)
            if out is not None and out.name not in seen:
                seen.add(out.name)
                described.append(out)
        return described

    # ------------------------------------------------------------------ answers

    @app.get("/answers", response_model=list[AnswerSummaryOut], tags=["answers"])
    def list_answers(
        session: Db,
        run_id: str | None = None,
        arm: str | None = None,
        # The contrast this whole project is built on is "the same question,
        # one field changed". Without this filter the only way to see what
        # every arm answered for one question was to pull 1,646 rows and sift
        # them client-side, so the comparison the research is about was the one
        # thing the UI could not show.
        question_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        """Recorded answers, newest run first, so one can be picked rather than
        guessed at.

        Ordered by descending id: the most recent rows are the ones an operator
        is looking for after a run, and a campaign appends.
        """
        query = (
            select(Answer, Experiment.run_id, Question.qid, Question.text)
            .join(Experiment, Experiment.id == Answer.experiment_id)
            .join(Question, Question.id == Answer.question_id)
            .order_by(Answer.id.desc())
            .limit(limit)
        )
        if run_id:
            query = query.where(Experiment.run_id == run_id)
        if arm:
            query = query.where(Answer.arm == arm)
        if question_id:
            query = query.where(Question.qid == question_id)
        return [
            AnswerSummaryOut(
                answer_id=answer.id,
                run_id=run,
                arm=answer.arm,
                question_id=qid,
                question=text,
                answer_text=answer.answer_text,
                abstained=answer.abstained,
                verdict=answer.verdict,
                agreed=answer.agreed,
                risk_score=answer.risk_score,
                band=band_for(answer.risk_score),
                correct=answer.correct,
                latency_seconds=answer.latency_seconds,
            )
            for answer, run, qid, text in session.execute(query).all()
        ]

    @app.get("/answers/{answer_id}", response_model=AnswerOut, tags=["answers"])
    def get_answer(answer_id: AnswerId, session: Db):
        answer = session.get(Answer, answer_id)
        if answer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no answer {answer_id}")
        question = session.get(Question, answer.question_id)
        verification = session.execute(
            select(VerificationRun).where(VerificationRun.answer_id == answer.id)
        ).scalar_one_or_none()

        # Both channels and the gold evidence. `AnswerOut` has carried these two
        # fields since the schema was written and this endpoint never filled
        # them, so it returned `channels: []` and `evidence: []` for every
        # answer while the rows sat in `reasoning_runs`, `program_runs` and
        # `evidence` - a contract promising the two things this project is about
        # and delivering neither. Found by querying the deployed stack; the unit
        # tests passed throughout because they asserted only on the fields the
        # endpoint did populate.
        reasoning = session.execute(
            select(ReasoningRun).where(ReasoningRun.answer_id == answer.id)
        ).scalar_one_or_none()
        program = session.execute(
            select(ProgramRun).where(ProgramRun.answer_id == answer.id)
        ).scalar_one_or_none()

        channels: list[ChannelOut] = []
        for name, run in (("natural", reasoning), ("program", program)):
            if run is None:
                # The arm has no such channel - B1/B2 have no program channel,
                # B3 no natural one. That is not the same as a channel that ran
                # and failed, so it is absent rather than listed unavailable.
                continue
            channels.append(
                ChannelOut(
                    name=name,
                    available=bool(run.available),
                    applicable=True,
                    value=run.value,
                    canonical=str(run.canonical) if run.canonical is not None else None,
                    failure_reason=run.failure_reason,
                )
            )

        spans = (
            session.execute(
                select(Evidence).where(Evidence.question_id == question.id)
            ).scalars().all()
            if question is not None
            else []
        )
        document = (
            session.get(Document, question.document_id)
            if question is not None and question.document_id
            else None
        )

        return AnswerOut(
            question=question.text if question else "",
            arm=answer.arm,
            answer_id=answer.id,
            question_id=question.qid if question is not None else None,
            answer=answer.answer_text,
            answer_text=answer.answer_text,
            canonical=str(answer.canonical) if answer.canonical is not None else None,
            answer_source=answer.answer_source,
            abstained=bool(answer.abstained),
            verdict=answer.verdict,
            agreed=answer.agreed,
            arm_config=arm_out(answer.arm),
            # As the artifact recorded it. NULL for runs predating RX-045, which
            # the UI reports as "this run recorded none" rather than as an empty
            # explanation - those are different statements.
            explanation=answer.explanation,
            risk_score=answer.risk_score,
            # Derived from the stored score by the same function the live path
            # uses. Left unset, this returned the schema default UNSCORED for
            # every recorded answer - including ones carrying a score - which
            # reads as "no detector ran" exactly where one did.
            band=band_for(answer.risk_score),
            latency_seconds=answer.latency_seconds,
            channels=channels,
            evidence=[
                EvidenceOut(
                    ref=span.group_id,
                    citation=f"p.{span.printed_page or span.page}",
                    page=span.page,
                    text=span.anchors,
                    document_id=document.document_id if document is not None else None,
                    document=(question.company if question is not None else None)
                    or (document.filename if document is not None else None),
                )
                for span in spans
            ],
            verification=(
                VerificationOut(
                    triggered=bool(verification.triggered),
                    available=bool(verification.available),
                    resolution=verification.resolution,
                    resolved=bool(verification.resolved),
                    value=verification.value,
                )
                if verification is not None
                else None
            ),
        )

    @app.get("/verification/{answer_id}", response_model=VerificationOut, tags=["answers"])
    def get_verification(answer_id: AnswerId, session: Db):
        verification = session.execute(
            select(VerificationRun).where(VerificationRun.answer_id == answer_id)
        ).scalar_one_or_none()
        if verification is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no verification record for answer {answer_id}"
            )
        return VerificationOut(
            triggered=bool(verification.triggered),
            available=bool(verification.available),
            resolution=verification.resolution,
            resolved=bool(verification.resolved),
            value=verification.value,
            reasoning=verification.reasoning,
        )

    @app.get("/evidence/{question_id}", response_model=list[EvidenceOut], tags=["answers"])
    def get_evidence(question_id: str, session: Db):
        """Gold evidence spans for a question, by qid.

        These are the spans a human validated, not what retrieval returned. The
        distinction matters: this endpoint answers "where should the answer come
        from", which is what a reader checking a figure actually wants.
        """
        question = session.execute(
            select(Question).where(Question.qid == question_id)
        ).scalar_one_or_none()
        if question is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no question {question_id!r}")
        # Ordered by page: these render as a citation list, and an unordered
        # query is free to hand back "p.84, p.67" on one call and the reverse on
        # the next. A citation list whose order is not stable is not a citation
        # list a reader can check a figure against.
        spans = session.execute(
            select(Evidence)
            .where(Evidence.question_id == question.id)
            .order_by(Evidence.page, Evidence.id)
        ).scalars().all()
        return [
            EvidenceOut(
                ref=span.group_id,
                citation=f"p.{span.printed_page or span.page}",
                page=span.page,
                text=span.anchors,
            )
            for span in spans
        ]

    # -------------------------------------------------------------- experiments

    @app.get("/experiments", response_model=list[ExperimentOut], tags=["research"])
    def list_experiments(session: Db):
        # Descending run_id: grouped by the id's prefix, and within a prefix
        # newest first, since the rest of the id is a UTC timestamp. NOT
        # chronological across prefixes - `slice_` sorts above `campaign_`
        # whatever the dates - and deliberately not ordered by `started_at`,
        # which records when a run was INGESTED, so re-running
        # `ingest_to_database.py --all` would reshuffle the list. The id is the
        # only key here that is stable under a re-ingest.
        #
        # Any total order beats none: two screens build a 42-option picker from
        # this list, and in database order it had no defined order at all.
        #
        # Two counts, as correlated subqueries rather than two outer joins: joining
        # answers and metric rows together multiplies one count by the other.
        answers = (
            select(func.count(Answer.id))
            .where(Answer.experiment_id == Experiment.id)
            .correlate(Experiment)
            .scalar_subquery()
        )
        metrics = (
            select(func.count(EvaluationResult.id))
            .where(EvaluationResult.experiment_id == Experiment.id)
            .correlate(Experiment)
            .scalar_subquery()
        )
        rows = session.execute(
            select(Experiment, answers, metrics).order_by(Experiment.run_id.desc())
        ).all()
        return [
            ExperimentOut(
                run_id=experiment.run_id,
                split=experiment.split,
                natural_model=experiment.natural_model,
                program_model=experiment.program_model,
                verifier_model=experiment.verifier_model,
                independence=experiment.independence,
                answers=answer_count,
                results=metric_count,
                source_path=experiment.source_path,
            )
            for experiment, answer_count, metric_count in rows
        ]

    @app.get(
        "/evaluation/results",
        response_model=list[EvaluationResultOut],
        tags=["research"],
    )
    def evaluation_results(
        session: Db,
        run_id: str | None = None,
        arm: str | None = None,
        metric: str | None = None,
    ):
        """Metric values from ingested reports.

        A NULL `value` means the metric was UNDEFINED for that arm and stratum -
        an AUROC on a stratum with no errors - and is deliberately different from
        zero. Clients must not coalesce it.
        """
        # Ordered so the metric table is reproducible. Clients group these rows
        # by arm and render them in receipt order, so without this the same
        # selection lists its metrics differently on two consecutive loads - and
        # a table of figures that reshuffles is a table nobody can diff against a
        # published one.
        query = (
            select(EvaluationResult, Experiment.run_id)
            .join(Experiment, Experiment.id == EvaluationResult.experiment_id)
            .order_by(
                Experiment.run_id,
                EvaluationResult.arm,
                EvaluationResult.metric,
                EvaluationResult.stratum,
            )
        )
        if run_id:
            query = query.where(Experiment.run_id == run_id)
        if arm:
            query = query.where(EvaluationResult.arm == arm)
        if metric:
            query = query.where(EvaluationResult.metric == metric)
        return [
            EvaluationResultOut(
                run_id=run,
                arm=row.arm,
                metric=row.metric,
                stratum=row.stratum,
                value=row.value,
                ci_low=row.ci_low,
                ci_high=row.ci_high,
                n=row.n,
                note=row.note,
            )
            for row, run in session.execute(query).all()
        ]

    return app


app = create_app()
