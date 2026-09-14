# Demonstration guide

A 5–10 minute walkthrough of FinVerify-AI on real data. Every command here was
run against this repository on 2026-09-01 and its output is what is described;
where a screen shows "not yet measured", that is the honest state of the
project, not a gap in the script.

**What this demo can and cannot show.** The corpus, the retrieval, the dual
channels, the sandbox, the verification record and the evaluation harness are
all live. What it cannot show is a headline accuracy figure, because the
evaluation campaign is four rows into a hundred and eighty and a rate over four
rows is not a rate. Section 6 says what to say about that.

---

## 0. Before you start (2 min, once)

Docker Desktop must be running.

```powershell
cd d:\Claude_Cowork_Code\AS\Case_Study

# Research services: PostgreSQL + Qdrant
docker compose up -d

# Application tier: API on 8000, dashboard on 5173
$env:BUILD_REF = git rev-parse --short HEAD
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

docker compose -f docker-compose.yml -f docker-compose.app.yml ps
```

All four containers should report healthy. If the API is `unhealthy`, check
PostgreSQL and Qdrant first — `/health` reports on its *dependencies*, so the
API process can be perfectly fine while the endpoint says otherwise. That is
deliberate and it is the first thing this demo can show going right.

---

## 1. The system reports what it can reach, not what it was told (1 min)

```powershell
curl http://localhost:8000/health
```

```json
{"status":"ok","database":true,"vector_index":true,
 "live_questions_enabled":false,"build_ref":"<short git sha>", ...}
```

Three things worth pointing at:

- **`vector_index: true` means "can answer", not "responds".** It runs a
  preflight that refuses when the collection's embedding model disagrees with
  the configured one. This field reported `true` for a full day while those two
  disagreed — reachable, non-empty, correctly-dimensioned, and returning the
  wrong page (D33).
- **`build_ref`** is the only field describing the *server*. Everything else
  describes external services, so all of them can be green while the container
  serves code from a fortnight ago.
- **`live_questions_enabled: false`** is not a fault. See section 5.

---

## 2. What is actually in the corpus (1 min)

Open **http://localhost:5173** — the dashboard.

```powershell
curl http://localhost:8000/stats
```

| | |
|---|---:|
| Filings | 5 |
| Pages | 1,664 |
| Tables | 2,780 |
| Extracted figures | 1,793 |
| Questions judged | 192 |
| **Validated gold** | **115** |
| Evidence spans | 198 |

Five Indian annual reports for FY2023-24, chosen for structural diversity — a
bank's balance sheet, a conglomerate's segment reporting and a pharmaceutical
company's inventory notes break extraction in different ways, and five IT
companies would have measured one template five times.

**The number to draw attention to is 115, not 192.** Only validated rows may be
scored against; quoting 192 as the benchmark size overstates the evidence base
by 77 rows. The dashboard breaks the three statuses out for exactly that reason.

Every figure is counted at request time. There is deliberately no dashboard tile
for accuracy, detection AUROC or hallucination rate — see section 6.

---

## 3. Provenance travels with every document (1 min)

Go to **Documents**, then open one.

```powershell
curl http://localhost:8000/documents
```

Each filing carries its company, fiscal year, page count, source URL, retrieval
date and **SHA-256**. The PDFs are not redistributed; the hash is what lets
someone confirm that the file they fetch from the publisher's investor-relations
site is the file this evaluation used.

---

## 4. A verified answer, end to end (3 min) — the centrepiece

Go to **Verification**. The table lists every recorded answer with its question,
arm, answer, verdict and risk. Pick the Infosys row and press **Inspect**.

```powershell
curl "http://localhost:8000/answers?limit=4"
```

The question is:

> For Infosys Limited, as reported for the year ended March 31, 2024, what were
> cost of technical sub-contractors?

All four arms answered **INR 12232 crore**, verdict `AGREE`, risk `0.0`, and the
gold answer agrees. What makes that worth showing is *how* the four differ:

| arm | what it is |
|---|---|
| **A** | the full system — both channels, deterministic verifier, arbiter |
| **B5** | self-consistency: five samples of one model in one modality |
| **G** | A minus the deterministic verifier (tests H3) |
| **H** | same model on both channels (tests H4) |

The baseline run `campaign_20260901T071345Z` adds **B3** — the program channel
alone, no natural-language reasoning at all — and it independently produces
`INR 12232 crore` for the same question. A figure reached by a generated Python
program and by natural-language reasoning over the same evidence, agreeing, is
the mechanism this project is built to test.

Point at the **Arbiter** panel too. It says "Not triggered", because the arbiter
fires only on disagreement — its *non*-firing is what the trigger rate is made
of, so it is shown rather than hidden. When it does fire and cannot settle the
question, it abstains, and the UI presents that as a first-class outcome: a
refusal to guess is correct behaviour, and rendering it as "—" would show a
deliberate decision as missing data.

**Evidence.** `GET /evidence/{question_id}` returns the gold spans — document,
page, and the anchor text — so any answer can be traced to the page it came
from.

---

## 5. Ask a live question (5 min)

Start the demo with **`RUN_LIVE_DEMO.bat`** (double-click it, or run `.\RUN_LIVE_DEMO.bat` from PowerShell), not with `docker compose` alone. Typed by its bare name in PowerShell it is "not recognized": PowerShell does not run a file from the current directory without `.\`. The
API container is web-only by design and cannot run the pipeline; the script runs
the same API code from `.venv` with live QA enabled and points the dashboard at it
(D51). Opened without it, **Financial QA** says asking is switched off and names
the script. That is the intended default (D30), not a fault.

On **Financial QA**, pick a filing and ask something that filing can answer - a
balance-sheet or P&L line in a stated year. Expect **a few minutes**: about three
were measured on this machine. Channel B generates a Python program, which is
AST-validated against an allowlist and then executed in a fresh container with
CPU, memory and timeout limits and no network. If Docker is down, program
execution is **blocked**; it does not quietly fall back to running on the host.

A question that shows the method working, run end to end on 2026-09-13 against
the HDFC Bank filing:

> For HDFC Bank Limited, as reported for the year ended March 31, 2023, what were diluted earnings per share (diluted earnings per share as reported in the consolidated financial statements)?

The gold answer is **82.27**. Channel A answered **78.89**; the executed program
answered **82.27**; the consistency engine returned **DISAGREE**; the arbiter
declined to choose, naming both figures and the evidence each came from; and the
answer was flagged **HIGH risk**. The system returned a wrong figure and said so,
which is the research claim visible in a single question.

**The same question does not always go the same way, and that is worth showing
too.** Asked again through the dashboard the same day, Channel B's generated
program had a syntax error and was rejected by the AST allowlist before it could
run. With only one channel answering, the verdict was **UNCERTAIN** and the risk
**0.511**: the system refused to execute malformed code and reported that it could
not corroborate, rather than presenting the single remaining answer as checked.
Across both campaign splits, generated programs failed validation or execution on
about 15% of calls (0.156 validation, 0.148 test), so a presenter should expect
either outcome. Use **Open it in the
full verification view** to show the saved result with both channels and the
arbiter's reasoning. It stays on Verification under the `live-qa` run after you
leave the page.

Each question spends free-tier LLM quota. Live answers are demonstrations: stored
so they can be reopened, never graded, and never counted in the dashboard or the
research figures.

---

## 6. What has been measured, and what has not (2 min)

This is the part that makes the demo credible rather than impressive, so do not
skip it.

**Measured, with numbers:**

- Retrieval, on validation-only gold (RX-054, RX-054b). Planned retrieval finds the
  gold evidence in the top 10 for 0.250 of 32 questions against 0.062 unplanned.
  The right chunk is in the top 10 for 0.125 of evidence groups, within rank 300
  for 0.719, and not ranked within 300 for 0.281. The earlier RX-034 figures ran on
  gold that included sealed test questions (RX-053) and are withdrawn.
- **A result that changed on clean data.** RX-035 had rejected a cross-encoder
  reranker at 0.240 against 0.280 on that mixed gold. Re-run on validation-only gold
  it scored **0.156 against 0.125**, net one group of 32. It is still not adopted:
  no measurable benefit, at 24.9 s per evidence group on this CPU.

**Measured, on three complete campaigns** (validation 315 rows, test 427,
oracle 45):

- **Detection AUROC 0.885 [0.801, 0.954]** for the full system on held-out data,
  against 0.907 on validation with nothing tuned between them — and it beats
  self-consistency at **43% of its token cost**, on both splits.
- **Numerical accuracy 0.361** on test, 0.400 on validation. Low, and the oracle
  arm says why: handed the chunks containing its gold evidence the same system is
  right **35 times in 37 (0.946)**. Retrieval is the entire story.
- **No hypothesis supported** after Holm correction, and H2 — the mechanism check
  — failed in the predicted direction's opposite. The defensible claim is a
  working verifier, not a validated mechanism.

**Still not measured:**

- Brier score and ECE. The risk score reports `calibrated=False` and will until
  it is fitted; AUROC is rank-based and valid uncalibrated, but those two are
  not, so they are absent rather than shown provisionally.
- The arbiter's resolution accuracy — it needs gold that pins the metric
  definition.

The dashboard says "Not yet measured" in words wherever a figure does not exist,
rather than showing a zero, because a zero in a metric tile reads as a
measurement.

**The honest limitation to state out loud:** of the 45 validation questions,
**one is multi-hop**. Whatever this system turns out to do, it will have been
tested almost entirely on single-figure lookups, and no claim about complex
multi-step financial reasoning is supported by it.

---

## 7. If someone asks "how do you know it works?" (1 min)

```powershell
.\.venv\Scripts\python.exe -m pytest        # 1464 passed, 1 skipped, ~8 min
```

Better answer than the test count: `EXPERIMENTS.md` records every experiment from RX-001 to RX-054,
including the failures and the ones that were my own error. Most defects on this
project were found by *running* it while the suite was green — a correctness
predicate that rejected the most complete answer a model can give on 44 of 115
gold answers (RX-031); a campaign reporting `completed 180, failed 0` with
Channel A dead for 166 of them (RX-033); a refusal by the program channel
recorded and then never read, which only became visible on the first arm that
had no natural channel (RX-036).

The test set — 61 further gold answers — is sealed behind an access log. It was
evaluated once, on 2026-09-05, after one voided and one superseded attempt, and has
not been used since. The freeze tag that evaluation named had been voided (D48), so
the held-out figures are single-use but not pre-registered.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| API `unhealthy` | PostgreSQL or Qdrant is down. `docker compose up -d`. |
| `connection refused` on 5432 | PostgreSQL publishes **5433** on this host (D31). |
| `vector_index: false` | `EMBEDDING_MODEL` and `QDRANT_COLLECTION` disagree. The preflight is refusing on purpose. |
| Dashboard loads, tables empty | Nothing ingested. `scripts/ingest_to_database.py --all`. |
| Asking is switched off, or live QA returns 503 | The API container cannot run the pipeline (D51). Start with `RUN_LIVE_DEMO.bat`. Section 5. |
