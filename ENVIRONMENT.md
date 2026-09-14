# Environment Readiness Record

Satisfies the spec's §3 pre-start gate. Every row below came from real command
output on this machine, not from assumption. Regenerate with:

```
.venv/Scripts/python scripts/verify_environment.py
```

**Audited:** 2026-08-23 · **Host:** Windows 11 Pro 10.0.26200, 8 CPUs, no NVIDIA GPU
**Status:** PARTIAL — every component verified except an LLM provider key.
One blocker remains: **no free-tier LLM key set** (`GROQ_API_KEY` and/or
`GEMINI_API_KEY`). Ghostscript is absent but **no longer needed** (decision
D10a).

---

## Runtime

| Component | Status | Version / detail |
|---|---|---|
| Python (project venv) | VERIFIED | 3.12.10 — `.venv/Scripts/python.exe` |
| pip | VERIFIED | 26.2.1 |
| Node.js / npm | VERIFIED | 24.19.0 / 11.17.0 |
| Git | VERIFIED | 2.45.1.windows.1 |
| Docker Engine (daemon) | VERIFIED | server 29.7.2, responding |
| Docker Compose | VERIFIED | 5.4.0 |

**Why Python 3.12 and not the system 3.14.7:** the host's only interpreter was
3.14.7, which shipped *without* pip, and much of the required scientific stack
(torch, camelot) had uncertain 3.14 wheel coverage. 3.12.10 was installed
alongside via winget (`Python.Python.3.12`, user scope). 3.14 is untouched.
Result: every dependency installed from a wheel, no source builds. Spec §6
names 3.12, so this is also the spec-aligned choice.

## Python packages

Verified by import + version lookup. Full pinned set: `requirements.lock.txt`
(117 packages).

| Package | Version | | Package | Version |
|---|---|---|---|---|
| fastapi | 0.141.1 | | torch | 2.13.0+cpu |
| pydantic | 2.13.4 | | sentence-transformers | 6.0.0 |
| pydantic-settings | 2.15.0 | | transformers | 5.15.1 |
| sqlalchemy | 2.0.52 | | qdrant-client | 1.19.0 |
| alembic | 1.19.1 | | rank-bm25 | 0.2.2 |
| psycopg | 3.3.4 | | langgraph | 1.2.11 |
| pymupdf | 1.28.2 | | langchain-core | 1.6.0 |
| pdfplumber | 0.11.10 | | anthropic | 1.0.0 (unused, D8a) |
| camelot-py | 2.0.0 | | pytest | 9.1.1 |
| pytesseract | 0.3.13 | | httpx | 0.28.1 |
| opencv-python-headless | 5.0.0.93 | | numpy / pandas | 2.5.2 / 3.0.5 |

torch is the explicit CPU build (installed from the PyTorch CPU index). There is
no NVIDIA GPU on this host, so embedding runs are CPU-bound — fine for BGE/E5
base-size models over a few thousand chunks, and it is recorded here because it
affects the latency numbers reported in the cost/performance analysis.

## External binaries

| Component | Status | Detail |
|---|---|---|
| Tesseract OCR | VERIFIED | 5.4.0.20240606 at `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| Ghostscript | **MISSING** | Not needed. Only Camelot `lattice` requires it; the pipeline uses `stream` (decision D10a). |

**Ghostscript is no longer an open question.** It was originally deferred pending
extraction-quality measurement. That measurement was taken on the real Infosys
filing and produced a clearer answer than expected: Camelot `stream` — which
needs no Ghostscript — extracted the financial tables correctly at 97.2% mean
parsing accuracy, while pdfplumber returned row labels with **every numeric
column empty**. Indian filings typeset statements as whitespace-aligned text with
no ruling lines, so line detection cannot see them at all.

Camelot `stream` is therefore the primary extractor, Ghostscript stays
uninstalled, and it would only become relevant if a future document required
`lattice`. Full evidence in `DECISIONS.md` D10a.

## Services (docker-compose.yml)

Both brought up and confirmed responding, not merely "started":

| Service | Image | Status | Evidence |
|---|---|---|---|
| PostgreSQL | postgres:16-alpine | VERIFIED healthy | `pg_isready` → `accepting connections` |
| Qdrant | qdrant/qdrant:latest | VERIFIED healthy | `GET /collections` → `{"result":{"collections":[]},"status":"ok"}` |

The Qdrant image is distroless (no curl/wget), so its healthcheck probes the port
through bash's `/dev/tcp` instead of adding a dependency purely for health checks.

## LLM access — free-tier providers (decision D8a)

No Anthropic API credits are available, so the runtime uses **free-tier
providers**. Claude Pro is not API access; API credits are separate and paid.

| Component | Status | Detail |
|---|---|---|
| provider layer | VERIFIED | `backend/services/llm/`, 47 tests |
| `GROQ_API_KEY` | **VERIFIED** | real API calls succeed |
| `GEMINI_API_KEY` | **VERIFIED** | real API calls succeed |

### Channel bindings — VERIFIED by real calls, 2026-08-23

`scripts/verify_llm_providers.py` exits 0. Each binding answered an arithmetic
question correctly; none of this is inferred from key presence.

| Role | Binding | Latency | Tokens (reasoning) |
|---|---|---|---|
| natural_channel | `gemini/gemini-3.7-flash` | 3.28s | 102 (0) |
| program_channel | `groq/openai/gpt-oss-120b` | 1.31s | 161 (33) |
| verification_agent | `gemini/gemini-3.7-flash` | 32.4s* | 91 (0) |
| question_understanding | `groq/openai/gpt-oss-20b` | 1.11s | 147 (26) |

\* includes a client-side rate-limit wait, not model latency.

**Channel independence (D1): cross-provider** — Gemini vs a Groq-served open
model. Reported at runtime by `channels_are_independent()`.

### Model ids had to be discovered, not assumed

The placeholder `llama-3.3-70b-versatile` **does not exist** on Groq's current
free lineup and would have 404'd. What Groq actually serves is
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.6-27b` and
`groq/compound*`; most of the rest of the lineup is audio or guard models. On
Gemini, `gemini-2.5-flash` 404s for this key while `gemini-3.7-flash` and
`gemini-3.5-flash` work. Always run `--list` before pinning a config.

### Decoding configuration

These providers **do** accept `temperature`, so runs are pinned at
`LLM_TEMPERATURE=0.0` and the reproducibility record retains a decoding
parameter (decision D7a). This is better than the superseded Anthropic plan,
where `temperature` is rejected outright on Opus 5 / Sonnet 5.

Temperature 0 is still not bit-level determinism — batching, hardware, and
silent checkpoint updates all vary output. Run-to-run variance is therefore
measured (n=5 repeats on a sample) rather than assumed, and the **resolved**
model id returned by the API is recorded per call so a substitution is visible.

### Cost

`cost_usd` is 0.00 on a free tier. `equivalent_cost_usd`, computed at published
paid rates, is what transfers to another deployment. Throughput is bounded by
request quota rather than money (decision D14).

---

## Outstanding

1. **BLOCKER — no LLM provider key.** Copy `.env.example` to `.env` and add
   `GROQ_API_KEY` and/or `GEMINI_API_KEY` (both free, no credit card). Then
   `scripts/verify_environment.py` must exit 0 and
   `scripts/verify_llm_providers.py` must report every channel binding VERIFIED
   from real API calls.
2. ~~Open — Ghostscript~~ **Resolved (D10a):** not needed; Camelot `stream` is
   the primary extractor and requires no external binary.
