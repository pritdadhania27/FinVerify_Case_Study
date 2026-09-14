# Decision Log

Architectural and methodological decisions, with the reasoning that produced
them. Spec §1 requires that no requirement be silently simplified — anything
that deviates from, sequences, or interprets the specification is recorded here.

Format: what was decided, why, what was rejected, and what would reverse it.

---

## D1 — Dual-channel independence is enforced by construction

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §13 of the master prompt, Modules 8/9

The entire research contribution rests on Channel A and Channel B being
*independent*. If they are two wrappers around the same reasoning process, then
their disagreement measures sampling noise, and the central research question
becomes unanswerable — not merely poorly answered, but meaningless.

**Updated 2026-08-23 (see D8a): independence is now CROSS-VENDOR, which is
strictly stronger than the original plan.** The move to free providers made
available what a single-vendor budget did not: Channel A on Google's Gemini and
Channel B on an open model served by Groq means different training corpora,
different architectures, and different failure modes - not merely two checkpoints
from one lab.

Independence is enforced by four mechanisms:

1. **Different vendors per channel.** `NATURAL_CHANNEL_MODEL=gemini/...`,
   `PROGRAM_CHANNEL_MODEL=groq/...`. Two organisations' models, not two weights
   from one. `channels_are_independent()` reports the live binding at runtime, so
   a configuration that collapses both channels onto one model is visible before
   results are produced rather than discovered afterwards.
2. **Structurally disjoint prompts.** Channel A is asked for evidence-grounded
   natural-language reasoning. Channel B is asked for an executable program. They
   share the question and the evidence set, and nothing else.
3. **No cross-channel state reads.** The program channel never receives Channel
   A's answer, reasoning, or confidence. Enforced by a unit test asserting the
   rendered program-channel prompt contains none of Channel A's output fields —
   a test, not a convention, because conventions decay.
4. **Channel→model mapping is configuration.** Setting both channels to the same
   model is a legitimate ablation arm ("does model diversity matter, or is prompt
   diversity sufficient?"), which turns the main threat to validity into a
   measurable result.

**Rejected:** a single model at two temperatures. Sampling noise is not
independence — two draws from one model share every systematic bias that model
has, which is exactly the limitation of same-model consistency checking that this
project sets out to test against.

**Reverses if:** the ablation shows same-model channels disagree at
indistinguishable rates from cross-model channels, which would mean model
diversity contributes nothing and the claim must be narrowed accordingly.

---

## D2 — Embeddings run locally, not through an API

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §6

Anthropic exposes no embedding endpoint, so embeddings need either a second
vendor or a local model. Local wins: `sentence-transformers` with BGE/E5
candidates, CPU. Python 3.12 makes the torch wheels available, and base-size
models over a few thousand chunks are comfortable on 8 cores.

This also removes a vendor from the reproducibility surface — a locally pinned
embedding model produces the same vectors in a year; a hosted embedding endpoint
may not. Spec §6 requires the model be chosen *by evaluation*, so BGE vs E5 is
decided by retrieval metrics in Module 6, not asserted here.

**Cost:** CPU embedding is slower than a hosted endpoint. This shows up in the
latency figures and is reported rather than hidden.

---

## D3 — Experiment tracking is versioned run directories

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §36 ("MLflow **or an equivalent**")

Each run writes `experiments/runs/<run_id>/` containing `config.json`,
`results.jsonl`, `metrics.json`, and `env.json`, capturing every field spec §36
enumerates. Append-only; raw results are committed and never overwritten.

MLflow would add a server and a backing database to gain a UI that this project
does not yet need. Plain files are diffable, reviewable in git, and survive
without a running service — which matters more for a reproducibility artifact
than a dashboard does. MLflow can be layered on later if its UI becomes worth
the operational cost.

---

## D4 — No database during the vertical slice

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §26, sequence position 21

The spec's own build order puts the database at step 21, after the reasoning
engine. The vertical slice writes run artifacts to files (D3), so it needs no
schema. PostgreSQL arrives at Module 18 with SQLAlchemy + Alembic.

PostgreSQL — not SQLite — from that point on, since Docker is available and the
target deployment already runs it. Avoids writing the ORM twice or discovering
dialect differences late.

---

## D5 — Qdrant in server mode from the start

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §27

Docker is available, so Qdrant runs as a real service via Compose rather than in
embedded mode. Matches the spec exactly and, more importantly, exercises the real
metadata-filtering semantics that Module 6 depends on — filtering behaviour is
the part most likely to differ between an embedded stub and the real service.

---

## D6 — The deterministic verifier's scope is stated honestly

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §18, Module 10

Module 10 requires a verification authority that does not depend on an LLM. The
arithmetic genuinely does not: given operands and an operation, the calculator is
pure Python and fully deterministic.

But **operand binding is not free of the LLM.** Deciding that "revenue growth"
means *these two numbers from these two rows* comes from question understanding
and retrieval. Describing the whole module as "deterministic verification" would
overclaim.

So the module is split and measured separately:
- **operand binding accuracy** — did it select the right numbers? (LLM-dependent)
- **calculation correctness** — given those numbers, is the arithmetic right?
  (fully deterministic)

Reporting these separately is the honest formulation and is also more useful: it
localises failures to retrieval/understanding versus arithmetic. Stated in
`VERIFICATION.md` and in the limitations section of the write-up.

---

## D7 — ~~Reproducibility pinned without temperature~~ → **SUPERSEDED by D7a**

**Date:** 2026-08-23 · **Status:** Superseded same day by D7a · **Spec:** §18

Spec §18 requires recording "temperature/decoding configuration where
applicable". On the current model generation it is **not** applicable:
`temperature`, `top_p`, and `top_k` are removed on Claude Opus 5 and Sonnet 5 and
return HTTP 400; `budget_tokens` is likewise removed.

There is therefore no `temperature=0` determinism knob on the headline models.
Each run instead records **model id + `effort` + thinking mode + prompt version +
the exact evidence set**. Runs are reproducible *in configuration*; they are not
bit-identical in output.

This is a real limitation and gets stated plainly in the write-up rather than
papered over — a paper implying determinism it does not have is worse than one
that measures and reports run-to-run variance. Consequences:

- Run-to-run variance is **measured**, by repeating a sample of questions n times
  and reporting the spread, rather than assumed to be zero.

**Why superseded:** this reasoning was correct for Claude Opus 5 / Sonnet 5, but
the project no longer runs on them (D8a). See D7a.

---

## D7a — Temperature is pinned at 0, a reproducibility improvement

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §18 · **Supersedes:** D7

The free providers serve open-weight and Gemini models over an OpenAI-compatible
API that **does accept `temperature`**. The constraint that forced D7 - frontier
Claude models rejecting the parameter with a 400 - simply does not apply here.

So `LLM_TEMPERATURE=0.0` is pinned and recorded per run, and the reproducibility
record regains a decoding parameter it had lost. This is one of the few respects
in which the free providers are genuinely *better* for this project rather than
merely cheaper.

**What is still not claimed.** Temperature 0 is not bit-level determinism.
Batching, hardware, and server-side model updates all introduce variation, and a
free provider may silently re-point a model id at a new checkpoint. So:

- Run-to-run variance is still **measured** (n=5 repeats on a question sample),
  not assumed away.
- The **resolved** model id returned by the API is recorded per call, not just
  the one requested, so a silent substitution shows up in the record.

---

## D8 — ~~Anthropic is the only runtime provider~~ → **SUPERSEDED by D8a**

**Date:** 2026-08-23 · **Status:** Superseded same day · **Spec:** §5

Original plan: Anthropic API, single provider, with an OpenAI-compatible adapter
stub kept for a later cross-provider ablation. Superseded because no Anthropic
API credits are available. See D8a.

---

## D8a — Free-tier providers (Groq + Gemini) behind one OpenAI-compatible adapter

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §5 · **Supersedes:** D8

**Constraint:** no Anthropic API access. Claude Pro is not API access, and API
credits are separate and paid.

**Chosen:** Groq and Google Gemini (AI Studio), both free, no credit card.

| Provider | Free tier (checked 2026-08-23) |
|---|---|
| Groq | 30 req/min, **14,400 req/day** |
| Gemini | ~15 req/min, **1,500 req/day** (Flash class) |
| OpenRouter `:free` | 20 req/min, **~50 req/day** at zero balance |

OpenRouter was rejected as the primary engine: ~50 requests/day cannot support a
6-arm, several-hundred-question evaluation. It stays registered as a
third-opinion or spillover provider.

**Both expose OpenAI-compatible `/chat/completions`**, so one adapter covers them
and every other candidate (Cerebras, Mistral, Together, local Ollama). Provider
choice becomes configuration rather than code - exactly what spec §5 asks for.

### This improves the research rather than degrading it

The single-vendor plan was the *weaker* design. D1's independence requirement is
now met across **two different organisations' models**, so channel disagreement
reflects genuinely different training data and architectures rather than two
checkpoints from one lab. The cross-provider ablation D8 deferred as a "later"
nice-to-have is now the default configuration.

### What is genuinely worse, and how it is handled

- **Model capability.** Free open models are weaker than frontier Claude at
  program synthesis, and this will show in absolute accuracy. It does **not**
  invalidate the research question, which concerns *relative* detection
  performance across arms measured on identical inputs. But absolute numbers must
  be reported as free-model numbers and never set beside published
  frontier-model results as though the setups matched.
- **Rate limits replace money as the binding constraint.** See D14.
- **Model lineups rotate.** Free providers retire model ids without notice, so
  ids are discovered via `scripts/verify_llm_providers.py --list` rather than
  trusted, and the resolved id is recorded per call.

---

## D14 — On a free tier, rate limits are the binding constraint, not money

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §33

Spec §33 asks how much reliability dual-channel verification buys relative to its
computational cost. On a free tier the dollar cost is 0.00, which would make that
analysis vacuous if dollars were the only thing recorded.

Cost is therefore measured three ways:

1. **Provider-agnostic primitives** - tokens, latency, model calls, sandbox
   executions. These transfer to any deployment and are the primary figures.
2. **Equivalent USD** at published *paid* rates, so the efficiency comparison
   still means something to someone reproducing this on paid infrastructure.
   Recorded as `equivalent_cost_usd`, always kept distinct from `cost_usd`
   (0.00), so a free-tier zero is never passed off as evidence the method is
   cheap.
3. **Quota consumption**, which is what actually limits throughput here.
   Combined Groq + Gemini give ~15,900 requests/day against an estimated
   6,000-8,000 for a full run: workable, but one careless retry loop can spend a
   day's allowance.

Engineering consequences, all implemented:

- A client-side sliding-window limiter, because an unthrottled loop fails within
  seconds against a 30 req/min cap.
- **Daily quota counters persisted to disk**, so a run that dies overnight does
  not restart believing it has a fresh allowance.
- `DailyQuotaExhausted` is raised rather than slept through: a reset can be hours
  away, and a silent multi-hour block is indistinguishable from a hang.
- Auth and model-not-found errors are never retried - a wrong key does not become
  right, and retrying it only burns quota.

---

## D15 — httpx directly, not the `openai` SDK

**Date:** 2026-08-23 · **Status:** Active

The adapter needs a few fields of plain JSON from `/chat/completions`, and httpx
is already a dependency. The genuinely hard part - free-tier rate limiting with
persisted daily quotas and 429 absorption - is custom either way. An SDK would
not have removed that work, only wrapped a dependency around it.

**Reverses if:** streaming, structured-output schemas, or provider-specific
features arrive that are materially harder to hand-roll than to adopt.

---

## D9 — Python 3.12 installed alongside the host's 3.14

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §6

The host had only Python 3.14.7, which shipped without pip and had uncertain
wheel coverage for torch and camelot. Installing 3.12.10 alongside (winget, user
scope) was cheaper than fighting source builds, and spec §6 names 3.12 anyway.
Every dependency then installed from a wheel with no build failures. 3.14 is
left untouched.

---

## D10 — ~~pdfplumber is the primary table extractor~~ → **REVERSED: Camelot `stream` is primary**

**Date:** 2026-08-23 · **Status:** Superseded by D10a on the same day · **Spec:** §6, Module 3

**Original reasoning:** Camelot's `lattice` mode needs the Ghostscript binary,
which is not in the winget catalog under a usable id. pdfplumber is already
installed, needs no external binary, and does both line-based and text-based
table extraction. Spec §6 lists Camelot as "where appropriate". So pdfplumber
would be primary, with the Ghostscript question deferred to measurement.

The reasoning contained an error and an untested assumption, and measurement
exposed both. See D10a.

---

## D10a — Camelot `stream` is the primary table extractor

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §6, Module 3 ·
**Supersedes:** D10

Measured on the real Infosys FY2023-24 consolidated financial statements
(pages 10–25), rather than reasoned about:

| Extractor | Tables | Columns | Numeric content |
|---|---|---|---|
| pdfplumber, default (lines) | 4 | **1** | none — every numeric column empty |
| pdfplumber, `text`/`text` | 12 | 19 | present but badly over-fragmented ("Consolidated" split across cells; rotated sidebar text read as a column) |
| pdfplumber, `text`/`lines` | 10 | 17 | labels only; **numeric columns still empty** |
| **Camelot `stream`** | **21** | **4+** | **complete**, mean parsing accuracy **97.2** |

On the consolidated balance sheet, pdfplumber returned row labels with every
figure lost, while Camelot returned
`['Equity share capital', '2.12', '2,071', '2,069']` at 100% parsing accuracy —
label, note reference, and both fiscal years.

**Two errors in the original decision:**

1. **The Ghostscript dependency applies only to `lattice` mode, not `stream`.**
   The whole premise for avoiding Camelot did not apply to the mode that was
   needed. Camelot `stream` runs fine with Ghostscript absent, as it is now
   doing.
2. **"pdfplumber does table extraction" was assumed, not verified.** Indian
   filings typeset financial statements as whitespace-aligned text with no
   ruling lines. Line detection cannot see a table that has no lines. `stream`
   targets exactly this case.

Had this not been measured, the pipeline would have been built on an extractor
that silently returns labels without numbers — producing a corpus that looks
populated and contains no financial data.

**Ghostscript remains uninstalled and is no longer needed** for the primary path.
It would only matter if a future document required `lattice`.

**Reverses if:** a document class appears where `stream` fails and `lattice`
succeeds; the fix is then to install Ghostscript, not to return to pdfplumber.

---

## D13 — Table scale context carries forward across pages

**Date:** 2026-08-23 · **Status:** Active · **Spec:** Modules 3, 5

Financial statements span several pages and declare their units **once**, on the
first page. In the Infosys filing, the consolidated balance sheet runs across
pages 11–12: page 11 carries "(In ₹ crore)", and page 12 —
"Consolidated Balance Sheet (contd.)" — declares nothing.

A table extracted from page 12 in isolation therefore has no recoverable
magnitude. Reading ₹88,461 crore as ₹88,461 is a **10,000,000× error** on a
figure that looks entirely plausible either way.

So page-level scale declarations carry forward until superseded. Two safeguards,
because carry-forward is an inference rather than a reading:

- The **page that made the declaration is recorded** (`scale_source_page`), so
  any inherited scale is auditable rather than taken on trust.
- Values parsed with an inherited scale carry the
  `SCALE_INHERITED_FROM_CONTEXT` warning, so the consistency engine can weight
  them accordingly.

Page-level detection uses a **stricter** pattern than caption-level detection: a
whole page of prose mentioning "crore" is not a statement about a table's units,
whereas the same word inside a short caption window is.

Effect on the measured corpus: scale-context coverage rose from 20% to 50% of
tables, with 11 tables correctly inheriting from an earlier page.

---

## D11 — A fifth baseline (self-consistency) is added beyond the spec's four

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §30 (which lists four baselines)

Spec §30 names four baselines: LLM-only, RAG, RAG+programmatic, Agentic RAG. All
four are **accuracy** baselines — they answer questions; they do not *detect their
own errors*.

Hypothesis H1 claims cross-modality disagreement detects numerical error better
than same-model consistency sampling. None of the four spec baselines performs
consistency-based detection, so as specified there is nothing for H1 to be better
*than*, and the central claim is unfalsifiable.

**Added: B5 — self-consistency.** n samples from a single model in a single
modality, disagreement among them used as the risk score (the SelfCheckGPT
premise), cost-matched against the dual-channel system.

This is an addition, not a substitution — all four specified baselines remain.
Recorded here because spec §1 requires documenting any change affecting
evaluation methodology.

---

## D12 — Detection metrics are stratified by error provenance

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §30

Both reasoning channels read the *same* retrieved evidence. When retrieval
surfaces the wrong number, both channels compute faithfully from it and agree on
a wrong answer. Agreement is therefore expected to be structurally near-blind to
retrieval-caused error while remaining informative about reasoning-caused error.

Reporting a single pooled detection AUROC would average across a regime where the
method cannot work by construction, and would overstate the contribution.

So detection metrics are always reported **stratified**: retrieval-caused
(gold evidence span absent from the retrieved set) vs. reasoning-caused (span
retrieved, answer still wrong). The pooled figure is still given, but never
alone. This is hypothesis H2, and it is the mechanism check on H1 — a positive H1
with a failed H2 must be reported as an unexplained correlation rather than a
validated design.

---

## D16 — Token accounting records the provider's own total, never a derived sum

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §33 (cost), §18 (reproducibility)

Found while verifying the free-tier bindings with real calls. A Gemini
`gemini-3.7-flash` response reported:

```
prompt_tokens: 29,  completion_tokens: 2,  total_tokens: 223
```

29 + 2 = 31, not 223. **192 tokens — 86% of the call — were hidden reasoning
reported in neither visible field.** Groq's `gpt-oss` models are more forthcoming,
exposing `completion_tokens_details.reasoning_tokens`, but they too bill for
thinking that is not in `completion_tokens`.

The original `Usage.total_tokens` derived `prompt + completion`. On that Gemini
call it would have reported 31 against an actual 223 — understating output cost
roughly sevenfold. Since RQ4 and H5 rest on cost-per-unit-detection, and the
whole point of H5 is an honest cost accounting, a systematic sevenfold
undercount would have quietly invalidated that analysis.

**Now:** the provider's reported `total_tokens` is recorded and used;
`reasoning_tokens` is captured where exposed; `billable_output_tokens` infers
hidden reasoning as `reported_total − prompt` where a provider reports only the
total. `equivalent_cost_usd` prices billable output, not visible output.

**Generalisable lesson, recorded because it will recur:** on reasoning models,
*derived* usage figures are wrong. Record what the provider reports.

---

## D17 — An empty completion is a failure, not an answer

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §7 rule 2

Also found during live verification. With a small `max_tokens`, current free
models spend the entire budget on hidden reasoning and return `content: ""` with
`finish_reason: "length"` — HTTP 200, tokens consumed, nothing said. The first
health check used `max_tokens=16` and reported three perfectly healthy models as
failures for this reason.

Passing `""` through as a channel answer would be worse than the failure it
masks: the consistency engine would receive an empty Channel A answer that looks
like a real one rather than a recorded channel failure.

**Now:** empty or whitespace-only content raises `ProviderUnavailableError`
naming `finish_reason`, `completion_tokens`, `reasoning_tokens` and `max_tokens`,
and suggesting the fix when reasoning consumed the budget. It is retried, since
a retry often succeeds; a persistent empty is recorded as a channel failure,
which the consistency engine already handles as UNCERTAIN.

**Consequence for prompts:** every channel must be given generous `max_tokens`.
Budgeting for the visible answer alone silently starves the reasoning these
models do first.

---

## D18 — Gold evidence spans are located by (page, anchors), not by character offsets

**Date:** 2026-08-23 · **Status:** Active · **Spec:** §14 · **Amends:** `EVALUATION.md` §4

`EVALUATION.md` §4 was drafted before the chunker existed and defined a retrieved
span as a chunk overlapping ≥50% of the gold span's **character offsets in the
extracted text**. That rule cannot be computed on this pipeline, and the reason
matters: a table chunk is *re-rendered* as a pipe table from Camelot cells, so it
is not a substring of the page text and has no character offsets into it. The
only text an offset rule could be computed against is the chunker's own output —
which would make the gold labels a function of the system under test. Circular
gold data is worse than no gold data, because it still produces numbers.

**Now:** a gold evidence span is `(page number in the source PDF, literal anchor
strings)`. It is satisfied by a retrieved chunk when the chunk comes from that
page and its text contains every anchor, after whitespace collapsing and case
folding. Digit separators are **not** normalised — `3,956` and `3956` stay
distinct, so an anchor cannot accidentally accept a chunk whose digit grouping
was mangled.

Both halves are verifiable against the PDF with no reference to any chunking
decision, and `scripts/evaluate_retrieval.py --validate-gold` re-checks every
anchor against the PyMuPDF page text on demand. A gold set nobody can check is a
gold set that can be quietly wrong.

**Evidence is grouped, not flat.** Each question carries evidence *groups*: every
group must be covered, and any span within a group covers it. Without this,
Infosys' trade payables — reported on the balance sheet (p.12) *and* in note 2.14
(p.51) — would score a correct retrieval as a 50% miss, while a return-on-equity
question needing profit *and* equity would score 100% on half the evidence. The
two cases are opposite and a flat list cannot express both.

**Cost:** an anchor is a weaker claim than an offset span — it says the evidence
is on the page and mentions these strings, not that a specific region was
retrieved. On a corpus where a page holds one or two tables that distinction is
small; it would matter more on dense multi-column layouts, and is recorded here
as a known limitation rather than argued away.

---

## D2a — E5-base-v2 is the embedding model (D2 resolved by measurement)

**Date:** 2026-08-24 · **Status:** Active · **Resolves:** D2 · **Evidence:** RX-003

D2 left the embedding model open and committed to deciding it from retrieval
metrics rather than preference. Measured on identical chunks with an identical
BM25 leg, `intfloat/e5-base-v2` beats `BAAI/bge-base-en-v1.5` on every retrieval
metric: Recall@10 0.568 vs 0.477 standalone, evidence-retrieval accuracy 0.545 vs
0.455, and 0.682 vs 0.568 in fusion.

**Now:** `EMBEDDING_MODEL=intfloat/e5-base-v2`. Both are 768-dimensional, so the
collection schema is unchanged; E5 needs the `passage: ` prefix on the indexed
side, which the embedder already applies by model family.

**What this does not settle.** The dense leg is still the weaker of the two, and
no fusion configuration measured so far is reliably better than BM25 alone
(RX-002, RX-003). The fusion default therefore stays at plain RRF, unadopted and
documented as such, until the gold set covers more than one document.

**Cost:** E5 embedded the same 906 chunks in 1,577s against BGE's 984s — about
60% slower on this CPU-only host. On a 1,665-page corpus that is a real
iteration-speed penalty, and it is the reason the chunk cache exists.

---

## D19 — Both channels share one QuestionSpec, and that bounds what agreement proves

**Date:** 2026-08-24 · **Status:** Active · **Spec:** §7, §12 · **Relates to:** D1, H2

Module 7 parses a question once and hands the same `QuestionSpec` to Channel A
and Channel B. This does **not** violate dual-channel independence: D1 is a claim
about two *reasoning processes* over given evidence, and question understanding
is upstream of both, like retrieval.

But it is a **common-mode failure path**, exactly as shared evidence is (H2). If
the question is mis-parsed — wrong metric, wrong operation, a missing figure in a
multi-hop decomposition — both channels reason correctly over the wrong task and
agree on a wrong answer. Channel agreement cannot detect that, and a detection
result that ignored it would overstate the method.

**Mitigations, all by construction:**

- `QuestionSpec.ambiguities` records every uncertainty the parser had: no fiscal
  year stated, a metric outside the lexicon, an LLM call that failed. Questions
  carrying ambiguities can be reported as a separate stratum rather than averaged
  in silently.
- The deterministic parser runs always and the LLM only refines it, so a parse is
  reproducible rather than a function of one model's output on one day.
- `PlannedRetrieval.coverage` reports how many chunks each sub-question
  contributed. A zero is a visible gap rather than a confident answer built from
  half the evidence.

**Consequence for the write-up:** the error taxonomy (Module 14) needs a
*question-understanding* error category alongside retrieval-caused and
reasoning-caused, and the stratified reporting in D12 should extend to it. A
question-understanding failure is neither of the two strata currently named, and
folding it into "reasoning-caused" would attribute it to the channels.

---

## D20 — Both channels on Groq, independence downgraded from cross-vendor to cross-lab

**Date:** 2026-08-24 · **Status:** **SUPERSEDED by D21 on 2026-08-29 (RX-014)** — Channel B moved to NVIDIA NIM and independence is cross-vendor again · **Superseded for:** the cross-vendor part of D1, now restored · **Evidence:** RX-006

Gemini enforces ~20 requests/day on `gemini-3.7-flash` (RX-006). At that rate
Channel A answers 20 questions a day, which makes any multi-arm evaluation
impossible. The project owner has set that problem aside for later and directed
both channels onto Groq.

**What independence now means here.** Groq serves models from different labs, so
the binding keeps the strongest form available on one provider:

| Role | Binding | Lab |
|---|---|---|
| natural_channel | `groq/qwen/qwen3.6-27b` | Alibaba |
| program_channel | `groq/openai/gpt-oss-120b` | OpenAI |

Different training corpora, different architectures, different failure modes —
but one inference stack, one serving configuration, one provider-side prompt
handling. `channels_are_independent()` reports this accurately as *"same
provider, different models"* rather than claiming cross-vendor, and the slice
runner still refuses outright on same-*model* bindings.

**What this costs the research claim.** H4 tested a stronger proposition under
D8a: that disagreement survives across vendors. It now tests that disagreement
survives across labs on a shared stack. Correlated failure induced by the serving
layer — identical tokenisation, identical quantisation, identical system-side
handling — is no longer excluded by construction. Any write-up must say so, and
must not describe the arrangement as cross-vendor.

**When to revisit.** As soon as a second viable provider exists. The cross-vendor
arrangement is the one worth reporting, and this is a throughput compromise, not
a design improvement.

**Discovered by making the change.** Rebinding is not configuration — it changes
the output contract. Qwen externalises its reasoning as `<think>` blocks in the
content field, which broke Channel A's JSON parsing outright, broke Channel B's
AST validation, and is rejected by Groq's server-side JSON mode. All three are
fixed and regression-tested; see the commit and `EXPERIMENTS.md` RX-006.

---

## D21 — Cross-vendor independence restored via a second free vendor (supersedes D20)

**Date:** 2026-08-25 · **Status:** **ACTIVE — acceptance PASSED on NVIDIA NIM 2026-08-29 (RX-014)** · **Supersedes:** D20 · **Owner decision**

> **Resolved.** The vendor is **NVIDIA NIM** (`integrate.api.nvidia.com`), not
> Cerebras. All four acceptance steps below pass, and `verify_llm_providers.py`
> exits 0 on all four channel bindings with real calls.
>
> ```
> NATURAL_CHANNEL_MODEL=groq/qwen/qwen3.6-27b
> PROGRAM_CHANNEL_MODEL=nvidia/nvidia/nemotron-3-ultra-550b-a55b
> ```
>
> **Independence is now cross-vendor, not cross-lab.** Different vendor,
> different serving stack, different lab, different model family. Write-ups may
> now say cross-vendor — and *must* stop saying cross-lab, which understates it.
>
> Verified end to end on RX-010's question: both channels independently returned
> INR 88,461 crore, AGREE, band HIGH. That figure now has **four** independent
> derivations, the fourth on another vendor's hardware.
>
> **The earlier Cerebras attempt (RX-008) is retained below**, because a
> checklist that only records its successes teaches nothing about why it exists.
> Cerebras passed step 1 and failed step 2 with HTTP 402 on every completion —
> which is precisely why step 2 demands a real call rather than a reachable
> endpoint.
>
> **Two caveats carried forward into the campaign:**
>
> 1. **The daily limit is NOT observed.** NVIDIA sends no rate-limit headers and
>    no daily refusal has been triggered. `requests_per_day=1000` in
>    `registry.py` is a pacing placeholder labelled as such, never a
>    measurement.
> 2. **Latency on this provider must not be reported.** Identical prompts
>    returned in 0.42s and 12.5s; the endpoint sheds load with HTTP 503 under
>    contention rather than 429 under quota. Request and token counts are
>    unaffected, so the cost analysis stands; the latency column does not.

D20 parked both channels on Groq because Gemini enforced 20 requests/day. The
project owner has now chosen to source a *second free vendor* rather than accept
the downgrade, restoring the cross-vendor independence D1 requires and H4 tests.

**Target binding.** Cerebras is already specced in `registry.py` at 30 rpm /
14,400 per day on open-weight models — the same class of allowance as Groq, and
roughly 700x Gemini's enforced daily cap.

| Role | Provider | Lab |
|---|---|---|
| natural_channel | groq | (to be fixed once model lineups are listed) |
| program_channel | cerebras | (different vendor, different serving stack) |

**What this is NOT yet.** `CEREBRAS_API_KEY` is empty. No call has been made. The
free tier, the served model lineup, and the real enforced limits are all
UNVERIFIED — and the Gemini episode is precisely why they must be observed rather
than read off documentation: the registry recorded 1,500/day and the provider
enforced 20, a 75x error that only a live 429 revealed.

**Acceptance before this decision moves to Active:**

1. `scripts/verify_llm_providers.py --list` returns a real Cerebras model lineup;
2. `scripts/verify_llm_providers.py` makes a real call on the new binding and exits 0;
3. the observed rate limit is recorded in `registry.py` with the same OBSERVED
   provenance comment Groq and Gemini carry;
4. `channels_are_independent()` reports `cross-provider`.

Until all four hold, D20's cross-lab arrangement stands and any write-up must
still describe independence as cross-lab. **All four now hold on NVIDIA NIM as
of 2026-08-29 (RX-014); write-ups should say cross-vendor.**

**Why it was worth the setup cost.** H4 asks whether disagreement survives across
vendors. On a shared inference stack that proposition cannot be tested — identical
tokenisation, quantisation and system-side prompt handling are exactly the
correlated-failure paths the hypothesis is about. Cross-lab-on-one-stack is a
weaker claim wearing the same words.

---

## D22 — Metric definitions are pinned, and ambiguity becomes its own measured subset

**Date:** 2026-08-25 · **Status:** Active · **Evidence:** RX-007 · **Owner decision**

RX-007 found all three channels splitting on return on equity using three
standard, defensible definitions (closing equity, average equity, owners' share).
The consistency engine flagged DISAGREE — correct by its own lights, and wrong for
a research question about *hallucination*.

**The dataset is therefore built in two parts:**

1. **Main set — definition pinned in the question text.** "Return on equity for
   FY2024 **using closing total equity**". Gold is a single value; disagreement
   here means what the research question needs it to mean.
2. **Ambiguity subset — deliberately unpinned, analysed separately.** The same
   metrics asked as a real analyst would ask them, with gold recording *every*
   defensible answer and the definition each corresponds to.

**Why the subset rather than just excluding ambiguous metrics.** Excluding them
would quietly inflate the headline detection number by removing the cases the
method handles worst, and would hide a real limitation. Measured separately, the
same data answers a question worth publishing: *how often does a consistency-based
hallucination detector mistake definitional ambiguity for a numerical error?*
A false-positive mode nobody has quantified is a contribution; a silently dropped
category is a flaw.

**Consequences.** Module 26 gold needs a `definition` field and an `ambiguous`
flag. Module 24 (error analysis) gains **definitional ambiguity** as a category of
its own, alongside retrieval-caused (D12) and question-understanding-caused (D19).
Detection metrics are reported on the main set, with the ambiguity subset reported
separately and never pooled into the headline figure.

---

## D23 — FinVerify-IND is 150 questions, not the specced 500

**Date:** 2026-08-25 · **Status:** Active · **Supersedes:** the dataset size in spec Module 26 · **Owner decision**

**Binding constraint: validation capacity, not generation.** The spec requires
human verification of every gold answer, and there is one validator (the project
owner). 500 questions is roughly 17 hours of focused validation at two minutes
each — and validation quality degrades long before hour 17.

150 is chosen as the largest number that can be validated *carefully*. A gold set
validated tiredly is worse than a smaller one validated well, because its errors
are invisible and land directly in the headline metric.

**What 150 costs, stated plainly.** Confidence intervals widen. Stratified
reporting (D12: retrieval-caused / reasoning-caused / question-understanding /
definitional ambiguity) will have thin cells, and any stratum below ~20 questions
must be reported as indicative rather than measured. The write-up states the
achieved N per stratum, never just the total.

**Mitigations, to be designed into Module 26:**

- a **double-pass on a random ~20%** to estimate intra-annotator consistency —
  the only agreement figure a single validator can honestly produce;
- the single-validator limitation reported explicitly, never phrased in a way that
  implies inter-annotator agreement was computed;
- question selection **stratified by design** rather than sampled, so the thin
  cells are thin by plan rather than by accident.

---

## D24 — Conference deadline: the research result ships, the product does not

**Date:** 2026-08-25 · **Status:** Active · **Owner decision** · **Revises:** spec §42 build order

The project targets a conference deadline. Scope is therefore cut explicitly and
on the record, rather than discovered as a squeeze late.

**Ships — the minimum defensible research result:**

| Module | Why it is load-bearing |
|---|---|
| 26 FinVerify-IND (150, D23) | there is no result without data |
| 14 Hallucination detection | the taxonomy the research question is stated in |
| 15 Confidence & risk | AUROC needs a **continuous** score; nothing else emits one |
| 22 Evaluation framework | the metrics themselves |
| 23 Baselines | a detection number without baselines is not a finding |
| 24 Ablation | this is what tests H4 and the independence claim |
| 25 Error analysis | the qualitative contribution, and where D22's subset lands |
| 28 Experiment management | reproducibility; already designed (D3), cheap |

**Deferred — real work, no bearing on the result:**

Module 18 (PostgreSQL), Module 20 (FastAPI), Module 21 (frontend), Module 27
(case study), Module 31 (deployment hardening). Run artifacts are files (D4);
files are sufficient for every experiment and every table in the paper. A
database and an API would move the same numbers between processes.

**Deferred with a caveat — Module 17 (LangGraph orchestration).** The pipeline is
wired procedurally in `scripts/run_slice.py` and works. The graph buys retry,
branching, and per-node state. Retry across a 150-question multi-arm campaign has
genuine value, so this is the first item to reinstate if time allows — but it
changes no measurement.

**The honest framing for the write-up.** These are deferrals, not claims of
completion. The paper describes a research prototype, and the architecture section
must not present unbuilt components as built.

---

## D25 — The error taxonomy has two axes, not one

**Date:** 2026-08-26 · **Status:** Active · **Spec:** §22 ("the taxonomy may be expanded during research, but changes must be documented") · **Module:** 14

Spec §22 lists ten minimum categories: wrong evidence, wrong number, wrong year,
wrong unit, arithmetic error, wrong formula, wrong metric, unsupported claim,
retrieval error, reasoning error.

**That list answers two different questions in one enum.** "Wrong unit" says what
the error *looks like*; "retrieval error" says where it *entered the pipeline*.
They are not alternatives — a wrong unit has a provenance, and a retrieval error
has a visible form. Forced into a single flat label they compete for one slot, so
tagging an answer `RETRIEVAL_ERROR` discards the fact that it was a scale
confusion, and tagging it `WRONG_SCALE` discards the stratum it belongs to.

That is not a tidiness argument. **D12 requires detection metrics stratified by
provenance, while Module 25 analyses by kind.** One axis cannot serve both, and
the choice of which to keep would silently decide which analysis is possible.

So `backend/verification/taxonomy.py` carries both on every label:

| Axis | Question | Consumers |
|---|---|---|
| `ErrorProvenance` | where did the error enter? | D12 stratification, H2 |
| `ErrorKind` | what does the error look like? | Module 25 error analysis |

### Additions beyond the spec's minimum

**Kinds:**

- `WRONG_SCALE`, split out of `WRONG_UNIT`. Crore/lakh/million confusion is the
  project's headline error class and the one the dual-channel design is most
  likely to catch. Folded into a general unit error, the headline result could
  not be reported at all.
- `SIGN_ERROR`, split out of `WRONG_NUMBER`. A parenthesised negative read as
  positive is a categorical misreading of financial notation, not a numeric slip,
  and it has a different fix.

**Provenances:**

- `QUESTION_UNDERSTANDING` (D19) — both channels consume one `QuestionSpec`, so a
  mis-parse makes both wrong identically and their agreement proves nothing. It
  is neither retrieval- nor reasoning-caused.
- `EXTRACTION` — the figure was already wrong in the retrieved chunk. Retrieval
  succeeded and reasoning was faithful; blaming either misattributes a
  document-intelligence defect.
- `DEFINITIONAL_AMBIGUITY` (D22, RX-007) — the answer differs from gold because
  the question admits several standard definitions. `counts_as_hallucination` is
  **False** for these, and excluding them is a research decision, not a
  convenience: counting defensible answers as failures would make the headline
  figure a measure of the dataset's precision rather than the detector's quality.

### Two constraints the implementation enforces

**Classification requires gold.** An error is disagreement with a *known correct
answer*. Channel disagreement is the predictor the detector uses to *forecast*
error — treating it as the error itself would let the system grade its own
homework, making detection trivially perfect and meaningless. A disagreement may
therefore only ever produce a `SUGGESTED` label, never a `MECHANICAL` one.

**Unknown never becomes false.** `question_parsed_correctly` and
`extraction_faulty` are tri-state. If either is unchecked, provenance is
`UNDETERMINED` with confidence `NEEDS_HUMAN` — never `REASONING`. Otherwise
`REASONING` becomes the default bucket for every error nobody investigated, and
the reasoning-caused stratum — the one the method is supposed to work on —
silently absorbs unexamined failures, inflating exactly the number H2 turns on.

---

## D26 — One pipeline, every arm a configuration

**Date:** 2026-08-26 · **Status:** Active · **Spec:** §17, §30, §31 · **Modules:** 17, 23, 24

`scripts/run_slice.py` wired the components procedurally. That was right for a
vertical slice and wrong for an evaluation: baselines and ablation arms differ
from the full system only in *which components run*, and as separate scripts
they drift. A reviewer asking "did B2 use the same retrieval as P?" would have to
diff two files and take the answer on trust.

**So there is exactly one pipeline and an arm is an `ArmConfig`.** Two arms that
should differ in one component provably differ in one field, and a test asserts
that every ablation arm differs from the full system in exactly one — with arm D
the sole, legitimate exception, because the arbiter is *triggered by* the
consistency verdict and cannot survive its removal.

This is a research-validity property, not a tidiness one. An ablation is only an
ablation if everything else is held constant, and holding it constant by
discipline across a dozen scripts is not something anyone should be asked to
believe.

**LangGraph is used** (spec §17). The pipeline is not linear — it branches to the
arbiter only on a real DISAGREE verdict — and the graph makes that branch
explicit while giving per-node state, which is what the run artifact records. A
disabled component appears in the node log as `skipped` rather than being absent,
so a reader can confirm from the artifact alone that arm C really ran without the
program channel.

### Consequence: not every arm is a detector

B1–B4 produce answers and no risk score; there is nothing in a single-channel RAG
pipeline to rank questions by. `ArmConfig.provides_detection_score` is False for
them and they are **absent from the detection table** rather than entered at
AUROC 0.5. A placeholder in that column would read as "this baseline detects
nothing" when the truth is "this baseline is not a detector", and a reader
comparing the full system against 0.5 would be comparing against a fiction.

---

## D27 — Self-consistency is scored continuously, or H1 wins for the wrong reason

**Date:** 2026-08-26 · **Status:** Active · **Hypothesis:** H1 · **Module:** 17

B5 is the comparator that makes H1 falsifiable. Its risk score is the dispersion
across *n* samples of one model, and the obvious estimator is `1 − modal_fraction`.

**That estimator would have handed H1 a win it did not earn.** With n = 5 it takes
five values, so B5's score is almost entirely ties — and AUROC counts a tie as
half a concordance. The full system, emitting a continuous score, would beat it
partly *because the baseline was quantised*. The mechanism under test is
cross-modality disagreement; the measured advantage would have included an
artefact of the estimator.

So the modal fraction sets a coarse level and the mean relative spread among the
samples orders continuously within it. Two runs that both split 3–2 are then
separated by how far apart their answers actually were, which is real information
the coarse form discards.

`self_consistency_risk` returns **None** below two usable samples. A model that
failed to answer twice has not agreed with itself, and a fabricated score would
enter the detection table as though it were a measurement.

---

## D28 — The database is a projection; files remain the source of truth

**Date:** 2026-08-26 · **Status:** Active · **Extends:** D4 · **Spec:** §26 · **Module:** 18

D4 made run artifacts files. Module 18 adds PostgreSQL, and the question is which
one is authoritative.

**The files are.** `experiments/runs/` is append-only and committed, so a number
in the paper traces to a file in a git history. Making Postgres authoritative
would put the evidence behind a service that has to be running, on a machine that
has to exist, in a state nobody can diff. Losing the database costs a re-ingest;
losing the artifacts would cost the results.

The database earns its place by making cross-cutting questions cheap — "every
question where the channels agreed and both were wrong, across all arms" is one
SQL statement and a painful loop over JSONL.

Three properties follow from the inverted precedence:

- ingestion is one-way and idempotent on natural keys, so an ingest interrupted
  halfway is repeated rather than repaired;
- `correct` and `stratum` are **copied from the grader**, never recomputed, so
  the database cannot disagree with the report about which answers were wrong —
  two graders is one too many, and the one in the database is the one nobody
  checks;
- a run row for a question the gold set does not contain is skipped, because
  creating a stub would put a row in the gold table that no human validated.

---

## D29 — Explanations are derived, never generated

**Date:** 2026-08-26 · **Status:** Active · **Spec:** §21 · **Module:** 16

Asking a model to write "why is this answer risky?" would put a language model in
the one place the system claims to be trustworthy, and let it produce a fluent
account that does not match the computation.

**A wrong number with a convincing justification is more dangerous than a wrong
number alone**, and this project exists because plausible-looking financial
figures are hard to catch. So every sentence in an explanation is a template over
recorded state, and a test asserts the module contains no provider call at all.

The cost is that explanations read as terse rather than fluent. That is the right
way round: each line is checkable against the run artifact, which is the property
that matters for a system whose output is a number someone will act on.

An explanation must also answer **what would change the verdict**. One that
cannot say what would make the system more confident is a description, not an
explanation.

---

## D30 — Live question answering is off unless switched on

**Date:** 2026-08-26 · **Status:** Active · **Relates to:** D14 · **Module:** 20

`POST /questions/ask` runs both reasoning channels and can trigger the arbiter.
Every request spends free-tier quota, and quota — not money — is this project's
binding constraint (D14). An open endpoint is how a day of campaign budget goes
to a crawler, a refresh loop, or a demo left open in a tab.

The endpoint requires `FINVERIFY_ENABLE_LIVE_QA=1`, returns **503 with the
reason** rather than a stub answer, and refuses before constructing a provider.
The dashboard states the switch's position so a user reads the refusal as a
guard rather than as a bug.

The same reasoning gives `POST /documents/upload` a **202**, not a 201: the file
is registered and hashed but not extracted, chunked, embedded or indexed, and
those take minutes to hours here. A 201 would tell a client the document is
searchable when it is not.

---

## D31 — The project's PostgreSQL moved to port 5433

**Date:** 2026-08-26 · **Status:** Active · **Modules:** 18, 31 · **Environment**

Not a preference — a diagnosis. Two processes were listening on 5432: the
project's container and a **native Windows PostgreSQL service**. Every connection
from the host authenticated against the native one, which has no `finverify`
role. The only symptom was `password authentication failed` while
`docker compose ps` reported the container healthy, and the cause was visible
only in `netstat`.

`POSTGRES_PORT` was already parameterised, so the project's container moved to
5433 and the user's system service was left untouched — changing someone's
machine-wide service to suit one project is not this project's call.

**The general lesson is recorded because it cost an afternoon: a healthy
container is not a reachable service.** The API's `/health` endpoint therefore
reports reachability by *making the call*, and carries the exception text when a
check fails — an earlier version swallowed every failure into `false`, so a
missing Python dependency in the image was reported as "the vector index is
unreachable", a deployment problem misattributed to a working service.

---

## D32 — OCR output is labelled, and only unread pages are read

**Date:** 2026-08-26 · **Status:** Active · **Spec:** §11 · **Module:** 3

OCR does not fail loudly. It fails by returning *something* — a 5 read as an S, a
decimal point lost to a speck — and the result is a plausible number on the right
page with no marker of doubt. **That is the failure this project exists to
detect, arriving through the front door.**

So OCR is opt-in per page and runs only where there is no usable text layer:
re-reading a page that already has selectable text swaps an exact source for a
lossy one. Every OCR'd page carries `source="ocr"` and its confidence into the
extraction quality report.

**Digit confidence is reported separately from the page mean.** A page that reads
its prose perfectly and its figures badly has a healthy mean and is precisely the
page worth distrusting.

A missing scale banner is reported rather than discovered downstream. Losing
"Rs. in crore" makes every figure on that page wrong by a factor of 10⁷.

Verified on page 12 of the Infosys filing: 226 words at 95.3% mean and 95.3%
digit confidence, recovering the same figures Module 4 reads from the text layer.

**Environment note.** Tesseract 5.4.0 is installed but does not add itself to
PATH on Windows, so `shutil.which` reported it missing. Left unhandled, every
scanned page would have been recorded as unreadable — a property of the
environment silently attributed to the document. Resolution is now
`TESSERACT_CMD`, then PATH, then the standard install locations.

---

## D33 — A vector collection records which model built it, and is refused otherwise

**Date:** 2026-08-27 · **Status:** Active · **Spec:** §6, §27, Modules 6, 19

**D2a is the cause, not a coincidence.** On 2026-08-23 it replaced
`BAAI/bge-base-en-v1.5` with `intfloat/e5-base-v2` on measured evidence
(RX-003) and re-indexed into a new collection, `finverify_e5`. It updated
`EMBEDDING_MODEL` in `.env`. It did not update `QDRANT_COLLECTION`, which went
on naming `finverify_chunks` — the superseded BGE index.

A decision applied to one of two variables that must move together.

**Why nothing caught it for four days.** Both models emit 768 dimensions.
Qdrant validates dimension, not provenance: a vector is a vector, and it
accepted E5 queries against BGE vectors without complaint. Everything that
could have objected agreed:

| Check | Reported |
|---|---|
| `docker compose ps` | healthy |
| `GET /health` | `vector_index: true` |
| `index.count() > 0` | true — 906 points |
| vector dimension | 768, matching |
| 978 tests | passing |
| `ruff` | clean |

Measured on the phrase *"total equity attributable to owners of the company"*:

| Query model | Collection | Top score | Top hit |
|---|---|---|---|
| e5 | `finverify_chunks` | 0.5403 | Infosys **p.67** |
| bge | `finverify_chunks` | 0.7761 | Infosys **p.12** — the balance sheet |
| **e5** | **`finverify_e5`** | **0.8865** | the right page |
| bge | `finverify_e5` | 0.4246 | wrong |

The mismatch does not error. It returns a different page, ranked and scored,
and the reader attributes the miss to retrieval quality.

**The decision.** `documents/index_manifest.json` records, per collection: the
embedding model that built it, the dimension, the point count, and the
companies and documents **verified present by querying the index** — not copied
from the registry, which would assert coverage nobody checked. `index_corpus.py`
writes it on every run. `backend/rag/manifest.py::preflight` reads it and
**raises** — never warns — before a campaign, a slice, a retrieval evaluation
or a live API question.

**Rejected: storing the manifest inside Qdrant.** A manifest held in the thing
it describes cannot report that the thing is missing, and files are already this
project's source of truth (D4, D28).

**Rejected: checking the dimension.** It is the check that already passed.

**Also fixed, same root cause.** Five scripts disagreed on the fallback for the
same two settings, and `index_corpus.py` and `evaluate_retrieval.py` never
loaded `.env` at all — so a bare-shell run took BGE against `finverify_chunks`.
That pairing is self-consistent, so past measurements taken with it are valid;
it silently measured the superseded index. Worse, `run_campaign.py` and
`run_slice.py` built their argparse defaults **before** calling `load_dotenv`,
so `.env` was read too late to reach them. The campaign escaped the wrong
collection by that accident alone. All five now load `.env` first and share one
default.

**The general rule this project keeps relearning.** Two settings that are only
meaningful together must be checked together, by something that runs before the
work starts. A dimension is a shape; a model is an identity. Validating shape
and calling it validation is how three of the seven defects on this project
survived — the same species as two PostgreSQL servers agreeing on a port
number, and a document registry whose keys were guessed rather than read.

---

## D34 — The metric lexicon is extended from the corpus, not from a textbook

**Date:** 2026-08-27 · **Status:** Active · **Spec:** §12, §34, Modules 4, 7, 26

The lexicon was built on the Infosys vertical slice and stayed there. It
contained `cost of technical sub-contractors` — a line item essentially unique to
IT services — and **no banking vocabulary at all**, while the corpus includes a
585-page bank filing. RX-012 made the cost concrete: retrieval is the binding
constraint, planned retrieval carries it, and planned retrieval depends on the
lexicon.

**The method: confirm before adding.** Every candidate term was counted as a
table **row label** across all five filings before being written down. 33 entries
became 61; 5 derived metrics became 6.

**Nine textbook-obvious terms were rejected because the corpus does not contain
them**, and seven of the nine were banking vocabulary — precisely the gap being
closed:

| Rejected | Why |
|---|---|
| `total deposits` | HDFC Bank writes **`deposits`** (17 row labels). `total deposits` appears **zero** times |
| `gross NPA`, `net NPA`, `CASA`, `demand deposits`, `savings bank deposits`, `term deposits` | no row label anywhere in the corpus |
| `segment revenue`, `total non-current liabilities`, `total current tax` | same |

**That is the decision.** The obvious repair for "the lexicon assumed an IT
company" is to add what a bank *ought* to report. Seven of nine such guesses were
wrong for these documents. **The fix for "I assumed" is not "assume harder".**

**Statement hints follow the filer's own convention.** Indian banks file a
*Profit and Loss Account* and *Schedules to the Balance Sheet*, never a
*Statement of Profit and Loss*. Since the statement is a soft query hint rather
than a filter, wrong wording costs ranking rather than correctness — but it costs
it on every banking question.

**One derived metric added, `debt to equity ratio`**, because both operands are
confirmed row labels in four filings each. Others were left out: *a derived
metric whose operands cannot be retrieved is a question with no answer, not a
harder question.* Its definition is pinned (D22) to `borrowings ÷ total equity,
excluding lease liabilities`, because gross-vs-net debt is genuinely contested —
the dataset audit refused it as a main-set question until that was written, which
is the audit doing its job.

**Consequences, including the unwelcome one.** Candidates went 160 → **268**.
Every company now clears the case study's 15-question reporting floor with
margin (Tata Motors 15 → 19, Reliance 17 → 29), so per-sector cells are
reportable for the first time. But HDFC Bank went 56 → **127**, and is now 47% of
the set: a 585-page bank filing with many schedules yields more year-labelled
columns than a 79-page IT report. **Aggregate detection metrics over this dataset
are substantially a statement about one bank**, and the write-up must stratify
rather than lead with the pooled figure.

Deliberately not capped per company. Discarding validated-able questions to
balance a table trades real gold labels for a cosmetic property, and the analysis
already stratifies. The imbalance is recorded here and in `DATASET.md` so it is
argued with rather than discovered.

---

## D35 — The validation worksheet is interleaved by company

**Date:** 2026-08-27 · **Status:** Active · **Spec:** §16 · **Extends:** D23, D27

**Validation is partial by design.** ~40 questions is enough for a first result;
268 is what the frozen test-set evaluation needs. So the *order* a validator
works down the sheet decides which companies that first result covers.

Grouped by company — the natural generation order — the first 40 rows of this
corpus are **40 HDFC Bank questions**, because one bank filing supplies 127 of
268 candidates. A first result from that prefix would be a statement about one
bank wearing a five-company label, and nothing in the pipeline would say so.

Interleaved round-robin, any prefix is balanced: 40 rows is now 8 per company
across all five. **Smallest company first**, so a validator who stops early never
starves the thinnest cell — the one already closest to the reporting floor.

This is D27's principle applied to human work rather than machine work: *make the
prefix of an interrupted job representative, because the job will be
interrupted.* The campaign runner is question-major for the same reason.

It changes nothing about the dataset — only the sequence a person meets it in —
which is why it needed no methodology approval, unlike D34's coverage change.

---

## D36 — A question's fiscal year admits the report that restates it

**Date:** 2026-08-29 · **Status:** Active, **amended 2026-08-30** ·
**Spec:** §14, Module 6 · **Evidence:** RX-015, RX-020

> **Amendment (RX-020).** This decision originally admitted `{Y, Y+1}` and
> justified stopping there: *"widening further would start admitting documents
> that cannot contain the figure at all."* **That reasoning was backwards.** The
> constraint is one-directional — a report states any year up to its own and
> none after — so a later filing can always restate an earlier year, and Indian
> listed companies publish ten-year summaries that do exactly that. The `Y+1`
> window left **115 of 268 questions (43%)** still retrieving zero chunks, all
> of them asking about FY2013-14 to FY2021-22. The window now runs forward to a
> bounded horizon of 11 years. Everything below stands; only the width was
> wrong.

**The filter matched the wrong two things.** A chunk's `fiscal_year` payload is
the year of the **document**. `retrieve_for_spec` matched it against the year the
**question** asked about, exactly:

```python
filters["fiscal_year"] = spec.fiscal_year        # before
```

Every chunk in this corpus carries `2023-24`. A question about the year ended
March 2023 therefore filtered to **zero chunks**, and retrieval returned an empty
list — not a poor ranking, nothing at all.

**Now:** a question about year Y admits documents for Y **and Y+1**, because an
annual report states its own year beside the previous one as comparatives, and
for a single-year corpus the prior-year figure exists *only* inside the later
filing.

```python
filters["fiscal_year"] = acceptable_document_years(spec.fiscal_year)
```

`QdrantIndex.build_filter` accepts a sequence and emits `MatchAny`. A test
asserts it is `MatchAny` and not a conjunction — no chunk can carry two fiscal
years, so an `AND` here would be worse than the original bug.

**Why it went unnoticed.** Nothing failed. `build_filter` was valid, Qdrant
answered normally, and an empty result set is indistinguishable from *this report
does not contain that figure*. Every retrieval measurement before RX-015 ran on
Infosys questions about the current year, which the exact match happens to serve
correctly.

The docstring beside the filter already warned that "guessing a year would
silently exclude the right evidence, which is worse than not filtering". The
filter did precisely that. **A comment describing a hazard is not a guard against
it** — that is the general lesson, and it is why the fix ships with tests rather
than a longer comment.

**Scale of what it would have cost.** 193 of FinVerify-IND's 268 questions (72%)
ask about a prior year. All of them would have run both channels over an empty
evidence set. The channels would have abstained or hallucinated, the consistency
engine would have scored the result, and the campaign would have attributed it to
the method.

**Why not widen further.** Comparatives go back one year, not five. Admitting a
wider window would trade a silent miss for a silent wrong-document hit, which is
harder to detect and worse to publish. A year that cannot be parsed is passed
through unwidened rather than guessed at.

**Superseded scope.** This does not fix retrieval *quality* off the development
document, which RX-015 measures separately at 0.38–0.61 evidence accuracy against
Infosys' 0.909. That is an open problem, not a solved one.

---

## D37 — Question understanding stays deterministic

**Date:** 2026-08-29 · **Status:** Active · **Spec:** §13, Module 7 ·
**Evidence:** RX-018

**Decision.** The orchestrator resolves questions with `parse_question`, the
rule-based parser. `understand_question`'s LLM refinement path stays present,
tested and **unused on the campaign path**.

**Why, measured rather than assumed.** RX-018 ran the refinement against a live
model for the first time. It is mechanically sound — 30/30 calls succeeded, none
raised, and it resolves metrics *better* than the rule parser. It also returns
the wrong Indian fiscal year on every prior-year question tested: "the year ended
March 31, 2023" is FY **2022-23**, and the model answers 2023-24, seven times out
of seven.

Three facts make that disqualifying rather than a tuning problem:

1. **72% of FinVerify-IND asks about a prior year** (D36). This is the majority
   case, not an edge.
2. **The fiscal year is load-bearing.** D36 exists because a wrong year returned
   an empty evidence set rather than a bad ranking.
3. **It is the common-mode path** (D19). One `QuestionSpec` feeds both channels,
   so a mis-parse makes them wrong *identically* and their agreement — the entire
   research signal — measures nothing.

A model that is better at the easy half and systematically wrong on the load-
bearing field is a worse component than a rule that is verbose and right.

**What would reverse this.** Not a better prompt alone. The refinement would have
to be measured at parity on fiscal year across a prior-year sample, and the
sensible shape is narrower than "use the LLM": let it resolve the *metric*, where
it is clearly better, and keep the fiscal year deterministic. That is a change to
`_spec_from_llm_payload`, not to this decision, and it needs its own measurement.

**Do not read the configuration as intent.** `QUESTION_UNDERSTANDING_MODEL` is
set in `.env` and `verify_llm_providers.py` health-checks it, which makes the
path look wired in. It is not, and before RX-018 that was an accident rather than
a decision.

---

---

## D38 — Configuration is read at call time, from one place, and must be consumed

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §37, Modules 8, 9, 20 ·
**Evidence:** RX-019, RX-021

**Decision.** Every runtime setting resolves through
`backend/services/llm/settings.py` or `SandboxConfig.from_env()`, read **at call
time**. A variable declared in `.env.example` and consumed nowhere is a defect,
and a test fails the build if one exists.

**Why.** An audit of the ten declared `LLM_*` and `SANDBOX_*` variables found
**nine read by nothing**. They had been present since Module 8, one annotated
`# reasoning models need headroom`, and every call ran on whatever default its
own function signature carried.

Not cosmetic. `LLM_MAX_TOKENS=4096` lost to Channel A's own `max_tokens=2048`,
which truncates a reasoning model mid-`<think>`; the channel then returned "no
JSON object in the reply" on **35% of questions** (RX-019). A campaign would
have recorded a third of Channel A's non-answers as the method failing, when the
cause was a token budget that had been configured correctly and ignored.

**Three sub-rules, each from a specific way this went wrong.**

1. **At call time, never at import.** A module-level `os.environ.get` is
   evaluated when the module is first imported — before `load_dotenv` in most
   entry points, and before any fixture can redirect it. This is the third
   instance: D33's argparse defaults, the API's upload directory, and now these.
2. **Malformed values raise.** `LLM_MAX_TOKENS=oops` fails loudly rather than
   reverting to a default, because a run that silently is not the run its config
   claims cannot be diagnosed afterwards.
3. **Every declared variable is consumed, by Python or by compose.**
   `tests/test_settings_are_wired.py` walks `.env.example` and fails on an
   orphan. A setting nothing reads is worse than no setting: it reports being
   configured.

**What was deleted rather than wired.** `ABLATION_SAME_MODEL` was also unread,
and wiring it would have been the wrong repair. Arms are `ArmConfig` objects over
one graph (D26) and `same_model_both_channels` lives there; a second source of
truth in the environment could disagree with the arm actually running, and the
run record would name one configuration while the code executed another.

**The sandbox case was benign and is fixed anyway.** The four `SANDBOX_*`
defaults happen to match `.env` exactly, so nothing ran unlimited. But an
operator tightening `SANDBOX_MEMORY_LIMIT` for an untrusted corpus would have got
no tightening and no error — the worse half of a security control is one that
reports being configured.

---

## D39 — Paths resolve against the repository, not the working directory

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §16, §41 ·
**Evidence:** RX-021

**Decision.** Every path constant and every `load_dotenv` anchors to
`backend/core/paths.PROJECT_ROOT` (or a script's own `PROJECT_ROOT`). A test
fails the build on a CWD-relative path constant or a bare `load_dotenv()`.

**Why.** Thirty-odd constants across eleven scripts and four library modules were
relative to the working directory. That is invisible while everything runs from
the repository root and silently wrong the moment something does not — and the
failure never looks like a path failure:

- **It cost a validation session.** `scripts/review_gold.py` run from another
  directory printed "no worksheet" and exited, which is indistinguishable in the
  file from a validator who reviewed nothing. 0 of 268 rows, and the worksheet
  byte-identical.
- **`TEST_ACCESS_LOG` is the audit trail for the sealed test split.** The guard's
  entire value is that bypassing it *leaves evidence in the repository* (spec
  §16). Written relative to the working directory, the evidence lands somewhere
  else — or nowhere — and the seal is decorative.
- **`RUNS_ROOT` decides where a campaign resumes from.** A resume that finds no
  prior artifacts does not fail; it starts again, and on a free tier that spends
  a day of quota.
- **`load_dotenv()`** searches upward from the working directory. Run from
  elsewhere and every API key silently goes missing, which reads as "no providers
  configured" rather than as a path problem.

None of these raise. Same shape as D-1, D36 and RX-020: a valid operation, a
normal-looking answer, and an empty or misdirected result.

---

## D40 — `/health` reports which build is answering

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §39 · **Evidence:** RX-021

**Decision.** The API image is stamped with `BUILD_REF` at build time and
`GET /health` echoes it, defaulting to the literal `"unknown"` rather than to
anything plausible.

**Why.** `/health` reports on the database and the vector index — both external.
All of it can be green while the process itself runs code from a fortnight ago,
and during this audit it was: the running container had no
`backend/core/paths.py` and answered `status: ok` throughout. A health check that
cannot detect "I am running old code" is the same shape as D-1, one layer out —
a healthy-looking report about the wrong thing.

`"unknown"` rather than a guess, for the same reason the manifest refuses rather
than assumes: a wrong version string is worse than an admitted missing one.

---

## D41 — FinVerify-IND is 268 questions, and the deviation that matters is not the count

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §34, §1 · **Evidence:** D34, RX-025

**Decision.** FinVerify-IND is frozen at **268 candidate questions** against
spec §34's *"Approximately 500 high-quality, human-validated questions"*. The
same section says *"Do not optimize for dataset size at the expense of gold-label
quality"*, and this is that trade taken deliberately. §1 requires a scope
deviation be documented and approved rather than absorbed silently; the owner
approved it on 2026-08-30.

**Why not extend to 500.** The last extension is the argument. D34 grew the
lexicon from 33 corpus-confirmed terms to 61 and took the set from 160 to 268 —
and the additional questions did not distribute evenly, because the lexicon
gains were concentrated in banking vocabulary:

| Company | Questions | Share |
|---|---:|---:|
| HDFC Bank | 127 | 47.4% |
| Sun Pharmaceutical | 48 | 17.9% |
| Infosys | 45 | 16.8% |
| Reliance | 29 | 10.8% |
| Tata Motors | 19 | 7.1% |

Another 232 questions from the same generator against the same five filings
would deepen that skew, not correct it. A benchmark where nearly half the
questions come from one filing already reports a per-company average that is
substantially one company's number, which is the failure RX-012 caught in
retrieval and D35 was written to keep out of the validation pass. Size is not
the binding constraint on this dataset's quality; balance is.

**The deviation that actually matters, and it is not the count.** §34 names
twelve question categories — revenue, profit, growth, margin, ratio, percentage,
comparison, trend, CAGR, multi-step, cross-table, cross-year. The set is
**256 lookup and 12 multi-hop**, so the derived categories are carried by 12
questions and several are not represented at all. That is a sharper limitation
than 268-vs-500 and it must be stated in the write-up in those terms, because
three consequences follow from it directly:

- **H1 is tested mostly on single-figure retrieval.** Whether disagreement
  detects hallucination on a *derived* quantity rests on 12 questions.
- **The deterministic channel engages on 12 of 268** — it abstains on lookups by
  design — so operand-binding accuracy has a 12-question ceiling before any
  other consideration (RX-025).
- **The program channel is exercised on the same 12.** An arm comparison
  dominated by lookups measures the cheaper half of the architecture.

**Amended the same day (D42).** The count moved to **193**, not by revisiting
this decision but by fixing the generator: RX-026 and RX-027 found that 35
candidates were read from the wrong set of financial statements and 164 more
from pages belonging to neither, and those were dropped or re-sourced. The
reasoning below is unchanged and better supported - generating toward 500 from
the old generator would have produced more of exactly what was removed. The
category limitation named below survives intact and is now 180 lookup / 13
multi-hop.

**What follows from this decision.** Validation proceeds against the current
dataset and nothing regenerates for size. Extending the dataset later is not forbidden — but it would
have to add *derived* questions across the under-represented filings, and it
would come after a first result rather than before one, so that the extension is
aimed by evidence instead of by the same impression that produced the skew.

---
## D42 — A question names the statements it was answered from, and is answered from them

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §12, §16, §34 · **Evidence:** RX-026, RX-027

**Decision.** Every FinVerify-IND question states which set of financial
statements its answer comes from, and the generator reads the answer from that
section. Three rules, in order:

1. **Consolidated is preferred** when the metric appears in both sections.
2. **Standalone is used when it is all there is** — and the question is reworded
   to say "standalone". A standalone figure answers a standalone question.
3. **A figure whose page belongs to neither section is not asked about at all.**

`statement_basis` is recorded in provenance, so a later audit checks agreement
mechanically instead of re-deriving it from a page number.

**Why.** An Indian filing states most metrics twice. Tata Motors' total
borrowings are 13,771.04 crore standalone and 98,500.09 crore consolidated —
seven times apart, because the consolidated figures carry Jaguar Land Rover.
Nothing checked which section a cited page belonged to, so a question naming the
consolidated basis could be answered from the standalone section with a figure
that is real, printed exactly where the provenance says, and wrong. The
generator's own docstring named this risk from the beginning and never enforced
it.

**Rule 3 is the one that costs something, and it is the one that matters.** A
front-of-report highlights table cannot be assigned a basis, so no truthful
question can be asked of it. HDFC Bank's "Summary of Financial Performance"
(p213) states advances as 1,600,585.9 where the consolidated balance sheet
(p412) says 1,661,949.29 — naming either basis would be a false claim about one
of them. This dropped 103 HDFC candidates. Keeping them would have meant a
larger dataset whose questions cannot be answered as worded, which is the
opposite of what §34 asks for.

**A fourth rule, from the same evidence.** A definition may not claim a total
the source row does not support. Under Ind AS the consolidated balance sheet
splits borrowings into current and non-current and prints no sum, so "total
borrowings as reported on the consolidated balance sheet" asks for a figure that
is not printed there. Where the row is not itself a total, the definition names
the line item instead — the question becomes answerable exactly as worded, which
is the only kind of question a gold label can honestly attach to.

**Question ids are now derived from content**, not from a counter. The counter
renumbered every question on every run, so one regeneration silently invalidated
every recorded human verdict — the single input this project cannot
re-manufacture. A question now keeps its id across regenerations, and a question
that disappears takes its id with it rather than handing it to a different one.

**Consequences, stated rather than buried.** The dataset went from 268 candidates
to **193**, and is better on every axis that matters: no question cites a page on
a basis it does not name (0 of 193, from 35), no cited page is unplaceable (0 of
193, from 164), the retrieval gold has no question whose evidence is entirely on
the unasked basis (0 of 100, from 27 of 91), and the largest company's share fell
from 47.4% to 23.8%. D41's reasoning is unchanged and better supported: generating
toward 500 from the old generator would have produced more of what was just removed.

---
## D43 — The deadline is 2026-09-09, and the scope is H1, H3 and H4 on all 45

**Date:** 2026-08-30 · **Status:** Active · **Spec:** §32, §42 · **Supersedes:** D24's
undated deadline, answered on 2026-08-30 as 2026-09-02 and extended the same day

**Decision.** The campaign runs arms **A, B5, G and H** over all **45** usable
validated questions, at `LLM_MAX_TOKENS=1024`, to a deadline of **2026-09-09**.
The owner extended the deadline specifically to buy this scope.

**Why these four arms.** A against B5 tests H1, which is the cheapest thing that
produces a real finding. G removes the deterministic verifier and tests H3. **H is
the reason the extension was worth taking**: it binds both channels to the same
model, which is the direct test of D1 and therefore of H4.

That matters more than sample size. Dual-channel independence is the assumption
the entire contribution rests on, and without arm H it is supported by
construction and a health check rather than by measurement — a reviewer asking
"how do you know the two channels are not two samples of one reasoning process"
would have to be answered with a design argument. Arm H answers it with a number.

**Verified before committing the week, not assumed.** Arm H differs from arm A in
exactly one field, `same_model_both_channels`, and that field is consumed:
`orchestrator.py` swaps both the model and the provider, and `campaign.py`
re-attributes those calls to the natural provider so the budget reflects them.
Had the flag been read by nothing — the RX-021 failure, which hit 9 of 10
settings — arm H would have run as a second copy of arm A and the extension would
have bought nothing. The campaign additionally refuses to run any non-H arm while
the channels are same-model, so the ablation cannot leak into the main arms.

**Budget.** 540 Groq calls, 1,514,160 tokens, 7.6 days of allowance against
1,800,812 available before the deadline. Gold is the binding constraint again
rather than quota: 45 questions are usable and 53 are affordable.

**~~Conditional on a measurement not yet taken.~~ AMENDED 2026-08-31 — the
measurement was taken and 1024 failed.** RX-032 asked six questions at both
budgets, paired: **three parse failures at 1024 that 4096 answered, none the
other way**. The bound model emits `<think>` reasoning before its answer, so a
1024-token ceiling truncates the reply mid-structure. **The campaign runs at
`LLM_MAX_TOKENS=4096`**, at 5,876 tokens per call and 70,512 per four-arm
question.

**The scope is still all 45, and is no longer a forecast to commit to.** At 4096
the allowance is expected to reach about 24 of the 45 before the deadline. That
number is not imposed as a `--limit`: the campaign is question-major and
resumable — an interruption leaves every finished question with every arm, and
`--resume` re-spends nothing, both properties under test. So the run attempts all
45 and lets the allowance decide how far it gets. Truncating to today's forecast
could only lose questions, never gain them; if calls come in under their reserved
budget the run simply gets further. Whatever completes is a balanced prefix in
the dataset's own seeded order, so the subset is not selected by how any question
behaves.

**What is deliberately still out.** The full 13-arm ablation needs 13.9 days and
does not fit. Baselines B2 and B3 do fit in the slack and are offered separately;
they are not taken by default, because the owner approved this scope and quietly
spending their extension on more arms is not a decision to make on their behalf.

**Both were subsequently taken, on the owner's word rather than by drift.** B3
ran on 2026-09-01 (RX-037, n=45) while the campaign was blocked, because it is
Channel B only and cost the blocked provider nothing. B2 was approved by the
owner on 2026-09-01 after D44 removed the token bottleneck, and runs in
`campaign_20260901T105355Z` as its seventh arm — 45 requests, bringing the run
to 315 rows. It is added to that run rather than a separate one so the final
comparison is one table across all seven arms, not two that have to be
reconciled.

---

## D44 — Both channels move to one provider, and the independence claim narrows to the model

**Date:** 2026-09-01 · **Status:** Active · **Spec:** §13, §29 · **Amends:** D1's
"different vendors per channel", D8a's provider split · **Requested by:** the owner,
2026-09-01, to finish the campaign in a day rather than eight

**Decision.** `NATURAL_CHANNEL_MODEL` and `VERIFICATION_AGENT_MODEL` move from
`groq/qwen/qwen3.6-27b` to `nvidia/openai/gpt-oss-120b`. `PROGRAM_CHANNEL_MODEL`
stays `nvidia/nvidia/nemotron-3-ultra-550b-a55b`. Both reasoning channels are now
served by one provider, bound to two different models from two different labs.

**Why.** Groq's free tier allows 200,000 tokens a day and the limiter rolls it on
the UTC day (D-note in `run_campaign_unattended`). The four-arm campaign was
budgeted at 3,701,880 tokens, which is eight days — and B1, B2 and B4 could not
run at all, because a baseline must share arm A's natural model and that model was
on the exhausted provider. NVIDIA's endpoint has **no observed daily token cap**;
its constraint is requests, and `--budget-only` puts the six-arm campaign at **720
requests** against a 1,000/day pacing placeholder. The whole thing fits in one day.

**What this costs, stated plainly.** D1 recorded cross-vendor binding as "strictly
stronger than the original plan", and this is a step back down to the original
plan — the one D1 was written to improve on. Two things are genuinely lost:

1. **Correlated availability.** NVIDIA's free 550B endpoint returns HTTP 503 under
   contention (RX-014). Both channels now fail together rather than independently.
   This affects how often the system answers, not whether its two answers are
   arrived at independently, and per-channel execution failures are recorded.
2. **Shared serving layer.** Any transformation NVIDIA applies at the edge — system
   prompt injection, tokenizer normalisation, safety filtering — now applies to
   both channels. This is unmeasured and is the real threat the change introduces.
   It is not the threat D1 names, which is two checkpoints from one lab.

**Why it is nonetheless permitted.** D1's stated rationale is "different training
corpora, different architectures, different failure modes — not merely two
checkpoints from one lab." NVIDIA's endpoint is a serving platform for 82 models
from many labs, not a lab. OpenAI's gpt-oss-120b and NVIDIA's Nemotron-3 are
different organisations, corpora and architectures; only the inference host is
shared. `channels_are_independent()` has always treated "same provider, different
models" as independent and returns that exact string, which `config.json` records
per run — so every artifact produced under this binding carries the narrowed claim
in its own metadata instead of inheriting a stronger one from documentation.

Arm H is unaffected and still bounds the answer: if H shows same-model channels
disagree indistinguishably from different-model channels, model diversity
contributes nothing and this concern is moot. That measurement is the point of H4.

**A new run id is mandatory, not tidiness.** Result rows record `natural` and
`program` values but not the model that produced them; only `config.json` records
the binding, once per run. Resuming `campaign_20260831T235851Z` under the new
binding would therefore mix two Channel A models inside one run with nothing in
the data to separate them. Its four rows (one question, four arms) are preserved
under the append-only rule and superseded, not deleted.

**Rejected — waiting for Groq.** Eight days to the 2026-09-09 deadline with no
slack, and B1/B2/B4 never run, leaving "better than plain RAG" unsupported.

**Rejected — swapping the channels** (natural to NVIDIA, program to Groq). This
keeps cross-vendor independence and was the strongest alternative. It fails on
comparability: B3's completed 45-row run (RX-037) used Nemotron as its program
channel, and moving arm A's program channel to Groq would make the project's only
finished baseline non-comparable to the system it is a baseline for. Re-running B3
costs a full Groq day and puts the bottleneck back on the program channel.

**Reverses if:** quota stops binding (a paid tier, or a second provider with
comparable headroom), in which case cross-vendor binding is restored — or arm H
shows model diversity dominates the disagreement signal, which would make the
shared serving layer a material confound rather than a noted limitation.

---
[INFO] Recording command outcome: cat

[OK] Command outcome recorded

## D45 — The operating threshold is 0.51125, chosen on validation and frozen

**Date:** 2026-09-03 · **Status:** Active · **Spec:** EVALUATION.md §5.2 ·
**Chosen on:** `campaign_20260901T105355Z`, arm A, validation split

**Decision.** The detection operating threshold is **0.51125**. It is frozen
here, before the test split is touched, and every subsequent report passes it
with `--threshold 0.51125` rather than re-selecting.

**Why this value.** All three candidate objectives land on the same point:

| objective | threshold |
|---|---:|
| F1-optimal | 0.51125 |
| recall floor 0.90 | 0.51125 |
| recall floor 0.95 | 0.51125 |

That convergence is the argument. A threshold that moves with the objective is a
choice; one that does not is a property of the score distribution, and the risk
score here takes only four distinct values, so the operating point is a genuine
plateau rather than a point fitted to noise.

**What it buys, on validation, arm A (n=45, 27 errors):**

| | |
|---|---:|
| true positives | 26 |
| false positives | 5 |
| true negatives | 13 |
| false negatives | **1** |
| precision | 0.839 |
| recall | 0.963 |
| F1 | 0.897 |
| false positive rate | 0.278 |

It catches 26 of 27 wrong answers and wrongly flags 5 of 18 correct ones. For a
verification tool that is the right side of the trade: a missed wrong answer is
the expensive error, a false flag costs a second look.

**Why it is frozen before test.** EVALUATION.md §5.2 is explicit that selecting
the threshold on test inflates precision and recall by construction, because the
threshold would be fitted to the data it is then scored on. Nothing in the code
enforces the split, so this decision is the enforcement.

**Applying it to other arms is not meaningful for B5.** The threshold was chosen
on arm A's score distribution. Arms G and H share that distribution's shape and
report sensibly (G: precision 0.730 / recall 1.000; H: 0.960 / 0.889). B5's
self-consistency score is on a different scale and flags nothing at this point,
reporting 0.000 across the board - that is a scale mismatch, not a measurement of
B5, and must not be quoted as one.

**Reverses if:** the benchmark changes (more questions, a different multi-hop
share) or the risk model stops being the four-valued facet score, either of which
would require re-selection on the new validation data and a new freeze.

---

## D46 — Channel A moves to gpt-oss-20b, and methodology-freeze-v1 is void for test

**Date:** 2026-09-03 · **Status:** Active · **Supersedes:** D44's model choice ·
**Voids:** `methodology-freeze-v1` as a basis for test evaluation, and D45's
threshold

**What happened.** `nvidia/openai/gpt-oss-120b` reached end of life at
**2026-09-03T08:00:00Z** and returns HTTP 410. D44 bound Channel A to it on
2026-09-01. It lasted two days. The test-split run that had just been started was
caught in its startup phase and wrote **zero rows**.

**Decision.** `NATURAL_CHANNEL_MODEL` and `VERIFICATION_AGENT_MODEL` move to
`nvidia/openai/gpt-oss-20b`, verified by live call. `PROGRAM_CHANNEL_MODEL` is
unchanged. Independence is unaffected: still two models from two labs on one
provider, which is what D44 argued for and what `channels_are_independent()`
reports.

**The listing is not evidence.** `GET /models` still lists gpt-oss-120b after it
began returning 410, and lists `qwen/qwen3.6-27b`, which 404s. ENGINEERING_RULES.md says
never hard-code a model id and to discover with `--list`; today shows that is
only half the rule. **A listing says a name is known, not that it is servable.**
Only a real completion establishes that, which is what
`verify_llm_providers.py` does — and it is now the gate before any campaign,
not just a diagnostic run when something looks wrong.

**The expensive consequence.** The 315-row validation campaign
(`campaign_20260901T105355Z`, RX-038) and the threshold frozen from it (D45) were
both produced on gpt-oss-120b. Evaluating the test split on gpt-oss-20b would
compare a held-out result against a validation baseline from a *different Channel
A model* — measuring the swap, not the system. So:

1. Re-run validation on gpt-oss-20b — 765 requests, all 7 arms, 45 questions.
2. Re-select the operating threshold from that run.
3. Cut `methodology-freeze-v2`.
4. Only then evaluate the test split.

**RX-038 is not withdrawn.** It is a complete, correctly measured campaign of a
real system configuration, and its findings — retrieval as the bottleneck, no
hypothesis supported, self-consistency costing 2.4x for worse detection — stand
as results about that configuration. What it can no longer be is the validation
baseline for a test evaluation, because its Channel A no longer exists.

**Reverses if:** nothing. A retired model does not come back. The lasting change
is procedural: a live health check gates every campaign from here.

---

## D47 — An oracle-retrieval arm, on validation only

**Date:** 2026-09-05 · **Status:** Active · **Spec:** H2, EVALUATION.md §5 ·
**Arm:** `O` — arm A with `retrieval="oracle"`

**The problem.** H2 asks whether dual-channel disagreement detects *reasoning*
errors, and needs errors made with the evidence in hand. The end-to-end system
barely produces any. Measured on the test run:

| | |
|---|---:|
| P(every gold group retrieved) | 0.344 |
| P(error \| evidence delivered) | 0.190 |
| **reasoning errors per question** | **0.066** |

One per fifteen questions. RX-039 had four, and RX-038 had none at all. Reaching
20 would need ~305 questions; 30 would need ~458. The benchmark cannot be grown
that far, and the multi-hop questions that were supposed to help contribute
nothing — there is **one** in each split and neither ever had its evidence
delivered, so no evidence exists that multi-hop raises the reasoning-error rate.

**Decision.** Arm O hands each question the chunks containing its gold evidence.
`P(evidence delivered)` becomes 1 by construction and **every remaining error is
a reasoning error by definition**. The oracle resolves for **43 of 45**
validation questions against real retrieval's 13.

**What it is not.** Not an end-to-end measurement, and it must never be reported
beside arm A as though it were one — it has been told where to look. It is the
standard oracle-context ablation, isolating the reasoning step. RX-039 remains
the end-to-end result.

**It gives away the location, not the answer.** Blocks are whole retrieved
chunks, byte-identical to what retrieval would have supplied, with the
neighbouring table rows and distractor columns intact. No span is highlighted
and the gold value is not injected. The model still has to find the right row
and apply the right scale.

**Refusing a partial oracle.** If any gold group resolves to nothing, the arm
gets **no** blocks rather than some. A partial oracle would put a question whose
evidence was never supplied into the reasoning stratum and count its failure as
a reasoning error — precisely the misattribution this arm exists to remove. The
`oracle` branch is also tested *before* the closed-book branch, which fires on
`deps.retriever is None`; the other order would silently turn arm O into B1.

**VALIDATION ONLY, and this is the important part.** This arm was designed
**after** seeing RX-039 — H2 failing on test is what motivated it. Running it on
the test split would be choosing a new measurement in response to what the
held-out set already showed, which is the leakage the one-shot rule exists to
prevent. It is a validation-stage diagnostic and is reported as one. Evaluating
oracle retrieval out of sample needs test data not yet spent, or an explicit
disclosure that the split was used twice.

**It removes the distractors as well as the search, and that is a real
confound.** Real retrieval hands the channels 8 blocks; the oracle hands them
one per gold group - typically one. So the arm does not isolate "evidence
present" alone, it isolates "evidence present AND almost nothing else". A low
reasoning-error rate under this condition therefore shows the model reasons
correctly when handed exactly the right table, **not** that it reasons correctly
when the right table arrives among seven near-misses. The honest reading of a
null result here is narrower than it first looks.

The fix, if this arm ever carries weight: pad the oracle blocks with the real
retrieval results up to `top_k`, giving guaranteed evidence in realistic
company. Not built - it needs a retriever in a path that currently needs none,
and the diagnostic answers its question without it.

**Reverses if:** the benchmark grows enough that the end-to-end reasoning
stratum is testable on its own, at which point the oracle becomes a supporting
comparison rather than the only route to H2.

---

## D48 — Three of D46's four preconditions were skipped, and the test split was evaluated anyway

**Date:** 2026-09-06 · **Status:** Active, recording a deviation ·
**Concerns:** D45, D46, RX-039 · **Found by:** the module audit, not by a test

**What D46 required before the test split could be touched**, in its own
numbering:

1. Re-run validation on gpt-oss-20b — 765 requests, 7 arms, 45 questions.
2. Re-select the operating threshold from that run.
3. Cut `methodology-freeze-v2`.
4. Only then evaluate the test split.

**What happened.** Step 4 happened on 2026-09-05
(`campaign_20260905T112212Z`, 427 rows). Steps 1, 2 and 3 did not. The evidence
is unambiguous and all of it is in the repository:

| D46 step | state | how it is checkable |
|---|---|---|
| 1. re-run validation on 20b | **not done** | the only 20b validation run before test is `campaign_20260903T163724Z`, a **1-row smoke test** carrying `SMOKE.md` |
| 2. re-select the threshold | **not done** | the operating threshold is still D45's **0.51125**, selected on `campaign_20260901T105355Z`, which ran gpt-oss-**120b** |
| 3. cut `methodology-freeze-v2` | **not done** | `git tag` lists `methodology-freeze-v1` and `results-final-v1`. There is no v2 |
| 4. evaluate the test split | **done** | 427 rows, and all seven entries in `experiments/test_set_access.log` give the reason as *"final one-shot evaluation against methodology-freeze-v1"* — the tag D46 had voided two days earlier |

`PROJECT_STATUS.md` recorded the *consequence* of this honestly from the day of
the run — the threshold caveat under RX-039 is accurate and was written without
prompting. What it did not say is that a written four-step precondition was
carried out one step in four. The caveat reads as a known limitation of the
result. It is that, and it is also a process failure, and the second framing is
the one that belongs in a decision log.

**What this does NOT invalidate.** The test campaign is a correctly executed
seven-arm run: 427 rows, zero provider failures, every arm on the same Channel
A, one evaluation of the split. Everything threshold-free survives intact —

- **AUROC 0.885 [0.801, 0.954]** for arm A, and every other arm's AUROC.
- Numerical accuracy, abstention rates, token and request counts.
- Every **between-arm** comparison inside the test run, because all seven arms
  shared one Channel A binding. The headline claim — better detection than
  self-consistency at 43% of its tokens — is a within-run comparison and is
  untouched.

**What it does affect.** Precision, recall, F1 and FPR at 0.51125 on the test
split, and any validation-versus-test comparison of those four. The threshold
describes a score distribution produced by a model that was not running.

**Decision 1 — the threshold is NOT re-selected now.** 0.51125 was chosen on
2026-09-03, before any test row existed, so in sequence it carries **no
leakage**. Re-selecting it today, with RX-039 read and its error cases known,
would introduce leakage into a number that currently has none, in exchange for
fixing a binding mismatch. That is a bad trade. 0.51125 stands, reported as
*pre-registered but selected under a superseded Channel A*, and every
threshold-dependent figure on the test split stays labelled **indicative**.

**Decision 2 — no tag is backdated.** Cutting `methodology-freeze-v2` now, after
the split is spent, would put a pre-registration mark on a decision that was not
pre-registered. The freeze that governed this evaluation is `v1`, voided, and
the access log naming it is therefore an accurate record of what actually
governed the run rather than a stale label. It stays as written.

**Decision 3 — validation is re-run on gpt-oss-20b, and it is reported as a
same-binding comparison rather than as a repair.** It costs 765 NVIDIA requests,
touches no test data, and is the only way to say what the system does on the
current binding at a threshold and on a split where saying so is free. It cannot
retroactively license the test evaluation and is not claimed to.

**The procedural lesson, which is the part worth keeping.** D46 wrote its
precondition as prose in a decision entry, and prose does not block anything.
`run_campaign.py` already refuses to start when a *model* fails a live call —
D46's own contribution. The analogous gate for a *methodology* precondition was
never built, so the only thing standing between a voided freeze and a spent
test split was that someone would re-read D46 before typing the command. Nobody
did. `RUN_TEST_SPLIT.bat` now checks for a live freeze tag and refuses without
one; the check is worth more than this entry is.

**Reverses if:** nothing. The split is spent. What changes is only how narrowly
the threshold-dependent numbers are stated, and they were already stated
narrowly.

---

## D49 — Three spec functions are scoped out explicitly, rather than left silently absent

**Date:** 2026-09-12 · **Status:** Active · **Spec:** §10, §19, §27, §43 ·
**Prompted by:** a module-by-module audit against the §43 checklist

An audit of all 33 modules found three functions the spec names, which this
project does not implement and nowhere admitted to not implementing. The absences
are defensible; the silence was not. Spec §43 makes a module COMPLETE only
against its own checklist, so each of these had been inflating a status row.

Recorded here so the omissions are arguable rather than invisible.

**1 — Document version management (§10).** No superseding or restatement link
between two filings of the same company-year. The corpus is five filings, all
FY2023-24, one report each: there is no pair for a chain to connect, so the
table would be an abstraction over nothing.

What matters about restatements is already handled, and by a stronger mechanism:
provenance records which *document* every number came from, so the same metric
for the same year extracted from two filings stays two distinguishable facts.
That is the actual trap ENGINEERING_RULES.md names. A supersession link would say which one
is current — a judgement the corpus cannot currently support, because there is no
second filing to compare.

**Reverses if** the corpus gains a second filing covering a year it already
covers. At that point provenance alone stops being enough: a reader asking "what
was revenue for FY23" needs to know which filing answers, and the link becomes
load-bearing rather than decorative.

**2 — Filtering the vector index by financial metric (§27).** `build_filter`
takes company, fiscal year, document, section, kind and page. It takes no metric.

This one is not a corpus accident — a metric filter on *chunks* would be wrong.
The indexed unit is a table or a text span, and a single financial-statement
table carries dozens of line items. Labelling that chunk with one metric would
discard the rest; labelling it with all of them makes the filter useless.
Metric-level selection belongs one layer down, on `FinancialFactRow`, which is
keyed per cell and does carry a metric label — and that is where the facts layer
already serves it. Within retrieval, the metric is what the *query* is for: it is
matched semantically and lexically, which is the job hybrid retrieval exists to
do.

**Reverses if** retrieval evaluation shows metric-labelled chunk filtering
beating query terms on the same gold. RX-035 is the cautionary precedent here:
the reranker that the rank decomposition implied should work was measured and
failed, so this is a claim to test, not to assume.

**3 — Frontend component and end-to-end tests (§43).** Module 21 had no tests of
any kind: no runner in `package.json`, no `*.test.*` under `frontend/src`.

Closed in two parts rather than by adding a second toolchain for nine files.
`tests/test_frontend_contract.py` now covers the seams, in Python, with no node
and no browser: every path `api.ts` calls exists in `main.py`, every route
resolves to a page that exists, no page is orphaned, no host is hardcoded, no
screen hardcodes an arm list, and the two states that must never read as zero
(`undefined-value`, `UNSCORED`) still have style rules.
`tests/test_frontend_contrast.py` pins WCAG AA on both themes from the declared
tokens. Rendering is covered by the scripted live probe against the running
containers.

That split is deliberate. Every defect the contract tests check has actually
occurred here — `Ask.tsx` hardcoded seven arms against fourteen served; a NUL
byte survived tsc, ruff and the build; a search box dropped characters — and not
one of them was a component-rendering bug a unit test of a component would have
caught. They were all seam bugs and stylesheet bugs.

**Reverses if** the UI grows stateful component logic worth testing in isolation
— a form with validation rules, a client-side computation. At that point the
logic is the thing under test rather than the wiring, and Vitest earns its place.

## D50 — The retrieval-gold seal breach is recorded and contained, not undone

**Date:** 2026-09-12 · **Status:** Active · **Spec:** §16, §30 ·
**Prompted by:** RX-053 · **Relates to:** D48, which is the same failure one lane up

RX-053 found that `datasets/retrieval_eval/*_v1.json` each declared
`split: "validation"` while 43 of the 76 sealed test questions (57%) sat among their
source questions. `scripts/evaluate_retrieval.py` gates on that declared field, so
RX-028, RX-034 and RX-035 ran on test-bearing gold with no `FINVERIFY_ALLOW_TEST=1`,
no stated reason, and no entry in `experiments/test_set_access.log`.

Four decisions, and the third is the uncomfortable one.

**Decision 1 — the label is derived, never declared.** `build_retrieval_gold.py`
now records `source_split` per question and computes the set-level label from it,
publishing `split_composition` so the claim is checkable. The existing four sets were
relabelled to `test`. No question, evidence span or answer note was altered, verified
field-by-field against the prior commit. The relabel moves strictly in the
unflattering direction — the sets are now refused by default, so figures from them
can no longer be presented as validation results.

**Decision 2 — the log records the commit, because this document always said it
did.** `EVALUATION.md` §1 promised "timestamp, run id, and git commit"; the code
wrote timestamp, reason and count. The promise is the better design, so the code was
raised to it rather than the claim lowered. Entries now carry the commit and the
invoking script. An access that cannot be tied to a state of the code is most of the
way to no evidence.

**Decision 3 — RX-035's rejection of the reranker stands, with the limitation
attached.** This is the finding with real consequences, so it gets stated plainly
rather than tidied.

RX-035 measured a cross-encoder reranker at 0.240 against a 0.280 baseline and
declined to adopt it. That measurement is partly on test-derived gold, which makes it
a tuning decision informed by the held-out split — the exact thing the seal exists
to prevent.

It is **not** re-run and **not** withdrawn, for reasons that should be weighed rather
than accepted:

- The decision's direction is conservative. It *declined* to add a component.
  Nothing was tuned **into** the system on held-out data, which is the failure mode
  that inflates a reported result. Re-running it on clean gold could only tell us to
  adopt the reranker or not; the current state already assumes "not".
- Re-running it **now** would spend a one-shot test evaluation on a split already
  recorded as spent against a VOID freeze (D48), which buys a number that still
  cannot be called pre-registered.
- What it cannot be is **cited as a clean validation result**, and `RAG.md`,
  `PROJECT_STATUS.md` module 6 and `EVALUATION.md` §1 now all say so.

**Reverses if** validation-only retrieval gold is built. At that point the honest
move is to re-run RX-035 against it and let the clean measurement replace the
compromised one. `build_retrieval_gold.py` can now produce such a set, and doing so
costs no quota and touches no sealed data — recorded in TODO 0f as the preferred
route.

**Update 2026-09-13 — the reversal condition was met and the re-run is done.**
Validation-only gold was built (RX-054) and RX-035 was re-run on it unchanged
(RX-035-revalidated): **0.156 reranked against a 0.125 baseline, net +1 of 32
evidence groups.** The compromised measurement is replaced, and its direction did not
survive — reranking does not make retrieval worse. The decision itself stands
(**not adopted**) on the grounds the clean data supports: no measurable benefit, and
24.9 s of CPU per evidence group on a path whose user already waits ~25 s for the
program channel. D50.3's "stands, with the limitation attached" is therefore closed:
the limitation is gone and the rationale is new.

**Decision 4 — the guard is a test, not a note.** `tests/test_retrieval_gold_split.py`
asserts that no part of the sealed split is reachable through a set claiming to be
validation, that a set's prose may not contradict its split field, and that the
builder may not hardcode the label again. The last matters most: the original defect
was a single literal in a dictionary, and every other check would pass on the current
files while the next generated set carried the same untruth.

**The pattern this is the second instance of.** D48's precondition was prose in a
decision entry, and prose blocks nothing; the fix was a gate in `run_campaign.py`.
Here the gate existed and read a field nobody computed. Both times the safeguard was
real and the thing it consulted was not. The rule worth carrying forward: **a guard
must read something derived from the data, never something asserted alongside it.**

**Reverses if:** nothing. The breach happened and is recorded. What remains open is
only whether RX-035 is re-measured, which Decision 3 leaves to the owner.

## D51 — Live questions run on the host API, are stored as demonstrations, and never enter a research count

**Date:** 2026-09-13 · **Status:** Active · **Spec:** §20, §26, §28, §29 ·
**Amends:** D28 (the database as a projection of the artifacts) · **Leaves unchanged:**
D30 (live QA off by default) · **Prompted by:** asking the system a real question
through its own API for the first time

Live QA had been off by default since D30, and nothing had exercised it end to end.
The first real question found four separate failures, each of which would have
broken a demonstration on its own:

1. **The API container could never answer.** `docker/api.Dockerfile` is deliberately
   web-only — no LangGraph, sentence-transformers, torch, LLM client or Docker
   access. `POST /questions/ask` imports the orchestrator, which imports LangGraph, so
   every request died on `ModuleNotFoundError` as a bare HTTP 500 with no reason.
2. **Every request rebuilt the embedding model, the retriever and three LLM clients.**
   The first question measured **365 s of wall time against 167 s inside the
   pipeline**, which is past nginx's 300 s proxy timeout: the dashboard would have
   received a 504 after the quota was already spent.
3. **The Ask screen sent no filing and no company.** Retrieval is scoped by company
   (RX-012) and the question parser does not extract one, so every question asked
   from the UI searched all five filings at once.
4. **A live answer was not stored**, so a user who navigated away lost it, and the
   full verification view could not open it.

The pipeline itself worked. Run from the project environment on a validation
question (HDFC Bank diluted EPS, gold 82.27), it retrieved the gold page, Channel A
answered 78.89, the executed program answered 82.27, the consistency engine returned
DISAGREE, the arbiter fired and declined — naming both figures and their evidence
blocks — and the answer was flagged **HIGH risk (0.912)**. A wrong answer, caught.

**Decision 1 — the API container stays web-only; live QA runs from the project
environment.** The Dockerfile's reasons still hold: roughly 2 GB of torch in a web
image nobody rebuilds, and — decisively — the program channel needs Docker. The
only way to give a container that is mounting `/var/run/docker.sock`, which hands
root-equivalent control of the host to a process that accepts file uploads. That was
rejected. Instead `RUN_LIVE_DEMO.bat` starts the same API code from `.venv` with
`FINVERIFY_ENABLE_LIVE_QA=1`, and the dashboard's nginx upstream became configurable
(`API_UPSTREAM`, default `api:8000`). The container's `/questions/ask` now returns
**503 naming the missing engine and the script**, instead of a reasonless 500.

**Decision 2 — the request-independent half of the pipeline is built once per
process**, and loaded in a background thread at startup when live QA is enabled, so
the first user does not pay for the model load. The proxy timeout rose to 600 s.

**Decision 3 — the Ask screen asks for a filing, defaulting to the first.** "All
filings at once" remains available, labelled as the weaker configuration it is. The
API also derives the company from a supplied `document_id`, so a client naming only
the filing cannot fall through to unscoped retrieval.

**Decision 4 — live answers are persisted to the database, and only there.** This
is the amendment to D28, so it is stated at its full size.

D28 makes the database a projection of the run artifacts. A live answer has no
artifact: nothing is written to `experiments/runs/`, and `tests/test_api.py` still
fails if the API layer so much as names that directory. So live answers are the one
class of row the database holds that no artifact backs. To keep that from
contaminating anything research-facing:

- they are stored through `ingest_run` — the code that loads campaign rows — under
  run `live-qa` and split `live`, so they cannot diverge in shape from recorded answers;
- each ask gets a **fresh question id**, because `ingest_run` upserts by
  (run, question, arm) and keeps the first row's answer fields — a reused id would
  show a stale answer beside new channel runs;
- `correct` stays NULL: a demonstration has no gold, so it is never graded;
- `/stats`, the Dashboard's run table and the Research run picker exclude them;
  `/answers` and `/experiments` include them, so Verification can list and reopen them;
- a database rebuilt from the artifacts does not contain them, **by design**.

For everything research-related, D28 holds unchanged.

**Decision 5 — D30 stands.** Live QA stays off unless enabled. The demo script
enables it deliberately; the default deployment does not.

**Reverses if** the system is ever deployed beyond a demonstration. The right shape
then is a separate worker image carrying the research engine and a sandbox runner
behind a queue, not the web image and not a host process.

---

## D52 — Five spec functions in the reasoning and verification core are scoped out explicitly

**Date:** 2026-09-13 · **Status:** Active · **Spec:** §16, §19, §21, §24, §25 ·
**Follows:** D49, which did the same for modules 2, 19 and 21 · **Prompted by:** the
audit of modules 8–17, whose figures were reproduced from the artifacts by hand
because its adversarial verifiers died on a quota limit

The first §43 audit (RX-052) never reached modules 8–17. The second did, and found
the same pattern D49 answered: functions the spec names, not implemented, and not
admitted to anywhere. Each is recorded here with its reasoning so the omission can be
argued with. Two further findings are **not** scoped out — they are gaps, and they
keep modules 12 and 14 PARTIAL.

**1 — Channel A does not report a confidence (§16).** The spec's structured output
includes one; the prompt asks for answer, unit, reasoning, evidence and figures used,
and nothing else. This is a design position, not an oversight: the project's risk
signal is **disagreement between two independent channels**, and a model's
self-reported confidence is the circular self-checking that CASE_STUDY_REPORT
limitation 1 names. Reasoning is recorded as one prose string rather than as steps.
*Reverses if* self-reported confidence is wanted as a baseline to beat — which would
be a reasonable experiment, and a separate arm.

**2 — The consistency engine compares numbers, not evidence or reasoning (§19).**
It compares value, unit, sign, scale, currency and the deterministic result. It does
not compare the evidence each channel used, because **both channels receive the
identical evidence blocks from one retrieval step** (CASE_STUDY_REPORT limitation 3),
so an evidence comparison would return "no difference" by construction. It does not
compare reasoning, because Channel B's reasoning is a program and Channel A's is
prose, and no comparison between the two would be defensible. *Reverses if* the
channels are ever given separately retrieved evidence.

**3 — The arbiter does not re-retrieve (§21).** It judges the two answers against
the same evidence they were produced from. Stated with its cost, because the cost is
real: RX-045 found that on **9 of the 16** resolved disagreements neither channel held
a correct answer, which is exactly the situation re-retrieval could help. It is
scoped out because a re-retrieving arbiter becomes a third answering channel with
different evidence, and its "resolution accuracy" would then measure the new
retrieval rather than the judgment §5.4 defines. That is a scope decision for this
project, not a claim that re-retrieval would not help.

**4 — The explanation does not show Channel B's calculation (§24).** The executed
program's source is not recorded in the run artifacts — the program record carries
its value and execution status — and `ProgramRun.program` is NULL on every row.
Exposing it needs a recorder change and new runs, neither of which is in scope. The
**source document** half of the same requirement is met as of D51: every evidence
item the API serves names its filing.

**5 — There is no evidence-validation stage before reasoning (§25).** Evidence
grounding is assessed **after** reasoning, as the evidence facet of the risk score
("the answer is grounded in retrieved evidence" / "retrieved but thin"). A gate that
filtered evidence before the channels read it would change what both see and couple
them through the filter, which works against the independence the design depends on.
README's architecture diagram drew the stage as though it existed; it is corrected.

**Not scoped out — kept as gaps:**

- **Module 12:** `report.disagreements` is never serialised, so only the four "hard"
  types can reach an artifact, and only `scale_mismatch` ever has (4 times). The spec
  §20 `difference` field is computed and discarded.
- **Module 14:** six of the spec's ten minimum error kinds — wrong evidence, number,
  year, formula, metric, and arithmetic error — are declared and never assigned. The
  test that looked like coverage asserted enum membership only; it is renamed to say so.

---
