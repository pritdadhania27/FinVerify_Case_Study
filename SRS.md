# Software Requirements Specification

This is a research system. Its requirements are therefore unusual in one
respect: **the most important ones are constraints on what the system may
claim**, not on what it may do. A feature that works but overstates its own
reliability has failed a requirement here.

---

## 1. Purpose and scope

FinVerify-AI answers numerical questions over financial reports through two
independent reasoning channels and measures whether their disagreement detects
numerical hallucination.

**In scope:** the research engine, its evaluation, and a thin product tier over
it (API, UI) for inspection and demonstration.

**Out of scope:** production deployment, multi-tenancy, authentication, and
anything whose absence does not affect whether the research question can be
answered.

---

## 2. Actors

| Actor | Uses the system to |
|---|---|
| Researcher | run campaigns, read results, inspect failures |
| Validator | judge candidate gold answers against source PDFs |
| Reader / reviewer | trace a published number back to a run artifact |
| Demonstration user | ask an ad-hoc question through the UI |

There is no end-user actor with a production workload. The `users` table exists
because spec §26 names it, and is deliberately unwired.

---

## 3. Functional requirements

Traced to spec modules and to the code that satisfies them.

| ID | Requirement | Module | Implementation |
|---|---|---|---|
| F1 | Acquire and register source documents with provenance and content hash | 2 | `documents/acquisition.py` |
| F2 | Extract text, tables, sections and page mapping from PDFs | 3 | `documents/extraction.py` |
| F3 | Read scanned pages via OCR, labelled as such | 3 | `documents/ocr.py` |
| F4 | Structure extracted figures as facts with full provenance | 4 | `documents/facts.py` |
| F5 | Normalise scale, sign, currency and percentage conventions traceably | 5 | `core/financial_value.py` |
| F6 | Retrieve evidence by hybrid semantic + keyword search with metadata filters | 6, 19 | `retrieval/`, `rag/indexing.py` |
| F7 | Parse a question into an executable specification | 7 | `agents/question_understanding.py` |
| F8 | Answer via natural-language reasoning over retrieved evidence | 8 | `agents/natural_channel.py` |
| F9 | Answer via generated code executed in isolation | 9 | `agents/program_channel.py`, `services/sandbox.py` |
| F10 | Verify arithmetic without a model in the path | 10 | `verification/deterministic*.py` |
| F11 | Compare channel answers and emit a verdict and a continuous score | 11, 12 | `verification/consistency.py` |
| F12 | Adjudicate disagreements, or abstain | 13 | `agents/verification_agent.py` |
| F13 | Classify errors on provenance and kind independently | 14 | `verification/taxonomy.py` |
| F14 | Emit a continuous risk score | 15 | `verification/confidence.py` |
| F15 | Explain an answer from recorded state | 16 | `verification/explanation.py` |
| F16 | Orchestrate the pipeline with branching and per-node state | 17 | `agents/orchestrator.py` |
| F17 | Persist and query runs, facts and results | 18 | `database/` |
| F18 | Serve documents, questions, answers and results over HTTP | 20 | `api/` |
| F19 | Present dashboard, documents, QA, verification and research views | 21 | `frontend/` |
| F20 | Compute QA, retrieval, detection, efficiency and statistical metrics | 22 | `evaluation/metrics/` |
| F21 | Run baselines and ablation arms through one pipeline | 23, 24 | `evaluation/arms.py` |
| F22 | Analyse errors, stratified by provenance | 25 | `evaluation/error_analysis.py` |
| F23 | Build, validate, split and seal a gold dataset | 26 | `evaluation/dataset.py`, `validation.py` |
| F24 | Produce a case study over a representative corpus | 27 | `evaluation/case_study.py` |
| F25 | Run a resumable, budgeted campaign with append-only artifacts | 28 | `experiments/campaign.py` |

---

## 4. Non-functional requirements

### NF1 — Reproducibility

A published number must be traceable to a committed file. Run artifacts are
append-only and committed (D4); the database is a projection (D28). Configuration
is recorded per run, including the **resolved** model id returned by the
provider, so a silent checkpoint substitution is visible afterwards.

**Not bit-reproducible.** Temperature is pinned at 0 (D7a) but these are hosted
models; the system is configuration-reproducible, and `EVALUATION.md` §11 says so.

### NF2 — Isolation

Generated code is validated against an AST allowlist **before** execution and
then run in a container with no network, a read-only filesystem, a non-root uid,
memory and CPU limits, and a timeout with a kill and cleanup.

**There is no host-execution fallback.** If Docker is unavailable, execution is
`BLOCKED`. Container escape via a Docker or kernel vulnerability is outside this
project's control and is **not** claimed to be mitigated.

### NF3 — Throughput

Bounded by free-tier request quota, not by compute or money (D14). The full
campaign is **4,350 requests** at the ceiling. The campaign runner is therefore
resumable, keyed on (arm, question), and question-major so an interruption leaves
a balanced prefix.

### NF4 — Latency

Not optimised. Measured and reported (p50/p95) because spec §33 asks for it. The
pipeline is sequential because the free-tier limiter is a shared process-wide
counter that concurrent calls would race; a parallel deployment would be faster
than the reported figures, and the write-up says so rather than implying it.

### NF5 — Security

No secrets in source. `.env` is gitignored; `alembic.ini` ships with an empty
URL; `session.py` raises rather than defaulting. The expensive endpoint is off by
default (D30). The API has **no TLS, authentication or rate limiting** and is not
suitable for network exposure.

### NF6 — Data protection

The test split is sealed behind an environment flag, a required reason, and an
append-only access log. Gold labels are never altered to improve a score, and
every correction records what it replaced.

---

## 5. Constraints

| Constraint | Consequence |
|---|---|
| Free-tier LLM providers only | quota is the binding limit; rate limiter persists daily counts |
| Channels on different vendors (Groq / NVIDIA NIM) | independence is **cross-vendor** (D21, RX-014). NVIDIA's daily limit is unobserved and its latency is not reportable |
| CPU-only host | embedding throughput constrains corpus size and latency |
| One human validator | intra-annotator consistency only; no inter-annotator agreement |
| Third-party filings | PDFs not redistributed; provenance and hash tracked instead |
| Windows host | Tesseract not on PATH; PostgreSQL port collision (D31, D32) |

---

## 6. The requirements that constrain claims

These are the ones a reviewer should check, and they are enforced in code rather
than by discipline.

| ID | Requirement | Enforced by |
|---|---|---|
| C1 | The program channel must never receive Channel A's output | function signature + two tests |
| C2 | A candidate answer may never be used as gold | `build_split` returns VALIDATED only |
| C3 | The test split may not be read without an explicit flag and a stated reason, and access is logged | `build_split`, `test_set_access.log` |
| C4 | An undefined metric must not be reported as a value | `roc_auc` returns `None`, never 0.5 |
| C5 | An arm with no detector must not appear in the detection table | `provides_detection_score` |
| C6 | An unchecked condition must not be reported as checked | tri-state provenance in the taxonomy |
| C7 | The operating threshold may not be selected on the data it is scored on | `analyse_campaign.py` refuses |
| C8 | An uncalibrated score must not be reported as a probability | `calibrated=False`, surfaced through API and UI |
| C9 | A cost figure may not be invented from an unestablished rate | `equivalent_cost_usd` returns `None` |
| C10 | An explanation may not be model-generated | test asserts no provider call |
| C11 | Quota exhaustion must stop a campaign, not become an abstention | orchestrator re-raises |
| C12 | Failed and abandoned experiments must be recorded | `EXPERIMENTS.md`, append-only runs |

---

## 7. Acceptance

The system is accepted when the research question can be **answered or
falsified**, not when the modules run.

That requires, in order: validated gold answers → a campaign across arms →
computed metrics with intervals → hypothesis verdicts under family-wise
correction → an error analysis that explains the failures.

**None of that has happened yet.** Every module is implemented and tested; no
result exists. `PROJECT_STATUS.md` states this as the headline rather than as a
footnote.

---

*Architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md) · Methodology:
[`EVALUATION.md`](EVALUATION.md) · Decisions: [`DECISIONS.md`](DECISIONS.md) ·
State: [`PROJECT_STATUS.md`](PROJECT_STATUS.md)*
