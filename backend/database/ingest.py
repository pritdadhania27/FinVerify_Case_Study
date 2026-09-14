"""Load run artifacts and the gold dataset into PostgreSQL (spec Module 18).

The direction is one-way and always will be: files -> database. Nothing here
writes back to `experiments/runs/`, and nothing in the research path reads from
the database. That keeps D4's guarantee intact - a number in the paper traces to
a committed file - while making the awkward questions cheap to ask:

    -- every question where the channels agreed and both were wrong
    SELECT q.qid, a.arm, a.answer_text, q.gold_text
    FROM answers a JOIN questions q ON q.id = a.question_id
    WHERE a.agreed AND a.correct IS FALSE;

**Idempotent on natural keys.** An ingest that is half-finished when a laptop
sleeps must be safe to repeat, so every upsert is keyed on something the artifact
itself determines: `document_id`, `qid`, `run_id`, and `(experiment, question,
arm)`. Re-running updates rather than duplicating, and the row count after two
ingests equals the row count after one.

**Grading is not re-done here.** `correct` and `stratum` are copied from the
analysis, not recomputed, so the database cannot disagree with the report about
which answers were wrong. One grader, one answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import (
    Answer,
    Company,
    Document,
    Evidence,
    EvaluationResult,
    Experiment,
    FinancialFactRow,
    HallucinationEvent,
    Page,
    ProgramRun,
    Question,
    ReasoningRun,
    Section,
    TableRecord,
    VerificationRun,
)
from evaluation.dataset import Dataset

__all__ = [
    "ingest_registry",
    "ingest_structure",
    "ingest_dataset",
    "ingest_facts",
    "ingest_run",
    "ingest_report",
]


def _get_or_create(session: Session, model, defaults: dict | None = None, **keys):
    instance = session.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if instance is None:
        instance = model(**keys, **(defaults or {}))
        session.add(instance)
        session.flush()
    elif defaults:
        for key, value in defaults.items():
            setattr(instance, key, value)
    return instance


def ingest_registry(session: Session, registry_path: Path | str) -> int:
    """Documents and companies from `documents/registry.json`."""
    payload = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    records = list(documents.values()) if isinstance(documents, dict) else list(documents)

    count = 0
    for record in records:
        company = None
        if record.get("company"):
            company = _get_or_create(session, Company, name=record["company"])
        _get_or_create(
            session,
            Document,
            defaults={
                "sha256": record.get("sha256", record.get("document_id", "")),
                "filename": record.get("filename", ""),
                "company_id": company.id if company else None,
                "fiscal_year": record.get("fiscal_year"),
                # Key names follow documents/registry.json, which is written by
                # scripts/acquire_documents.py. Guessing them cost a column of
                # dashes in the UI that looked like missing data rather than a
                # mismatched read.
                "page_count": record.get("page_count") or record.get("pages"),
                "source_url": record.get("source_url") or record.get("url"),
                "retrieved_on": record.get("retrieved_at") or record.get("retrieved_on"),
            },
            document_id=record["document_id"],
        )
        count += 1
    return count


def ingest_structure(session: Session, chunk_files: list[Path] | list[str]) -> dict[str, int]:
    """Pages, sections and tables, from the chunk cache (spec Module 18, 26).

    These three entities are named in spec 26 and had no ingest path, so the
    schema carried them and the database never held one. That is worse than not
    modelling them: an empty table with no explanation reads as a bug, and the
    one column with genuine analytical value - `tables.parsing_accuracy` - had
    nowhere to live. 103 tables in this corpus parsed below 80% accuracy, and
    until now the only record of that was console output nobody kept.

    `pages.text` is deliberately left NULL. Chunks carry chunk text, not page
    text, and reassembling a page by concatenating its chunks would produce
    something that looks like the page and is not it - overlapping windows,
    tables interleaved with prose. A NULL says "not captured"; a reconstruction
    would say "this is the page" and be wrong. What the row *does* establish is
    that the page produced extractable content at all, which is why the count
    of page rows against the registry's `page_count` is an extraction-coverage
    figure rather than a restatement of the PDF.

    Idempotent on natural keys - (document, page), (document, title),
    (document, page, table_index) - so a repeated ingest updates in place.
    """
    counts = {"pages": 0, "sections": 0, "tables": 0}

    for file in chunk_files:
        path = Path(file)
        if not path.exists():
            continue

        with path.open(encoding="utf-8") as handle:
            header = json.loads(handle.readline())
            document = session.execute(
                select(Document).filter_by(document_id=header["document_id"])
            ).scalar_one_or_none()
            if document is None:
                # The registry is the authority on which documents exist. A
                # chunk file for an unregistered document is a leftover, not a
                # reason to invent a document row.
                continue

            pages: set[int] = set()
            sections: dict[str, tuple[int, int]] = {}
            tables: dict[tuple[int, int], dict] = {}

            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                page = chunk.get("page")
                if page is None:
                    continue
                pages.add(int(page))

                title = chunk.get("section")
                if title:
                    first, last = sections.get(title, (page, page))
                    sections[title] = (min(first, page), max(last, page))

                if chunk.get("kind") == "table" and chunk.get("table_index") is not None:
                    key = (int(page), int(chunk["table_index"]))
                    # A table split across several chunks yields one row. The
                    # first part carries the chunk id; parsing accuracy is
                    # identical across parts because it describes the table.
                    if key not in tables:
                        tables[key] = {
                            "chunk_id": chunk.get("chunk_id"),
                            "context_scale": chunk.get("context_scale"),
                            "context_currency": chunk.get("context_currency"),
                            "parsing_accuracy": (chunk.get("metadata") or {}).get(
                                "parsing_accuracy"
                            ),
                        }

        for number in sorted(pages):
            _get_or_create(session, Page, document_id=document.id, number=number)
            counts["pages"] += 1

        for title, (start, end) in sections.items():
            _get_or_create(
                session,
                Section,
                defaults={"start_page": start, "end_page": end},
                document_id=document.id,
                title=title[:400],
            )
            counts["sections"] += 1

        for (page, table_index), fields in tables.items():
            _get_or_create(
                session,
                TableRecord,
                defaults=fields,
                document_id=document.id,
                page=page,
                table_index=table_index,
            )
            counts["tables"] += 1

        session.flush()

    return counts


def ingest_dataset(session: Session, dataset: Dataset) -> int:
    """FinVerify-IND questions, evidence spans and gold answers.

    `validation_status` is carried across so a SQL user is subject to the same
    rule as the pipeline: a PENDING question is a candidate, and treating it as
    gold is a mistake the schema makes visible rather than one the application
    alone prevents.
    """
    count = 0
    for question in dataset.questions:
        document = session.execute(
            select(Document).filter_by(document_id=question.document_id)
        ).scalar_one_or_none()
        answer = question.answer
        row = _get_or_create(
            session,
            Question,
            defaults={
                "text": question.question,
                "document_id": document.id if document else None,
                "company": question.company,
                "fiscal_year": question.fiscal_year,
                "question_type": question.question_type,
                "difficulty": question.difficulty,
                "definition": question.definition,
                "ambiguous": question.ambiguous,
                "split": question.split,
                "gold_text": answer.text if answer else None,
                "gold_unit": answer.unit if answer else None,
                "gold_canonical": (
                    float(answer.canonical()) if answer and answer.canonical() is not None
                    else None
                ),
                "validation_status": question.validation.status.value,
                "validator": question.validation.validator,
                "validated_on": question.validation.validated_on,
            },
            qid=question.qid,
        )

        # Evidence is replaced wholesale rather than merged: a span that was
        # removed from the gold set must disappear here too, or a stale span
        # would keep satisfying a retrieval check nobody can see any more.
        for existing in list(row.evidence):
            session.delete(existing)
        session.flush()
        for group in question.evidence:
            for span in group.get("any_of", ()):
                session.add(
                    Evidence(
                        question_id=row.id,
                        group_id=group.get("group_id", ""),
                        page=span.get("page"),
                        printed_page=span.get("printed_page"),
                        anchors=json.dumps(span.get("anchors", [])),
                    )
                )
        count += 1

    # A question removed from the gold set must disappear here too - the same
    # rule the evidence spans above already follow, applied one level up.
    #
    # Upsert alone cannot express a deletion, and D42 made that concrete: it
    # replaced sequential qids (`FI0250`) with content-derived ones
    # (`FI222c2fc4`), so no new row collided with an old one and a re-ingest
    # left all 268 pre-D42 questions sitting beside the 192 current ones. The
    # table read 460. Every stale row carried the wrong-statement provenance
    # RX-026 and RX-027 were written to remove, and a `SELECT` returning both
    # generations looks entirely plausible.
    #
    # Safe because the direction is one-way (D4): the files are canonical and
    # this table is a projection of them, so anything not in the artifact has no
    # claim to exist here. Answers are removed with their question - an answer
    # to a question that no longer exists cannot be graded against anything.
    live = {q.qid for q in dataset.questions}
    stale = [
        row for row in session.execute(select(Question)).scalars().all()
        if row.qid not in live
    ]
    for row in stale:
        for answer in list(row.answers):
            session.delete(answer)
        for evidence in list(row.evidence):
            session.delete(evidence)
        session.delete(row)
    if stale:
        session.flush()
        print(f"  pruned {len(stale)} question(s) no longer in the dataset")
    return count


def ingest_facts(session: Session, facts) -> int:
    """Module 4 facts. Keyed on the CELL, so a re-extraction updates in place."""
    count = 0
    for fact in facts:
        document = session.execute(
            select(Document).filter_by(document_id=fact.document_id)
        ).scalar_one_or_none()
        if document is None:
            continue
        _get_or_create(
            session,
            FinancialFactRow,
            defaults={
                "metric": fact.metric,
                "amount": float(fact.value.amount),
                "canonical": float(fact.canonical),
                "unit": fact.unit,
                "currency": fact.currency,
                "scale": fact.value.scale.label,
                "year": fact.year,
                "year_source": fact.year_source,
                "page": fact.page,
                "section": fact.section,
                "table_index": fact.table_index,
                "column_label": fact.column_label,
                "warnings": json.dumps(list(fact.warnings)),
            },
            document_id=document.id,
            chunk_id=fact.chunk_id,
            row_label=fact.row_label,
            column_index=fact.column_index,
        )
        count += 1
    return count


def prune_facts(session: Session, live: set[tuple[str, str, str, int]]) -> int:
    """Remove facts the current extraction no longer produces.

    The same defect `ingest_dataset` prunes for, one level down, and it survived
    that fix because the shapes differ: questions arrive as one artifact, facts
    arrive one chunk file at a time. `ingest_facts` therefore never sees the whole
    live set and cannot tell a fact that vanished from a fact that belongs to a
    file it has not reached yet - so it upserts, and upsert cannot express a
    deletion. The RX-027 extraction fixes left ~21 facts behind that way, and a
    `SELECT` over the table returns two generations of extraction looking equally
    plausible.

    So the caller accumulates the live keys across every file and prunes once, at
    the end, when "not present" finally means something. Keyed exactly as
    `ingest_facts` upserts - (document, chunk, row label, column index) - because
    a prune that keys differently from the write is a delete-everything bug
    waiting for its first re-ingest.

    Safe for the same reason as the question prune: the direction is one-way
    (D4). The chunk files are canonical and this table is a projection of them.
    """
    documents = {
        row.id: row.document_id
        for row in session.execute(select(Document)).scalars().all()
    }
    stale = [
        row for row in session.execute(select(FinancialFactRow)).scalars().all()
        if (documents.get(row.document_id), row.chunk_id, row.row_label, row.column_index)
        not in live
    ]
    for row in stale:
        session.delete(row)
    if stale:
        session.flush()
        print(f"  pruned {len(stale)} fact(s) no longer produced by extraction")
    return len(stale)


def ingest_run(
    session: Session,
    run_id: str,
    records: list[dict],
    *,
    config: dict | None = None,
    source_path: str = "",
    analysis_by_qid: dict[tuple[str, str], dict] | None = None,
) -> int:
    """One campaign's rows.

    `analysis_by_qid` maps (arm, qid) to the graded outcome. Passing it copies
    `correct` and `stratum` from the analysis instead of recomputing them, so
    the database and the report cannot disagree about which answers were wrong.
    Omitting it leaves both NULL, which is honest: ungraded, not correct.
    """
    config = config or {}
    experiment = _get_or_create(
        session,
        Experiment,
        defaults={
            "source_path": source_path,
            "git_commit": config.get("git_commit"),
            "split": config.get("split"),
            "natural_model": config.get("natural_model"),
            "program_model": config.get("program_model"),
            "verifier_model": config.get("verifier_model"),
            "independence": config.get("independence"),
        },
        run_id=run_id,
    )

    count = 0
    for record in records:
        qid = record.get("question_id")
        arm = record.get("arm")
        if not qid or not arm:
            continue
        question = session.execute(select(Question).filter_by(qid=qid)).scalar_one_or_none()
        if question is None:
            # A row for a question the dataset does not contain. Skipped rather
            # than invented: creating a stub question here would put a row in
            # the gold table that no human ever validated.
            continue

        graded = (analysis_by_qid or {}).get((arm, qid), {})
        answer = _get_or_create(
            session,
            Answer,
            defaults={
                "answer_text": record.get("answer_text") or record.get("answer"),
                "canonical": (
                    float(record["answer_canonical"])
                    if record.get("answer_canonical") is not None
                    else None
                ),
                "answer_source": record.get("answer_source"),
                "abstained": bool(record.get("abstained")),
                "agreed": record.get("agreed"),
                "verdict": record.get("verdict"),
                "consistency_score": record.get("consistency_score"),
                "risk_score": record.get("risk_score"),
                "correct": graded.get("correct"),
                "stratum": graded.get("stratum"),
                "latency_seconds": record.get("latency_seconds"),
                "error": record.get("error"),
                # Copied through, not regenerated. Re-deriving the explanation
                # here would let the database disagree with the artifact about
                # why an answer was flagged, and the artifact is canonical (D4).
                # Absent for runs predating RX-045, which is recorded as NULL.
                "explanation": record.get("explanation"),
            },
            experiment_id=experiment.id,
            question_id=question.id,
            arm=arm,
        )

        for existing in session.execute(
            select(ReasoningRun).filter_by(answer_id=answer.id)
        ).scalars():
            session.delete(existing)
        for existing in session.execute(
            select(ProgramRun).filter_by(answer_id=answer.id)
        ).scalars():
            session.delete(existing)
        for existing in session.execute(
            select(VerificationRun).filter_by(answer_id=answer.id)
        ).scalars():
            session.delete(existing)
        for existing in session.execute(
            select(HallucinationEvent).filter_by(answer_id=answer.id)
        ).scalars():
            session.delete(existing)
        session.flush()

        natural = record.get("natural")
        if isinstance(natural, dict):
            session.add(
                ReasoningRun(
                    answer_id=answer.id,
                    model=config.get("natural_model"),
                    available=bool(natural.get("available")),
                    failure_reason=natural.get("failure_reason"),
                    value=natural.get("value"),
                    canonical=(
                        float(natural["canonical"])
                        if natural.get("canonical") is not None
                        else None
                    ),
                )
            )
        program = record.get("program")
        if isinstance(program, dict):
            session.add(
                ProgramRun(
                    answer_id=answer.id,
                    model=config.get("program_model"),
                    available=bool(program.get("available")),
                    failure_reason=program.get("failure_reason"),
                    value=program.get("value"),
                    canonical=(
                        float(program["canonical"])
                        if program.get("canonical") is not None
                        else None
                    ),
                )
            )
        verification = record.get("verification") or {}
        session.add(
            VerificationRun(
                answer_id=answer.id,
                triggered=bool(verification.get("triggered")),
                available=bool(verification.get("available")),
                resolution=verification.get("resolution"),
                resolved=bool(verification.get("resolved")),
                value=verification.get("value"),
            )
        )

        label = graded.get("label")
        if label:
            session.add(
                HallucinationEvent(
                    answer_id=answer.id,
                    provenance=label.get("provenance", "undetermined"),
                    kind=label.get("kind", "unclassified"),
                    label_confidence=label.get("confidence", "needs_human"),
                    counts_as_hallucination=bool(label.get("counts_as_hallucination", True)),
                    evidence=json.dumps(label.get("evidence", [])),
                )
            )
        count += 1
    return count


def _metric_rows(arm: str, block: dict, stratum: str = "all"):
    if not block:
        return
    ci = block.get("auroc_ci") or {}
    for metric in ("auroc", "auprc"):
        if metric in block:
            yield {
                "arm": arm,
                "metric": metric,
                "stratum": stratum,
                "value": block.get(metric),
                "ci_low": ci.get("ci_low") if metric == "auroc" else None,
                "ci_high": ci.get("ci_high") if metric == "auroc" else None,
                "n": block.get("n"),
                "note": "; ".join(block.get("notes", ())) or None,
            }


def ingest_report(session: Session, run_id: str, report: dict) -> int:
    """Metric values from `evaluation.report.build_report`.

    Long and thin: one row per (arm, metric, stratum). A NULL value means the
    metric was UNDEFINED - an AUROC on a stratum with no errors - and is
    deliberately distinguishable from a value of zero.
    """
    experiment = session.execute(
        select(Experiment).filter_by(run_id=run_id)
    ).scalar_one_or_none()
    if experiment is None:
        raise ValueError(f"run {run_id!r} has not been ingested; ingest_run first")

    count = 0
    for arm, block in (report.get("arms") or {}).items():
        qa = block.get("qa") or {}
        rows = [
            {
                "arm": arm,
                "metric": name,
                "stratum": "all",
                "value": qa.get(name),
                "ci_low": None,
                "ci_high": None,
                "n": qa.get("n"),
                "note": None,
            }
            for name in (
                "numerical_accuracy",
                "exact_match",
                "execution_accuracy",
                "abstention_rate",
                "parse_failure_rate",
            )
            if name in qa
        ]
        rows.extend(_metric_rows(arm, block.get("detection") or {}))
        for stratum, sub in (block.get("detection_by_provenance") or {}).items():
            if isinstance(sub, dict):
                rows.extend(_metric_rows(arm, sub, stratum))

        for row in rows:
            _get_or_create(
                session,
                EvaluationResult,
                defaults={
                    "value": row["value"],
                    "ci_low": row["ci_low"],
                    "ci_high": row["ci_high"],
                    "n": row["n"],
                    "note": row["note"],
                },
                experiment_id=experiment.id,
                arm=row["arm"],
                metric=row["metric"],
                stratum=row["stratum"],
            )
            count += 1

    count += _ingest_ablation(session, experiment, report.get("ablation") or {})
    return count


def _ingest_ablation(session: Session, experiment: Experiment, ablation: dict) -> int:
    """Module 24 contrasts, filed under the arm each one ablates.

    Stored in the same long-thin table as every other metric rather than a table
    of its own, so the API and the UI render them with no new code - and so a
    contrast always appears beside the arm's own AUROC, which is the number a
    reader will otherwise compare it against by eye and get wrong.

    The note carries the Holm verdict and the underpowered warning together.
    A significant p-value resting on three errors is the single most misleading
    thing this table can show, and the warning has to travel in the same cell as
    the value or it will be read without it.
    """
    baseline = ablation.get("baseline")
    contrasts = ablation.get("contrasts") or {}
    if not baseline or not contrasts:
        return 0

    correction = ablation.get("family_wise_correction") or {}
    count = 0
    for name, block in contrasts.items():
        for family in ("all", "committed"):
            measured = block.get(family) or {}
            if not measured.get("testable"):
                continue
            holm = (correction.get(family) or {}).get(name) or {}
            verdict = (
                f"Holm {'significant' if holm.get('significant') else 'NOT significant'}"
                if holm
                else "not entered into the correction"
            )
            # A percentile bootstrap cannot report a p-value below 1/resamples;
            # it reports zero, meaning "no resample landed the other side of
            # zero". Printing that as "p=0" claims a certainty no resampling
            # method has, so it is rendered as the bound it actually is.
            p_value = measured.get("p_value")
            resamples = measured.get("resamples") or 0
            shown = (
                f"p<{1 / resamples:.1g}"
                if p_value == 0 and resamples
                else f"p={p_value:.4g}"
            )
            note = (
                f"removes {block.get('removes')}; "
                f"{shown}; {verdict}; "
                f"{measured.get('errors')} error(s) in the paired set"
            )
            if measured.get("underpowered"):
                note = f"UNDERPOWERED - {measured.get('reason')}; {note}"
            _get_or_create(
                session,
                EvaluationResult,
                defaults={
                    "value": measured.get("point"),
                    "ci_low": measured.get("ci_low"),
                    "ci_high": measured.get("ci_high"),
                    "n": measured.get("n"),
                    "note": note,
                },
                experiment_id=experiment.id,
                arm=block.get("arm", name),
                metric=f"auroc_delta_vs_{baseline}",
                stratum=family,
            )
            count += 1
    return count
