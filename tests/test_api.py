"""Tests for the FastAPI layer (spec Module 20).

The endpoint that needed care is `POST /questions/ask`: it runs both reasoning
channels and can trigger the arbiter, so every request spends free-tier quota
that the evaluation campaign depends on (D14). An open endpoint is a way to lose
a day of campaign budget to a crawler or a refresh loop, so it is off unless
switched on deliberately - and the test below is what keeps it off.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api import main as api_main
from backend.database.ingest import ingest_dataset, ingest_registry, ingest_report, ingest_run
from backend.database.models import (
    Answer,
    Document,
    Evidence,
    EvaluationResult,
    Experiment,
    Question,
)
from backend.database.session import build_engine, create_all, session_scope
from evaluation.dataset import (
    Dataset,
    DatasetQuestion,
    GoldAnswer,
    ValidationRecord,
    ValidationStatus,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app wired to an in-memory database with a small ingested corpus."""
    # A FILE, not :memory:. An in-memory SQLite database is per-connection, and
    # TestClient serves requests on a different thread from the fixture - so the
    # tables would exist on the setup connection and nowhere else, failing with
    # "no such table" on every read endpoint.
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'api.db'}")
    create_all(engine)

    # Without this the upload tests below write their fake PDFs into the real
    # documents/raw/, beside the five annual reports the corpus is built from.
    # They did, until 2026-08-29.
    monkeypatch.setenv("FINVERIFY_UPLOAD_DIR", str(tmp_path / "uploads"))

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "document_id": "d1",
                        "sha256": "a" * 64,
                        "filename": "infosys.pdf",
                        "company": "Infosys Limited",
                        "fiscal_year": "2023-24",
                        "pages": 79,
                        "source_url": "https://example.invalid/infosys.pdf",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    question = DatasetQuestion(
        qid="Q1",
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

    with session_scope(engine) as session:
        ingest_registry(session, registry)
        ingest_dataset(session, Dataset("s", "v", (question,)))
        ingest_run(
            session,
            "r1",
            [
                {
                    "question_id": "Q1",
                    "arm": "A",
                    "answer": "3956",
                    "answer_text": "INR 3956 crore",
                    "answer_canonical": "39560000000",
                    "answer_source": "natural",
                    "agreed": True,
                    "verdict": "AGREE",
                    "risk_score": 0.12,
                    "latency_seconds": 2.1,
                    # Both channels, so the answer-detail endpoint has something
                    # to fail to return. Without these the fixture cannot
                    # distinguish "no channel rows exist" from "the endpoint
                    # never reads them" - which is exactly how it shipped
                    # returning `channels: []` for every answer.
                    "natural": {
                        "available": True,
                        "value": "3956",
                        "canonical": "39560000000",
                        "failure_reason": None,
                    },
                    "program": {
                        "available": False,
                        "value": None,
                        "canonical": None,
                        "failure_reason": "program executed and reported the "
                                          "evidence as insufficient",
                    },
                    "verification": {"triggered": False},
                }
            ],
            config={"split": "validation", "natural_model": "m-a"},
        )
        ingest_report(
            session,
            "r1",
            {
                "arms": {
                    "A": {
                        "qa": {"numerical_accuracy": 1.0, "n": 1},
                        "detection": {
                            "auroc": None,
                            "auprc": None,
                            "n": 1,
                            "notes": ["undefined: one class"],
                            "auroc_ci": {},
                        },
                    }
                }
            },
        )

    app = api_main.create_app()

    def _session():
        with session_scope(engine) as session:
            yield session

    # Override the app's session dependency rather than the environment, so the
    # test never depends on a reachable PostgreSQL.
    app.dependency_overrides[api_main.get_session] = _session

    monkeypatch.delenv("FINVERIFY_ENABLE_LIVE_QA", raising=False)
    test_client = TestClient(app)
    # Stashed so a test can write to the same database the app reads, which is
    # the only way to check that a read endpoint reflects a write rather than
    # caching its first answer.
    test_client.engine = engine
    return test_client


class TestLiveQaIsOffByDefault:
    """The endpoint that spends the campaign's budget."""

    def test_asking_is_refused_unless_explicitly_enabled(self, client):
        response = client.post("/questions/ask", json={"question": "trade payables?"})
        assert response.status_code == 503
        assert "free-tier quota" in response.json()["detail"]

    def test_the_refusal_names_the_switch(self, client):
        response = client.post("/questions/ask", json={"question": "what were payables?"})
        detail = response.json()["detail"]
        assert "FINVERIFY_ENABLE_LIVE_QA=1" in detail

    def test_it_refuses_before_touching_a_provider(self, client, monkeypatch):
        """No API call, no embedding load, no index connection."""
        def explode(*a, **k):
            raise AssertionError("a provider was built despite live QA being disabled")

        monkeypatch.setattr("backend.services.llm.registry.build_provider", explode)
        assert client.post(
            "/questions/ask", json={"question": "what were payables?"}
        ).status_code == 503

    def test_the_health_endpoint_reports_the_switch(self, client):
        assert client.get("/health").json()["live_questions_enabled"] is False

    def test_an_unknown_arm_is_rejected(self, client, monkeypatch):
        monkeypatch.setenv("FINVERIFY_ENABLE_LIVE_QA", "1")
        response = client.post(
            "/questions/ask", json={"question": "trade payables?", "arm": "Z"}
        )
        assert response.status_code == 400
        assert "unknown arm" in response.json()["detail"]


class TestDocuments:
    def test_documents_are_listed_with_provenance(self, client):
        rows = client.get("/documents").json()
        assert len(rows) == 1
        assert rows[0]["company"] == "Infosys Limited"
        assert rows[0]["sha256"] == "a" * 64
        assert rows[0]["source_url"]

    def test_a_missing_document_is_404(self, client):
        assert client.get("/documents/nope").status_code == 404

    def test_upload_returns_202_not_201(self, client):
        """202 because the document is registered, not searchable."""
        response = client.post(
            "/documents/upload",
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["indexed"] is False
        assert "index_corpus" in body["next_step"]

    def test_a_duplicate_upload_is_recognised_by_its_bytes(self, client):
        payload = b"%PDF-1.4 identical"
        first = client.post(
            "/documents/upload",
            files={"file": ("a.pdf", io.BytesIO(payload), "application/pdf")},
        ).json()
        second = client.post(
            "/documents/upload",
            files={"file": ("different-name.pdf", io.BytesIO(payload), "application/pdf")},
        ).json()
        assert second["duplicate"] is True
        assert second["document_id"] == first["document_id"]

    def test_two_different_files_sharing_a_name_do_not_overwrite(self, client, tmp_path):
        """`annual-report.pdf` is what several of these companies call their filing.

        Storing under the bare name would let the second upload overwrite the
        first while the registry still records the first document's sha256 -
        a hash that no longer describes any file on disk. In a system whose
        claim is provenance, that is a corruption, not an inconvenience.
        """
        for body in (b"%PDF-1.4 first", b"%PDF-1.4 second"):
            response = client.post(
                "/documents/upload",
                files={"file": ("annual-report.pdf", io.BytesIO(body), "application/pdf")},
            )
            assert response.status_code == 202

        stored = sorted((tmp_path / "uploads").iterdir())
        assert len(stored) == 2, "the second upload overwrote the first"
        assert {p.read_bytes() for p in stored} == {b"%PDF-1.4 first", b"%PDF-1.4 second"}

    def test_a_non_pdf_is_refused(self, client):
        response = client.post(
            "/documents/upload",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert response.status_code == 415

    def test_an_empty_file_is_refused(self, client):
        response = client.post(
            "/documents/upload",
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert response.status_code == 400


class TestReadEndpoints:
    def _answer_id(self, client) -> int:
        experiments = client.get("/experiments").json()
        assert experiments and experiments[0]["answers"] == 1
        return 1

    def test_an_answer_is_retrievable(self, client):
        body = client.get(f"/answers/{self._answer_id(client)}").json()
        assert body["arm"] == "A"
        assert body["risk_score"] == pytest.approx(0.12)
        assert body["answer_text"] == "INR 3956 crore"

    def test_a_missing_answer_is_404(self, client):
        assert client.get("/answers/9999").status_code == 404

    def test_a_recorded_answer_is_banded_by_its_stored_score(self, client):
        """The endpoint returned the schema default UNSCORED for every recorded
        answer, including scored ones - "no detector ran" printed exactly where
        one did. The band is derived from the score, never stored (§23), so the
        fix is to derive it here rather than add a column.
        """
        body = client.get(f"/answers/{self._answer_id(client)}").json()
        assert body["risk_score"] == pytest.approx(0.12)
        assert body["band"] == "LOW"

    def test_the_listing_carries_the_band_so_the_ui_need_not_derive_it(self, client):
        """The verification table shows a banded pill per row. Deriving the band
        in the client would put the thresholds in two places, and they would
        drift the first time either moved.
        """
        rows = client.get("/answers").json()
        assert rows and all("band" in row for row in rows)
        scored = [row for row in rows if row["risk_score"] is not None]
        assert scored and all(row["band"] != "UNSCORED" for row in scored)

    def test_an_arm_with_no_detector_stays_unscored(self, client):
        """UNSCORED must keep meaning "there is no score", or it means nothing."""
        from backend.verification.confidence import band_for

        assert band_for(None) == "UNSCORED"
        assert band_for(0.0) == "LOW"

    def test_the_answer_carries_both_channels(self, client):
        """`AnswerOut` has had `channels` since the schema was written and the
        endpoint never filled it, so every answer came back with `channels: []`
        while the rows sat in `reasoning_runs` and `program_runs`. The whole
        project is about what the two channels each said; an endpoint that
        cannot report that is not serving its own contract.

        The unit suite passed throughout, because it asserted only on the fields
        the endpoint did populate. This was found by querying the deployed
        stack."""
        body = client.get("/answers/1").json()
        by_name = {c["name"]: c for c in body["channels"]}
        assert set(by_name) == {"natural", "program"}
        assert by_name["natural"]["available"] is True
        assert by_name["natural"]["value"] == "3956"

    def test_a_channel_that_declined_reports_why(self, client):
        """An unavailable channel is not a blank. The reason distinguishes a
        program that crashed from one that ran and found the evidence
        insufficient, which is the distinction RX-036 turned on."""
        body = client.get("/answers/1").json()
        program = next(c for c in body["channels"] if c["name"] == "program")
        assert program["available"] is False
        assert "insufficient" in (program["failure_reason"] or "")

    def test_the_answer_carries_its_gold_evidence(self, client):
        """`evidence` was empty for the same reason `channels` was. An answer a
        reader cannot trace to a page is the thing this system exists to
        avoid."""
        body = client.get("/answers/1").json()
        assert len(body["evidence"]) == 1
        assert body["evidence"][0]["page"] == 12
        assert body["evidence"][0]["citation"] == "p.12"

    def test_verification_is_retrievable_even_when_not_triggered(self, client):
        """Absence of a trigger is data: it is what the trigger RATE is made of."""
        body = client.get(f"/verification/{self._answer_id(client)}").json()
        assert body["triggered"] is False

    def test_gold_evidence_is_served_by_qid(self, client):
        rows = client.get("/evidence/Q1").json()
        assert len(rows) == 1
        assert rows[0]["page"] == 12

    def test_evidence_for_an_unknown_question_is_404(self, client):
        assert client.get("/evidence/NOPE").status_code == 404

    def test_experiments_carry_their_source_path(self, client):
        row = client.get("/experiments").json()[0]
        assert row["run_id"] == "r1"
        assert row["split"] == "validation"

    def test_an_undefined_metric_is_null_not_zero(self, client):
        """AUROC on a stratum with no errors is undefined. Coalescing it to 0
        would put "the detector failed" in a table where the truth is "this
        could not be measured"."""
        rows = client.get("/evaluation/results", params={"run_id": "r1"}).json()
        auroc = [r for r in rows if r["metric"] == "auroc"]
        assert auroc and auroc[0]["value"] is None
        assert "undefined" in (auroc[0]["note"] or "")

    def test_results_can_be_filtered(self, client):
        rows = client.get(
            "/evaluation/results", params={"arm": "A", "metric": "numerical_accuracy"}
        ).json()
        assert len(rows) == 1
        assert rows[0]["value"] == pytest.approx(1.0)


class TestListingAnswers:
    """A recorded answer is only inspectable if it is first findable.

    The verification screen used to ask for an integer primary key, which nobody
    outside the database knows - so the arbiter's behaviour, the thing this
    project exists to demonstrate, was reachable only by guessing.
    """

    def test_answers_can_be_listed_without_knowing_an_id(self, client):
        rows = client.get("/answers").json()
        assert len(rows) == 1
        assert rows[0]["answer_id"] == 1

    def test_each_row_carries_the_question_text_not_just_a_key(self, client):
        """A list of integers is not a picker."""
        row = client.get("/answers").json()[0]
        assert row["question"] == "what were trade payables?"
        assert row["question_id"] == "Q1"
        assert row["run_id"] == "r1"

    def test_it_can_be_filtered_by_run_and_arm(self, client):
        assert len(client.get("/answers", params={"arm": "A"}).json()) == 1
        assert client.get("/answers", params={"arm": "NOPE"}).json() == []
        assert len(client.get("/answers", params={"run_id": "r1"}).json()) == 1
        assert client.get("/answers", params={"run_id": "nope"}).json() == []

    def test_an_ungraded_answer_reports_null_not_false(self, client):
        """`correct: false` says the system got it wrong; `correct: null` says
        nobody has checked. A UI cannot tell those apart if the API cannot."""
        with session_scope(client.engine) as session:
            session.execute(
                Answer.__table__.update().where(Answer.id == 1).values(correct=None)
            )
        assert client.get("/answers").json()[0]["correct"] is None

    def test_an_arm_without_a_detector_reports_no_risk_score(self, client):
        """B1-B4 have no detector. A default of 0.5 would enter them into the
        detection table as though they had been measured."""
        with session_scope(client.engine) as session:
            session.execute(
                Answer.__table__.update().where(Answer.id == 1).values(risk_score=None)
            )
        assert client.get("/answers").json()[0]["risk_score"] is None

    def test_the_limit_is_bounded(self, client):
        assert client.get("/answers", params={"limit": 0}).status_code == 422
        assert client.get("/answers", params={"limit": 100000}).status_code == 422


class TestCorpusStats:
    """The dashboard renders these numbers, so a wrong one is a wrong claim.

    Every field is a count of rows that exist. The failure mode being guarded
    against is the one this project has hit repeatedly in its own documentation:
    a plausible figure that was true once and is quoted long after it stopped
    being true. Counting at request time makes that impossible; these pin that
    the counts are of the right things.
    """

    def test_it_counts_the_ingested_corpus(self, client):
        stats = client.get("/stats").json()
        assert stats["documents"] == 1
        assert stats["questions_total"] == 1

    def test_validation_status_is_broken_out_not_totalled(self, client):
        """`questions_total` includes pending and rejected rows. Reporting only
        the total would present 192 candidates as 192 gold answers."""
        stats = client.get("/stats").json()
        assert stats["questions_validated"] == 1
        assert stats["questions_rejected"] == 0
        assert stats["questions_pending"] == 0
        assert (
            stats["questions_validated"]
            + stats["questions_rejected"]
            + stats["questions_pending"]
        ) == stats["questions_total"]

    def test_arms_come_from_the_answers_actually_recorded(self, client):
        """Not from a config listing what a campaign intends to run. A planned
        arm that never produced a row must not appear as though it had."""
        assert client.get("/stats").json()["arms"] == ["A"]

    def test_graded_answers_are_counted_separately_from_produced_ones(self, client):
        """An answer with `correct` still NULL exists but has not been scored.
        Folding the two together reports a campaign as further along than it is."""
        stats = client.get("/stats").json()
        assert stats["answers"] == 1
        assert stats["answers_graded"] <= stats["answers"]

    def test_it_reflects_a_write_rather_than_caching_the_first_answer(self, client):
        """Counted at request time, not memoised. A corpus figure that survives
        the write that invalidated it is the stale-number failure mode this
        project keeps finding in its own documentation."""
        before = client.get("/stats").json()["questions_total"]

        with session_scope(client.engine) as session:
            session.add(Question(qid="Q2", text="added mid-test", split="train"))

        assert client.get("/stats").json()["questions_total"] == before + 1


class TestContract:
    def test_every_spec_endpoint_exists(self, client):
        """Spec §28 names these. A missing one is a gap, not a design choice."""
        schema = client.get("/openapi.json").json()["paths"]
        required = {
            ("/documents/upload", "post"),
            ("/documents", "get"),
            ("/documents/{document_id}", "get"),
            ("/questions/ask", "post"),
            ("/answers/{answer_id}", "get"),
            ("/verification/{answer_id}", "get"),
            ("/evidence/{question_id}", "get"),
            ("/experiments", "get"),
            ("/evaluation/results", "get"),
        }
        for path, method in required:
            assert path in schema and method in schema[path], f"{method.upper()} {path}"

    def test_the_contract_is_documented(self, client):
        """FastAPI generates it from the Pydantic models; spec §28 requires it."""
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"] == "FinVerify-AI"
        assert "AnswerOut" in schema["components"]["schemas"]

    def test_risk_score_is_nullable_in_the_contract(self, client):
        """An arm with no detector has no score, and the schema must allow that."""
        properties = client.get("/openapi.json").json()["components"]["schemas"][
            "AnswerOut"
        ]["properties"]
        risk = properties["risk_score"]
        assert "null" in json.dumps(risk)

    def test_a_too_short_question_is_rejected_by_the_contract(self, client, monkeypatch):
        monkeypatch.setenv("FINVERIFY_ENABLE_LIVE_QA", "1")
        assert client.post("/questions/ask", json={"question": "x"}).status_code == 422


class TestPersistenceIsUntouched:
    def test_the_api_writes_no_run_artifacts(self):
        """An API request is a demonstration, not an experiment (D4).

        Mixing ad-hoc questions into experiments/runs/ would put unlabelled,
        unplanned rows in the evidence base the paper is drawn from.
        """
        import ast
        import inspect

        # The module docstring EXPLAINS this rule, so scanning the raw source
        # would match its own prose. Strip it and scan the code.
        tree = ast.parse(inspect.getsource(api_main))
        tree.body = [
            node for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code = ast.unparse(tree)
        for forbidden in ("CampaignRecorder", "experiments/runs", "run_campaign"):
            assert forbidden not in code, f"{forbidden!r} appears in the API layer"


def test_ingested_answers_link_back_to_their_question(client):
    """A join that returns nothing is the failure mode a smoke test misses."""
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    with session_scope(engine) as session:
        assert session.execute(select(Answer)).first() is None
        assert session.execute(select(Question)).first() is None


class TestRegressionsFoundByProbingTheDeployedStack:
    """Defects the unit suite passed straight through.

    Each of these was found by driving the running containers rather than the
    TestClient, which is the same way the empty-`channels` bug was found. They
    are pinned here so the next refactor cannot quietly restore them.
    """

    def test_an_out_of_range_answer_id_is_rejected_not_a_server_error(self, client):
        """`answers.id` is a Postgres int4. A larger id reached psycopg and came
        back as a 500, so a typo in the verification screen's lookup box read as
        a broken server."""
        response = client.get("/answers/99999999999")
        assert response.status_code == 422, response.text
        assert client.get("/answers/0").status_code == 422

    def test_the_arms_endpoint_describes_the_ablation(self, client):
        rows = client.get("/arms").json()
        by_name = {row["name"]: row for row in rows}

        assert by_name["A"]["removes"] is None
        assert by_name["C"]["uses_program"] is False
        assert "executed-program" in by_name["C"]["removes"]
        # The distinction the verification screen was getting wrong.
        assert by_name["D"]["uses_arbiter"] is False
        assert by_name["A"]["uses_arbiter"] is True

    def test_a_recorded_answer_carries_the_configuration_it_ran_under(self, client):
        body = client.get(f"/answers/{self._first_answer_id(client)}").json()
        assert body["arm_config"] is not None
        assert body["arm_config"]["name"] == body["arm"]

    def test_answers_can_be_filtered_to_one_question(self, client):
        """The cross-arm view needs this; without it the comparison the whole
        project is about meant pulling every row and sifting client-side."""
        rows = client.get("/answers").json()
        assert rows
        qid = rows[0]["question_id"]
        filtered = client.get(f"/answers?question_id={qid}").json()
        assert filtered
        assert {row["question_id"] for row in filtered} == {qid}
        assert client.get("/answers?question_id=NOT_A_QID").json() == []

    def test_an_unwritable_upload_directory_reports_why(self, client, monkeypatch, tmp_path):
        """The upload directory defaulted into the corpus mount, which compose
        mounts read-only, so every upload died on an OSError behind a bare 500.
        A deployment fault the caller cannot fix must say what it is."""
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("this is a file, so mkdir under it must fail", encoding="utf-8")
        monkeypatch.setenv("FINVERIFY_UPLOAD_DIR", str(blocked / "uploads"))

        response = client.post(
            "/documents/upload",
            files={"file": ("x.pdf", b"%PDF-1.4 probe", "application/pdf")},
        )
        assert response.status_code == 507, response.text
        assert "not writable" in response.json()["detail"]

    def _first_answer_id(self, client) -> int:
        rows = client.get("/answers").json()
        assert rows, "the fixture should have recorded at least one answer"
        return rows[0]["answer_id"]


class TestEveryListingHasADefinedOrder:
    """A listing query with no ORDER BY returns rows in whatever order the
    database finds convenient, and it may choose differently next time.

    Four endpoints shipped that way. `/documents` was the sharp one - it pairs
    its unordered query with a LIMIT, so the default page of 50 is *any* 50 of
    the filings, and "the registered corpus" could list a different set on a
    reload. The other three feed tables and 42-option pickers that a reader is
    meant to diff against a published figure.

    Each test inserts rows in an order the endpoint must NOT return, which is
    what makes them fail against the unordered queries rather than passing by
    the luck of SQLite handing back rows in rowid order.
    """

    def test_documents_are_ordered_and_not_an_arbitrary_page(self, client):
        with session_scope(client.engine) as session:
            # Inserted descending, so receipt order and sorted order differ.
            for doc_id in ("d3", "d2"):
                session.add(
                    Document(
                        document_id=doc_id,
                        sha256=doc_id * 32,
                        filename=f"{doc_id}.pdf",
                    )
                )

        ids = [row["document_id"] for row in client.get("/documents").json()]
        assert ids == ["d1", "d2", "d3"], ids
        # And the page is the same page twice, which is the property a LIMIT
        # without an ORDER BY does not have.
        assert [r["document_id"] for r in client.get("/documents?limit=2").json()] == [
            "d1",
            "d2",
        ]

    def test_experiments_are_ordered_by_id_not_by_ingest_order(self, client):
        """Descending run_id, so the order survives a re-ingest. Ordering by
        `started_at` would not: that column records when the row was written, so
        `ingest_to_database.py --all` would reshuffle the picker."""
        with session_scope(client.engine) as session:
            # Written in an order that is neither ascending nor descending, so
            # neither receipt order nor its reverse can pass by accident.
            for run_id in ("r0", "r2"):
                session.add(Experiment(run_id=run_id, source_path=f"/runs/{run_id}"))

        runs = [row["run_id"] for row in client.get("/experiments").json()]
        assert runs == ["r2", "r1", "r0"], runs

    def test_experiments_report_their_metrics_as_well_as_their_answers(self, client):
        """The metrics screen picks runs by metric rows, not answers.

        Picking by answers offered 26 development runs with nothing to show and
        would have hidden the pooled ablation report, which carries the headline
        contrasts and no answers of its own. Both counts come from one query, so
        the answers count must not be multiplied by the metric rows beside it.
        """
        with session_scope(client.engine) as session:
            session.add(Experiment(run_id="slice_dev", source_path="/runs/slice_dev"))

        rows = {row["run_id"]: row for row in client.get("/experiments").json()}
        served = client.get("/evaluation/results", params={"run_id": "r1"}).json()
        assert len(served) > 1
        assert rows["r1"]["results"] == len(served)
        assert rows["r1"]["answers"] == 1
        assert rows["slice_dev"]["answers"] == 0
        assert rows["slice_dev"]["results"] == 0

    def test_gold_evidence_is_ordered_by_page(self, client):
        question_id = self._question_id(client)
        with session_scope(client.engine) as session:
            # Page 5 added after the fixture's page 12: receipt order is 12, 5.
            session.add(
                Evidence(
                    question_id=question_id,
                    group_id="earlier",
                    page=5,
                    printed_page=5,
                    anchors="Trade payables (prior page)",
                )
            )

        pages = [row["page"] for row in client.get("/evidence/Q1").json()]
        assert pages == [5, 12], pages

    def test_metric_rows_come_back_in_a_reproducible_order(self, client):
        with session_scope(client.engine) as session:
            experiment_id = session.execute(
                select(Experiment.id).where(Experiment.run_id == "r1")
            ).scalar_one()
            # Both sort before the fixture's rows, and the second sorts before
            # the first, so neither insertion order nor reversal would pass.
            for metric in ("abstention_rate", "aaa_probe"):
                session.add(
                    EvaluationResult(
                        experiment_id=experiment_id,
                        arm="A",
                        metric=metric,
                        stratum="all",
                        value=0.5,
                    )
                )

        rows = client.get("/evaluation/results").json()
        keys = [(r["run_id"], r["arm"], r["metric"], r["stratum"]) for r in rows]
        assert keys == sorted(keys), keys

    def _question_id(self, client) -> int:
        with session_scope(client.engine) as session:
            return session.execute(
                select(Question.id).where(Question.qid == "Q1")
            ).scalar_one()


class TestLiveAnswersAreStoredAsDemonstrations:
    """D51. A live answer is stored so it can be reopened, and kept out of every
    research count, because a demonstration is not an experiment."""

    RECORD = {
        "arm": "A",
        "answer": "78.89",
        "answer_text": "INR 78.89",
        "answer_canonical": "78.89",
        "answer_source": "natural",
        "abstained": False,
        "agreed": False,
        "verdict": "DISAGREE",
        "risk_score": 0.91,
        "latency_seconds": 166.9,
        "natural": {"available": True, "value": "78.89", "canonical": "78.89",
                    "failure_reason": None},
        "program": {"available": True, "value": "82.27", "canonical": "82.27",
                    "failure_reason": None},
        "verification": {"triggered": True, "available": True,
                         "resolution": "unresolved", "resolved": False, "value": None},
        "explanation": {
            "answer": "INR 78.89", "answer_source": "natural", "risk_score": 0.91,
            "band": "HIGH", "citations": ["E1: p.477, Financial Statements, table 1"],
            "channel_statements": [], "caveats": [], "what_would_change_it": [],
            "reasons": ["agreement: FAILED - the channels produced different figures"],
        },
    }

    def _persist(self, client, question="what was diluted EPS?"):
        with session_scope(client.engine) as session:
            return api_main._persist_live_answer(
                session,
                question=question,
                company="Infosys Limited",
                document_id="d1",
                record=dict(self.RECORD),
                models={"natural_model": "m-a", "program_model": "m-b", "verifier_model": "m-a"},
            )

    def test_a_live_answer_can_be_reopened(self, client):
        answer_id, qid = self._persist(client)
        assert answer_id is not None and qid.startswith("LIVE-")

        body = client.get(f"/answers/{answer_id}").json()
        assert body["question"] == "what was diluted EPS?"
        assert body["answer_id"] == answer_id and body["question_id"] == qid
        assert {c["name"]: c["value"] for c in body["channels"]} == {
            "natural": "78.89",
            "program": "82.27",
        }
        assert body["explanation"]["reasons"]

        listed = client.get("/answers", params={"run_id": api_main.LIVE_RUN_ID}).json()
        assert [row["answer_id"] for row in listed] == [answer_id]
        # A demonstration has no gold answer, so it is never graded.
        assert listed[0]["correct"] is None

    def test_live_answers_never_enter_the_research_counts(self, client):
        before = client.get("/stats").json()
        self._persist(client)
        self._persist(client)
        after = client.get("/stats").json()
        for field in ("questions_total", "questions_validated", "questions_pending",
                      "runs", "answers", "answers_graded", "arms"):
            assert after[field] == before[field], field

    def test_asking_the_same_question_twice_stores_two_answers(self, client):
        """ingest_run upserts by (run, question, arm) and keeps the FIRST row's
        answer fields, so a reused question id would show a stale answer beside
        freshly written channel runs."""
        first, _ = self._persist(client, "same question")
        second, _ = self._persist(client, "same question")
        assert first != second

    def test_recorded_evidence_names_its_filing(self, client):
        answer_id = client.get("/answers").json()[0]["answer_id"]
        evidence = client.get(f"/answers/{answer_id}").json()["evidence"]
        assert evidence and evidence[0]["document_id"] == "d1"
        assert evidence[0]["document"] == "Infosys Limited"

    def test_a_chunk_id_names_its_filing(self):
        assert api_main._document_of_chunk("40f73920a8b153ec:p477:t0:0") == "40f73920a8b153ec"
        assert api_main._document_of_chunk(None) is None
        assert api_main._document_of_chunk("no-separator") is None

    def test_without_the_research_engine_asking_is_a_503_that_says_why(
        self, client, monkeypatch
    ):
        """The API container is web-only. Asking there was a bare 500 from an
        unhandled ModuleNotFoundError, hidden while live QA stayed switched off."""
        monkeypatch.setenv("FINVERIFY_ENABLE_LIVE_QA", "1")

        def engine_missing():
            raise ModuleNotFoundError("No module named 'langgraph'")

        monkeypatch.setattr(api_main, "_live_pipeline_parts", engine_missing)
        response = client.post("/questions/ask", json={"question": "what were trade payables?"})
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "research engine" in detail and "langgraph" in detail
        assert "RUN_LIVE_DEMO.bat" in detail


class TestDependencyFailuresAreReportedNotHung:
    """Found by stopping PostgreSQL and Qdrant under the running stack."""

    def test_a_stopped_database_is_a_503_with_the_reason(self, client):
        from sqlalchemy.exc import OperationalError

        refused = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

        class Unreachable:
            def execute(self, *args, **kwargs):
                raise refused

            def get(self, *args, **kwargs):
                raise refused

        def unreachable_session():
            yield Unreachable()

        client.app.dependency_overrides[api_main.get_session] = unreachable_session
        for path in ("/stats", "/documents", "/answers", "/answers/1"):
            response = client.get(path)
            assert response.status_code == 503, path
            assert "database is unreachable" in response.json()["detail"], path

    def test_an_unreachable_vector_index_is_a_503_not_a_500(self, client, monkeypatch):
        """Refused before any quota is spent, with the cause named."""
        import backend.rag.manifest as manifest

        monkeypatch.setenv("FINVERIFY_ENABLE_LIVE_QA", "1")
        monkeypatch.setattr(
            api_main,
            "_live_pipeline_parts",
            lambda: {"index": object(), "model_name": "m", "channels": {},
                     "providers": {}, "retriever": None},
        )

        def refused(*args, **kwargs):
            raise ConnectionRefusedError("[WinError 10061] connection refused")

        monkeypatch.setattr(manifest, "preflight", refused)
        response = client.post("/questions/ask", json={"question": "what were trade payables?"})
        assert response.status_code == 503
        assert "vector index is unreachable" in response.json()["detail"]

    def test_a_postgres_engine_gives_up_on_a_stalled_connection(self, monkeypatch):
        """Without a connect timeout, a stopped database hung every request."""
        from backend.database import session as session_module

        captured: dict = {}
        monkeypatch.setattr(
            session_module,
            "create_engine",
            lambda url, **kwargs: captured.update(kwargs) or object(),
        )
        session_module.build_engine("postgresql+psycopg://u:p@127.0.0.1:5433/finverify")
        assert captured["connect_args"]["connect_timeout"] > 0
        assert captured["pool_pre_ping"] is True
