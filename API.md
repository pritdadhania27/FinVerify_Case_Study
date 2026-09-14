# API

FastAPI. Contracts are generated from the Pydantic models in
`backend/api/schemas.py`, so the live schema at `/openapi.json` is authoritative
and this document explains the parts a schema cannot.

```bash
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8000
# /docs   Swagger UI      /redoc   ReDoc      /openapi.json   the contract
```

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | measured, not declared — including whether the index can actually answer |
| `GET` | `/documents` | the registered corpus with provenance |
| `GET` | `/documents/{document_id}` | |
| `POST` | `/documents/upload` | **202**, not 201 — see below |
| `POST` | `/questions/ask` | **disabled by default** — see below |
| `GET` | `/answers/{answer_id}` | from ingested run artifacts |
| `GET` | `/verification/{answer_id}` | including `triggered: false` |
| `GET` | `/evidence/{qid}` | **gold** spans, not what retrieval returned |
| `GET` | `/experiments` | runs, with their source path |
| `GET` | `/evaluation/results` | metric values; filterable |
| `GET` | `/stats` | corpus and campaign counts, counted per request and never cached |
| `GET` | `/answers` | recorded answers, filterable by arm, run and question; the list `/answers/{id}` reads from |
| `GET` | `/arms` | every configuration a campaign can run, and which field each removes from arm A |

---

## The three things a schema will not tell you

### 1. `POST /questions/ask` is off unless you switch it on

Every request runs both reasoning channels and can trigger the arbiter, spending
free-tier quota the evaluation campaign depends on (D14, D30). Quota — not money
— is this project's binding constraint, and an open endpoint is how a day of
campaign budget goes to a crawler or a refresh loop.

```
503  Live question answering is disabled. Each request runs both reasoning
     channels and can trigger the arbiter, spending free-tier quota that the
     evaluation campaign depends on (D14). Set FINVERIFY_ENABLE_LIVE_QA=1 to
     enable it deliberately.
```

The refusal happens **before a provider is constructed**, and returns the reason
rather than a stub answer. Enable it deliberately, from the project environment:

```bash
FINVERIFY_ENABLE_LIVE_QA=1 .venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8001 --env-file .env
```

Not on the API container, which is web-only: there it returns **503 naming the
missing research engine** (D51). `RUN_LIVE_DEMO.bat` does all of this and points the
dashboard at the live API.

Send `document_id` (or `company`) with the question. Retrieval is scoped by company
(RX-012), and without one a question searches every filing at once. A successful
response carries `answer_id` and `question_id`: the answer is stored under run
`live-qa`, so `GET /answers/{answer_id}` reopens it. Storage failure does not lose
the answer - it is still returned, with `answer_id: null` and a note. Every evidence
item names its filing in `document`.

The `arm` field accepts any name from `evaluation/arms.py`, so a client can
reproduce a baseline through the same code path the campaign uses.

### 2. Upload returns 202 because the document is not searchable yet

The file is stored and hashed. It is **not** extracted, chunked, embedded or
indexed — on this hardware that is minutes to hours — so the response names the
step that remains:

```json
{
  "document_id": "f356fc75d6d63fa3",
  "sha256": "f356fc75…",
  "duplicate": false,
  "indexed": false,
  "next_step": "run scripts/index_corpus.py to extract, chunk, embed and index. Until then this document is registered but not searchable."
}
```

A 201 would tell a client the document is ready when it is not.

**Duplicate detection is on the bytes.** The same file uploaded under two names
is one document; a different file under the same name is a different one.

### 3. `null` means undefined, and you must not coalesce it

Two fields are nullable on purpose, and treating either as zero would put a
fiction on screen:

- **`risk_score`** — arms B1–B4 have no detector. `null` means *this
  configuration does not measure that*; `0.5` would read as *the detector was no
  better than chance*.
- **`value`** on `/evaluation/results` — an AUROC over a stratum containing no
  incorrect answers is **undefined**, not zero. The `note` field carries why.

`calibrated` on an answer is likewise meaningful: the risk score ranks answers,
it is not a probability, and a UI that renders it as one is misrepresenting it.

---

## `/health` reports reachability

Each field is checked by **making the call**, not by reading an environment
variable:

```json
{
  "status": "ok",
  "database": true,
  "vector_index": true,
  "live_questions_enabled": false,
  "note": "measured, not declared: each field was checked by making the call…"
}
```

This is a direct consequence of an afternoon lost to `docker compose ps`
reporting PostgreSQL healthy while every host connection reached a *different*
server on the same port (D31). A health endpoint that reports configuration
would have agreed with `docker compose ps` and been just as wrong.

When a check fails, `note` carries the exception. An earlier version swallowed
every failure into `false`, so a missing Python dependency in the API image was
reported as *"the vector index is unreachable"* — a deployment problem
misattributed to a working service.

**`vector_index` means "can answer", not "responds".** A reachable, non-empty,
correctly-dimensioned collection is still useless if a different embedding model
built it, and this endpoint reported `true` for a full day while exactly that
was true (D33). It now runs the retrieval preflight, so:

```json
{
  "vector_index": false,
  "note": "… Vector index check failed with embedding model mismatch on collection 'finverify_chunks'."
}
```

That output is not hypothetical — it is what the endpoint returned when the
misconfiguration was deliberately restored to confirm the check can fail. A
health check nobody has seen go red has not been tested.

---

## What the API deliberately does not do

- **It writes no run artifacts.** An API request is a demonstration, not an
  experiment (D4). Mixing ad-hoc questions into `experiments/runs/` would put
  unlabelled, unplanned rows in the evidence base the paper is drawn from. A test
  enforces this.
- **It cannot reach the test split.** `/questions/ask` answers free-text
  questions against the index and does not read the dataset at all.
- **It has no authentication, TLS, or rate limiting.** It runs on localhost for a
  research project. Exposing it to a network needs all three; the `users` table
  exists as a place to put an identity, not because anything uses one.

---

*Deployment: [`DEPLOYMENT.md`](DEPLOYMENT.md) · Schema:
[`DATABASE.md`](DATABASE.md) · Architecture:
[`ARCHITECTURE.md`](ARCHITECTURE.md)*
