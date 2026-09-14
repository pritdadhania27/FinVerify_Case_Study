# Database

PostgreSQL 16, SQLAlchemy 2.0, Alembic. **Sixteen entities, against the seventeen
spec §26 names.** The one not implemented is `reports`, and that is a decision
rather than an omission: §26 lists `documents` and `reports` separately, but in
this corpus one document *is* one annual report — five filings, five reports,
a strict 1:1 — so a `reports` table would hold the same five rows keyed by the
same five ids and add a join to every provenance query. The fiscal year and
company that would distinguish a report from its file live on `Document`
already. If the corpus ever holds two reports in one PDF, or one report split
across PDFs, this stops being true and the table has to appear.

This line previously read "All sixteen entities spec §26 names", which was wrong
twice: §26 names seventeen, and sixteen are built.

**The database is a projection of the run artifacts, not their replacement.**
That is decision D28, and it inverts the usual precedence, so it shapes every
choice below.

---

## Why the files stay authoritative

D4 made run artifacts files. `experiments/runs/` is append-only and committed, so
a number in the paper traces to a file in a git history. Making PostgreSQL
authoritative would put the evidence behind a service that has to be running, on
a machine that has to exist, in a state nobody can diff.

Losing the database costs a re-ingest. Losing the artifacts would cost the
results.

The database earns its place by making cross-cutting questions cheap. This is one
statement here and a painful loop over JSONL otherwise:

```sql
-- the method's blind spot, every arm at once
SELECT q.qid, a.arm, a.answer_text, q.gold_text
FROM answers a JOIN questions q ON q.id = a.question_id
WHERE a.agreed AND a.correct IS FALSE;
```

---

## Setup

```bash
docker compose up -d postgres
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe scripts/ingest_to_database.py --all
```

`DATABASE_URL` comes from `.env`. **There is no default anywhere in source** —
`alembic.ini` ships with `sqlalchemy.url` empty and `session.py` raises rather
than falling back. A connection string in a committed file is a credential in the
repository even when it is only a local one, and that is precisely the kind that
survives into a deployment because it happens to work.

The project's PostgreSQL listens on **5433**, not 5432 (D31). Two servers were
listening on 5432 on this machine and the host was authenticating against the
wrong one.

---

## Entities

| Group | Tables |
|---|---|
| Corpus | `companies`, `documents`, `pages`, `sections`, `tables` |
| Knowledge | `financial_facts` |
| Dataset | `questions`, `evidence` |
| Runs | `experiments`, `answers`, `reasoning_runs`, `program_runs`, `verification_runs` |
| Analysis | `hallucination_events`, `evaluation_results` |
| Access | `users` |

`users` exists because spec §26 names it. This project has no multi-user story —
the research runs as one operator on one machine — and it is deliberately not
wired into any research path. An evaluation whose results depended on who was
logged in would be a different kind of project. **It is the only table here that
is empty by design**, and it says so rather than leaving a reader to guess.

### `pages`, `sections`, `tables` — modelled, then actually populated

These three carried a schema and no ingest path for the whole build, so the
database held zero of them. That is worse than not modelling them: an empty
table with no note reads as a bug, and `tables.parsing_accuracy` — the one
column here with real analytical value — had nowhere to live, so extraction
quality survived only as console output nobody kept.

`ingest_structure` now fills them from the chunk cache. Current corpus:

| | rows |
|---|---:|
| `pages` | 1,664 |
| `sections` | 537 |
| `tables` | 2,780 |

1,664 against the registry's 1,665 total pages is not a rounding artefact —
it is an **extraction-coverage figure**. One page produced no extractable
content at all, and the gap is the only place that fact is recorded.

What the table rows make askable for the first time:

```sql
SELECT c.name,
       count(*)                                          AS tables,
       round(avg(t.parsing_accuracy)::numeric, 1)        AS mean_accuracy,
       count(*) FILTER (WHERE t.parsing_accuracy < 80)   AS below_80
FROM companies c
JOIN documents d ON d.company_id = c.id
JOIN tables    t ON t.document_id = d.id
GROUP BY 1 ORDER BY below_80 DESC;
```

| Company | Tables | Mean accuracy | Below 80 | Below 50 |
|---|---:|---:|---:|---:|
| HDFC Bank | 918 | 96.2 | 49 | 9 |
| Tata Motors | 960 | 96.8 | 29 | 3 |
| Sun Pharmaceutical | 486 | 95.8 | 25 | 2 |
| Infosys | 129 | 97.5 | 3 | 0 |
| Reliance | 287 | 98.2 | 0 | 0 |

The bank has the worst tail, which is the expected shape: ruled, densely-nested
schedules are where Camelot's `stream` mode is weakest, and it is the case for
revisiting D10a **with a measurement** if the first campaign shows HDFC
underperforming.

**`pages.text` is deliberately NULL.** Chunks carry chunk text, not page text,
and reassembling a page by concatenating overlapping windows would produce
something that looks like the page and is not it. A NULL says *not captured*; a
reconstruction would say *this is the page* and be wrong.

---

## Six decisions in the schema

### 1. Money is `Numeric`, never `Float`

A float round-trip introduces errors that look exactly like the small arithmetic
mistakes this project is built to detect. A detector cannot be allowed to trip
over its own storage layer.

### 2. `answers.risk_score` is nullable

Arms B1–B4 have no detector. A `DEFAULT 0.5` would enter them in the detection
table as though they had been measured.

### 3. `answers.correct` is copied, never recomputed

Grading happens once, in `evaluation/error_analysis.py`, and the verdict is
carried in. Two graders eventually disagree, and the one in the database is the
one nobody checks. `NULL` means **ungraded**, which is not the same as incorrect.

### 4. `financial_facts.year_source` is stored

A figure whose year came from the document's fiscal year is a weaker claim than
one whose year came from the table's own column header. A query that needs the
stronger claim must be able to ask for it.

### 5. `hallucination_events` has two axes, not one

`provenance` and `kind` are separate columns because D12 stratifies by provenance
while Module 25 analyses by kind, and one enum cannot serve both (D25).
`label_confidence` is stored so a SUGGESTED label is never counted as a
MECHANICAL one in an aggregate.

### 6. `evaluation_results` is long and thin

One row per (arm, metric, stratum) rather than a wide table with a column per
metric. The metric set will grow, and a schema migration per new metric is how a
reporting table stops being updated. `value IS NULL` means the metric was
**undefined** — an AUROC on a stratum with no errors — and is deliberately
distinguishable from zero.

---

## Ingestion

One direction only: **files → database**. Nothing writes back to
`experiments/runs/`, and nothing in the research path reads from PostgreSQL.

```bash
scripts/ingest_to_database.py --all              # registry, facts, dataset, every run
scripts/ingest_to_database.py --run <run_id>     # one campaign
```

**Idempotent on natural keys** — `document_id`, `qid`, `run_id`, `(experiment,
question, arm)` — so an ingest interrupted when a laptop sleeps is safe to
repeat rather than needing repair. Child rows (`reasoning_runs`, `program_runs`,
`verification_runs`, `hallucination_events`) are **replaced**, not appended, or a
re-ingest would silently multiply them.

A run row for a question the gold set does not contain is **skipped**. Creating a
stub would put a row in the gold table that no human ever validated.

`questions.validation_status` is carried across so a direct SQL user is subject
to the same rule as the pipeline: a PENDING question is a candidate, and treating
it as gold is a mistake the schema makes visible.

---

## Migrations

```bash
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "what changed"
.venv/Scripts/python.exe -m alembic upgrade head
```

`env.py` loads `.env` and sets the URL from `DATABASE_URL`, so `alembic upgrade
head` uses the same database as everything else rather than requiring the
operator to export it again and get it subtly different. `compare_type=True` is
on, so a column whose type changed is caught by autogenerate instead of drifting
from the model.

Generated migration files are excluded from ruff. Reformatting them to the
project style means the next autogenerate produces a file that fails lint again;
they are still read and reviewed, just not formatted.

---

*Decisions: [`DECISIONS.md`](DECISIONS.md) D4, D28, D31 · Deployment:
[`DEPLOYMENT.md`](DEPLOYMENT.md) · API: [`API.md`](API.md)*
