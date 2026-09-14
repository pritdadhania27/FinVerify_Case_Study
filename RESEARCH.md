# Research Specification

**Project:** FinVerify-AI
**Title:** A Dual-Channel Agentic Framework for Numerical Hallucination Detection
in Financial Document Question Answering

Companion documents: [`LITERATURE_REVIEW.md`](LITERATURE_REVIEW.md) ·
[`RESEARCH_QUESTIONS.md`](RESEARCH_QUESTIONS.md) ·
[`HYPOTHESES.md`](HYPOTHESES.md) · [`EVALUATION.md`](EVALUATION.md)

---

## 1. Problem

Financial analysts increasingly ask language models to answer numerical questions
over annual reports. The failure mode that matters is not a model saying "I don't
know" — it is a model producing a **specific, plausible, wrong number**, with a
fluent derivation and no signal that anything went wrong. In finance a 10× scale
error reads as ordinary until someone reconciles it against the filing.

Two properties make this domain unusually hostile:

1. **Evidence is heterogeneous.** The number lives in a table; the fact naming it
   lives in prose; the unit convention lives in a header three pages earlier.
2. **Errors are silent.** Natural-language arithmetic fails without raising
   anything. There is no exception, no stack trace — just a confident wrong
   number.

## 2. Research gap

Established separately, not yet combined (full argument in
`LITERATURE_REVIEW.md` §6):

- Program-aided reasoning (PAL, PoT) improves numerical **accuracy**, validated
  on FinQA / ConvFinQA / TAT-QA.
- Consistency-based hallucination detection (SelfCheckGPT) works over
  **homogeneous** samples that share a model's systematic biases.
- Self-correction without **external** feedback is unreliable and can degrade
  performance (Huang et al., ICLR 2024).

Nobody has systematically measured whether **disagreement between reasoning
channels of different modality** — natural-language reasoning vs. an *executed*
program vs. a *deterministic* recomputation — is a reliable **detector** of
numerical hallucination in financial document QA.

The design intuition follows directly from Huang et al.: if self-correction fails
without external feedback, then verification must come from something that is not
the model. **A Python interpreter is not a second opinion — it has no beliefs
about revenue and cannot be persuaded by a plausible derivation.**

## 3. Contribution claimed

1. An architecture in which numerical answers are produced by two
   modality-independent channels and cross-checked by a deterministic verifier.
2. An empirical characterisation of **when cross-modality disagreement detects
   error and when it cannot** — in particular, its predicted near-blindness to
   retrieval-caused error, measured rather than glossed.
3. A detection-per-unit-cost analysis, since dual-channel verification is not
   free.
4. **FinVerify-IND**, a human-validated Indian financial QA benchmark with
   evidence spans and gold programs.

Claims 1–3 stand or fall on `HYPOTHESES.md` H1–H5. Claim 4 is a deliverable.

## 4. Scope

**In:** numerical QA over published English-language financial reports; error
detection; evidence-grounded explanation; reproducible evaluation.

**Out:** non-numerical hallucination, multilingual reports, real-time market
data, model fine-tuning, investment advice. The system reports what a document
says and how confident it is that it read it correctly. It does not advise.

## 5. Threats to validity, and what is done about each

| Threat | Mitigation |
|---|---|
| **Channels are not genuinely independent** — the central threat; if false, disagreement measures sampling noise and the RQ is unanswerable | **Different vendors** per channel (Gemini vs Groq-served open model), disjoint prompts, no cross-channel state reads enforced by test, runtime independence check, plus ablation arm H (decisions D1/D8a, hypothesis H4) |
| **Shared retrieval is common-mode failure** | Not solvable by architecture; measured by stratifying detection metrics on error provenance (H2, `EVALUATION.md` §5.3) |
| **Gold labels wrong** | Human validation with recorded inter-annotator disagreement |
| **Test-set leakage** | Sealed split, access log, SHA-256 manifest, single evaluation per frozen methodology |
| **Run-to-run nondeterminism** | Temperature pinned at 0 (D7a); variance still measured by n=5 repeats since temperature 0 is not bit-level determinism; improvements below the noise floor are not claimed |
| **Free-tier models are weaker than frontier models** | Absolute accuracy will be lower. The claim is about *relative* detection across arms on identical inputs; absolute figures are reported as free-model figures and never compared to published frontier results |
| **Cherry-picked arms** | All arms pre-registered; all results reported including negatives (spec §46) |
| **Metric chosen to flatter** | Metrics defined and frozen before evaluation; TAU sensitivity published |

## 6. Deliverables

Research engine and case study are **one system** — the implementation exists to
generate the evidence (spec §35). Every claim in the write-up must trace to
`dataset → experiment → raw result → metric → analysis`.

- Software: document pipeline, hybrid RAG, dual channels, deterministic verifier,
  consistency engine, verification agent, explainability, research dashboard.
- Data: FinVerify-IND with provenance and validation protocol.
- Research: literature review, baselines, 8-arm ablation, error analysis,
  statistical analysis, cost/latency analysis.
- Case study: real Indian filings, end-to-end, with failure analysis.

## 7. Status

Module 0 (research foundation) drafted; methodology **not yet frozen**. Current
state: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).
