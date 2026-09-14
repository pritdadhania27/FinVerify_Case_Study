# Architecture

The system exists to answer one question — *can independent dual-channel
reasoning and consistency verification reliably detect numerical hallucination
in agentic financial document QA?* — so the architecture is organised around
what that question needs to be **falsifiable**, not around what a product would
need.

Two consequences run through everything below:

1. **Independence is enforced by construction, not by convention.** The program
   channel has no parameter through which Channel A's answer could arrive.
2. **Uncertainty is never resolved into confidence.** Every layer distinguishes
   *"we checked and it was fine"* from *"nobody looked"*, and refuses to collapse
   the second into the first.

---

## The pipeline

```
question
   │
   ▼
question understanding ──────────────────┐   ONE QuestionSpec feeds everything.
   │  (deterministic parser + lexicon)   │   D19: a mis-parse makes both channels
   ▼                                     │   wrong identically, so their agreement
planned retrieval                        │   proves nothing. Common-mode path.
   │  (hybrid: semantic + BM25 + RRF)    │
   ▼                                     │
evidence blocks ─────────────────────────┘   ONE formatter, both channels. If they
   │                                          saw different renderings, disagreement
   ├──────────────┬──────────────┐            could come from the rendering.
   ▼              ▼              ▼
Channel A     Channel B     Deterministic
(natural      (program →    verifier
 language)     AST allow-   (regex + lexicon,
   │           list →        NO model)
   │           sandbox)         │
   └──────────────┴─────────────┘
                  │
                  ▼
          consistency engine
          (verdict + continuous score,
           base_score and coverage kept apart)
                  │
         DISAGREE │ else
                  ▼
          verification agent  ← sees the question and the evidence,
          (arbiter)             NEVER which channel said what
                  │
                  ▼
          risk assessment  ── continuous score, higher = more likely wrong
                  │
                  ▼
          explanation  ── derived from recorded state, never generated
```

`backend/agents/orchestrator.py` is that graph, in LangGraph. Every arm —
baseline or ablation — is an `ArmConfig` over the *same* graph (D26), so two
arms that should differ in one component provably differ in one field.

---

## Why three channels rather than two

Channel A and Channel B are the research contribution. The deterministic
verifier is the thing that makes the *both-agree-wrong* cell visible.

| | Channel A | Channel B | Deterministic |
|---|---|---|---|
| Reasoning | natural language | executed Python | regex + lexicon |
| Model in the arithmetic path | yes | yes (writes the code) | **no** |
| Applies to | everything | everything | only questions it can bind |
| Fails by | prose instead of JSON | execution error | refusing |

The deterministic channel **abstains rather than guesses**. That distinction is
load-bearing: on a lookup question there is no arithmetic to check, and
recording that as a failed verification biased the primary metric until the
coverage penalty was fixed to count *achievable* corroboration rather than
absolute.

---

## The four layers

### Documents → facts

```
PDF → PyMuPDF text  ─┐
    → Camelot tables ─┤→ chunks (tables stay tables, header + unit banner repeated)
    → OCR if no text ─┘         │
                                ▼
                        FinancialFact  (Module 4)
                        metric · value · unit · currency · year ·
                        company · document · page · section · table ·
                        row · column · year_source · warnings
```

Three traps handled here, each of which produces a plausible wrong number rather
than an error:

- **Scale.** ₹ crore (10⁷) vs lakh (10⁵) vs million, often mixed within one
  document. Scale is carried from the table's banner into every cell (D13) and
  flagged as inherited.
- **The prior-year column.** Taking the first numeric cell is right by
  convention and wrong the moment a table leads with its comparative. Columns
  are labelled from the table's own header; a fact whose year could not be read
  says so rather than inheriting the document's fiscal year.
- **Merged columns.** Camelot collapses two year-columns into `"76  161"`, and a
  naive parse returns 76. Merged cells are split, flagged, and left year-less
  unless the merged header names the years.

### Retrieval

Hybrid: semantic (E5-base-v2, chosen by measurement in RX-003) + BM25, fused
with plain RRF. RX-004 found the apparent need for reranking was largely a
query-formulation artifact, so reranking is **deferred with reasons**, not
dropped.

Both channels read the **same** evidence. That is deliberate — the question is
whether two *reasoning processes* over identical evidence disagree informatively
— and it is also the largest common-mode failure path (H2), which is why
detection metrics are stratified by provenance rather than pooled.

### Verification

```
consistency.compare(a, b, deterministic) → ConsistencyReport
    verdict          AGREE | DISAGREE | UNCERTAIN
    base_score       agreement BEFORE the coverage penalty
    coverage         how much achievable corroboration was obtained
    disagreements    scale / sign / currency / unit-kind / magnitude / …
```

`base_score` and `coverage` are exposed **separately** because they mean
different things: contradiction is *evidence of error*, missing corroboration is
*uncertainty*. Pre-multiplying them throws the distinction away and the risk
layer cannot recover it from the product.

`confidence.assess(...)` combines the signals with a damped noisy-OR — the
strongest signal sets a floor, weaker ones close part of the remaining gap — and
reports `calibrated=False`. AUROC is rank-based and therefore valid on an
uncalibrated score; **Brier and ECE are not, and must not be quoted** until it is
fitted.

### Evaluation

```
run artifacts (JSONL, append-only, committed)   ← the source of truth (D4)
        │
        ├── evaluation/report.py     → every table in the write-up
        ├── evaluation/case_study.py → spec §35's eight analyses
        └── backend/database/ingest  → PostgreSQL, a queryable PROJECTION (D28)
```

The direction is one-way and always will be. Losing the database costs a
re-ingest; losing the artifacts would cost the results.

---

## The product tier

```
Frontend (React, nginx)
     │  /api  →
FastAPI  ── reads the projection; POST /questions/ask is OFF by default (D30)
     │
     ├── PostgreSQL   (Alembic migrations)
     ├── Qdrant       (chunk vectors, metadata filters)
     └── Agent engine ── runs on the HOST, not as a service
             └── Sandbox: a FRESH container per execution
```

The sandbox is deliberately not a long-lived service. Isolation is per-run
rather than per-deployment: a container that has already run other generated
code is not a clean room.

---


*Verification detail: [`VERIFICATION.md`](VERIFICATION.md) · Retrieval:
[`RAG.md`](RAG.md) · Decisions: [`DECISIONS.md`](DECISIONS.md) · Deployment:
[`DEPLOYMENT.md`](DEPLOYMENT.md)*
