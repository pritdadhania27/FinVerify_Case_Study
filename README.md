# FinVerify-AI

**A Dual-Channel Agentic Framework for Numerical Hallucination Detection in
Financial Document Question Answering**

A research system that answers numerical questions over financial reports through
two *independent* reasoning channels — natural-language reasoning and an executed
program — cross-checked by a deterministic calculator, and measures whether their
disagreement reliably detects numerical hallucination.

> Research validity first, production quality second, visual polish third.

---

## The idea in one paragraph

When a language model answers "what was the operating margin in FY2024?", it can
produce a specific, plausible, **wrong** number with a fluent derivation and no
signal that anything went wrong. Prior work shows models cannot reliably
self-correct reasoning without *external* feedback, and that consistency checks
over repeated samples of the same model inherit that model's systematic biases.
FinVerify-AI asks whether feedback from a genuinely different modality — a
generated program run through a Python interpreter, which has no beliefs about
revenue — provides a detection signal that same-model sampling cannot.

**Central research question:** can independent dual-channel reasoning and
consistency verification reliably detect numerical hallucinations in agentic
financial document QA?

## Architecture

```
Question
   ↓
Question Understanding  →  Hybrid Retrieval (semantic + BM25 + table + filters)
                                   ↓
                 Evidence blocks, shared by both channels
                    ┌──────────────┴──────────────┐
                    ↓                             ↓
        Channel A: NL reasoning       Channel B: program → sandbox
                    └──────────────┬──────────────┘
                                   ↓
                    Deterministic Numerical Verification
                                   ↓
                          Consistency Engine
                          ┌────────┴────────┐
                        AGREE            DISAGREE → Verification Agent
                          └────────┬────────┘
                                   ↓
     Confidence / Risk: evidence grounding · arithmetic · agreement · unit
                                   ↓
                        Evidence-grounded answer
```

Evidence is checked **after** reasoning, as the grounding facet of the risk score.
There is no filtering gate before the channels read it: one would change what both
channels see and couple them through the filter. An earlier version of this diagram
drew such a stage; the system has never had one (D52).

The two channels are independent by construction: different models, structurally
disjoint prompts, and **no cross-channel state reads** — the program channel
never sees Channel A's answer. There is no parameter through which it could
arrive, and two tests enforce it, because the research contribution is void
without it.

**Independence is same-provider, cross-lab** (D44, 2026-09-01). Channel A runs
`openai/gpt-oss-20b` and Channel B `nvidia/nemotron-3-ultra-550b-a55b`, both
served by NVIDIA NIM — two labs, two model families, one serving stack. It was
cross-*vendor* until D44 (Groq vs NVIDIA, D21/RX-014), and moving both channels
onto one provider was what removed the 200,000-tokens-a-day bottleneck that had
made a seven-arm campaign an eight-day job and left three baselines unrunnable.

**D44 records the cost rather than hiding it.** What is lost is correlated
availability and any shared serving-layer transformation — the second unmeasured,
and the real limitation. `channels_are_independent()` reports the exact string
*"same provider, different models"* and every run's `config.json` records it, so
no artifact can inherit a stronger independence claim than the binding that
produced it.

An earlier vendor candidate failed acceptance at the real-call step (RX-008:
valid key, HTTP 402 on every completion), which is why the checklist demands a
completion rather than a reachable endpoint — a rule that paid for itself again
on 2026-09-03, when a model kept appearing in `/models` for hours after it began
returning 410 Gone (D46).

Two caveats travel with it: NVIDIA's **daily limit is unobserved** (it sends no
rate-limit headers), and **latency on that endpoint is not reportable** —
identical prompts returned in 0.42s and 12.5s, and it sheds load with HTTP 503
rather than 429. Request and token counts are unaffected.

Hypothesis H4 tests whether model diversity contributes beyond prompt and
modality diversity, and a null result there would be the more useful outcome.

## Status

**The research result is in. Five campaigns are complete and the held-out split
is spent.** Audited 2026-09-08 against the spec §43 checklist: **29 modules
COMPLETE, 5 PARTIAL, 1 BLOCKED.**

| campaign | split | rows | what it settled |
|---|---|---:|---|
| `campaign_20260901T105355Z` | validation | 315 | 7 arms × 45 (RX-038) |
| `campaign_20260905T112212Z` | **test** | 427 | 7 arms × 61, evaluated once (RX-039) |
| `campaign_20260906T092553Z` | validation | 45 | oracle-retrieval diagnostic (RX-043) |
| `campaign_20260906T221521Z` | validation | 225 | ablation arms B–F (RX-047) |
| `campaign_20260908T164126Z` | validation | 45 | arm A rebased to the current binding (RX-049) |

**What holds out of sample, and what that number actually is.** Arm A's risk
score reaches **AUROC 0.885 [0.801, 0.954]** on held-out data against 0.907 on
validation, with nothing tuned between them, and it beats self-consistency at
**43% of the tokens** on both splits.

But 0.885 is a **deployment triage** figure, not a hallucination-detection one
(RX-048). The score takes **7 distinct values over 61 held-out questions, 3 of
them covering 93%** — `EVALUATION.md` §5.1 forbids exactly that — and **64% of
the errors it ranks are the system's own abstentions**, which are themselves an
input to the score. Restricted to answers the system actually committed to, where
a numerical hallucination can occur at all, the held-out figure is **0.679, 95%
CI [0.500, 0.839] — an interval reaching chance.** Both belong in any honest
summary; only the second answers the research question.

**What the ablation supports (RX-049, corrected in RX-050).** With arm A re-run
on the current binding, the project has its first clean single-field ablation —
and **over all rows**, removing the **executed-program channel** costs the
detector **+0.102 AUROC [0.028, 0.196]**, surviving Holm across the four
contrasts. Removing the *natural* channel costs +0.066 and does not survive. On
this evidence it is specifically the program channel that carries the detection
signal, which is the project's thesis, and it is the first measurement here that
separates that thesis from noise.

**Two limits sit on it, and neither is a footnote.** The committed-answers family
— the one a hallucination claim actually needs, since a hallucination is a
*committed* wrong figure — holds **three errors**, so although both contrasts
clear Holm there it cannot corroborate the asymmetry in either direction; the
figures are reported flagged rather than withheld. And this is **validation only,
permanently**: the test split was spent before these arms ran, so they can never
be held out. An earlier version of this table read "+0.361 … surviving Holm in
both families"; it came from a scratch script with one wrong cell and the
contrasts are now generated by tested code.

**What does not.** No hypothesis is supported after Holm correction (H1 p=0.100,
H3 p=0.580, H4 p=0.641). H2 became testable on test and **failed**: detection is
*worse* on reasoning-caused errors (0.787) than on retrieval-caused ones (0.837),
the opposite of its prediction. So this is a working verifier with an
**unvalidated mechanism** — the risk score ranks errors well and cheaply, and
nothing here shows dual-channel disagreement is why.

**The cleanest finding is about retrieval, not reasoning.** Handed the chunks
containing its gold evidence, the system answers correctly **35 times in 37
(0.946)** against **0.400** with real retrieval — same models, same prompts, same
temperature. Every accuracy figure this project reports is a retrieval
measurement. It also puts H2 out of reach rather than merely untested: at
P(error | correct evidence) = 0.054, a testable reasoning stratum needs ~370
questions with retrieval working, against this benchmark's 45.

Human validation is **done**: 192 candidates judged, **115 usable as gold** (29
rejected, 48 left pending by choice). The dataset layer refuses to serve an
unvalidated question to an evaluation, so those 115 are the entire evidence base
— quoting 192 as a benchmark size would overstate it by 77 rows.

**1,464 tests passing, 1 skipped** — with Docker, PostgreSQL, Tesseract and the
source filings all present. Tests needing an absent service skip rather than
pass, so a green run on a bare machine never reads as "verified".

**The gaps, named rather than glossed.** All eight ablation arms have now run —
B, C, D, E and F landed on 2026-09-07 (225 rows) — but they used a **different
Channel A model** from arms A/G/H, and every arm is defined as "A minus one
field", so they still cannot be compared to the arm that gives them meaning.
`RUN_ARM_A_REBASE.bat` closes that for 135 requests. Separately, the test split
was evaluated against a methodology freeze that had been voided two days earlier
(D48) — AUROC is unaffected; precision, recall and F1 at the operating threshold
are indicative. Both are recorded in [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

**A third was found and fixed.** The arbiter's prompt takes deliberate care not
to reveal which channel produced which answer — and then passed the natural
channel to `CANDIDATE 1` on every arbitration ever run, making position a perfect
proxy for identity in the one component allowed to see both answers (RX-045).
Found by finally computing a number the methodology had asked for since it was
written, at zero cost, from artifacts that had always contained it.

Most defects on this project were found by **running** it rather than by testing
it, and the suite was green for every one:

- a misconfiguration querying a 906-chunk index with an embedding model that did
  not build it - reachable, non-empty, correctly-dimensioned, and returning the
  wrong page (D33);
- a campaign that would have searched five filings without knowing which company
  each question was about, at 0.727 evidence accuracy instead of 0.909 (RX-012);
- a scale token discarded without comment, giving the right answer for the wrong
  reason on a bank's balance sheet (RX-013);
- a campaign reporting `completed 180, failed 0` with Channel A dead for 166 of
  them, because two quota exceptions were unrelated classes (RX-033);
- a correctness predicate that rejected the *most complete* answer a model can
  give on 44 of 115 gold answers, and would have shipped as a units-failure rate
  (RX-031).

`EXPERIMENTS.md` records all 35, including the ones that turned out to be my own
arithmetic, and the negative results — RX-035 tried the reranker that RX-034's
headroom implied and measured it making retrieval *worse*.

See [`COMPLETION_GUIDE.md`](COMPLETION_GUIDE.md) for the audited state and the
ordered path to a result, [`PROJECT_STATUS.md`](PROJECT_STATUS.md) for
per-module status, and [`TODO.md`](TODO.md) for the critical path.

## Quick start

```powershell
# 1. Environment (Python 3.12 venv is already provisioned)
copy .env.example .env
# then add the two FREE keys the channels need - no credit card for either:
#   GROQ_API_KEY    -> https://console.groq.com   Channel A. 8,000 tokens/min
#                                                 is the binding limit in practice
#   NVIDIA_API_KEY  -> https://build.nvidia.com   Channel B. Daily limit is
#                                                 UNOBSERVED; sheds load with 503

# 2. Services
docker compose up -d

# 3. Gates - both must exit 0
.\.venv\Scripts\python.exe scripts\verify_environment.py
.\.venv\Scripts\python.exe scripts\verify_llm_providers.py

# 4. Tests
.\.venv\Scripts\python.exe -m pytest
```

To bring up the application tier as well — the API on 8000 and the dashboard on
5173. It is a separate compose file on purpose: a campaign runs from the host
and needs only PostgreSQL and Qdrant, so an evaluation never waits on a web
build.

```powershell
$env:BUILD_REF = git rev-parse --short HEAD
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

# Then: http://localhost:5173  (dashboard)   http://localhost:8000/docs  (API)
```

`BUILD_REF` is worth setting. `/health` reports on the database and the vector
index — both external — so every field can be green while the container itself
serves code from a fortnight ago, and `build_ref` is the one field that
describes the *server*. It reads `unknown` rather than guessing.

Answering questions live is **off by default** and needs the research engine,
which the API container deliberately does not carry. For a demonstration with live
questions, run **`RUN_LIVE_DEMO.bat`** (double-click it, or run `.\RUN_LIVE_DEMO.bat` from PowerShell): it starts the database and index, the
dashboard, and the API from `.venv` with live QA enabled (D51). Each question runs
both channels and can trigger the arbiter, spends free-tier quota, and takes a few
minutes. Every read endpoint works either way.

Requires Docker Desktop running. PostgreSQL listens on **5433**, not 5432 - see
decision D31 for the diagnosis, which is worth reading before assuming a
connection failure is a credentials problem.

Published free-tier limits are not enforced limits. Both numbers above were
measured, and the Gemini figure differs from its published one by a factor of 75.

## Documentation

**Research** — [`RESEARCH.md`](RESEARCH.md) ·
[`LITERATURE_REVIEW.md`](LITERATURE_REVIEW.md) ·
[`RESEARCH_QUESTIONS.md`](RESEARCH_QUESTIONS.md) ·
[`HYPOTHESES.md`](HYPOTHESES.md) · [`EVALUATION.md`](EVALUATION.md)

**System** — [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`AGENTS.md`](AGENTS.md) ·
[`VERIFICATION.md`](VERIFICATION.md) · [`RAG.md`](RAG.md) ·
[`DATABASE.md`](DATABASE.md) · [`API.md`](API.md) · [`SRS.md`](SRS.md)

**Data and results** — [`DATASET.md`](DATASET.md) ·
[`EXPERIMENTS.md`](EXPERIMENTS.md) ·
[`CASE_STUDY_REPORT.md`](CASE_STUDY_REPORT.md) (the written case study) ·
[`CASE_STUDY.md`](CASE_STUDY.md) (its results section, regenerated from run
artifacts)

**Seeing it run** — [`DEMO.md`](DEMO.md), a 5–10 minute walkthrough on real
data, including what it deliberately cannot show yet

**Where everything lives** — [`PROJECT_MAP.md`](PROJECT_MAP.md)

**Project** — [`PROJECT_STATUS.md`](PROJECT_STATUS.md) · [`TODO.md`](TODO.md) ·
[`DECISIONS.md`](DECISIONS.md) · [`CHANGELOG.md`](CHANGELOG.md) ·
[`ENVIRONMENT.md`](ENVIRONMENT.md) · [`TESTING.md`](TESTING.md) ·
[`DEPLOYMENT.md`](DEPLOYMENT.md)

**Agent operating manual** — [`ENGINEERING_RULES.md`](ENGINEERING_RULES.md)

**Full specification** —
[`FinVerify_AI_Full_Project_Execution_Specification.md`](FinVerify_AI_Full_Project_Execution_Specification.md)

## Research integrity

This repository commits raw experiment results, including failures. The defects
found by *running* the system rather than testing it are recorded with what each
would have cost had it shipped - see the table in
[`PROJECT_STATUS.md`](PROJECT_STATUS.md). Measured dead ends are kept too:
RX-035 is a reranker that made retrieval worse and was not adopted. Citations are verified against primary
sources — one figure in the literature review is
explicitly *not* cited because it could not be confirmed from the paper itself.
The test set is sealed behind an access log and evaluated once per frozen
methodology version. Negative results for H1–H5 will be reported with the same
prominence as positive ones.

## Not investment advice

The system reports what a document states and how confident it is that it read it
correctly. It does not offer financial advice.
