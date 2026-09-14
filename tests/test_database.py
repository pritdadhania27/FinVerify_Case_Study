"""Tests for the persistence layer (spec Module 18).

The database is a projection of the run artifacts, never their replacement
(D4), and these tests pin the properties that keep it honest: ingestion is
idempotent, nothing is invented for a row the gold set does not contain, and
`correct` is copied from the grader rather than recomputed here - two graders
would eventually disagree, and the one in the database would be the one nobody
checks.

Most run on SQLite for speed. The Postgres-only test is SKIPPED, not passed,
when no database is reachable, so a green run on a machine without it never
reads as "the schema was verified".
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import func, select

from backend.database.ingest import (
    ingest_dataset,
    ingest_facts,
    ingest_registry,
    ingest_report,
    ingest_run,
    ingest_structure,
    prune_facts,
)
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
    Question,
    Section,
    TableRecord,
    VerificationRun,
)
from backend.database.session import build_engine, create_all, database_url, session_scope
from backend.documents.facts import facts_from_chunk
from evaluation.dataset import (
    Dataset,
    DatasetQuestion,
    GoldAnswer,
    ValidationRecord,
    ValidationStatus,
)


@pytest.fixture
def engine():
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    return engine


def registry_file(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "documents": {
                    "d1": {
                        "document_id": "d1",
                        "sha256": "a" * 64,
                        "filename": "infosys.pdf",
                        "company": "Infosys Limited",
                        "fiscal_year": "2023-24",
                        "pages": 79,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def gold_question(qid="Q1", **kw) -> DatasetQuestion:
    base = dict(
        question="what were trade payables?",
        company="Infosys Limited",
        fiscal_year="2023-24",
        document_id="d1",
        answer=GoldAnswer(text="3,956", unit="INR crore", source_page=12),
        definition="as reported on the consolidated balance sheet",
        evidence=(
            {"group_id": "tp", "any_of": [{"page": 12, "anchors": ["Trade payables"]}]},
        ),
        split="validation",
        validation=ValidationRecord(
            status=ValidationStatus.VALIDATED, validator="owner", validated_on="2026-08-26"
        ),
    )
    base.update(kw)
    return DatasetQuestion(qid=qid, **base)


def run_row(qid="Q1", arm="A", **kw) -> dict:
    base = {
        "question_id": qid,
        "arm": arm,
        "answer": "3956",
        "answer_text": "INR 3956 crore",
        "answer_canonical": "39560000000",
        "answer_source": "natural",
        "abstained": False,
        "agreed": True,
        "verdict": "AGREE",
        "consistency_score": 0.95,
        "risk_score": 0.12,
        "latency_seconds": 2.4,
        "natural": {"available": True, "value": "3956", "canonical": "39560000000"},
        "program": {"available": True, "value": "3956", "canonical": "39560000000"},
        "verification": {"triggered": False},
    }
    base.update(kw)
    return base


class TestIngestionIsIdempotent:
    """An ingest interrupted halfway must be repeated, not repaired."""

    def test_the_registry_can_be_loaded_twice(self, engine, tmp_path):
        path = registry_file(tmp_path)
        with session_scope(engine) as session:
            ingest_registry(session, path)
        with session_scope(engine) as session:
            ingest_registry(session, path)
            assert session.scalar(select(func.count()).select_from(Document)) == 1
            assert session.scalar(select(func.count()).select_from(Company)) == 1

    def test_a_dataset_can_be_loaded_twice(self, engine, tmp_path):
        dataset = Dataset("s", "v", (gold_question(),))
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, dataset)
        with session_scope(engine) as session:
            ingest_dataset(session, dataset)
            assert session.scalar(select(func.count()).select_from(Question)) == 1
            assert session.scalar(select(func.count()).select_from(Evidence)) == 1

    def test_a_run_can_be_loaded_twice(self, engine, tmp_path):
        dataset = Dataset("s", "v", (gold_question(),))
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, dataset)
        for _ in range(2):
            with session_scope(engine) as session:
                ingest_run(session, "r1", [run_row()], config={"split": "validation"})
        with session_scope(engine) as session:
            assert session.scalar(select(func.count()).select_from(Answer)) == 1
            assert session.scalar(select(func.count()).select_from(Experiment)) == 1
            # Child rows are replaced, not appended, or a re-ingest would
            # multiply them silently.
            assert session.scalar(select(func.count()).select_from(VerificationRun)) == 1

    def test_evidence_removed_from_the_gold_set_disappears(self, engine, tmp_path):
        """A stale span would keep satisfying a retrieval check nobody can see."""
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question(),)))
        with session_scope(engine) as session:
            ingest_dataset(session, Dataset("s", "v", (gold_question(evidence=()),)))
            assert session.scalar(select(func.count()).select_from(Evidence)) == 0

    def test_a_question_removed_from_the_gold_set_disappears(self, engine, tmp_path):
        """Upsert alone cannot express a deletion, and D42 made that concrete.

        It replaced sequential qids with content-derived ones, so no new row
        collided with an old one and a re-ingest left all 268 pre-D42 questions
        beside the 192 current ones - a table reading 460, half of it carrying
        the wrong-statement provenance RX-026 and RX-027 removed.
        """
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question("FI0250"),)))
            assert session.scalar(select(func.count()).select_from(Question)) == 1
        with session_scope(engine) as session:
            # A regenerated dataset: same question, different id.
            ingest_dataset(session, Dataset("s", "v", (gold_question("FI222c2fc4"),)))
            assert session.scalar(select(func.count()).select_from(Question)) == 1
            assert session.scalar(select(Question.qid)) == "FI222c2fc4"

    def test_pruning_a_question_takes_its_evidence_with_it(self, engine, tmp_path):
        """Orphaned spans would outlive the question they describe."""
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question("FI0250"),)))
            assert session.scalar(select(func.count()).select_from(Evidence)) == 1
        with session_scope(engine) as session:
            ingest_dataset(session, Dataset("s", "v", (gold_question("FInew"),)))
            assert session.scalar(select(func.count()).select_from(Evidence)) == 1

    def test_a_question_still_in_the_dataset_is_never_pruned(self, engine, tmp_path):
        """The obvious way to get this wrong is to delete everything."""
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            questions = (gold_question("Q1"), gold_question("Q2"))
            ingest_dataset(session, Dataset("s", "v", questions))
        with session_scope(engine) as session:
            ingest_dataset(session, Dataset("s", "v", questions))
            assert session.scalar(select(func.count()).select_from(Question)) == 2


class TestNothingIsInvented:
    def test_a_row_for_an_unknown_question_is_skipped(self, engine, tmp_path):
        """Creating a stub would put a row in the gold table nobody validated."""
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question("Q1"),)))
        with session_scope(engine) as session:
            written = ingest_run(session, "r1", [run_row(qid="Q999")])
            assert written == 0
            assert session.scalar(select(func.count()).select_from(Question)) == 1

    def test_an_ungraded_answer_is_null_not_false(self, engine, tmp_path):
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question(),)))
            ingest_run(session, "r1", [run_row()])
        with session_scope(engine) as session:
            answer = session.scalar(select(Answer))
            assert answer.correct is None
            assert answer.stratum is None

    def test_the_grade_is_copied_from_the_analysis(self, engine, tmp_path):
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question(),)))
            ingest_run(
                session,
                "r1",
                [run_row()],
                analysis_by_qid={
                    ("A", "Q1"): {
                        "correct": False,
                        "stratum": "reasoning_caused",
                        "label": {
                            "provenance": "reasoning",
                            "kind": "wrong_scale",
                            "confidence": "mechanical",
                            "counts_as_hallucination": True,
                            "evidence": ["scale gap"],
                        },
                    }
                },
            )
        with session_scope(engine) as session:
            answer = session.scalar(select(Answer))
            assert answer.correct is False
            assert answer.stratum == "reasoning_caused"
            event = session.scalar(select(HallucinationEvent))
            assert (event.provenance, event.kind) == ("reasoning", "wrong_scale")

    def test_a_report_cannot_be_loaded_before_its_run(self, engine):
        with session_scope(engine) as session, pytest.raises(ValueError):
            ingest_report(session, "never-ingested", {"arms": {}})


class TestQueryability:
    """The reason the projection exists at all."""

    def test_the_blind_spot_is_one_query(self, engine, tmp_path):
        questions = tuple(gold_question(f"Q{i}") for i in range(3))
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", questions))
            ingest_run(
                session,
                "r1",
                [run_row(f"Q{i}") for i in range(3)],
                analysis_by_qid={
                    ("A", "Q0"): {"correct": False, "stratum": "reasoning_caused"},
                    ("A", "Q1"): {"correct": True, "stratum": "reasoning_caused"},
                    ("A", "Q2"): {"correct": True, "stratum": "reasoning_caused"},
                },
            )
        with session_scope(engine) as session:
            rows = session.execute(
                select(Question.qid)
                .join(Answer, Answer.question_id == Question.id)
                .where(Answer.agreed.is_(True), Answer.correct.is_(False))
            ).scalars().all()
            assert rows == ["Q0"]

    def test_validation_status_reaches_the_database(self, engine, tmp_path):
        """A SQL user is subject to the same rule as the pipeline."""
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(
                session,
                Dataset("s", "v", (gold_question("Q1"), gold_question(
                    "Q2", validation=ValidationRecord()))),
            )
        with session_scope(engine) as session:
            usable = session.execute(
                select(Question.qid).where(Question.validation_status == "validated")
            ).scalars().all()
            assert usable == ["Q1"]

    def test_metric_rows_are_long_and_thin(self, engine, tmp_path):
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_dataset(session, Dataset("s", "v", (gold_question(),)))
            ingest_run(session, "r1", [run_row()])
            ingest_report(
                session,
                "r1",
                {
                    "arms": {
                        "A": {
                            "qa": {"numerical_accuracy": 0.8, "n": 10},
                            "detection": {
                                "auroc": 0.9,
                                "auprc": 0.7,
                                "n": 10,
                                "notes": [],
                                "auroc_ci": {"ci_low": 0.7, "ci_high": 0.99},
                            },
                            "detection_by_provenance": {
                                "reasoning_caused": {
                                    "auroc": 0.95, "auprc": 0.8, "n": 6, "notes": []
                                },
                            },
                        }
                    }
                },
            )
        with session_scope(engine) as session:
            rows = session.execute(select(EvaluationResult)).scalars().all()
            names = {(r.metric, r.stratum) for r in rows}
            assert ("auroc", "all") in names
            assert ("auroc", "reasoning_caused") in names
            assert ("numerical_accuracy", "all") in names


class TestFacts:
    def test_a_fact_is_keyed_on_its_cell(self, engine, tmp_path):
        chunk = {
            "chunk_id": "d1:p12:t0:0",
            "kind": "table",
            "text": "| Particulars | 2024 | 2023 |\n| Trade payables | 3,956 | 3,865 |\n",
            "document_id": "d1",
            "page": 12,
            "table_index": 0,
            "company": "Infosys Limited",
            "fiscal_year": "2023-24",
            "context_scale": "crore",
            "context_currency": "INR",
        }
        facts = facts_from_chunk(chunk)
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_facts(session, facts)
        with session_scope(engine) as session:
            ingest_facts(session, facts)
            rows = session.execute(select(FinancialFactRow)).scalars().all()
            assert len(rows) == 2
            assert {r.year for r in rows} == {"2024", "2023"}
            assert all(r.year_source == "column_header" for r in rows)

    def test_a_re_extraction_that_drops_a_row_prunes_it(self, engine, tmp_path):
        """Upsert cannot express a deletion - the same defect as D42's questions.

        The RX-027 extraction fixes removed facts, and because `ingest_facts` sees
        one chunk file at a time it upserted the new ones beside the old. The table
        kept ~21 orphans and a `SELECT` returned two generations of extraction, both
        looking equally plausible.
        """
        def chunk(text):
            return {
                "chunk_id": "d1:p12:t0:0", "kind": "table", "text": text,
                "document_id": "d1", "page": 12, "table_index": 0,
                "company": "Infosys Limited", "fiscal_year": "2023-24",
                "context_scale": "crore", "context_currency": "INR",
            }

        both = facts_from_chunk(chunk(
            "| Particulars | 2024 |\n| Trade payables | 3,956 |\n| Borrowings | 1,200 |\n"
        ))
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_facts(session, both)
            assert len(session.execute(select(FinancialFactRow)).scalars().all()) == 2

        # Re-extraction now yields only Trade payables. Without a prune the
        # Borrowings row survives, indistinguishable from a current fact.
        remaining = facts_from_chunk(chunk(
            "| Particulars | 2024 |\n| Trade payables | 3,956 |\n"
        ))
        with session_scope(engine) as session:
            ingest_facts(session, remaining)
            live = {
                (f.document_id, f.chunk_id, f.row_label, f.column_index)
                for f in remaining
            }
            assert prune_facts(session, live) == 1
            rows = session.execute(select(FinancialFactRow)).scalars().all()
            assert [r.row_label for r in rows] == ["Trade payables"]

    def test_pruning_keys_the_same_way_ingestion_writes(self, engine, tmp_path):
        """A prune keyed differently from the write deletes the whole table.

        This is the failure mode worth a test of its own: it only shows up on the
        SECOND ingest, it looks like a successful re-ingest, and the damage is
        total rather than partial.
        """
        chunk = {
            "chunk_id": "d1:p12:t0:0", "kind": "table",
            "text": "| Particulars | 2024 | 2023 |\n| Trade payables | 3,956 | 3,865 |\n",
            "document_id": "d1", "page": 12, "table_index": 0,
            "company": "Infosys Limited", "fiscal_year": "2023-24",
            "context_scale": "crore", "context_currency": "INR",
        }
        facts = facts_from_chunk(chunk)
        with session_scope(engine) as session:
            ingest_registry(session, registry_file(tmp_path))
            ingest_facts(session, facts)
        with session_scope(engine) as session:
            live = {(f.document_id, f.chunk_id, f.row_label, f.column_index) for f in facts}
            assert prune_facts(session, live) == 0
            assert len(session.execute(select(FinancialFactRow)).scalars().all()) == 2


class TestConfiguration:
    def test_no_default_connection_string_with_credentials(self, monkeypatch):
        """A URL with a password in source is a credential in the repository."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError) as excinfo:
            database_url()
        assert "DATABASE_URL" in str(excinfo.value)

    def test_an_explicit_sqlite_default_is_allowed_for_tests(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert database_url(default_sqlite="sqlite://") == "sqlite://"

    def test_a_rollback_leaves_nothing_behind(self, engine, tmp_path):
        """A half-committed ingest would leave rows a re-run then skips."""
        with pytest.raises(RuntimeError):
            with session_scope(engine) as session:
                ingest_registry(session, registry_file(tmp_path))
                raise RuntimeError("interrupted")
        with session_scope(engine) as session:
            assert session.scalar(select(func.count()).select_from(Document)) == 0


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="no PostgreSQL configured; skipped rather than passed so a green run "
    "on a machine without it never reads as 'the schema was verified'",
)
def test_the_schema_applies_to_real_postgres():
    from sqlalchemy import inspect

    engine = build_engine(os.environ["DATABASE_URL"])
    tables = set(inspect(engine).get_table_names())
    required = {
        "users", "companies", "documents", "pages", "sections", "tables",
        "financial_facts", "questions", "evidence", "reasoning_runs",
        "program_runs", "verification_runs", "answers", "hallucination_events",
        "experiments", "evaluation_results",
    }
    missing = required - tables
    assert not missing, f"migration has not been applied; missing {sorted(missing)}"


# --------------------------------------------------------------------------
# Pages, sections and tables (spec 26) - the three entities that had a schema
# and no ingest path, so the database held none of them.
# --------------------------------------------------------------------------


def chunk_file(tmp_path, document_id="d1", rows=()):
    path = tmp_path / f"{document_id}.chunks.jsonl"
    lines = [json.dumps({"document_id": document_id, "sha256": "a" * 64, "pages": 79})]
    lines += [json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def table_chunk(page, table_index, *, part=0, accuracy=100.0, section=None, cid=None):
    return {
        "chunk_id": cid or f"d1:p{page}:t{table_index}:{part}",
        "kind": "table",
        "text": "x",
        "document_id": "d1",
        "page": page,
        "section": section,
        "table_index": table_index,
        "part": part,
        "context_scale": "crore",
        "context_currency": "INR",
        "metadata": {"parsing_accuracy": accuracy},
    }


def test_structure_ingest_populates_all_three_entities(engine, tmp_path):
    path = chunk_file(
        tmp_path,
        rows=[
            table_chunk(12, 0, section="Consolidated Balance Sheet"),
            table_chunk(13, 0, section="Consolidated Balance Sheet"),
            {"chunk_id": "d1:p20:c0", "kind": "text", "text": "y", "document_id": "d1",
             "page": 20, "section": "Notes"},
        ],
    )
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        counts = ingest_structure(session, [path])

    assert counts == {"pages": 3, "sections": 2, "tables": 2}


def test_a_table_split_across_parts_is_one_row(engine, tmp_path):
    """Chunking splits a long table into parts; the table is still one table.

    Counting parts would inflate the table count and, worse, average one
    table's parsing accuracy several times into a per-document figure.
    """
    path = chunk_file(
        tmp_path,
        rows=[table_chunk(12, 0, part=p, accuracy=42.0) for p in range(4)],
    )
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        counts = ingest_structure(session, [path])
        rows = session.execute(select(TableRecord)).scalars().all()

    assert counts["tables"] == 1
    assert len(rows) == 1
    assert rows[0].parsing_accuracy == 42.0


def test_parsing_accuracy_survives_the_trip(engine, tmp_path):
    """The one column here with analytical value. 103 tables in the real corpus
    parsed below 80%, and before this path existed that fact lived only in
    console output nobody kept."""
    path = chunk_file(tmp_path, rows=[table_chunk(82, 1, accuracy=26.8)])
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        ingest_structure(session, [path])
        row = session.execute(select(TableRecord)).scalar_one()
        assert row.parsing_accuracy == 26.8
        assert row.page == 82 and row.table_index == 1


def test_page_text_is_null_rather_than_reassembled(engine, tmp_path):
    """A page rebuilt from overlapping chunks looks like the page and is not it.

    NULL says "not captured". A reconstruction would say "this is the page"
    and be wrong, which is the more expensive of the two.
    """
    path = chunk_file(tmp_path, rows=[table_chunk(12, 0)])
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        ingest_structure(session, [path])
        assert session.execute(select(Page)).scalar_one().text is None


def test_structure_ingest_is_idempotent(engine, tmp_path):
    path = chunk_file(
        tmp_path, rows=[table_chunk(12, 0, section="Balance Sheet"), table_chunk(12, 1)]
    )
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        ingest_structure(session, [path])
        ingest_structure(session, [path])
        assert session.scalar(select(func.count()).select_from(Page)) == 1
        assert session.scalar(select(func.count()).select_from(TableRecord)) == 2
        assert session.scalar(select(func.count()).select_from(Section)) == 1


def test_a_section_spans_the_pages_it_appears_on(engine, tmp_path):
    path = chunk_file(
        tmp_path,
        rows=[
            table_chunk(12, 0, section="Balance Sheet"),
            table_chunk(15, 0, section="Balance Sheet"),
            table_chunk(13, 0, section="Balance Sheet"),
        ],
    )
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        ingest_structure(session, [path])
        section = session.execute(select(Section)).scalar_one()
        assert (section.start_page, section.end_page) == (12, 15)


def test_chunks_for_an_unregistered_document_are_skipped_not_invented(engine, tmp_path):
    """The registry is the authority on which documents exist.

    A leftover chunk file must not conjure a document row - that would put a
    document in the database that provenance cannot trace to a registered,
    hashed source.
    """
    path = chunk_file(tmp_path, document_id="ghost", rows=[table_chunk(1, 0)])
    with session_scope(engine) as session:
        ingest_registry(session, registry_file(tmp_path))
        counts = ingest_structure(session, [path])
        assert counts == {"pages": 0, "sections": 0, "tables": 0}
        assert session.scalar(select(func.count()).select_from(Document)) == 1
