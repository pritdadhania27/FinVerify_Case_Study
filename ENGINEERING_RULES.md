# ENGINEERING_RULES.md — Operating Instructions

Persistent instructions for Claude Code sessions on this project.
**Full specification:** `FinVerify_AI_Full_Project_Execution_Specification.md`.
This file is the operating manual, not a copy of the spec.

---

## What this project is

FinVerify-AI: a **research system**, not a chatbot. It answers numerical
questions over financial reports through two independent reasoning channels and
measures whether their disagreement detects numerical hallucination.

The single question everything serves:

> Can independent dual-channel reasoning and consistency verification reliably
> detect numerical hallucinations in agentic financial document QA?

If a change does not help answer that, it is not a priority. Research validity
first, production quality second, visual polish third.

## Read these first

`PROJECT_STATUS.md` (current state) → `TODO.md` (next actions) →
`DECISIONS.md` (why things are the way they are) → the module's own doc.

Do not re-derive decisions already recorded in `DECISIONS.md`. If you disagree
with one, argue against the recorded reasoning rather than silently doing
something else.

## Environment

```powershell
# Python 3.12 venv - NOT the host's 3.14 (no pip, uncertain wheels)
.\.venv\Scripts\python.exe

# Environment gate - must exit 0 before implementation work
.\.venv\Scripts\python.exe scripts\verify_environment.py

# Services (PostgreSQL + Qdrant)
docker compose up -d
docker compose ps

# Tests
.\.venv\Scripts\python.exe -m pytest
```

Windows host. PowerShell and Bash tools both available — each needs its own
syntax. Docker Desktop must be running for the sandbox, Qdrant, and PostgreSQL.

## Hard rules

**Never fabricate.** No invented results, metrics, citations, datasets, gold
answers, or "successful" installs. If something is untested, say untested. If
blocked, say blocked. A module is `COMPLETE` only against the spec §43 checklist;
otherwise it is `PARTIAL` or `BLOCKED`. This is not stylistic — fabricated
evidence in a research system invalidates the entire project.

**Never verify a citation from memory.** Retrieve it. `LITERATURE_REVIEW.md`
records a figure that was *not* cited precisely because it could not be confirmed
against the source — match that standard.

**Never touch the test set** without `FINVERIFY_ALLOW_TEST=1`. Access is logged.
The test set is evaluated once per frozen methodology version.
A split label on any derived artifact (retrieval gold, a filtered set) is
**computed from the source questions, never declared**: a hand-written
`"split": "validation"` let 43 of 76 sealed test questions through a gate that
read the label (RX-053). A guard must read something derived from the data.

**Never alter gold labels to improve a score.** Ever.

**Never execute generated code outside the Docker sandbox.** Untrusted code gets
AST allowlist validation *then* a container with CPU/memory/timeout limits and no
network. If Docker is down, program execution is BLOCKED — it does not silently
fall back to running on the host.

**Never commit secrets.** `.env` is gitignored. Check `git diff --cached` before
committing.

**Preserve raw results.** `experiments/runs/` is append-only and committed. Failed
experiments stay, and are recorded in `EXPERIMENTS.md`.

## Dual-channel independence — the thing most easily broken

The research contribution dies if Channel A and Channel B become two wrappers
over one reasoning process. When touching either channel:

- The program channel must **never** receive Channel A's answer, reasoning, or
  confidence. A test enforces this — if it fails, that is a research-validity
  bug, not a test to update.
- Channels use different models by default (`.env.example`). Same-model is an
  ablation arm, not a default.
- Prompts stay structurally disjoint.

## LLM usage — free-tier providers (D8a)

Provider-agnostic layer in `backend/services/llm/`. **Groq + Google Gemini**,
both free, no credit card. One OpenAI-compatible adapter serves both.

```powershell
# List models each provider ACTUALLY serves right now (lineups rotate)
.\.venv\Scripts\python.exe scripts\verify_llm_providers.py --list

# Real health check on every channel binding - makes actual API calls
.\.venv\Scripts\python.exe scripts\verify_llm_providers.py

# Free-tier quota consumed today
.\.venv\Scripts\python.exe scripts\verify_llm_providers.py --quota
```

- Bindings are `provider/model` in one variable, e.g.
  `NATURAL_CHANNEL_MODEL=gemini/gemini-2.5-flash`.
- **Never hard-code a model id.** Free providers retire ids without notice.
  Discover with `--list`; the resolved id is recorded per call.
- Temperature **is** settable on these models, so runs are pinned at 0 (D7a).
- Rate limits are the binding constraint, not money (D14). The limiter persists
  daily counts to disk — do not bypass it, and do not add retry loops on top of
  the adapter's, which would burn a day's quota.
- Auth and model-not-found errors are never retried, by design.

**Cost reporting:** `cost_usd` is 0.00 on the free tier;
`equivalent_cost_usd` (published paid rates) is what transfers. Never report the
free-tier zero as evidence the method is cheap.

## Module workflow (spec §8 of the master prompt)

Understand → Inspect → Plan → Implement → Test → Validate → Document →
Update state → Commit → Next.

Every module: unit tests, failure-case tests (not just happy path), docs updated,
`PROJECT_STATUS.md` + `TODO.md` + `CHANGELOG.md` updated, git commit, and a
status report in spec §38 format.

Failure cases that must be covered where applicable: malformed PDF, scanned PDF,
missing table, ambiguous question, wrong unit, negative values, zero denominator,
conflicting evidence, missing evidence, model timeout, code execution failure.

## Domain traps that cause silent wrong answers

- **Scale.** ₹ crore (10⁷) vs lakh (10⁵) vs million vs billion, often mixed
  within one document. This is the highest-frequency source of plausible-looking
  10×–100× errors.
- **Parenthesised negatives.** `(1,234)` is −1234.
- **Footnote markers glued to numerals**, thousands separators, currency symbols.
- **Restated prior-year figures** — the same metric for the same year can differ
  between two reports. Provenance must record which document a number came from.

## Build order

Spec §42, and every phase of it has been built. Module status is judged against
the §43 checklist in `PROJECT_STATUS.md` — COMPLETE, PARTIAL or BLOCKED — and that
file, not this line, is the current state. Two audits (RX-052, 2026-09-12/13) found
status rows drifting in the flattering direction; re-derive a figure from the
artifacts before repeating it.

## Running the application

```powershell
# Read-only screens: database, index, API container, dashboard
docker compose up -d
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

# Demo WITH live questions
RUN_LIVE_DEMO.bat
```

The API container is **web-only by design** and cannot run the pipeline; asking a
question there returns 503 naming the missing engine. `RUN_LIVE_DEMO.bat` runs the
same API from `.venv` on port 8001 with `FINVERIFY_ENABLE_LIVE_QA=1` and points the
dashboard at it (D51). Live answers are stored under run `live-qa`, never graded,
and excluded from `/stats` and the research views. Verify a deployment with
`scripts/verify_deployed_stack.py`.

## Conventions

- Python 3.12, ruff line length 100, type hints on public functions.
- Pydantic models for structured LLM output and API contracts.
- Provenance travels with data: every extracted value keeps document, page,
  section, table, row/column.
- Comments explain *why*, not *what*.
