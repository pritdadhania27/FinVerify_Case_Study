# Experiment Log

One entry per measured run. **Failed, abandoned and inconvenient results are
recorded here too** (spec §46.7) — an experiment log that only holds successes is
a marketing document.

Raw artefacts live in `evaluation/reports/` and `experiments/runs/` and are
append-only. Entries below point at them rather than restating them.

---

## RX-001 — Retrieval on the Infosys FY2023-24 consolidated statements

**Date:** 2026-08-23 · **Split:** validation · **Status:** complete, and the
result is worse than the module's own docstring claimed

**Question.** Module 6 shipped with retrieval quality unmeasured and looking
weak. Does it work, and does hybrid fusion earn its place?

**Setup.**

| | |
|---|---|
| Corpus | Infosys FY2023-24 consolidated financial statements, 79 pages |
| Gold | `datasets/retrieval_eval/infosys_fy24_v1.json` — 22 questions, 62 evidence spans |
| Index | 906 chunks (554 table, 352 text), Qdrant, cosine |
| Embeddings | BAAI/bge-base-en-v1.5, CPU, query-instruction prefix applied |
| Keyword | BM25Okapi over the same filtered candidate set |
| Fusion | Reciprocal Rank Fusion, K=60, equal weights |
| Metrics | `EVALUATION.md` §4, span matching per D18 |

### Result — hybrid fusion is **beaten by one of its own inputs**

Macro averages over 22 questions:

| Arm | R@1 | R@5 | R@10 | MRR | Evidence retrieval acc. @10 |
|---|---|---|---|---|---|
| semantic only | 0.182 | 0.318 | 0.477 | 0.251 | 0.455 |
| **keyword only (BM25)** | **0.227** | **0.591** | **0.705** | **0.355** | **0.682** |
| hybrid (RRF, equal weights) | 0.273 | 0.455 | 0.568 | 0.349 | 0.545 |

`backend/retrieval/hybrid.py` asserted that fusing the two legs is worthwhile
because they fail in different places. On this corpus that is not what happens:
**BM25 alone beats the fusion at every K.** RRF weights both legs equally, so
averaging a strong ranker with a weak one lands between them. The claim in the
docstring was written from reasoning, not measurement, and the measurement
disagrees.

This is not yet a general finding. It is one corpus, one document, 22 questions,
one embedding model — see the limits below. It is enough to stop treating "hybrid
is better" as established.

### What the diagnosis found, which matters more than the ranking

`--diagnose` separates *the evidence was ranked too low* from *the evidence never
reached the index*. **Every miss, in every run, was `ranked_too_low`.** Extraction
and chunking are placing the evidence in the index; retrieval is failing to
surface it. That points all remaining work at ranking and none at extraction —
which is the opposite of where the effort would have gone on the eyeball
evidence, since the extraction defects were the visible ones.

### Three runs, and why the numbers moved

Kept in full because the movement is the interesting part.

| Run | Arm label | Hybrid R@10 | Keyword R@10 | What changed |
|---|---|---|---|---|
| 1 | `bge-base` | 0.364 | 0.455 | baseline, as Module 6 shipped |
| 2 | `bge-base-postfix1` | 0.409 | 0.545 | merged table headers, section detection, spillover removal |
| 3 | `bge-base-goldfix` | 0.568 | 0.682 | **gold set corrected — see below** |
| 4 | `bge-base-final` | 0.568 | 0.705 | heading detection tightened (wrapped-line and column-header fragments no longer become section names) |

**Run 2 → 3 is not a retrieval improvement.** It is the correction of a
measurement error, and it was the largest single movement in the table. The first
gold set listed only the evidence location chosen by hand, but financial
statements restate figures across the document: Infosys' revenue appears on four
pages (the P&L, note 2.18, the segment note, the function-wise classification),
profit for the year on four, trade payables on four. The metric was therefore
scoring *"did retrieval find my page"*, and 29 of 62 spans were missing. Every
miss looked like a retrieval defect.

Two questions (R03, R13) even *regressed* between runs 1 and 2 for a reason that
only makes sense once this is understood: removing duplicated table text deleted
the flattened page-text fragment that had been satisfying the span by luck, and
the properly structured table chunk ranked lower than the fragment had.

`--validate-gold` now re-runs the completeness audit and reports any group that
lists fewer pages than state its figure, so this class of error announces itself
instead of inflating a deficit.

### Limits of this result

- **One document, one company, one sector.** Cross-sector transfer is untested;
  a bank's balance sheet is structurally different (see `configs/corpus.json`).
- **22 questions** is small. Differences below roughly 0.1 in these tables are
  within the noise a set this size can support, and no significance testing has
  been done.
- **Validation split, used for tuning.** These numbers must never be reported as
  test results.
- **Anchor-based span matching (D18)** is a weaker claim than offset overlap: it
  says the evidence is on the page and mentions these strings.
- Question types are hand-chosen to include known traps (paraphrase, negatives,
  multi-hop, unit traps). That makes the set *harder* than a random sample and
  the absolute numbers correspondingly pessimistic.

**Raw:** `evaluation/reports/retrieval_bge-base_*.json`,
`retrieval_bge-base-postfix1_*.json`, `retrieval_bge-base-goldfix_*.json`,
`retrieval_bge-base-final_*.json`

---

## RX-002 — Does the dense leg earn its place? A fusion weight sweep

**Date:** 2026-08-24 · **Split:** validation · **Depends on:** RX-001

**Question.** RX-001 showed equal-weight RRF scoring below BM25 alone. Is there a
weight at which fusion wins, and does the semantic leg contribute anything the
keyword leg does not?

### The sweep has no interior optimum

Each question's two legs were computed once and re-fused at every weight, so all
rows are scored on identical candidate sets. Keyword weight fixed at 1.0.

| semantic weight | R@5 | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|---|
| **0.00** (BM25 alone) | **0.591** | **0.705** | **0.355** | **0.682** |
| 0.25 | 0.500 | 0.659 | 0.346 | 0.636 |
| 0.50 | 0.455 | 0.614 | 0.333 | 0.591 |
| 0.75 | 0.455 | 0.568 | 0.349 | 0.545 |
| 1.00 (plain RRF) | 0.455 | 0.568 | 0.349 | 0.545 |
| 1.50 | 0.455 | 0.523 | 0.316 | 0.500 |
| 2.00 | 0.455 | 0.523 | 0.297 | 0.500 |

**Monotonic.** Every amount of semantic weight costs recall. There is no interior
optimum to tune toward, which is a more useful result than a tuned weight would
have been — it says the problem is not the setting.

### But the dense leg is not useless, and that is the interesting part

Per-question, at K=10, which arm retrieves complete evidence:

| | count | which |
|---|---|---|
| solved by the **semantic leg alone** | **1** | R02 — *"How much did the company owe its suppliers?"* |
| solved by the **keyword leg alone** | 6 | R06, R11, R12, R17, R19, R22 |
| lost by fusion that keyword alone solves | 3 | R17, R19, R22 |
| rescued by fusion that neither leg solves | 0 | — |

R02 is exactly the case BM25 cannot serve by construction: neither "owe" nor
"suppliers" appears anywhere in the evidence, which reports *trade payables*. A
purely lexical retriever cannot answer a paraphrased question, and paraphrase is
not an exotic query type in this domain.

So the dense leg has a real and narrow contribution, and **rank-averaging is the
wrong instrument for combining a narrow contribution with a broad one**: equal
weights spend three keyword wins to preserve none of the one semantic win.

### What this does NOT license

**The default is deliberately left at plain RRF.** Setting it to BM25-only
because BM25-only won on 22 questions would be tuning a headline configuration on
a validation set too small to support it — the same "optimise against what you
happened to look at" failure that this harness exists to prevent, in a more
respectable costume. The finding is recorded; the configuration decision is
**deferred until the gold set covers more than one document**, and is listed as
such in `TODO.md`.

The mechanism that plausibly fixes this — a reranker over the union of both legs,
rather than rank-averaging them — is now justified by measurement rather than by
convention, and is the next retrieval task.

**Raw:** `evaluation/reports/retrieval_weight_sweep_finverify_chunks.json`

## RX-003 — D2 settled: E5-base beats BGE-base on this corpus

**Date:** 2026-08-24 · **Split:** validation · **Depends on:** RX-001, RX-002

**Question.** D2 has been open since Milestone 1: BGE or E5? It was always to be
decided by retrieval measurement rather than preference. Same 906 cached chunks,
same BM25 leg, same gold set — only the embedding model differs.

| Arm | R@5 | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|---|
| semantic, bge-base-en-v1.5 | 0.318 | 0.477 | 0.251 | 0.455 |
| **semantic, e5-base-v2** | **0.432** | **0.568** | **0.290** | **0.545** |
| keyword (BM25, identical both runs) | 0.591 | 0.705 | 0.355 | 0.682 |
| hybrid, bge-base | 0.455 | 0.568 | 0.349 | 0.545 |
| **hybrid, e5-base** | **0.523** | **0.682** | **0.341** | **0.636** |

**E5 wins the dense leg on every metric**: +0.09 Recall@10 and +0.09 evidence
accuracy standalone, +0.11 and +0.09 in fusion. The BM25 leg is byte-identical
across the two runs, as it must be — same chunks, same tokenizer — which is a
useful check that the comparison isolates the embedding model and nothing else.

### The weight sweep changes shape with the better model

| semantic weight | R@5 | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|---|
| 0.00 (BM25 alone) | 0.591 | 0.705 | 0.355 | **0.682** |
| 0.25 | 0.568 | 0.659 | 0.385 | 0.636 |
| 0.50 | 0.523 | 0.659 | 0.362 | 0.636 |
| 0.75 | 0.477 | **0.727** | 0.366 | **0.682** |
| 1.00 (plain RRF) | 0.523 | 0.682 | 0.341 | 0.636 |
| 1.50 | 0.523 | 0.682 | 0.339 | 0.636 |
| 2.00 | 0.568 | 0.682 | 0.340 | 0.636 |

With BGE the sweep was monotonically bad. With E5 it is not — `w_sem = 0.75`
reaches R@10 = 0.727, above BM25 alone.

**That apparent win is not claimed.** 0.727 vs 0.705 is roughly half an evidence
group across 22 questions, the curve is non-monotonic on either side of it (0.50
and 1.00 are both worse), and **evidence-retrieval accuracy is 0.682 at both
points — identical**. A peak that appears in one metric, vanishes in the metric
that actually gates downstream reasoning, and sits inside the noise a 22-question
set can support is a peak worth reporting and not worth adopting.

**Honest summary:** upgrading the dense leg moves fusion from *clearly worse than
BM25 alone* to *roughly par with it*. No configuration measured so far is
reliably better than the keyword leg on its own.

**Raw:** `evaluation/reports/retrieval_e5-base_*.json`,
`retrieval_weight_sweep_finverify_e5.json`

## RX-004 — The retrieval deficit was mostly the *question*, not the retriever

**Date:** 2026-08-24 · **Split:** validation · **Supersedes the conclusion of:** RX-002

**Question.** RX-001/RX-003 left evidence-retrieval accuracy at 0.636 and every
miss diagnosed as `ranked_too_low`. Before building a reranker, where in the
ranking does the evidence actually sit, and why is it there?

### Diagnosis 1 — depth: the evidence is deep, and two misses are unreachable

Recall by depth, hybrid, e5-base:

| K | 5 | 10 | 20 | 50 | 100 | 400 |
|---|---|---|---|---|---|---|
| evidence acc. | 0.500 | 0.636 | 0.727 | 0.818 | 0.909 | 1.000 |

The missed groups sat at ranks 12, 16, 40, 43, 70, 74 — and **336 and 362**. So
"add a reranker over the top 100" would have recovered six of eight and been
structurally incapable of recovering the other two. Worth knowing before building
one.

### Diagnosis 2 — why: the question's content words are the corpus's filler

Document frequency across the 906 chunks, for *"What were total assets as at
March 31, 2024?"*:

| term | what | were | total | assets | as | at | march | 31, | 2024 |
|---|---|---|---|---|---|---|---|---|---|
| % of chunks | 0.0 | 5.0 | **16.4** | **25.2** | 56.5 | 35.2 | 33.7 | 32.7 | 26.9 |

Every term a reader would call *the question* is among the most common terms in
the corpus, and the scaffolding around it matches a third of the document. BM25
sums term contributions, so chunks stuffed with common terms outscore the one
chunk that answers. Even a highly selective term does not rescue this: `finance`
appears in 0.8% of chunks, and *"What was the finance cost for the year ended
March 31, 2024?"* still failed, because `the` (82%), `for` (45%), `march` (34%)
and `2024` (27%) swamped it.

### The fix, and it is free

Strip interrogative and reporting-period scaffolding before searching. No
re-indexing, no new model, no new component — `backend/retrieval/query.py`.

| Configuration | R@5 | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|---|
| hybrid, raw questions | 0.523 | 0.682 | 0.341 | 0.636 |
| **hybrid, scaffolding stripped** | **0.773** | **0.864** | **0.551** | **0.818** |
| keyword, scaffolding stripped | 0.682 | 0.795 | 0.544 | 0.773 |

**+0.18 evidence accuracy and +0.21 MRR**, the largest single retrieval gain
measured in this project, from deleting words.

A `kind="table"` filter was tested in the same pass and **rejected**: alone it
gained nothing (0.682), and combined with stripping it *lost* ground (0.727 vs
0.818), because several evidence groups are legitimately satisfied by narrative
chunks. Recorded because it is the obvious idea and it does not work.

### This supersedes RX-002's conclusion

Re-running the weight sweep on clean queries:

| semantic weight | 0.00 | 0.25 | 0.50 | 0.75 | 1.00 | 1.50 | 2.00 |
|---|---|---|---|---|---|---|---|
| evidence acc.@10 | 0.773 | **0.818** | **0.818** | **0.818** | **0.818** | **0.818** | **0.818** |

RX-002 concluded that fusion was beaten by its own keyword leg and that the
sensible default was in doubt. **That conclusion was an artifact of noisy
queries.** With scaffolding removed, every non-zero weight beats BM25 alone and
the curve is flat across all of them — so plain RRF (w = 1.0) is correct, and
*no hyperparameter needs to be tuned or reported*. The deferred fusion decision
in `TODO.md` is resolved by making the question cleaner rather than by choosing a
weight.

The general lesson is worth keeping for the reasoning modules: **a measured
deficit is not evidence about the component you were looking at.** Two rounds of
work were pointed at fusion and reranking by a number whose real cause was the
query text.

### What remains

3 of 22 questions still miss evidence at K=10:

| qid | type | why |
|---|---|---|
| R05, R15 | lookup / cross-table | *"total assets"* — both terms are among the corpus's most common; there is no selective term to find |
| R14 | cross-statement multi-hop | *"return on equity"* needs profit **and** equity from two statements; one query cannot rank both |

Both residual classes point at **question understanding (Module 7)**, not at
retrieval: decomposing a multi-hop question into per-figure sub-queries, and
routing a non-selective query through the metadata filters the index already has.

**Raw:** `evaluation/reports/retrieval_e5-base-stripped_*.json`,
`retrieval_weight_sweep_finverify_e5.json`

## RX-005 — Module 7: decomposition fixes what no retriever could

**Date:** 2026-08-24 · **Split:** validation · **Depends on:** RX-004

**Question.** RX-004 left three failures and argued all three were question
problems rather than retrieval problems. Does question understanding actually fix
them?

| Configuration | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|
| hybrid, stripped query (RX-004) | 0.864 | 0.551 | 0.818 |
| **+ Module 7 planning** | **0.932** | **0.616** | **0.909** |

**Reproduce with:** `python scripts/evaluate_retrieval.py --arms --planned`
(added 2026-08-24; this row was originally measured from a scratch script and was therefore not reproducible from the repository — a number only a deleted script can regenerate is a claim from memory. Re-run on 2026-08-24 against the same collection: identical to three decimals.)

Two questions fixed, none broken:

- **R14** *"return on equity"* — decomposed into `profit for the year` +
  `total equity`, retrieved separately, interleaved. This had failed at every K
  in four consecutive measurements. No amount of retrieval tuning reaches it:
  "return on equity" is a line item in no document.
- **R05** *"total assets"* — given its statement (`consolidated balance sheet`)
  as a query hint, because the phrase has no selective term of its own.

### Three mechanisms tried; one kept

Recorded in full because two of them cost measurable ground and the reasons
generalise.

| Mechanism | Result | Kept? |
|---|---|---|
| Decompose derived metrics into components | +R14 | **yes** |
| Statement hint on *any* lexicon match | +R05, **−R16** | no |
| Issue hinted *and* unhinted queries, interleaved | −R05, −R16 | no |
| Statement hint only on an **exact** line-item match | +R05, nothing lost | **yes** |

The middle two are the interesting failures. Attaching the statement to any
lexicon match lost R16: *"total other financial liabilities"* merely *contains*
the lexicon entry "other financial liabilities", and that figure is reported in
note 2.13, not on the balance sheet the lexicon points at — a confidently wrong
hint. Issuing both queries instead split a 10-slot budget in half and lost both.
The rule that works is narrow: apply the hint only when the stripped question
reduces *exactly* to a known line item, so the statement is known rather than
inferred from a substring.

### Two bugs the measurement exposed, both silent

- **A metadata filter that matched nothing returned nothing.** `extract_fiscal_year`
  took the *first* year in the question, so *"between March 31, 2023 and March 31,
  2024"* produced `fiscal_year="2022-23"` — a year this single-year corpus does
  not contain. Retrieval returned an empty list, silently, and R13 regressed the
  moment filtering was switched on. A question naming more than one year now
  yields **no** filter, which is the correct answer rather than a parse failure.
- **`"pat"` matched inside `"patents"`.** The metric lexicon used plain substring
  matching, so *"How many patents were filed?"* confidently resolved to "profit
  for the year" (PAT). Also latent: `eps` in "steps", `roa` in "broad". This is
  the same bug class as `"in(cr)ease of 500"` read as 500 crore in
  `financial_value.py` — caught there by probing, caught here by a test written
  to assert the parser does **not** guess. Aliases are now word-boundary anchored.

### What remains, and why it is not tuned away

Two of 22 still miss evidence at K=10:

| qid | why |
|---|---|
| R02 | *"owe its suppliers"* — pure paraphrase. Only the dense leg can serve it, and it ranks the answer below 10. |
| R15 | decomposes correctly, but with two sub-questions each holding 5 of 10 slots, the `total assets` component's evidence sits below its share. |

Both are addressable — a reranker for R02, a larger budget for R15 — and neither
is attempted here. At 22 questions the difference between 0.909 and 0.955 is one
question, and choosing a mechanism on that basis is fitting noise. The gold set
needs to cover more than one document before these are worth chasing.

**Raw:** measured via `retrieve_for_spec` against `finverify_e5`; per-question
table in the commit for this entry.

## RX-006 — First live dual-channel run (the vertical slice)

**Date:** 2026-08-24 · **Split:** validation · **Status:** the mechanism works;
throughput does not

**Question.** Do two independently-bound channels actually produce comparable
answers from the same evidence, end to end, against live providers?

**Setup.** Channel A `gemini/gemini-3.7-flash`, Channel B
`groq/openai/gpt-oss-120b` — cross-provider, so independence is by construction
(D1). 4 questions from the retrieval gold set, e5-base retrieval, top-8 evidence,
temperature 0. Artifact: `experiments/runs/slice_20260824T013039Z/`.

| # | Question | Channel A | Channel B | Verdict |
|---|---|---|---|---|
| 1 | trade payables FY24 | 3,956 crore | 3,956 crore | **AGREE** (0.900) |
| 2 | *"owe its suppliers"* (paraphrase) | declined — evidence insufficient | program printed `null` | UNCERTAIN (0.250) |
| 3 | revenue from operations | 429 quota | 153,670 crore | UNCERTAIN (0.275) |
| 4 | profit for the year | 429 quota | 26,248 crore | UNCERTAIN (0.275) |

### What worked

**Question 1 is the project's first end-to-end result.** Channel A read the
evidence and answered 3,956 crore; Channel B *wrote a program*, which passed the
AST allowlist, executed in a container in 2.2s, and printed 3,956 crore. Two
different vendors, two different reasoning modes, one answer, scale preserved
through both paths.

**Question 2 is arguably the more interesting one.** Retrieval failed (R02 is a
known miss — pure paraphrase), and **both channels abstained rather than
inventing a number.** Channel A said the evidence did not contain the figure;
Channel B's program printed `null` with a reason. The consistency engine reported
UNCERTAIN rather than agreement. That is the designed behaviour on missing
evidence, observed for the first time — and it is also the H2 common-mode case,
since both were blinded by the same retrieval failure.

### What did not: throughput

`gemini-3.7-flash` returned HTTP 429 after ~17 requests in a day, naming
`generate_content_free_tier_requests, limit: 20`. The registry recorded
**1,500/day** for this provider, taken from published Flash-class limits — wrong
for this model id by roughly 75×.

**This changes the evaluation plan, not just a constant.** At 20 requests/day,
Channel A answers 20 questions/day. The 500-question FinVerify-IND evaluation
across 6 arms is *months* of wall-clock on this binding. Options, none yet
chosen: bind Channel A to a Groq-served model and find a different second vendor;
add a third provider for spillover; reduce the question count with a stated
power argument; or accept a long, resumable campaign. **Decide before Module 26,
not during it.**

### Three bugs, all found by running it rather than by tests

- **Non-ASCII in generated code crashed the pipeline.** `subprocess.run(text=True)`
  encodes stdin with the host codepage; on Windows that is cp1252, which cannot
  represent `₹`, an en-dash, or the non-breaking hyphen that actually appeared in
  a generated comment. `UnicodeEncodeError` propagated **out of the containment
  layer** — the component whose entire contract is to return an `ExecutionResult`
  rather than raise. Fixed with explicit UTF-8 plus a last-resort guard, and
  regression-tested across five characters.
- **A spent quota was retried for 621 seconds.** A pace limit and an exhausted
  allowance share HTTP 429 and had one retry policy. `QuotaExhaustedError` now
  separates them and is never retried.
- **`ConsistencyReport.band` is a method, not a property**, so the run printed a
  bound method into its own report. Cosmetic, but it was in an artifact.

**Raw:** `experiments/runs/slice_20260824T013039Z/`

## RX-007 — Adversarial audit of the verification layer, and what running it found

**Date:** 2026-08-25 · **Status:** four defects fixed, one open research question

**Question.** A false positive in the consistency engine (RX-006 follow-up) was
found by *running* the three-channel slice, not by any of the 31 tests that
passed throughout. If one defect of that shape survived the suite, how many more
are there?

**Method.** Five independent audit lenses over `consistency.py`,
`financial_value.py`, the deterministic channel, the reasoning channels, and a
research-validity sweep — each finding then handed to an adversarial verifier
briefed to *refute* it by default and to reproduce the failure against the real
code before accepting it. Every confirmed finding below was **also reproduced by
hand** before being acted on.

### Four defects, all invisible to a green test suite

| # | Defect | Why it mattered |
|---|---|---|
| 1 | `_compare_pair` rejected PERCENT vs RATIO on the unit-kind **label**, before `canonical()` ran | A return-on-equity question where all three channels agreed numerically (0.2977 / 0.2977 / 0.2967) scored **DISAGREE at 0.000** — a false positive in the detector, on the question type the detector exists for |
| 2 | An unclosed `<think>` block let an **abandoned** mid-thought figure become the channel's answer | A model wrote *"First guess: {3956}. No — the question asks for the consolidated figure, so I should use"* and was cut off; the channel returned 3956 with `available=True`. A genuine channel failure became confident fabricated data entering the agreement comparison |
| 3 | Channel B kept a **second, smaller copy** of the unit vocabulary | `INR trillion`, `Rs cr`, `Rs mn`, `USD bn`, `INR lacs`, `Rs. '000` all collapsed to `Scale.UNIT` in Channel B while Channel A resolved them correctly — gaps of 10³ to 10¹². Identical text resolving to different scales produces a clean power-of-ten split, which the engine then labels **SCALE_MISMATCH**: a parser gap manufacturing the project's headline error class |
| 4 | Coverage penalty counted **absolute** channels, not achievable ones | Every lookup capped at 0.90 because the deterministic channel abstains by design, while computed questions reached 1.00. The score partly encoded *"is this a lookup?"* rather than *"is this wrong?"* |

Defects 1, 3 and 4 all push the same direction: they inflate measured
disagreement and depress AUROC. The method was being made to look **worse** than
it is, for reasons having nothing to do with the models.

### An open research question the audit did not cause

Running return on equity through all three channels:

| Channel | Answer | Operands used |
|---|---|---|
| A | 29.77% | profit attributable to owners ÷ equity attributable to equity holders |
| Deterministic | 29.67% | profit for the year ÷ total equity |
| B | 32.08% | profit ÷ **average** equity |

All three are standard, defensible definitions of ROE. The verdict was DISAGREE
at 0.067, and the arbiter selected 32.08%.

**This is not hallucination, and the detector cannot tell the difference.**
Disagreement here reflects *definitional ambiguity in the question*, not a wrong
reading of the evidence. Any question whose metric admits several accepted
formulations will be flagged as risky, which is a false positive for
*hallucination* detection specifically.

Consequences to carry into Module 26:

- FinVerify-IND gold answers must **pin the definition** in the question text
  ("using closing total equity"), or the gold must accept a documented range;
- error analysis needs *definitional ambiguity* as its own category, separate
  from retrieval-caused and reasoning-caused error (D12);
- the arbiter's resolution accuracy must be measured against a gold that states
  which definition is intended, or it is being graded on an unanswerable question.

### Process note

Two audit agents **edited source files** despite a read-only brief — one left
`if False:` dead code in the verdict logic, one wrote a scratch script into the
repo. Both were reverted and every finding was reproduced independently before
being acted on. Audit briefs are explicitly read-only from here on. Recorded
because an agent silently "fixing" the component under audit is a real threat to
the integrity of an audit result.

**Raw:** workflow `wf_3fc3ff69-5f0`; run artifacts
`experiments/runs/slice_20260824T1*`

## RX-008 - Cerebras cannot serve this project: 402 on every completion

**Date:** 2026-08-26 - **Status:** D21 BLOCKED - **Outcome:** negative, and recorded because it is

**Question.** D21 chose to restore cross-vendor channel independence by sourcing
a second free vendor, and named Cerebras: the registry listed it at 30 rpm /
14,400 per day on open-weight models, roughly 700x Gemini's enforced cap. D21 was
deliberately filed as DECIDED, NOT VERIFIED with a four-point acceptance
checklist, because the Gemini episode (RX-006) had already shown that a published
free-tier figure and an enforced one can differ by 75x.

**Method.** Key installed, program channel rebound to `cerebras/gpt-oss-120b`,
then the checklist run in order.

| Step | Result |
|---|---|
| 1. `--list` returns a real lineup | **PASS** - two models: `gpt-oss-120b`, `gemma-4-31b` |
| 2. real call on the new binding, exit 0 | **FAIL** - HTTP 402 `payment_required_error`, param `quota` |
| 3. record the observed limit | n/a - no successful call to observe one from |
| 4. `channels_are_independent()` reports cross-provider | reported cross-provider, but on a binding that cannot answer |

**Both** models were then probed directly through the project's own adapter.
Both returned the identical 402. The failure is **account-wide, not
model-specific**, and it is **not a bad key**: authentication succeeds and
`GET /models` returns a real lineup, so the credential is valid and only
completions are refused.

An earlier raw `urllib` probe returned HTTP 403 `error code: 1010`. That is
Cloudflare bot protection rejecting a request without a browser-like User-Agent,
**not** the API's answer - a reminder that a hand-rolled probe can manufacture a
different diagnosis than the real client. Only the adapter's result is evidence.

**What this does and does not tell us.** It does not tell us Cerebras has no free
tier; it tells us that *this account* cannot call it. Whether the tier needs
separate activation, has been withdrawn, or wants a card on file is account state
no API response exposes, and the project cannot inspect it. That distinction
matters for what to do next, and guessing at it would be exactly the kind of
unverified claim the acceptance checklist exists to prevent.

**Immediate consequence.** The binding was reverted to
`groq/openai/gpt-oss-120b` and the full verifier re-run: all four bindings
VERIFIED, exit 0. The system is working; it is working on D20's **cross-lab**
arrangement, not D21's cross-vendor one. Any write-up must still say cross-lab.

> **Superseded 2026-08-29 by RX-014.** The finding above stands as recorded —
> Cerebras still returns 402 — but its *consequence* no longer holds. NVIDIA NIM
> passed the same checklist, Channel B moved to it, and independence is now
> cross-vendor. Do not act on the "must still say cross-lab" instruction above;
> it was true when written and is kept because a register that quietly edits its
> own failures teaches nothing.

**Why a failed attempt is recorded rather than retried quietly.** Half an hour of
setup produced a negative result, and negative results about infrastructure are
the ones most likely to be repeated by the next person - who will read D21, see a
plausible plan, and burn the same half hour. The registry entry now carries the
402 alongside the published limits it contradicts, so the next reader meets the
evidence before the assumption.

**Raw:** `scripts/verify_llm_providers.py` output; adapter probe on both models.

---

*Current project state: [`PROJECT_STATUS.md`](PROJECT_STATUS.md) · Next actions:
[`TODO.md`](TODO.md)*

---

## RX-009 — How far into a table chunk the year header actually is

**Date:** 2026-08-26 · **Status:** applied · **Outcome:** positive, and it nearly doubled dataset yield

**Question.** `facts_from_chunk` searched the first 3 rows of a chunk for a
column header naming years. Coverage looked poor on the non-Infosys filings —
most facts were falling back to the document's fiscal year rather than carrying
a stated one — and the dataset generator, which only emits a question for a
figure whose year the *table* states, was producing 103 candidates against a
target of 150.

The obvious move was to widen the search window. The obvious move is also how a
data-row gets mistaken for a header, so it was measured rather than assumed.

**Method.** Over all 13,559 table chunks in the five-filing corpus, count the
chunks in which some row within the first *N* yields a year mapping.

| Search window | Chunks with a readable year header | Coverage |
|---|---:|---:|
| 3 rows (before) | 2,456 | 18.1% |
| **6 rows** | **4,354** | **32.1%** |
| 10 rows | 5,096 | 37.6% |

**Result.** The window stops at 6. Rows 4–6 nearly double coverage; rows 7–10
add 5.5 points for two-thirds more scanning and, more importantly, for a much
higher chance of reaching genuine data rows.

**Why the header is not at the top.** The chunker repeats a table's header in
every part, but a part frequently opens with a caption line, a unit banner
("(In ₹ crore)") and a separator before it. Three rows was not enough to clear
them.

**The guard is what makes widening safe.** A candidate header must not carry
data: a cell holding `3,956` makes the row data, while a cell holding `2024` does
not. Without it, a row labelled *"As at March 31, 2024"* with figures beside it
names a year, sits within six rows of the top, and would be accepted as the
header — mapping **every column to the wrong year**. That is the silent
prior-year error Module 4 exists to prevent, and widening the window without the
guard would have introduced it while appearing to improve coverage.

**Effect on the dataset.** FinVerify-IND candidates went from 103 to **160**
(150 main-set + 10 ambiguity subset), reaching D23's target with headroom for the
questions validation will reject. Per-company: HDFC Bank 56, Sun Pharma 39,
Infosys 33, Reliance 17, Tata Motors 15.

**What this does not fix.** The remaining ~68% of table chunks have no
recoverable year header at all — they are continuation parts where the header
genuinely did not survive, or tables whose columns are not years (maturity
buckets, segment names). Those facts still carry `year_source = document` or
`unknown`, and `index_by_metric(require_stated_year=True)` excludes them. The
real fix is chunker-side and is recorded in `TODO.md` rather than attempted here.

---

## RX-010 — The orchestrated pipeline, live, and a three-way agreement worth noting

**Date:** 2026-08-26 · **Status:** verified · **Outcome:** positive · **Cost:** 2 requests

**Question.** Module 17 replaced the procedural wiring in `scripts/run_slice.py`
with a LangGraph pipeline in which every arm is a configuration (D26). Forty-four
tests cover it against stubs. Stubs do not prove the graph runs against real
providers, a real index and a real container — so it was run.

**Method.** One question through arm A (full system) against the live bindings,
the 16,060-chunk index, and the Docker sandbox.

> *For Infosys Limited, as reported for the year ended March 31, 2024, what was
> total equity?*

**Result.**

| | |
|---|---|
| Channel A (natural language) | INR 88,461 crore |
| Channel B (executed program) | INR 88,461 crore |
| Deterministic verifier | *does not apply* — a lookup has no arithmetic to check |
| Verdict | AGREE, risk **0.000**, band LOW |
| Arbiter | not triggered — correct, the channels agreed |
| Nodes run | understand → retrieve → natural → program → deterministic → consistency → assess |

Top-ranked evidence was `p.12, Consolidated Balance Sheet (contd.), table 1` —
the right page.

**The part worth recording.** That figure has now been produced by **three
independent paths** on this project:

1. **Module 4**, reading the extracted text layer, column labelled `2024` from
   the table's own header: `total equity = INR 88461 crore` (p.12).
2. **OCR** (RX-009's sibling work), reading a 300-dpi *render* of the same page
   through Tesseract at 95.3% digit confidence: `Total equity 88,461 75,795`.
3. **The live pipeline**, where a language model and an executed program each
   read retrieved evidence and independently returned 88,461.

Those paths share the PDF and nothing else — different libraries, different
representations, and in the OCR case a raster image rather than the text layer at
all. Their agreement is not proof the pipeline is correct, but it is a meaningful
check on the parts most likely to be silently wrong: scale carry-forward, column
selection, and Indian digit grouping. A scale error would have shown as 88,461
vs 884,610,000,000; a column error as 75,795.

**Also confirmed, incidentally.** The consolidated balance sheet gives equity
share capital 2,071 + other equity 86,045 = 88,116, against total equity 88,461 —
a difference of 345, which is the non-controlling interest line. The extraction
is reading a *consolidated* statement correctly rather than conflating it with
the standalone one.

**What this does NOT establish.** One question, one arm, one company, and the
answer was a lookup — the easiest question type, and the one where the
deterministic channel contributes nothing. It says the graph, both channels, the
sandbox, the branch logic and the risk layer work as a unit. It says nothing
about detection quality, which needs validated gold and a campaign.

---

## RX-011 — Which model built which collection, settled by probe

**Date:** 2026-08-27 · **Status:** verified · **Outcome:** negative (a defect) ·
**Cost:** 0 API requests

**Question.** `.env` paired `EMBEDDING_MODEL=intfloat/e5-base-v2` with
`QDRANT_COLLECTION=finverify_chunks`. Two collections existed. Both are
768-dimensional, so nothing in the system could say which model built which —
and the recorded reports could be read two ways. Rather than infer it, measure
it.

**Method.** Embed one phrase certain to appear in an Indian annual report —
*"total equity attributable to owners of the company"* — with each model, using
that model's own query prefix, and search both collections. A query embedded by
the model that built a collection lands near a real passage; one embedded by the
other model lands elsewhere in the space.

**Result.**

| Query model | Collection | Top score | Mean of top 3 | Top hit |
|---|---|---:|---:|---|
| e5 | `finverify_chunks` | 0.5403 | 0.5318 | Infosys **p.67** |
| **bge** | **`finverify_chunks`** | **0.7761** | 0.6865 | Infosys **p.12** |
| **e5** | **`finverify_e5`** | **0.8865** | 0.8606 | Tata Motors p.227 |
| bge | `finverify_e5` | 0.4246 | 0.4219 | Tata Motors p.227 |

Unambiguous: `finverify_chunks` is BGE, `finverify_e5` is E5. Confirmed
independently against the recorded reports in `evaluation/reports/`, where all
four `bge-base` runs name `finverify_chunks` and both `e5-base` runs name
`finverify_e5`.

**The part that matters more than the scores.** Under BGE, the top hit in
`finverify_chunks` is p.12 — the consolidated balance sheet, where that line
actually appears. Under E5, the same collection returns p.67. **The mismatch
does not fail; it returns a different page.** The score gap is a diagnostic
available only to someone who already suspects the problem.

**Consequences.**

1. **Every recorded experiment is valid.** No measurement used the cross-model
   pairing — it existed only in the live `.env`. RX-001…RX-003 are BGE on
   `finverify_chunks`; RX-004…RX-005 are E5 on `finverify_e5`.
2. **RX-005 does not describe the current index.** Its 0.909 evidence accuracy,
   0.932 R@10 and 0.616 MRR were measured on the **906-chunk Infosys slice**.
   `finverify_e5` now holds **22,930 chunks across five filings**. More
   documents means more distractors, so 0.909 is an optimistic prior for the
   full corpus, not a current fact. **Retrieval must be re-measured before it is
   quoted alongside campaign results.**
3. The pairing is now enforced rather than remembered (D33).

**Also checked, and clean.** Chunk ids map to Qdrant point ids by a
deterministic UUID5. Across all 22,930 chunk ids there are **zero collisions**
and **zero chunks on disk missing from the index** — an earlier count of "5
missing" was an arithmetic error on my part that counted each file's cache
header line as a chunk. 22,935 lines − 5 headers = 22,930. Recorded because a
retracted defect is still a measurement.

**What this does NOT establish.** Nothing about retrieval quality on the full
corpus. It establishes only which model built which collection, and that the
configuration paired them wrongly.

---

## RX-012 — Retrieval was measured on one document; the campaign searches five

**Date:** 2026-08-27 · **Status:** verified · **Outcome:** negative (a defect) ·
**Cost:** 0 API requests

**Question.** RX-011 established that RX-005's figures were measured on the
906-chunk Infosys slice while the index now holds 22,930 chunks across five
filings, and said they should be re-measured. Re-measuring exposed something
larger than a stale number: **every retrieval measurement on this project had
been filtered to a single document, and the campaign does not filter at all.**

`scripts/evaluate_retrieval.py` passed `document_id=meta["document_id"]` on
every call. `scripts/run_campaign.py` built one `PipelineDeps` for the whole
campaign with `document_id=None` and `company=None` — necessarily, since one
object serves every question. Those are different tasks. The filtered one asks
*can it find the right page in this report*; the unfiltered one also asks *can
it pick the right report*.

**Method.** Added `--corpus-wide` and `--no-company` to the retrieval
evaluation, and scored the same 22 gold questions under each condition. The gold
set is Infosys-only by design here: it holds the questions fixed and grows the
haystack, so the delta is attributable to scope and nothing else.

**Result.**

| Condition | R@10 | MRR | Evidence acc.@10 | Misses |
|---|---:|---:|---:|---:|
| planned, filtered to the document (= RX-005) | 0.932 | 0.616 | 0.909 | 2/22 |
| planned, corpus-wide, **company known** | **0.932** | **0.616** | **0.909** | 2/22 |
| planned, corpus-wide, **company unknown** | 0.750 | 0.357 | **0.727** | 6/22 |
| hybrid, corpus-wide, no planning | 0.523 | 0.293 | 0.500 | — |
| semantic, corpus-wide | 0.386 | 0.131 | 0.364 | — |
| keyword, corpus-wide | 0.500 | 0.297 | 0.500 | — |

**Three findings, in order of importance.**

1. **The campaign would have run at 0.727, not 0.909.** `parse_question` does
   not extract the company from the question text — it returns `None` even for
   *"For HDFC Bank Limited, as reported for the year ended March 31, 2024, what
   were total deposits?"* — and `deps.company` was `None`. So `retrieve_for_spec`
   added no company filter and every question searched all five filings. MRR
   falls hardest (0.616 → 0.357): the right evidence, when found at all, ranks
   much lower, and at `top_k=8` the channels would frequently never see it.
   Misses triple, from 2 to 6.

2. **Module 7's planned retrieval is not an incremental gain; it is what makes a
   multi-company corpus work.** On the single-document slice it read as +0.068
   R@10 over hybrid (0.864 → 0.932). Corpus-wide it is **+0.409** (0.523 →
   0.932). The slice measurement understated its contribution roughly sixfold,
   and would have understated it in the write-up.

3. **Scoping is worth more than fusion here.** Corpus-wide and unscoped, hybrid
   (0.523) barely beats keyword alone (0.500) and the semantic leg collapses to
   0.386. Knowing *which company* is worth more than any retrieval-side tuning
   measured on this project so far.

**Fix.** The company travels with the question, from the dataset, which knows it
authoritatively — rather than being parsed back out of the question text, which
would be a second inference to get wrong. `run_question` takes `company` and
`document_id`; `PipelineState` carries them; the per-question value overrides
the campaign-wide default. Three gates in `test_orchestrator.py` pin it,
including one asserting that an unknown company yields **no filter rather than a
guessed one** — a wrong filter returns another company's evidence with no signal
that anything was assumed.

**What this does NOT establish.** The gold set is 22 Infosys questions. The
corpus-wide numbers say what happens to *these* questions among five filings;
they do not measure retrieval on HDFC, Reliance, Sun Pharma or Tata Motors
questions, which have no gold evidence spans. Building those is the honest next
step for retrieval measurement, and it is separate from validating answer gold.

**Standing correction.** RX-005's 0.909 / 0.932 / 0.616 are correct *for a
company-scoped search*, which is what the system now always does. They were
quoted without that qualifier in `PROJECT_STATUS.md` and `README.md`, and the
qualifier is not cosmetic — without it the same pipeline scores 0.727.

---

## RX-013 — The pipeline live on a bank, and a scale token read right by accident

**Date:** 2026-08-27 · **Status:** verified · **Outcome:** mixed ·
**Cost:** 4 requests

**Question.** RX-010 verified the orchestrated pipeline live on Infosys, when
the index held one filing. The corpus is now five, retrieval scoping has changed
(RX-012), and the lexicon has banking vocabulary for the first time (D34). Does
the whole chain work on a company that is not Infosys?

**Method.** One question through the full pipeline against the live bindings, the
22,930-chunk index and the Docker sandbox, scoped to HDFC Bank and searching the
whole corpus.

> *For HDFC Bank Limited, as reported for the year ended March 31, 2024, what
> were total deposits as reported on the consolidated balance sheet?*

**Result.**

| | |
|---|---|
| Channel A (natural language) | 23,79,786 — canonical 2.379786 × 10¹³ |
| Channel B (executed program) | 23,79,786 — canonical 2.379786 × 10¹³ |
| Deterministic verifier | *does not apply* — a lookup has no arithmetic to check |
| Verdict | AGREE, score **1.000**, band HIGH, 14.8s |
| Sandbox | OK in 1.3s |

**Verified against the source, not assumed.** HDFC Bank's own narrative on p.217
reads *"Total Deposits rose by 26.4 per cent to ₹ 23,79,786 crore from ₹
18,83,395 crore."* The answer is correct.

**The finding: it was correct by accident.** The retrieved evidence came from the
FY24 highlights page, which heads its column **`Deposits (K Cr)`** and prints
`23,79,786` — the same digits the narrative gives as *crore*. So "K Cr" means
crore for this filer.

The parser matched `cr`, **silently discarded the `K`**, and produced the right
number. It did not reason about the pair. A filer using "K Cr" to mean what it
says would have been misread by **three orders of magnitude, with no warning at
all** — precisely the failure class this project exists to detect, arriving
through the front door.

Probing the parser directly exposed a second case:

| Input | Read as | Correct | |
|---|---|---|---|
| `23,79,786 K Cr` | 2.379786 × 10¹³ | 2.379786 × 10¹³ | right, by luck |
| `1 thousand crore` | 1 × 10³ | 1 × 10¹⁰ | **wrong by 10⁷** |

The loop takes the first scale token it matches and stops.

**Frequency, measured rather than guessed.** Compound scale expressions occur
**29 times** in the corpus — `k cr` ×14, `k crore` ×11, `k\xa0crore` ×4 — **all
of them in HDFC Bank's filing**. `thousand crore` occurs zero times.

**Fix, and what was deliberately not fixed.** A new `AMBIGUOUS_COMPOUND_SCALE`
warning fires when a second scale-shaped token survives beside the one used, and
the trace names both. The arithmetic is **unchanged**:

- Promoting bare `k` to a scale would turn the one case that actually occurs
  into a 1000× error, against documentary evidence that crore is right.
- Changing compound handling to satisfy `thousand crore`, which no document
  here uses, is how D10 reached two conclusions measurement overturned.

The value is kept and the doubt is recorded. A flagged figure a consistency
check can weight down beats a confident one nobody questions.

**What this does NOT establish.** One question, one arm, one company, and a
lookup — the easiest type, and the one where the deterministic channel
contributes nothing. It says the graph, both channels, the sandbox, retrieval
scoping and the banking vocabulary work as a unit on a non-Infosys filing. It
says nothing about detection quality.

---

## RX-014 — Cross-vendor independence achieved: NVIDIA NIM passes where Cerebras failed

**Date:** 2026-08-29 · **Status:** verified · **Outcome:** positive · **Cost:** ~30 requests

**Question.** D21 has been BLOCKED since 2026-08-25. Cerebras failed acceptance
(RX-008): valid key, real model lineup, HTTP 402 on every completion. Channel
independence has therefore been **cross-lab, not cross-vendor**, and every
write-up has had to say so. Does NVIDIA NIM pass the same four-point checklist?

**Method.** D21's acceptance checklist, in order, with the rate limit *observed*
rather than read from documentation — the Gemini episode (registry said
1,500/day, provider enforced 20) is why that distinction is enforced.

**Result — all four steps pass.**

| D21 step | Cerebras (RX-008) | NVIDIA NIM |
|---|---|---|
| 1. Real model lineup from `--list` | pass (2 models) | **pass** (83 models) |
| 2. Real completion, verifier exits 0 | **FAIL — 402 on every model** | **pass** |
| 3. Rate limit recorded with OBSERVED provenance | n/a | **pass, with an honest gap** |
| 4. `channels_are_independent()` says cross-provider | n/a | **pass** |

```
[OK  ] channel independence (D1): cross-provider:
       groq/qwen/qwen3.6-27b vs nvidia/nvidia/nemotron-3-ultra-550b-a55b

natural_channel        VERIFIED  groq/qwen/qwen3.6-27b                     2.09s
program_channel        VERIFIED  nvidia/nvidia/nemotron-3-ultra-550b-a55b  2.81s
verification_agent     VERIFIED  groq/qwen/qwen3.6-27b                     2.70s
question_understanding VERIFIED  groq/openai/gpt-oss-20b                   1.95s
```

**End to end, on the question RX-010 already cross-verified.**

| | |
|---|---|
| Channel A | groq/qwen/qwen3.6-27b → **INR 88,461 crore** |
| Channel B | nvidia/nemotron-3-ultra-550b-a55b → **INR 88,461 crore** |
| Sandbox | generated program executed, OK in 1.5s |
| Verdict | AGREE, risk **1.000** consistency, band HIGH, 24.2s |

RX-010 recorded a three-way agreement on this figure (text-layer extraction,
OCR at 95.3% digit confidence, and the live pipeline). It is now **four-way**,
and the fourth path runs on a different vendor's hardware and serving stack.

**What was observed about the limits, and what was not.**

- `requests_per_minute=20` is an observed **floor**: 10 requests paced 3s apart
  gave 9 successes. **No 429 has ever been seen**, so the request-rate ceiling
  was never reached and its true value is unknown.
- `requests_per_day=1000` is **NOT OBSERVED**. NVIDIA returns no rate-limit
  headers of any kind and no daily refusal has been triggered. It is a
  conservative pacing placeholder, recorded as such in `registry.py`. Quoting it
  as a measurement would repeat the Gemini error exactly.

**The real constraint is different in kind.** This endpoint returns HTTP 503
`Service temporarily overloaded` under contention, not 429 under quota:

```
burst, no pacing:   1:200 8.42s  2:200 12.52s  3:200 0.58s  4:200 0.59s  5:503
paced 3s apart:     1:503  then 9 consecutive 200s
```

A 503 arrived on a *cold first request* as well as after four rapid ones, so it
is shared-capacity contention on a free 550B endpoint rather than a per-key
quota. 503 is already in `_RETRYABLE_STATUS`, so the adapter absorbs it without
a code change.

**Consequence for the efficiency analysis (Module 25).** Identical prompts
returned in 0.42s and in 12.5s. **Latency measured on this provider is close to
meaningless** and must not be reported as a property of the method. Token counts
and request counts are unaffected.

**One integration hazard worth recording.** Nemotron is a reasoning model that
returns `reasoning_content` as a separate field. At `max_tokens=16` the budget
was consumed by reasoning and `content` came back holding *reasoning prose*
with `finish_reason: length` — which the JSON-parsing channels would have read
as a malformed answer rather than as a truncation. At the channels' actual
budget (2,048) it returns clean JSON in `content`. The adapter already detects
and reports this case, having met it once before on gpt-oss-120b.

**What this does NOT establish.** One question, one arm, one company, a lookup.
It establishes that the binding works and that independence is now cross-vendor.
It says nothing about whether cross-vendor disagreement detects hallucination
better than cross-lab did — that is H4, and it needs the campaign.

---

## RX-029 - An Indian fiscal-year column was labelled a year early

**Date:** 2026-08-30 - **Status:** verified - **Outcome:** a defect found by the
validator, not by the tests - **Cost:** 0 API requests

**How it surfaced.** The owner validated 45 questions and rejected 25. Two
rejections said the same specific thing: the candidate came from the column
labelled "2022-23" while the question asked for the year ended March 31, **2022**.

**The defect.** `_normalise_year` took the first four-digit literal in a header
cell. "2022-23" contains exactly one, so it read 2022 - but an Indian fiscal year
labelled 2022-23 **ends on 31 March 2023**. Every such column was labelled a year
early, and the question generated from it asked about the wrong year while
pointing at a real figure on a real page with correct provenance.

The convention was not a judgment call: every other header in this corpus already
resolves to the ending year ("As at March 31, 2024" gives 2024), so a span
resolving to its starting year disagreed with other columns **in the same table**.

**Reach: 19 of 193 questions (10%), all Reliance**, the only filing that labels
columns this way. Of those 19, six had been judged - **and all six were rejected.
6 for 6.**

That is the most useful number here. A validator with no access to the code, no
knowledge that this bug existed, and 39 other questions to get through, caught
every instance put in front of them. The machine pre-check passed all six: the
figure IS printed on the cited page, in the column the provenance names. Only a
person comparing the question's year against the column's meaning could see it.

**Fixed** by resolving a fiscal-year span to its ending year, checked before the
bare-literal branch. The two-digit tail completes against the start year's
century, so "1999-00" is 2000 and not 2100. Thirteen tests, including that a span
and an explicit date for the same period must agree.

**The regeneration cost 3 verdicts and kept 20.** Content-derived question ids
(D42) meant the unaffected questions kept theirs; the ids of the affected ones
changed with their year, as they should. Verdicts were carried across only where
the question text AND the candidate answer were byte-identical - a surviving id
is not enough, because the row it names can have been re-sourced. All 20
validated verdicts survived; 3 rejections did not, and those were rejections of
questions that no longer exist.

---

## RX-030 - Four gold answers were stored as paragraphs

**Date:** 2026-08-30 - **Status:** verified - **Outcome:** unusable gold, caught
before any evaluation ran - **Cost:** 0 API requests

**What happened.** The 13 derived-metric questions carry no candidate answer on
purpose (D35), so the validator computes the figure. Working one out means writing
out the working, and pasting all of it into the answer field is the natural thing
to do. `review_gold.py` accepted it, and four gold answers were stored as 400-700
character paragraphs ending "Conclusion: The derived return on assets is 1.59%".
The unit fields said `crore` and `million` for what are percentages.

**Why it mattered more than it looks.** An evaluator comparing a model's "1.59"
against that paragraph scores a **correct answer wrong**. The failure would have
appeared in the results as the system being unable to answer derived-metric
questions - a plausible, publishable-looking finding about the method, produced
entirely by a data-entry format. Four of the twenty validated questions, and all
four of the multi-hop ones.

**Resolved by transcription, not by judgment.** Each paragraph ends in an explicit
conclusion naming the figure, so the figure was lifted from the validator's own
stated conclusion, the unit set to `percent`, and the full working preserved
verbatim in the note. Nothing was decided that the validator had not already
written down. One carried a caveat - Tata Motors' net profit margin is 7.26% on
revenue from operations and 7.17% on total income, and the validator chose the
former, which is what D22's pinned definition specifies.

**The tool no longer accepts it.** `as_figure` requires a number, and where the
text ends in an explicit conclusion it reads the figure from it rather than
refusing - making a validator retype a number they have already written invites a
transcription error into gold data. Prose with no figure re-prompts.

---

## RX-051 - Twenty defects the unit suite passed through, found by driving the running stack

**Date:** 2026-09-10 · **Status:** verified · **Cost:** zero, no model calls ·
**Concerns:** the API, all six original screens, the deployment · **Severity:**
one of them made a documented figure wrong; several made the UI assert things
that were not true

Eight finders were run against the RUNNING containers rather than the source.
The adversarial verification stage was cut short by a session limit (35 of 45
agents died), so **these are not panel-verified**: every item below was
reproduced by hand before it was fixed, and two finder dimensions - database
truth and error paths - were covered manually afterwards instead.

### The pattern worth recording

**Every one of these was invisible to 1,374 passing tests**, because the tests
exercise the application and these live in the seams: nginx config, a volume
mount mode, image directory ownership, a proxy status code, a truncation width.
The same lesson as the `channels: []` bug - a contract can be green in the test
suite and broken in the only place a reader will ever see it.

### Three stacked bugs, each hiding the next

`POST /documents/upload` could not succeed at any file size:

1. nginx applied its **1 MB default** to the proxied body. Four of the five
   filings in this corpus are 9.2-20.6 MB, so every real annual report was a
   413 before it reached the API.
2. Past that, the API wrote into `documents/raw`, which compose mounts `:ro` on
   purpose so a running service cannot alter the documents its index was built
   from. The OSError surfaced as a bare 500.
3. Past that, the new uploads volume was **root-owned**: Docker seeds a named
   volume from the image directory it covers, and `/app/uploads` did not exist
   in the image, so the non-root process could not write to it.

Fixed at all three layers and verified by uploading 2.9 MB end to end.

### The UI asserted things that were not true

- **The arbiter panel** branched on `triggered` alone and printed *"Not
  triggered. The arbiter runs only when the channels actually disagree"* for
  three structurally different states, including arms D and B1-B4 which have no
  arbiter and no consistency engine, and including a failed fetch (the 404 was
  swallowed by a bare `.catch(() => null)`). Claiming a component declined to
  act when it does not exist in that configuration is the same class of error as
  reporting an unmeasured metric as zero. Fixing it required the API to describe
  an arm, which exposed that `evaluation.arms` drags in langgraph through the
  orchestrator - so `GET /arms` was a 500 inside the container while passing
  locally. `ArmConfig` now lives in a stdlib-only module with a test that checks
  in a fresh interpreter that the arm table imports nothing heavy.
- **A scale disagreement rendered as two identical numbers.** The channel tables
  showed the bare magnitude, so two channels reporting "12232" while meaning
  crore and million looked like agreement. Both tables now carry the canonical
  form beside it.
- **"70 arm-strata carry a detection AUROC"** counted rows across 8 runs. **36**
  distinct exist. That figure was written into the dashboard on 2026-09-09 by
  me and was nearly double the truth.
- **"192 judged"** contradicted the tile beside it reporting 48 awaiting review.
  Judged is validated + rejected = 144.
- **The research table** stacked eight runs' metrics under one arm heading with
  no run column and duplicate React keys, so three identical-looking rows
  carried three different values.
- **The question column** truncated at 64 characters. Every gold question opens
  with the same stem, so 45 distinct questions collapsed to **6 displayed
  strings** - 199 of 200 rows byte-identical in the column meant to identify
  them.

### Also fixed

`useAsync` kept the previous run's data on screen under the new run's label; no
catch-all route or error boundary (an unknown hash rendered an empty shell, a
render throw replaced the document); 42 run links that all pointed at the same
unfiltered page; Ask defaulting live QA to ENABLED whenever `/health` had not
answered; `Register` doing nothing silently with no file chosen; `Inspect`
rendering its result below a 200-row table with no scroll; a 500 on an
out-of-range integer id (Postgres int4); FastAPI's 422 `detail` array discarded
because the client read only the string form; evidence anchors dumped as escaped
JSON; the document viewer's source URL forcing the whole page to scroll sideways
at 360px; and `build_ref` sent by `/health` and displayed nowhere.

### What was checked and found clean

Database truth: identical calls return identical bodies (stable ordering) on
every listing; `/stats` matches the database row-for-row on all five counts; a
served answer matches its artifact row on `answer_text`, `abstained`, `verdict`,
`risk_score` and `agreed`; all 14 arms with recorded answers are described by
`/arms`. Error paths: with the API stopped, every screen renders a labelled
error box and keeps its navigation - an earlier reading that five screens
"failed silently" was a measurement artifact of waiting 2.5s when nginx takes
~3s to return 502.

---

## RX-050 - The ablation table had no code behind it, and one of its cells was wrong

**Date:** 2026-09-09 · **Status:** verified · **Cost:** zero, re-analysis of
committed artifacts · **Concerns:** RX-049, the headline claim, CASE_STUDY_REPORT
§11.7, PROJECT_STATUS · **Severity:** weakens the project's only supported result

Found while wiring the ablation into the database so the frontend could show it.
`evaluation/ablation/` was an **empty directory**, and the pooled report's
`family_wise_correction` was `{}`. RX-049's ten-cell table had been computed in a
scratch script that no longer exists — so the project's one supported result had
no code path that regenerated it, in a repository whose operating rules require
every number to trace to a committed file.

### Writing the module reproduced nine cells and refuted one

`evaluation/ablation/contrasts.py` now computes the contrasts from the same
`paired_bootstrap_difference` / `holm_bonferroni` machinery the hypothesis tests
use. Against RX-049's published table:

| cell | published | recomputed |
|---|---|---|
| A − B all | +0.066 [−0.004, 0.164] p=0.082 | **identical** |
| A − C all | +0.102 [0.028, 0.196] p=0.0020 | **identical** |
| A − C committed | +0.361 [0.235, 0.500] | **identical** |
| A − E, A − F (both families) | as published | **identical** |
| **A − B committed** | **+0.139 [−0.094, 0.375] p=0.233** | **+0.417 [0.233, 0.567] p<0.0001** |

Nine of ten reproduce to three decimals, which is what makes the tenth
diagnostic rather than ambiguous. The recomputed value is confirmable without any
bootstrap: over the 17 questions both arms committed, AUROC(A) = 0.8810 and
AUROC(B) = 0.4643, and 0.8810 − 0.4643 = **0.4167**. Three candidate definitions
of "committed" were tried (both arms, baseline only, ablated arm only) and none
produces +0.139; the "both arms" definition reproduces every other cell exactly.
**+0.139 was not a different convention. It was wrong.**

### What it costs

RX-049's conclusion was **"A − C survives Holm in both families. Nothing else
does."** The second sentence is false: A − B also survives in the committed
family, with a *larger* effect than A − C. The asymmetry that the whole
interpretation rested on exists in the all-rows family and reverses in the
committed one.

### The deeper problem the correction exposed

Both committed-only contrasts rest on **three errors**. An AUROC ranks errors
above correct answers, so its effective sample size is the error count, not the
question count — and no p-value computed over three errors can separate two arms.
The module now carries `MIN_ERRORS_FOR_A_CONTRAST` and flags such a contrast
`underpowered` while still reporting it, the same way `MIN_STRATUM_FOR_A_CLAIM`
labels a thin stratum rather than hiding it. The flag travels into the database
note and onto the research screen, in the same cell as the value, because that
warning is worthless anywhere else.

This compounds RX-048. The committed-only family is the one the research question
is actually about, and on this corpus it holds three errors. **The project's
supported result is in the all-rows family, where 62% of what is "detected" is
the system's own abstention.** That is a real result and a narrow one, and it is
now stated that way wherever it appears.

### Fixed

- `evaluation/ablation/contrasts.py` + `__init__.py`, wired into `build_report`
  as a top-level `ablation` block, printed by `analyse_campaign.py`.
- 12 tests in `tests/test_ablation_contrasts.py`, including the "committed means
  both arms" rule that was one of the candidate explanations for the bad cell.
- `ingest_to_database.py` now ingests **pooled** reports (`<run-a>+<run-b>`),
  which the per-run loop had skipped silently — the reason the headline result
  had never reached the database or the UI at all.
- Corrected in `EXPERIMENTS.md` (RX-049), `CASE_STUDY_REPORT.md`,
  `PROJECT_STATUS.md`, `CHANGELOG.md`.

---

## RX-049 - The first clean single-field ablation, and the program channel earns its place

**Date:** 2026-09-08 · **Status:** verified · **Runs:**
`campaign_20260908T164126Z` (arm A, 45 rows, 0 failed) pooled with
`campaign_20260906T221521Z` (B–F, 225 rows) · **same binding, same 45 questions**
· **Report:**
`evaluation/reports/results_campaign_20260906T221521Z+campaign_20260908T164126Z.json`

RX-047 left the ablation uninterpretable: every arm is *"arm A minus exactly one
field"* and the A they needed had run on a retired model. Arm A has now been
re-run on `gpt-oss-20b`, giving this project its **first genuine single-field
ablation** — one binding, one split, one set of 45 questions.

### The model swap changed the risk score but not one answer

Predicted in RX-047 and now measured directly rather than inferred:

| | arm A on gpt-oss-120b | arm A on gpt-oss-20b |
|---|---|---|
| correct answers | 18 | **18 — the identical set** |
| detection AUROC | 0.907 | **0.981** |

**Nine arm-configurations across two models return the same 18 questions.** The
confound was empirically null on accuracy, exactly as claimed — and **real on the
risk score**, which is what every hypothesis is tested on. The refusal to pool
the two campaigns was correct, and this is the proof rather than the assumption.
`analyse_campaign.py --run` is now repeatable and **refuses** a pool whose split
or channel bindings differ, because the confounded table looks perfectly
reasonable.

### The ablation

| arm | removes | accuracy | AUROC | 95% CI |
|---|---|---:|---:|---|
| **A** | nothing | 0.400 | **0.981** | [0.951, 1.000] |
| B | the natural channel | 0.311 | 0.916 | [0.817, 0.986] |
| **C** | **the program channel** | 0.400 | **0.880** | [0.780, 0.963] |
| D | consistency engine + arbiter | 0.400 | — | no risk score by construction |
| E | the verification agent | 0.400 | 0.952 | [0.883, 0.995] |
| F | hybrid retrieval → semantic only | 0.178 | 0.983 | [0.946, 1.000] |

Paired over the same questions, with the family-wise correction the methodology
requires:

| contrast | all rows | **committed answers only** |
|---|---|---|
> **CORRECTED 2026-09-09 — see RX-050.** The committed-only column below was
> computed in a scratch script; one of its ten cells was wrong and the sentence
> drawn from it does not survive. The table now carries the output of
> `evaluation/ablation/contrasts.py`, and the error count each figure rests on.

| contrast | all rows (27 errors) | **committed answers only (3 errors)** |
|---|---|---|
| A − B (minus natural) | +0.066 [−0.004, 0.164] p=0.082 | +0.417 [0.233, 0.567] p<0.0001 · underpowered |
| **A − C (minus program)** | **+0.102 [0.028, 0.196] p=0.0020** | +0.361 [0.235, 0.500] p<0.0001 · underpowered |
| A − E (minus arbiter) | +0.030 [−0.012, 0.091] p=0.238 | +0.259 [−0.032, 0.605] p=0.108 |
| A − F (minus hybrid) | −0.002 [−0.037, 0.039] p=0.873 | −0.102 [−0.318, 0.118] p=0.272 |

**Over all rows — the only adequately powered family — A − C survives Holm and
nothing else does.** In the committed-only family both A − B and A − C clear
their Holm thresholds, but that family carries **three errors**, so it cannot
separate two arms whatever its p-value says, and its point estimates run the
*other* way.

### What this supports, stated exactly

**Removing the executed-program channel costs the detector 0.102 AUROC over all
rows** [0.028, 0.196], p=0.0020, surviving Holm across the four contrasts. Arm C
is not a weaker detector by accident: it is arm A with the second,
different-modality channel taken away.

**The asymmetry holds, but only in the family that can carry it.** Over all rows,
removing the *natural* channel (A − B, +0.066) does not survive correction while
removing the *program* channel does — so on this evidence the executed program
contributes detection signal the natural channel does not. That is the project's
thesis, and it is the first measurement in the project that separates it from
noise at all.

**It is not the family that matters most, and that has to be said plainly.**
RX-048 established that a numerical hallucination is a *committed* wrong figure
and that 62% of what the all-rows detector flags is the system's own abstention.
The committed-only family is therefore the one the research question is really
about — and it has three errors in it. Both contrasts clear Holm there, A − B by
more than A − C. Nothing about the asymmetry can be concluded from three errors,
in either direction. **The supported claim lives in the all-rows family; the
family that would make it matter is too thin to test.**

**Also: arm A's blind spot on this binding is 0 of 12 agreed answers.** No case
of both channels agreeing on a wrong figure.

### What it does NOT support, and the limits are severe

- **Validation only, and permanently so.** The test split was spent on
  2026-09-05, and these arms ran after it. They can never be held out. Every
  earlier held-out claim in this project weakened on the test split, and this one
  has not faced that.
- **It is not H1.** H1 is defined against arm B5 (self-consistency), which exists
  only on the retired binding. This is the spec's ablation contrast, answering
  the same underlying question against a different comparator. **H1, H3 and H4
  remain NOT TESTABLE on this binding** — B5, G and H would each need a rebase.
- **n = 45, and 21 for the committed-only contrast.** The interval on the
  headline figure is [0.235, 0.500]: wide.
- **H2 is still not testable** — the reasoning stratum is 13 questions with 0
  errors, reproducing RX-038 on the new binding.
- **Part of the effect is structural.** Arm C has one reasoning channel, so its
  risk score has less information available by construction. That is what the
  ablation is *for*, but it means the result reads "the second channel
  contributes detection signal", not "cross-modality specifically does".

---

## RX-048 - The risk score is a three-level flag, and 62% of what it "detects" is the system's own abstention

**Date:** 2026-09-07 · **Status:** verified · **Cost:** zero, re-analysis of
committed artifacts · **Concerns:** the headline result, D45, EVALUATION.md §5.1
· **Severity:** this changes what the project may claim

Found by chasing why arm C's risk score kept printing exactly `0.51125` — the
frozen operating threshold. It is not a coincidence, and what it led to is the
most consequential finding of the audit.

### The score is not continuous. The methodology forbids exactly this

`EVALUATION.md` §5.1, written before Module 15 and quoted in full because it
anticipated this failure mode precisely:

> **The confidence module must emit a continuous score, not only
> HIGH/MEDIUM/LOW.** AUROC is undefined over three ordinal buckets in any useful
> sense, and AUROC is the primary metric. Discrete bands are a presentation layer
> *derived* from the continuous score, never the underlying representation.

Arm A's realised risk score, held-out split:

| risk | n | errors | error rate | facet signature |
|---|---:|---:|---:|---|
| **0.0** | 19 | 5 | 0.263 | everything VERIFIED |
| **0.51125** | 14 | 6 | 0.429 | agreement PARTIAL, evidence PARTIAL |
| **0.550625** | 24 | **24** | **1.000** | agreement PARTIAL, evidence PARTIAL |
| 4 singletons | 4 | 4 | 1.000 | agreement FAILED |

**Seven distinct values over 61 questions; three of them cover 57 (93%).** The
module emits a float, so the constraint passes on inspection and fails in fact.
The check was never run — and RX-007 had already caught this exact defect in
**B5** ("would have been quantised to 5 values… H1 'supported' partly because the
baseline was rounded off"). Nobody applied it to the primary arm, which has 6–7.

Two levels share an **identical facet signature** and differ in error rate 0.429
against 1.000, so the separation comes from a continuous sub-term
(`coverage`), not from the four reported facets. The score does carry signal —
it is just not carrying it where the report says.

It also explains **D45's "plateau"**. D45 froze 0.51125 because "all three
candidate objectives land on the same point, which is what makes it a plateau
rather than a value fitted to noise". With three mass points there are only about
two distinguishable cut points, so the objectives agreeing is **arithmetic, not
robustness**. The operating point means exactly *"flag unless all four facets
verified"*, which reproduces the reported precision/recall to the digit
(validation 0.812/0.963, test 0.810/0.872).

### The bucket that is always wrong is the system abstaining

The 0.550625 bucket is **21 of 21 wrong on validation and 24 of 24 on test** —
perfect prediction, replicated across two splits and two Channel A models. That
looked like the project's cleanest result until the obvious deflationary check:

| | abstained | committed a figure |
|---|---:|---:|
| validation, 0.550625 bucket | **20 of 21** | 1 |
| test, 0.550625 bucket | **24 of 24** | 0 |

It is the abstention bucket. And `EVALUATION.md` §3 grades an abstention as
incorrect, while abstention is itself an **input** to the risk score
(`coverage`, `usable_channels` in `assess()`). So the detector is substantially
detecting the system's own refusal, which the grader then counts as an error —
**24 of the 39 held-out errors (62%)**. That is close to circular.

### The number that answers the research question

A numerical hallucination is a **committed wrong figure**. A refusal is the
opposite of one. Restricting to answers the system actually committed to:

| arm A | all rows (as §5.1 specifies) | **committed answers only** |
|---|---:|---:|
| validation | 0.907 | **0.802**  [0.591, 0.960] |
| **TEST (held out)** | **0.885**  [0.801, 0.954] | **0.679**  [0.500, 0.840] |

**The held-out interval reaches exactly 0.500 — chance.** Among committed answers
the score takes two values on 33 of 36 questions, so it is a binary flag there.
Arms G (0.692) and H (0.717) land in the same band, so this is a property of the
approach, not of one arm.

### What is and is not being claimed

**The reported 0.885 is not an error.** §5.1 defines the label as *"the system's
final answer is incorrect"*, and an abstention is a failure to deliver one. As a
**deployment triage** number — *should I trust this output?* — 0.885 is valid and
useful, and the abstentions it flags are genuinely outputs not to trust.

**But it is not the research question.** RQ asks whether dual-channel
disagreement detects numerical *hallucination*, and 0.679 [0.500, 0.840] is that
answer, held out.

**The committed-only figure has its own weakness**, stated so it is not
oversold: it conditions on abstention, which is a post-treatment variable driven
by the same evidence quality that drives correctness. That is a selection effect
and possibly a collider. Neither number is a clean causal estimand; they answer
different questions and **both belong in the write-up, labelled**.

**Nothing here is retracted.** Every published figure was computed exactly as the
frozen methodology specifies. What changes is the interpretation: the headline is
a triage flag whose held-out hallucination-detection interval includes chance,
not a continuous detector at 0.885.

**CORRECTED 2026-09-13 (fact check of the case study figures).** Two numbers in this
entry are off by one step, and the conclusion is unchanged. Test arm A has **25**
abstentions, all of them errors: the 24 in the 0.550625 bucket and one singleton at
0.55625. So abstentions are **25 of the 39 held-out errors (64%)**, not 24 (62%);
the "62%" repeated in RX-049 and RX-050 inherits the same miscount. And the
committed-only interval's upper bound is 0.83946, which rounds to **0.839**, not
0.840. Recomputed from `results.jsonl` with the project's own `bootstrap_ci`
(10,000 resamples, seed 20260826). The singleton's facets are agreement PARTIAL,
not FAILED as the bucket table above implies for all four singletons.

---

## RX-047 - The five missing ablation arms, and the binding that makes them uncomparable

**Date:** 2026-09-07 · **Status:** verified · **Run:**
`campaign_20260906T221521Z` · validation, arms B C D E F × 45 · **225 rows, 0
failed, 0 skipped** · Run by the owner from a real terminal

The gap RX-044's audit named is closed: arms B, C, D, E and F now exist as data.

### What the arms do

| arm | removes | accuracy | abstains | accuracy when it answers |
|---|---|---:|---:|---:|
| B | the natural channel | 0.311 | 0.533 | 0.667 |
| C | the program channel | **0.400** | 0.489 | 0.783 |
| D | consistency engine + arbiter | **0.400** | 0.467 | 0.750 |
| E | the verification agent | **0.400** | 0.511 | 0.818 |
| F | hybrid retrieval → semantic only | **0.178** | **0.711** | 0.615 |

**C, D and E return the byte-identical set of 18 correct questions** (Jaccard
1.000). Removing the program channel, the consistency engine, or the arbiter
changes not merely the score but not even *which* questions are right.

**And it is the same 18 arm A returns** — across a Channel A model swap. Arms A,
B5, G, H and B2 on gpt-oss-**120b** and arms C, D and E on gpt-oss-**20b** all
return one identical 18-question set. Every arm that keeps the natural channel
returns the same answers regardless of what else is removed and regardless of
which model runs it.

The mechanism reproduces RX-038 exactly, on the new binding: **13 of 13 correct
where retrieval delivered every gold evidence group, 2 of 25 where it did not,
and 0 cases of complete evidence with a wrong answer.**

### Arm F degrades safely, which is the useful part

F still receives **8 evidence blocks** — the same as every other arm. It
retrieves *worse*, not *less*. Accuracy falls 0.400 → 0.178, and the fall is
**abstention** (0.489 → 0.711), not fabrication: when F commits it is right 0.615
of the time. Degrading retrieval makes this system decline rather than invent.

### The comparison the run cannot support

Every ablation arm is defined as *"arm A minus exactly one field"* — a property
`evaluation/arms.py` has its own test for. So each is only interpretable against
arm A. And the two validation campaigns ran different Channel A models:

| run | arms | Channel A |
|---|---|---|
| `campaign_20260901T105355Z` | A B5 G H B1 B2 B4 | `openai/gpt-oss-120b` |
| `campaign_20260906T221521Z` | B C D E F | `openai/gpt-oss-20b` |

`gpt-oss-120b` reached end of life on 2026-09-03 (D46) and cannot be re-run, so
**no same-binding comparison of arm A against arms B–F exists**, and reading
"the verification agent costs 0.044 AUROC" off A(0.907) − E(0.952) crosses the
model boundary. An adversarial verification panel confirmed this independently,
enumerating all 38 run directories: arms B–F occur in exactly one run ever.

Three refinements from that panel, none overturning it:

- **The 20b binding is attested by `config.json` alone.** No row in that campaign
  records a Channel A model id — `node_log` carries `model` only for the program
  node. Worth fixing in the recorder; recorded here as a provenance limit.
- **One same-binding arm-A row does exist**: `campaign_20260903T163724Z`, a 1-row
  smoke test at a revision 19 commits stale. "No same-binding *comparison*" is
  right; "no arm-A data at this binding" would be off by one row.
- **Arm B is exempt from the confound's mechanism**: it spent 0 natural-channel
  tokens, so the swap enters an A-vs-B contrast only through A's side.

**On accuracy the confound is empirically null** — the identical-18 result proves
the swap changes nothing there. It is unproven for the **risk score**, which is a
distribution rather than a set. `RUN_ARM_A_REBASE.bat` closes it for 135
requests.

### The arbiter could not be re-measured here

It fired **once in 225 rows**, as the arm definitions require: B has no natural
channel and C no program channel, so neither can produce a two-channel
disagreement; D disables the consistency engine and the arbiter; E disables the
arbiter. Only F retains both. TODO's advice to fold RX-045's re-measurement into
this run was wrong — these are precisely the arms that switch the arbiter off.

### What it did deliver

**All 225 rows carry an `explanation`.** This is the first campaign in the
project whose rows do, and it is the acceptance evidence Module 16 was missing.

---

## RX-046 - The deterministic verifier's blocker is upstream, and both prerequisites are now measured

**Date:** 2026-09-06 · **Status:** verified · **Cost:** zero, offline against the
chunk cache · **Concerns:** TODO 0c, Module 10, H3 · **Supersedes nothing**

TODO 0c named two steps that would unblock the deterministic verifier and said
of the second: *"this is the step that decides whether the rest is worth it"*. It
also said *"All of it is offline against the existing chunk cache, at zero API
cost"*, which was true. Both steps are now measured, and **neither is reachable
from inside `operand_binding`.**

### Step 1 - the note column. The stated signal does not discriminate

TODO described it as *"a column of small bare integers in a consistent
position"*. Implemented literally over all **634** table chunks with four or more
figure rows:

| | tables |
|---|---:|
| detected a note column | **33** |
| inspected and genuine | **~0** |

Every detection examined was a **false positive**, and for one reason: the corpus
is full of ESG and HR tables whose data genuinely *is* small bare integers —
headcounts, complaint counts, percentages, columns of zeros. "Small bare
integers in a consistent position" describes a note reference and also describes
a diversity disclosure.

**The missing constraint is context.** A note reference sits *before real money*:

```
| Investments | 8 | 1,005,681.63 | 511,581.71 |
```

Requiring at least one predominantly grouped-or-decimal column to its right cuts
33 detections to **4**, of which **1 is genuine** — Reliance p67, which is
exactly RX-041's case:

```
| Intangible Assets Under Development | 1 | 17,338   | 23,385   |
| Investments                         | 2 | 3,01,400 | 2,65,067 |
| Loans                               | 3 | 10,051   | 12,431   |
```

The surviving three are headcount tables where a column of zeros sits beside a
`2,13,527`. **Precision ~25% on 4 detections corpus-wide.** Any further
refinement would be fitted to four examples, and this feeds a component whose own
docstring says *"the consistency engine weights the deterministic channel highly
enough to overrule two agreeing channels. Wrong-and-confident is the worst
possible behaviour here."*

### Step 2 - the reporting-year column. Bounded at 0.279 by data that is not there

Where does a fact's year actually come from? Over all **1,793** extracted facts:

| `year_source` | facts | share |
|---|---:|---:|
| `document_fiscal_year` | 1,243 | **0.693** |
| `column_header` | 500 | **0.279** |
| `unknown` | 50 | 0.028 |

**Only 27.9% of facts have a year read from a column header.** The 69.3%
fallback records which *filing* the table came from — it cannot say which
*column* is the reporting year and which the comparative, which is the entire
question step 2 asks.

And the fallback is not merely uninformative. **90 of the 188 validated questions
(47.9%) ask about the comparative year** in their text — *"as reported for the
year ended March 31, 2023"* inside an FY2023-24 filing. On roughly half the
benchmark, `document_fiscal_year` points at the wrong column by construction.

### Verdict: BLOCKED, and the blocker has moved upstream

Module 10 stays **BLOCKED**, but the blocker is no longer "column identification"
in the abstract. It is **chunker-side header recovery**, a known separate
limitation with its own measured ceiling: a 6-row header search recovers 32.1% of
table chunks and 10 rows reaches 37.6%, *"the remaining chunks genuinely have no
header, and the fix is chunker-side"*. That figure and the 0.279 above agree,
which is the useful cross-check — two independent measurements of the same
missing thing.

**No amount of work inside `operand_binding` reaches it.** Nothing was shipped.
The `metric_in_text` helper built for RX-041 stays unwired, the abstention test
stays in place, and H3 remains untestable for the same reason it was before —
now with the reason located and priced.

**Why this is worth an entry despite shipping nothing.** TODO 0c read as a
scoped afternoon of offline work. It is a dead end, and the honest cost of
finding that out was one afternoon of offline work. Recording it stops the next
person spending theirs.

---

## RX-045 - The arbiter's accuracy, measured at last, and the fixed candidate order it exposed

**Date:** 2026-09-06 · **Status:** verified · **Runs:** all three campaigns
re-analysed, **no new API calls** · **Cost:** zero · **Module:** 13

`EVALUATION.md` §5.4 has asked for *"verification-agent resolution accuracy when
it is triggered"* since the methodology was written, and it was the last
unmeasured item on Module 13's acceptance list. It needed no new data: every row
already records whether the arbiter fired, whether it resolved, and what the
final answer was. Nobody had computed it.

### What the arbiter does

Pooled over arms A, G, H and O across all three campaigns:

| | |
|---|---:|
| triggered | **17** (2.2%–8.2% of questions per arm) |
| resolved | 16 |
| declined | 1 |
| **final answer correct** | **3 of 16** |
| **a channel actually held the right answer** | **7 of 16** |
| **arbiter returned it when it was there** | **3 of 7** |

**The second denominator is the one that matters.** The arbiter fires on
disagreement, and on 9 of those 16 questions *neither* channel had a correct
answer — there was nothing to choose. Reading 3/16 as "the arbiter is 19%
accurate" measures how hard disagreement questions are, not how well it chooses.
On the 7 where a correct answer was on the table it returned it 3 times.

### The pattern in the four misses

Every one of the four had the **program** channel correct and the arbiter took
the **natural** channel's wrong answer. Per distinct question:

| a correct answer existed in | questions | arbiter chose correctly |
|---|---:|---:|
| the natural channel | 2 | **2** |
| the program channel | 4 | **1** |

n=6 distinct questions. That is far too small to be a finding on its own — but
it pointed at the code, and **the code confirms the mechanism**.

### The defect: the prompt hid the labels and left the positions fixed

`run_verification_agent` passed `channel_a` to `CANDIDATE 1` and `channel_b` to
`CANDIDATE 2`, and the orchestrator always passes natural as A and program as B.
**CANDIDATE 1 was the natural channel on every arbitration this project has ever
run.**

The prompt carries a comment showing the author was thinking about exactly this:

> *"Deliberately does NOT name the channels by their implementation … Telling the
> arbiter that one answer came from executed code invites it to defer to that
> channel on authority rather than on evidence"*

The mitigation was half-applied. Hiding the label while fixing the order hides
nothing — position is a perfect proxy for identity, and first-position preference
is a well-known behaviour in pairwise LLM judging. Any such preference would show
up as a systematic preference for the natural channel, which is what the six
questions show.

**This is a research-validity defect, not a quality issue.** The arbiter is the
one component D1 permits to see both answers. A bias there is a bias in the
resolution of every disagreement the system reports, and it lands specifically on
the questions where the two channels differ — the questions the whole
contribution is about.

### The fix

Candidate order is now decided per question by `program_goes_first`, a SHA-256 of
the question text — **deterministic, not random**, because runs are pinned at
temperature 0 (D7a) and an arbiter that reordered on re-run would make campaigns
irreproducible. It comes out at 87 of 188 validated questions (0.463). The
verdict is translated back into channel terms before anything downstream reads
it, and `metadata.candidate_1` records the order actually shown, so the effect is
measurable after the fact — which it was not until now. 8 tests.

### What this does and does not license

**Not a correction to any published number.** No accuracy, AUROC or hypothesis
figure changes: the arbiter resolved 16 times across 787 rows, and its answer is
already in the graded outcome.

**It does weaken one reading of H3 and H4.** Those compare the full system
against arms that remove the deterministic verifier and vary the model binding,
and all three arms carry the same arbiter with the same fixed ordering. A
component with a systematic bias, present identically in every arm, is one more
reason those comparisons could return nothing — and it cannot be separated from
the others on this data.

**The fix is untested against a run.** Nothing has been executed with the
randomised order. The 3-of-7 figure is the *old* arbiter's, and re-measuring
needs a campaign. Recorded as an open item rather than claimed as improved.

---

## RX-044 - Re-analysing the held-out run on the repaired dataset moves exactly one number

**Date:** 2026-09-06 · **Status:** verified · **Run:**
`campaign_20260905T112212Z` re-analysed, **no new API calls** ·
**Report:** `evaluation/reports/results_campaign_20260905T112212Z_post_anchor_repair.json`
· **Cost:** zero

The anchor repair (RX-042) landed on 2026-09-06, a day *after* the test split was
evaluated. It dropped the evidence anchors from 22 corrected gold rows, 12 of
them on the test split. Nothing about the answers changed, so the graded rows
cannot have changed — but the H2 stratifier reads **evidence**, not answers, and
RX-039's stratification was computed against the stale anchors.

So the question this settles is narrow and worth settling: **does RX-039 survive
the repair?** The original report is preserved untouched; this is a second report
beside it.

### What moved, and what did not

| arm A | RX-039 (stale anchors) | **post-repair** |
|---|---:|---:|
| pooled AUROC | 0.8846 | **0.8846** |
| precision / recall / F1 at 0.51125 | 0.810 / 0.872 / 0.840 | **identical** |
| H1 / H3 / H4 point, CI and p | — | **identical** |
| reasoning-caused | n=21, 4 errors, AUROC **0.787** | n=21, 4 errors, AUROC **0.787** |
| **retrieval-caused** | n=40, 35 errors, AUROC **0.851** | **n=32, 27 errors, AUROC 0.837** |
| **unknown** | n=0 | **n=8, 8 errors, AUROC undefined** |

**One stratum moved and nothing else did.** That is the expected signature of
this repair and is itself the check: had accuracy, pooled AUROC or any hypothesis
shifted, the repair would have touched something it had no business touching.

### The finding survives, and the number it rests on is now smaller

RX-039's H2 result was *detection is **worse** on reasoning-caused errors
(0.787) than on retrieval-caused ones (0.851)*, the opposite of H2's prediction.
Post-repair it is 0.787 against **0.837**. Same sign, same conclusion, gap
narrowed from 0.065 to 0.050.

**Quote 0.837, not 0.851.** The larger figure was computed over 40 questions of
which 8 were stratified using anchors that cited a rejected candidate. It is
superseded, not withdrawn: it was correctly computed from what the dataset said
at the time.

### The 8 questions are unclassifiable, not reclassified

They are corrected-answer rows whose anchors the repair dropped, so
`evidence_was_retrieved` returns `None` — undecidable — rather than `False`. The
analyser puts them in `unknown` and its own note says why that direction is the
safe one:

> *"Reported as `unknown`, never folded into reasoning-caused - that direction
> would make H2 look supported"*

All 8 are errors. Folding them into either stratum would move that stratum's
AUROC on 8 wrong answers with no evidence to justify the placement, and the
direction that flatters the hypothesis is the one that would have been easiest to
argue for. They stay out until their anchors are re-derived (TODO 0b).

### Why this was not caught until an audit

Nothing re-runs the analysis when the dataset changes. The repair commit changed
`finverify_ind_v1.json`; the committed report for the run that reads it stayed as
it was, and no test compares them. That is a real gap and it is cheap to close —
the analysis costs nothing and takes under a minute — but it is not closed here,
because the fix is a CI step this project does not have. Recorded in TODO rather
than asserted as done.

---

## RX-043 - With correct evidence the system is right 35 times in 37, and H2 is out of reach

**Date:** 2026-09-06 · **Status:** verified · **Run:**
`campaign_20260906T092553Z` · arm O, validation, 45 questions ·
**Supersedes:** RX-040, which ran on stale anchors · **Cost:** 135 NVIDIA
requests

The first oracle run whose evidence is actually correct. RX-040's oracle
resolved through anchors that cited figures the validator had rejected (RX-042),
so on up to 10 of its 43 questions it handed the channels the **wrong chunk** and
the resulting errors were filed as reasoning errors. Those anchors have since
been dropped, and this is the re-run.

### The headline

Restricted to the **37 questions the oracle actually built for**, where every
error is a reasoning error by construction:

| | value |
|---|---:|
| correct | **35 of 37** |
| **accuracy** | **0.946** |
| errors | **2** |
| blind spot (agreed and wrong) | **1 of 33** |

**Handed the right evidence, this system answers correctly 95% of the time.**

Against arm A's 0.400 with real retrieval, on the same questions, with the same
models, prompts and temperature. Nothing about the reasoner changed between
those two numbers.

### What the repair changed

| | stale anchors (RX-040) | **repaired (this run)** |
|---|---:|---:|
| oracle built for | 43 | 37 |
| accuracy on those | 0.860 | **0.946** |
| errors on those | 6 | **2** |
| blind spot | 5 of 33 | **1 of 33** |
| AUROC on those | 0.541 | 0.714 |

**Four of RX-040's six "reasoning errors" were the oracle handing over the wrong
chunk.** The blind-spot count falls from 5 to 1 for the same reason: those cases
were both channels correctly reading a chunk that was the wrong chunk, which
looks identical to a shared misreading and is not one.

The pooled all-45 AUROC of **0.943** in the analysis output should not be
quoted. It spans the 8 questions whose anchors the repair dropped, which the
oracle therefore handed *nothing* - closed-book rows wearing an oracle arm's
name, 0 of 8 correct and all high-risk. That inflates AUROC for a reason
unrelated to reasoning.

### The two remaining errors

**FI40d1603f** - gold 452,982.84, both channels abstained, risk **0.551**. Not a
wrong answer but a refusal, counted as an error because no prediction was
produced. The detector flagged it.

**FI46e02586** - gold 12,798.00, both channels returned **25,018.28**, agreed,
risk **0.0**. This is the failure D1 exists to worry about: two channels over one
evidence set producing the same wrong figure and corroborating each other into
confidence. **n=1.**

Both carry `provenance=UNDETERMINED` and `confidence=NEEDS_HUMAN`, and the error
analyser refuses to call either a reasoning error:

> *"evidence was retrieved and the answer is wrong, but question understanding
> and/or extraction were not checked - so 'reasoning' cannot be concluded, only
> assumed"*

So the reasoning stratum is not merely small. It is **2 unconfirmed cases**.

### H2 is out of reach, and now the arithmetic says by how much

| | |
|---|---:|
| P(error \| correct evidence) | **2/37 = 0.054** |
| questions needed for 20 reasoning errors | **~370** |
| questions needed for 30 | **~555** |

The benchmark has 45 validated validation questions. Reaching a testable
reasoning stratum needs an order of magnitude more, **with retrieval working**,
and retrieval currently delivers on about a third of questions - so end to end it
is nearer 1,000.

This is no longer "H2 was untestable on this run". It is: **H2 is not reachable
on a benchmark of this size, because the system almost never makes a reasoning
error when it has the evidence.**

### What this settles, and what it does not

**Settled: retrieval is the entire story.** 0.400 with real retrieval, 0.946
with correct evidence, same everything else. Every accuracy figure this project
has reported is a retrieval measurement.

**Settled: RX-040's mechanism claim is void.** It rested on 6 errors of which 4
were the oracle's own fault. Its AUROC of 0.541 - "indistinguishable from
chance" - was measuring a broken oracle. The corrected figure is 0.714 on 2
errors, which measures nothing at all.

**Not settled, and now demonstrably unreachable here: whether dual-channel
disagreement detects reasoning hallucination.** One confident-wrong case in 37 is
consistent with almost any hypothesis. The honest statement is that this
benchmark cannot answer the question, and the reason is a good property of the
system rather than a flaw in the experiment.

### Caveats

- The oracle supplies one block per gold group against retrieval's eight, so
  this is reasoning with the right table *and few distractors* (D47).
- Validation only, by design (D47) - the arm was built after seeing the test
  result.
- 37 questions, 2 errors. Every ratio here has an interval wide enough to
  contain most alternatives.
- The 8 dropped-anchor questions are excluded from the headline and reported
  separately rather than being allowed to inflate it.

---

## RX-042 - The gold answers are right; the evidence anchors are stale, and that is what broke the oracle arm

**Date:** 2026-09-06 · **Status:** verified · **Supersedes:** the diagnosis in
RX-040's correction, which was wrong · **Cost:** 0 API requests

**RX-040's correction said** that 11 gold questions carry an answer contradicting
their own evidence, and inferred that the answers were suspect and needed
re-validation. **The answers are not suspect. They are the validator's own, and
they are right.**

### What actually happened

`datasets/finverify_ind/worksheet.csv` carries the whole history, and it is
unambiguous. For all 11 the verdict is `validated` with a **corrected answer**
and a note explaining the correction:

| qid | candidate | corrected → dataset | anchors still cite |
|---|---:|---:|---:|
| FI82a95e99 | 5667 | **61269** | 5667 |
| FI939a55f7 | 2271.29 | **76641.43** | 2271.29 |
| FI995ccc77 | 16230 | **20426** | 16230 |
| FI5d61cd35 | 31806.75 | **31399.09** | 31806.75 |
| FIce2f3063 | 6944.85 | **79214** | 6944.85 |
| … | | | |

**10 of 11 anchor sets cite the candidate the validator explicitly rejected.**
The notes are specific about why:

> *"₹5,667 crore is not the FY 2023–24 consolidated figure. In the consolidated
> balance sheet…"*
>
> *"The candidate ₹9,301.96 crore is specifically Deferred Tax Assets, not the
> requested…"*

So the sequence is:

1. Auto-extraction produced a candidate answer **and** evidence anchors citing it.
2. The validator read the filing, **rejected the candidate**, and supplied the
   correct figure with a note.
3. `build_finverify_ind.py import` wrote the corrected **answer** into the
   dataset.
4. **It left the evidence anchors pointing at the rejected figure.**

The answer is the human's. The anchors are the machine's first guess, preserved
after the human overruled it.

### This is what broke the oracle arm

Arm O resolves its evidence *through the anchors*. For these questions it
therefore located the chunk containing the **rejected** figure and handed that
to both channels. The channels read it faithfully and returned the rejected
value, and the grader — comparing against the corrected answer — called it wrong.

So those rows are neither gold defects nor reasoning errors. They are **oracle
rows built from the wrong chunk**, and the oracle condition was never actually
achieved on them.

RX-040's sensitivity split still stands numerically — excluding these rows takes
the oracle AUROC from 0.655 to **0.817** — but the reason for excluding them is
this, not "the gold is wrong".

### What is actually broken, and where

**The import path.** `build_finverify_ind.py import` updates `answer` from
`corrected_answer` and does not touch `evidence`. Nothing downstream notices,
because every metric reads `answer` and only the retrieval-side machinery reads
`evidence` — and until arm O existed, nothing compared the two.

That is the same shape as the two defects before it: a reader and a writer
disagreeing about a field, invisible to every test, surfaced only by a new
consumer.

**Blast radius.** Anything that reads evidence rather than answers is pointed at
the wrong row for these questions: the oracle arm, `evidence_was_retrieved`, the
H2 stratifier, and the retrieval metrics' notion of a hit.

### The repair, and its limits

The corrected answer is locatable in the filing's own chunk cache for **8 of the
10** affected lookups, so anchors can be re-derived and offered for review.
`FI82a95e99` (61269) and `FIce2f3063` (79214) appear in no chunk — the notes
suggest both are consolidated figures the validator assembled, so those two need
a person.

**No gold has been altered.** Re-deriving an anchor is repairing metadata to
match a decision the human already made, not changing a label to move a score -
but it is still gold data, and it goes through review rather than a script
writing it silently.

`review_gold.py` now takes `--qid` and prints the conflict explicitly, saying
that the answer is not what needs checking.

### Correcting my own record

RX-040's headline was withdrawn once on the strength of a diagnosis that was
itself wrong. The sequence is worth stating plainly, because it is three
instrument failures in a row on one question:

1. "The detector collapses to chance" — wrong; three errors were not errors.
2. "The gold answers contradict their evidence and need re-validation" — wrong;
   the answers are validated and correct, the anchors are stale.
3. What is true: **the anchors are stale, the oracle arm inherited that, and the
   mechanism question remains unanswered.**

The first two were each published before the check that overturned them had been
run. The measurement that settles it — reading `worksheet.csv`, which had the
verdicts and the notes in it the whole time — cost nothing and was available
throughout.

---

## RX-041 - The deterministic verifier cannot be enabled on lookups: it binds the note-reference column

**Date:** 2026-09-06 · **Status:** verified · **Outcome:** NEGATIVE — the
proposed fix is measured, rejected, and not shipped · **Cost:** 0 API requests

**The problem it was meant to fix.** The deterministic verifier fires on **1
question of 45** (RX-040). It abstains on lookups by design:

> *"no arithmetic to verify: a lookup answer is a figure read from a row, and
> re-reading it would be structural corroboration rather than an independent
> check"*

44 of 45 validation questions are lookups, so **arm G removes a component that
never runs, and H3 has been testing nothing.** Enabling the lookup path looked
like the single highest-value fix available.

**Why it should have worked.** `operand_binding.bind_operands` already matches a
metric's label against table rows and reads the figure - regex and lexicon, no
model. Its failure mode is *refusing to bind*, where a language model's is
*confidently picking the wrong row*. Those are different failure modes over the
same evidence, which is what an independent check requires. The abstention's
stated reason - that re-reading is mere corroboration - is answerable.

**First half built.** `metric_in_text` resolves the metric from the question
against the 61-entry lexicon. `parse_question` hands the channel a sub-question
metric with the boilerplate attached - *"HDFC Bank Limited, reported provisions
provisions reported consolidated financial statements"* - which `_labels_for`
can never match against a row. Longest alias wins, so *"other financial
liabilities"* does not bind as *"financial liabilities"*. Five tests.

### The measurement that stopped it

Binding run against the **oracle** run's evidence - the best case available,
since the right chunk was handed over:

| | count |
|---|---:|
| questions | 45 |
| no lexicon metric found | 0 |
| metric found, did not bind | 14 |
| **bound** | **31** |
| — agrees with gold (τ=0.5%) | **8** |
| — **disagrees** | **23** |

**A naive lookup path would be wrong 23 times in 31.**

### Why, exactly

```
| Investments | 8 | 1,005,681.63 | 511,581.71 |
```

Cell 1 is the **note reference**. `bind_operands(column=0)` takes the first
figure-shaped cell after the label, and `_is_figure("8")` is True: `_NOTE_REF`
catches dotted references like `2.14`, but a bare integer passes `_BARE_INT`.
So the binder returns **8** where gold is **511,581.71**.

The same shape produced `other equity` → 2 (gold 452,982.84) and
`property, plant and equipment` → 10 (gold 8,282.56).

And there is a second problem underneath: gold for that question is
**511,581.71**, the *third* numeric cell, not the second. Which column carries
the reporting year is not fixed across filings, so skipping the note column is
necessary and not sufficient.

### Why it is not shipped

`operand_binding`'s own docstring states the stake:

> *"A binder that guessed which row was meant would manufacture a confident
> third opinion out of a mis-read label, and the consistency engine weights the
> deterministic channel highly enough to overrule two agreeing channels.
> Wrong-and-confident is the worst possible behaviour here."*

Shipping a lookup path that is wrong 23 times in 31 would do precisely that, to
44 of 45 questions, three days before the deadline. **The abstention stands, and
a test now pins it** so the path cannot be enabled without this measurement being
confronted.

### What this changes about the earlier diagnosis

RX-040 reported the verifier's inapplicability as a defect with an obvious fix.
It is not obvious. The blocker is not the abstention rule - it is that **the
binder cannot identify which column of a financial table holds the value**, and
nothing in the project currently can.

That makes H3 untestable for a deeper reason than "a flag is set wrong": there
is no working deterministic third opinion on lookups to ablate.

### What would actually be needed

1. **Identify the note column per table, not per row.** It is a column of small
   bare integers appearing in the same position across rows. Per-row heuristics
   cannot see that; per-table ones can.
2. **Identify which numeric column is the reporting year.** Column order varies
   by filing, and the header row or the caption carries it.
3. Only then enable the lookup path, and re-measure against gold before wiring
   it into the consistency engine.

Each is testable offline against the existing chunk cache at zero API cost. None
is a small change, and step 2 is the one that decides whether the whole thing is
worth having.

---

## RX-040 - Oracle retrieval: accuracy doubles, and a gold defect is exposed

> **SUPERSEDED by RX-043.** This run's oracle resolved through anchors that
> cited figures the validator had rejected, so on up to 10 of its 43 questions
> it handed the channels the WRONG chunk. Four of its six "reasoning errors"
> were that. The repaired re-run gives accuracy 0.946 on 37 questions with 2
> errors, not 0.860 with 6. Do not quote this entry's figures.

> **The original title claimed the detector collapses to chance. That claim is
> WITHDRAWN — see the correction at the end of this entry. Three of the eight
> errors it rested on are gold-label defects, and excluding them the AUROC is
> 0.817, not 0.655. What survives is the retrieval finding.**

**Date:** 2026-09-06 · **Status:** verified · **Run:**
`campaign_20260906T005652Z` · arm O, validation split, 45 questions ·
**Outcome:** the mechanism question is answered, and the answer is negative ·
**Cost:** 135 NVIDIA requests

**Why this arm exists.** H2 needs errors made *with* the evidence in hand, and
the end-to-end system produces one per fifteen questions (D47). Arm O hands each
question the chunks containing its gold evidence, so every remaining error is a
reasoning error by construction. The oracle built for **43 of 45** questions.

### Retrieval is the bottleneck — the cleanest measurement in the project

| | arm A, real retrieval | **arm O, oracle** |
|---|---:|---:|
| numerical accuracy | 0.400 | **0.822** |
| abstention rate | 0.444 | **0.067** |
| errors | 27 of 45 | **8 of 45** |

**Accuracy doubles and abstention collapses when the evidence arrives.** Nothing
about the reasoner changed — same models, same prompts, same temperature. The
40% figure the system has carried all along is a retrieval score wearing a
reasoning score's clothes.

### And the detector stops working

| | arm A, real retrieval | **arm O, oracle** |
|---|---:|---:|
| AUROC | 0.907 | **0.655** [0.435, 0.876] |
| AUROC, reasoning stratum | n/a (empty) | **0.541** |
| precision @ 0.51125 | 0.810 | **0.250** |
| recall @ 0.51125 | 0.872 | **0.375** |
| errors caught | 26 of 27 | **3 of 8** |

**The interval on 0.655 contains 0.5.** On the reasoning stratum alone the score
reaches **0.541** — indistinguishable from chance. At the frozen operating point
it catches three of eight errors and raises nine false flags to do it.

This is the answer to the project's research question, and it is negative:
**dual-channel disagreement does not detect numerical hallucination in
reasoning. What it detects is retrieval failure.**

### Why, mechanically — and this is the part that generalises

The risk score is built from channel agreement and evidence coverage. Those two
signals behave in opposite ways depending on which failure is occurring.

**When retrieval fails**, both channels are handed nothing useful. Coverage is
low, one or both abstain, and they disagree about a figure neither can support.
The score goes up. The answer is wrong. Detection succeeds — for a reason that
has nothing to do with reasoning.

**When retrieval succeeds and the model misreads the table**, both channels read
**the same correct evidence** and make **the same mistake**. They agree. Coverage
is high. The score goes down. The answer is wrong and the system is confident.

The blind-spot count is the direct evidence: **5 of 33 agreed answers were
wrong** (0.152). Independence at the model level does not help here, because the
error is not sampled noise — it is a shared misreading of a shared input, which
is the common-mode failure path D19 names, and which CASE_STUDY_REPORT §13.3
calls irreducible: one `QuestionSpec` and one evidence set feed both channels.

The error shapes say the same thing. Of the 8 errors, `mismatch_counts` puts
**5 in `magnitude`** and 3 in `missing_prediction`; the independent error-kind
taxonomy labels 2 of them `wrong_unit`. Magnitude errors are the
crore/lakh/million trap CLAUDE.md names as the highest-frequency source of
plausible-looking 10x-100x mistakes. Both channels inherit the same
`context_scale` from the same chunk and both apply it the same wrong way.

### What this does to the earlier results

It does not contradict RX-038 or RX-039; it explains them.

- Arm A's **0.885 held-out AUROC is real** and remains the honest end-to-end
  number. It is a good detector of *this system's* errors, and this system's
  errors are overwhelmingly retrieval failures.
- **H1's near-miss (p=0.100) is no longer interesting as evidence for the
  mechanism.** Whatever advantage arm A has over self-consistency, it is not
  earned on the error class the design targets.
- **RX-039's H2 result is confirmed and sharpened.** There, detection was worse
  on reasoning-caused errors (0.787 vs 0.851) on 4 errors. Here it is 0.541 on 6,
  under a condition built to isolate exactly that stratum. Two independent
  routes, same direction.

### Caveats, and they are not small

- **6 reasoning errors.** The interval on 0.541 is enormous. This shows the
  score is *not demonstrably better than chance* at this task; it does not show
  it is exactly chance.
- **The oracle removes the distractors too.** One block per gold group against
  retrieval's eight (D47). So this measures reasoning when handed exactly the
  right table, and the real condition — right table among seven near-misses — is
  somewhere between this arm and arm A.
- **Validation only, by design.** The arm was built after seeing RX-039 and
  running it on test would be leakage (D47). Nothing here is held out.
- **The threshold is arm A's.** 0.51125 was selected on a different score
  distribution; the precision/recall row is indicative. AUROC is not affected.

### What it is worth

The project set out to ask whether dual-channel disagreement detects numerical
hallucination. It now has an answer rather than an absence of one: **no, and
here is the mechanism by which it fails** — the two channels share their
evidence, so they share the errors that evidence induces, and agreement is
uninformative exactly when the evidence is good.

That is a more useful result than a null. It identifies the condition under
which the architecture cannot work, and it points at what would have to change:
channels that disagree about *the same evidence* need genuinely different
readings of it - different extraction, different scale resolution - not
different models over one shared parse.

### CORRECTION 2026-09-06 — three of the eight "reasoning errors" are not errors

> **The numbers below stand; the DIAGNOSIS in this section is superseded by
> RX-042.** It concluded the gold answers were suspect. They are not - they are
> the validator's own corrections, and it is the evidence ANCHORS that are stale,
> which is also what pointed the oracle arm at the wrong chunk. Read RX-042.

**The claim above — that detection collapses to chance under oracle retrieval —
is withdrawn.** It rested on 8 errors. Three of them are not errors.

**What was found.** A gold lookup question carries an `answer` and `evidence`
anchors naming where that answer is found. For a lookup those are the same claim
made twice: the answer *is* the figure at the cited location. They can therefore
contradict each other, and they do.

`FI82a95e99` is the clearest case:

| | |
|---|---|
| gold anchors | `["Other Financial Liabilities", "5667"]` |
| gold answer | `61269` |
| system predicted | **`5667`** |
| the cited chunk | `Other Financial Liabilities \| 18 \| 5,667 \| 7,704` |

**The system read exactly the figure the gold evidence points at and was graded
wrong**, because the answer field disagrees with the evidence field. Three of the
eight oracle-arm errors are this shape: `FI82a95e99`, `FI939a55f7`, `FI995ccc77`,
each predicting precisely its own gold anchor's numeral.

**How widespread**, counting only lookups, where answer and anchor must agree by
definition:

| split | gold answer matching NO numeric anchor in its own evidence |
|---|---|
| validation lookups | **5 of 42 — 12%** |
| test lookups | **6 of 58 — 10%** |

Derived questions are excluded: a ratio or growth figure is *computed* from its
anchors and legitimately matches none of them.

**The sensitivity analysis, which is the point.**

| | AUROC | errors |
|---|---:|---:|
| all 45 questions, as first reported | 0.655 | 8 |
| **excluding the gold-inconsistent ones** | **0.817** | **5** |

0.817 against arm A's 0.907 is a modest drop on five errors with an enormous
interval. It is **not** chance, and "the detector was detecting retrieval failure
all along" is not supported by this run.

### What survives, and what does not

**Survives: retrieval is the bottleneck.** Accuracy 0.400 → **0.822** and
abstention 0.444 → **0.067** when the evidence is supplied. Gold defects affect
both arms identically, so the doubling is unaffected by them. This remains the
cleanest measurement in the project.

**Withdrawn: the mechanism conclusion.** This run does not show that
dual-channel disagreement fails to detect reasoning errors. It returns the
position to RX-039's: **not demonstrated**, on a stratum too small to demonstrate
anything either way. The blind-spot observation (5 of 33 agreed answers wrong)
is likewise inflated - at least three of those five are gold disagreements, not
shared misreadings.

**Unchanged: the deterministic verifier fires on 1 question of 45.** It abstains
on lookups by design - *"a lookup answer is a figure read from a row, and
re-reading it would be structural corroboration rather than an independent
check"* - and 44 of 45 questions are lookups. That is why arm G, which removes
it, scores the same as arm A: H3 tests a component that never runs.

**Unchanged: `scale` is `None` on all 44 oracle evidence blocks**, so the `unit`
facet reports VERIFIED on every error including the wrong ones. A facet that
cannot fail carries no information.

### Why this was not caught earlier

Nothing in the pipeline checks that a gold answer agrees with its own evidence
anchors. The human validation pass judged answers against the source document;
it did not cross-check the two gold fields against each other, and no test does
either. The defect is invisible to every metric the project computes, because
every metric takes the `answer` field as ground truth by definition.

It surfaced only because the oracle arm made the disagreement visible: handed
the cited chunk and nothing else, the system returned the cited figure, and the
grader called it wrong.

**No gold has been altered.** CLAUDE.md forbids editing labels to move a score,
and the fix is not to pick whichever field makes the number better - it is to
re-validate the affected questions against the filings, which needs the human
validator. The affected qids are listed above and in TODO.

---

## RX-039 - The held-out result: detection generalises, the mechanism does not

**Date:** 2026-09-05 · **Status:** verified · **Run:**
`campaign_20260905T112212Z` · **Split:** TEST, evaluated once ·
**Outcome:** detection transfers to held-out data; no hypothesis supported; H2
testable for the first time and it FAILS · **Cost:** ~1.6M NVIDIA tokens

**Scope.** Seven arms x 61 validated, non-ambiguous test questions = **427 rows,
every question carrying every arm.** **Zero provider failures on either channel**
- 0 of 427 Channel A, 0 of 183 Channel B. Bindings: Channel A and arbiter
`nvidia/openai/gpt-oss-20b`, Channel B `nvidia/nvidia/nemotron-3-ultra-550b-a55b`.

### QA - and this time the arms separate

| arm | accuracy | exact | abstain |
|---|---:|---:|---:|
| **A** (full system) | **0.361** | 0.361 | 0.410 |
| **H** (same model both channels) | **0.361** | 0.344 | 0.377 |
| G (no deterministic verifier) | 0.344 | 0.344 | 0.410 |
| B2 (plain RAG) | 0.328 | 0.328 | 0.426 |
| B5 (self-consistency) | 0.328 | 0.328 | 0.475 |
| B4 (agentic RAG) | 0.311 | 0.311 | 0.443 |
| B1 (closed book) | **0.000** | 0.000 | 1.000 |

On validation all five retrieving arms returned the *same* 18 answers. Here they
do not: **arm A leads plain RAG by 0.033 (2 questions of 61)**. That is a
difference, and it is far too small at this n to be a claim - the interval on a
2-question gap covers zero comfortably. Reported because it is the first sign of
separation, not because it is evidence.

B1 abstains on **61 of 61**. Closed-book has no access to the filings' numbers
and correctly declines rather than inventing them.

### Detection - it transfers

| arm | n | errors | AUROC | 95% CI |
|---|---:|---:|---:|---|
| **A** | 61 | 39 | **0.885** | [0.801, 0.954] |
| H | 61 | 39 | 0.857 | [0.749, 0.945] |
| G | 61 | 40 | 0.855 | [0.736, 0.947] |
| B5 | 37 | 17 | 0.606 | [0.479, 0.736] |

**Validation 0.907 -> test 0.885.** The risk score ranks held-out errors almost
exactly as well as it ranked the errors it was developed against, and **arm A is
now the top arm** where on validation it trailed both its own ablations. Nothing
was tuned between the two runs; the ordering changed because n=45 could not
separate arms whose intervals overlapped by that much, and n=61 barely can.

At the frozen threshold 0.51125:

| arm | TP | FP | TN | FN | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 34 | 8 | 14 | 5 | 0.810 | 0.872 | 0.840 |
| G | 36 | 9 | 12 | 4 | 0.800 | 0.900 | 0.847 |
| H | 32 | 3 | 19 | 7 | 0.914 | 0.821 | 0.865 |
| B5 | 1 | 0 | 20 | 16 | 1.000 | 0.059 | 0.111 |

Arm A catches **34 of 39** wrong answers and wrongly flags 8 of 22 correct ones.
B5's row is a scale artefact, not a measurement: the threshold was selected on
arm A's distribution and B5's self-consistency score does not share it.

**A caveat that must travel with those four columns.** The threshold was frozen
on validation *before* any test data existed, so there is no leakage in
sequence - but that validation ran on `gpt-oss-120b`, which the provider retired
on 2026-09-03 (D46), and this test ran on `gpt-oss-20b`. The operating point is
therefore sourced from a different Channel A model than it is applied to. **AUROC
is unaffected, being threshold-free, and it is EVALUATION.md's primary metric.**
The threshold-dependent columns should be read as indicative until validation is
re-run on the current binding.

### The hypotheses - none supported, but all four now point the right way

| | comparison | point | 95% CI | p | Holm |
|---|---|---:|---|---:|---|
| H1 | AUROC(A) - AUROC(B5) | **+0.159** | [-0.034, 0.338] | 0.100 | vs 0.017 - no |
| H3 | AUROC(A) - AUROC(G) | **+0.030** | [-0.062, 0.136] | 0.580 | vs 0.025 - no |
| H4 | AUROC(A) - AUROC(H) | **+0.027** | [-0.068, 0.139] | 0.641 | vs 0.050 - no |

Every interval still crosses zero, and nothing survives correction. What changed
from validation is the *sign*: H3 and H4 were negative there - the ablations
beat the full system - and are positive here. That is consistent with the full
system being mildly better and with n being far too small to show it; it is not
evidence of either.

**H1 came closest** (p=0.100) and is the comparison the project cares most
about, since B5 is the SelfCheckGPT-style comparator D11 names.

### H2 is testable at last, and it fails

On validation the reasoning stratum was empty - every error came from retrieval,
so the mechanism check could not run. The test split has reasoning-caused
errors, and the check runs:

| stratum | questions | errors | AUROC |
|---|---:|---:|---:|
| retrieval-caused | 40 | 35 | **0.851** |
| reasoning-caused | 21 | 4 | **0.787** |

H2 predicted detection would be **far better** on reasoning-caused errors,
because that is the failure dual-channel disagreement is supposed to catch. It
is **worse**, by 0.065.

> **SUPERSEDED in one cell by RX-044.** The anchor repair landed the day after
> this run and dropped the stale anchors from 12 test rows. The stratifier reads
> evidence, so 8 of those questions became undecidable and left this table:
> retrieval-caused is **n=32, 27 errors, AUROC 0.837**, and the gap is **0.050**
> rather than 0.065. Reasoning-caused is unchanged, as are pooled AUROC,
> accuracy, precision, recall, F1 and every hypothesis. **Quote 0.837.** The
> entry is left as written — it was correctly computed from what the dataset said
> at the time, and rewriting it would erase the fact that the number moved.

H2's own rationale states the consequence: *"A positive H1 with a failed H2 must
be reported as an unexplained correlation, not a validated design."* H1 is not
positive either, so the honest reading is stronger than that: **the risk score
demonstrably ranks errors well, and this campaign provides no evidence that it
does so by the mechanism the system was built around.**

Caveats that keep this from being a strong negative: 4 errors in the reasoning
stratum is a very small denominator, the comparison is unpaired across different
question sets, and no interval is reported for the difference.

### Blind spot - the validation signal did not replicate

| arm | both channels agreed AND both wrong | rate |
|---|---|---:|
| G | 4 of 16 | 0.250 |
| A | 5 of 19 | 0.263 |
| H | 7 of 26 | 0.269 |

On validation arm H's rate was twice arm A's (0.150 against 0.071), which is the
direction D1 predicts for same-model channels corroborating each other's
mistakes. Here the three arms are indistinguishable. **That signal did not
survive out of sample**, and reporting the validation figure without this would
have been reporting noise.

### Efficiency - the one claim that replicates cleanly

| arm | AUROC | tokens/question |
|---|---:|---:|
| B1 | - | 526 |
| B2 | - | 2,466 |
| B4 | - | 2,485 |
| H | 0.857 | 5,391 |
| **A** | **0.885** | **6,445** |
| G | 0.855 | 6,464 |
| **B5** | **0.606** | **14,816** |

**Self-consistency costs 2.3x the full system and detects far worse** - 14,816
tokens against 6,445, for AUROC 0.606 against 0.885. Validation measured 2.4x
and the same direction. This is the only quantitative claim in the project that
holds its size and sign across both splits.

### What this establishes

1. **The detector generalises.** AUROC 0.885 held out against 0.907 on
   validation, with nothing tuned between them.
2. **It beats self-consistency on both axes at once** - better detection for
   43% of the tokens - and that replicates across splits.
3. **No hypothesis is supported.** The full system cannot be distinguished from
   its own ablations at n=61.
4. **The mechanism check fails.** Detection is worse on exactly the error class
   the design targets. Whatever makes the risk score work, this campaign gives
   no evidence it is dual-channel disagreement over reasoning.
5. **A validation-only finding did not replicate** (the blind-spot gap), which is
   the ordinary reason held-out evaluation exists.

The project's defensible claim is therefore narrower than its motivation: it
built a numerical-answer verifier whose risk score ranks errors well and cheaply,
and it did not demonstrate that the dual-channel design is why.

---

## RX-038 - The first complete campaign: the detector works, the benchmark has nothing for it to detect

**Date:** 2026-09-02 · **Status:** verified, **with a CORRECTION dated 2026-09-03 at the end of this entry — the H2 stratification below was computed with a broken evidence matcher; read the correction before quoting any provenance figure** · **Run:** `campaign_20260901T105355Z`
· **Outcome:** the project's first complete multi-arm campaign; no hypothesis
supported; one defect found that had zeroed every efficiency figure ever
produced · **Cost:** ~1.4M NVIDIA tokens, **0 Groq**

**Scope.** Seven arms x 45 validated questions = **315 rows, every question
carrying every arm.** Channel B failed 1 of 135 program calls (0.7%), below the
4.4% baseline this endpoint showed on B3 (RX-037). D44 bindings: Channel A and
the arbiter on `nvidia/openai/gpt-oss-120b`, Channel B on
`nvidia/nvidia/nemotron-3-ultra-550b-a55b`, recorded in `config.json` as *"same
provider, different models"*.

### QA - every arm that retrieves gets the same answers

| arm | accuracy | exact | abstain | parse-fail |
|---|---:|---:|---:|---:|
| A (full system) | **0.400** | 0.356 | 0.444 | 0.022 |
| B2 (plain RAG) | **0.400** | 0.356 | 0.467 | 0.000 |
| B5 (self-consistency) | **0.400** | 0.356 | 0.489 | 0.000 |
| G (no deterministic verifier) | **0.400** | 0.356 | 0.489 | 0.000 |
| H (same model both channels) | **0.400** | 0.356 | 0.467 | 0.000 |
| B4 (agentic RAG) | 0.356 | 0.333 | 0.489 | 0.022 |
| B1 (closed book) | **0.000** | 0.000 | 0.978 | 0.022 |

**Five arms return the same 18 correct answers.** The full dual-channel system
is not more accurate than plain RAG. That is not a defect: all five share
Channel A at temperature 0 over the same retrieved evidence, and the program
channel and arbiter almost never overturn it - the arbiter fired on 4 of 45
questions in arm A. The machinery changes the *risk score*, not the answer.

Worth stating plainly because it is easy to imply otherwise: **on this
benchmark the contribution is not accuracy.** B1 at 0.000 is the floor and the
one unambiguous baseline result - closed-book cannot answer numerical questions
about specific filings at all, and abstains on 97.8% rather than inventing them.

### Detection - the risk score ranks errors well

| arm | n | errors | AUROC | 95% CI |
|---|---:|---:|---:|---|
| G | 45 | 27 | **0.959** | [0.909, 0.992] |
| H | 45 | 27 | 0.936 | [0.861, 0.998] |
| A | 45 | 27 | 0.907 | [0.780, 0.992] |
| B5 | 23 | 5 | 0.600 | [0.500, 0.833] |

B1, B2 and B4 produce no risk score and are absent by design rather than
entered at 0.5, which would read as "detects nothing" instead of "is not a
detector".

**A reading hazard in this table.** Each arm is scored over everything *it*
scored, and B5 only scores questions it answered - 23 of 45, at a 21.7% base
error rate against arm A's 60%. Comparing the rows directly (0.907 vs 0.600)
overstates the gap roughly twofold. The H1 statistic below does **not** make
that mistake: it pairs on the 23 questions both scored, where **A scores 0.767**
and the difference is +0.167. Verified by recomputing it independently.

### Hypotheses - none supported

| | comparison | point | 95% CI | p | Holm |
|---|---|---:|---|---:|---|
| H1 | AUROC(A) - AUROC(B5) | +0.167 | [-0.071, 0.389] | 0.172 | vs 0.017 - **no** |
| H3 | AUROC(A) - AUROC(G) | **-0.051** | [-0.174, 0.030] | 0.368 | vs 0.025 - **no** |
| H4 | AUROC(A) - AUROC(H) | **-0.029** | [-0.167, 0.084] | 0.708 | vs 0.050 - **no** |
| H2 | mechanism | \- | \- | \- | **NOT TESTABLE** |

Every interval crosses zero. H3 and H4 additionally point the *wrong way*:
removing the deterministic verifier and collapsing both channels onto one model
each scored slightly **higher** than the full system, though neither
significantly. At n=45 this campaign cannot distinguish the full system from its
own ablations.

### H2 is the finding, and it is structural

H2 is untestable because of *how* the errors are distributed, not how many
there are. Error provenance is identical across all four detector arms:

| provenance | count |
|---|---:|
| retrieval | 24 |
| question understanding | 3 |
| **reasoning** | **0** |

**Not one of the 27 errors is a reasoning error.** The system fails because
retrieval does not put the right number in front of it, or because the question
was parsed wrongly - never because a channel reasoned incorrectly over correct
evidence. A method built to catch numerical hallucination in *reasoning* has,
on this benchmark, almost nothing of its target to catch.

This is the single-hop limitation made quantitative. 44 of 45 validation
questions are lookups; a lookup that retrieves the right cell is arithmetic a
model does not get wrong, and one that retrieves the wrong cell is a retrieval
failure. The dominant error kind is `unsupported_claim` (21 of 27), which is the
signature of missing evidence rather than bad inference.

### Efficiency - recovered by fixing a defect, and it is the best result here

| arm | AUROC | tokens/question | calls/question |
|---|---:|---:|---:|
| H | 0.936 | 4,713 | 2.0 |
| A | 0.907 | 5,783 | 1.96 |
| G | 0.959 | 5,839 | 2.0 |
| **B5** | **0.600** | **13,709** | **6.0** |
| B2 | \- | 2,280 | 1.0 |
| B1 | \- | 523 | 0.98 |

**Self-consistency costs 2.4x the full dual-channel system and detects worse.**
D11 requires H1 to be compared "at matched API cost"; the cost is not matched,
and it is not matched in the direction that favours the proposed system. This
is the clearest supported claim the campaign produced - and it was invisible
until the defect below was fixed, because every efficiency figure the project
had ever generated was 0.0.

### The defect: a reader looking for a key nothing ever wrote

`build_efficiency` in `evaluation/report.py` read
`record[channel]["tokens"]`. The recorder has always written
`record[channel]["usage"]["total_tokens"]`. Nothing has ever written `tokens`.

So `rows_with_token_usage` was **0 on every arm of every run**,
`tokens_per_question` was 0.0, H5 was null, and D11's cost-matched comparison
could not run - on 312 of 315 rows that carried complete usage blocks.

It survived because it failed politely. The note it emitted said the zero was
*"0 by absence rather than by measurement"*, which reads as scrupulous and was
false: the data was there and was not being read. A wrong answer wearing the
costume of an honest one.

Fixed to read the `usage` block, with the flat `tokens` key still honoured for
older artifacts. It now also reads **`samples`** - B5 makes five calls per
question, and counting it as one understated the arm fivefold, which is exactly
the quantity H1's matched-cost condition exists to test.

**A gap this exposed and did not close:** the verification agent's tokens are
recorded nowhere - not in its own block, not in the row's `tokens_used`. The
arbiter fires on 4 of 45 questions in arm A, so arm A's cost is understated by
those calls. `unattributed_tokens` is now reported per arm to make that visible;
it reads 0 because the row totals omit the arbiter too.

### Blind spot - the one place H4's mechanism shows

| arm | both channels agreed AND both wrong | rate |
|---|---|---:|
| G | 0 of 8 | 0.000 |
| A | 1 of 14 | 0.071 |
| **H** | **3 of 20** | **0.150** |

Arm H puts one model on both channels, and its agreed-and-wrong rate is twice
arm A's. That is precisely the failure mode D1 predicts for same-model channels
- they corroborate each other's mistakes - and it is the only measurement in
this campaign that moves in the direction the research argument requires. It
does not rescue H4: AUROC does not separate the arms (p=0.708), and 3 of 20
against 1 of 14 is not a result at this n. It is a signal worth powering
properly.

### What this campaign establishes, stated conservatively

1. The pipeline runs end to end, balanced, at scale, with a 0.7% provider
   failure rate. **First time in the project.**
2. The risk score ranks errors well (AUROC 0.91-0.96) - but so does every
   ablation of it, so nothing attributes that to the dual-channel design.
3. The full system is **cheaper and better than self-consistency**, the
   SelfCheckGPT-style comparator D11 names.
4. **The benchmark cannot test the central claim.** Zero reasoning-caused
   errors means dual-channel disagreement has no reasoning hallucination to
   detect here; what it detects is retrieval failure.

(4) is the result that should shape what happens next. Widening the benchmark's
multi-hop share is no longer a nice-to-have for external validity - it is the
precondition for the research question being answerable at all.

### Threshold

`operating_threshold` is **NOT SET**, so precision, recall, FPR and FNR are
omitted rather than computed at an unrecorded operating point. Choosing it from
this validation run, freezing it, and only then touching the sealed test split
is the correct order and has not yet been done.

### CORRECTION 2026-09-03 — the stratifier was broken, and the conclusion survives on better evidence

Everything above was computed with an evidence matcher that could not match a
number. **The conclusion does not change. The evidence for it changes
completely, and the original evidence was worthless.**

**The defect.** Gold evidence anchors store the bare numeral — `12232` — because
that is what the answer carries. Filings print the grouped one — `12,232`.
`normalise()` in `evaluation/metrics/retrieval.py` deliberately preserved commas,
on the documented reasoning that matching either form "would quietly accept a
chunk whose digit grouping was mangled". `EvidenceSpan.satisfied_by` requires
**all** anchors. So a group whose anchors were
`("Cost of technical sub-contractors", "12232")` was **unsatisfiable however
well retrieval had done**: the label matched, the numeral could not.

This is the project's own documented domain trap — thousands separators, listed
in CLAUDE.md — reaching its evaluator instead of its extractor.

**What it did to this campaign.** `all_evidence_retrieved` was True on **1 of 45
questions**. The true figure is **13**. Every error was therefore attributed to
retrieval by construction, and the reasoning stratum was empty because nothing
could enter it.

The tell was in the data and was visible before the fix: **17 questions were
answered correctly while the predicate said none of their evidence had been
retrieved.** A system cannot answer 17 numerical questions about specific
filings correctly from chunks containing none of the required evidence. The
predicate and the outcome flatly disagreed, and the predicate was wrong.

**The fix.** `degroup()` removes commas only from *canonical* grouping — Western
`1,234,567` and Indian `12,34,567`, both present in this corpus — and
`satisfied_by` now compares each anchor literally and degrouped. A mangled
`1,2232` is still not read as `12232`, so the caution the original docstring was
protecting is intact. Eight tests pin it, including the mangling case and the
requirement that loosening the numeral must not loosen the conjunction.

The matcher is shared with the retrieval metrics, so **every retrieval figure
this project has published is understated by the same defect** and needs
recomputing before it is quoted again. RX-034's ceiling is the one that matters.

### The corrected stratification

| stratum | questions | errors |
|---|---:|---:|
| evidence retrieved — reasoning *could* have failed | **13** | **0** |
| evidence not retrieved | 32 | 27 |

**On all 13 questions where retrieval delivered the evidence, the system was
correct.** On the 32 where it did not, it was wrong on 27.

So "not one error is a reasoning error" stands, and now means something it did
not mean before: it is no longer an artifact of a stratifier that could not
populate its own reasoning bucket. It is the measured statement that **the
system did not once fail on a question whose evidence it actually had.**

Stated with the honesty the denominator demands: 0 errors in 13 is consistent
with a true reasoning-error rate up to roughly 25% at 95% confidence. This shows
reasoning failures are *rare*, not that they are *absent*.

H2 remains NOT TESTABLE, and the report now says why instead of printing an
empty reason: AUROC needs at least one error and one correct answer in a
stratum, and the reasoning stratum has 13 questions and no errors. The
untestability is itself the result.

### What this does and does not change

**Unchanged:** every QA figure, every AUROC, every hypothesis verdict, the
efficiency curve. H1/H3/H4 are still unsupported; B5 still costs 2.4x for worse
detection. None of those depend on the stratifier.

**Changed:** the reason the headline holds. The original text argued "the
benchmark has nothing for it to detect" from a provenance table that a broken
predicate had produced. That argument was not sound, and the section heading
above overstates what the original evidence could carry. The claim is now
supported by the corrected 13/32 split, which is a stronger result reached the
right way.

**Strengthened:** retrieval as the bottleneck is no longer an inference from
error labels. It is a direct measurement — **100% accuracy when the evidence
arrives, 16% when it does not** — and it is the number the next phase of work
should be aimed at.

---

## RX-037 - The first complete arm: B3 answers a third of the time and is right when it does

**Date:** 2026-09-01 · **Status:** verified · **Outcome:** first complete arm in
the project; a real baseline with an interval · **Cost:** ~450k NVIDIA tokens,
**0 Groq**

**Why this arm and why today.** The four-arm campaign was blocked on Groq
(196,887 of 200,000). B3 is "RAG + programmatic reasoning, single channel" -
Channel B only - and `parse_question` is rule-based, so a B3 row makes exactly
one API call and it goes to NVIDIA. `--budget-only` confirmed it before
spending: 45 calls, all nvidia. It ran alongside a blocked campaign and the Groq
counter never moved.

### Result — n=45, all 45 validation questions

| | value | 95% CI |
|---|---:|---|
| Numerical accuracy | **0.289** | [0.156, 0.422] |
| **Accuracy when it answers** | **0.929** (13/14) | [0.786, 1.000] |
| Answers at all | 0.311 | |
| **Abstains** | **0.489** | |
| Execution failure | 0.200 | |

`mismatch_counts`: `none` 13, `missing_prediction` 31, `magnitude` 1.

**The shape is the finding, not the headline number.** Of the 14 questions where
the program channel committed to a figure, **13 were right and one was a
magnitude error**. It is not a weak reasoner; it is a precise one that declines
most of the time. Half of all questions end in a program that ran cleanly in the
sandbox and printed `{"value": null}` because the retrieved evidence did not
support an answer.

**That corroborates the retrieval limitation from the other side.** Evidence
accuracy is 0.08-0.56 (RX-028) and the right chunk reaches the top 10 for 0.280
of evidence groups (RX-034). An abstention rate of 0.489 on an arm that only
sees retrieved evidence is what that looks like measured at the far end of the
pipeline. The binding constraint on this system is not the reasoning; it is what
reaches the reasoner.

**Tolerance-insensitive.** `tau_sensitivity` is flat at 0.289 from τ=0.001
through τ=0.01 and reaches only 0.311 at τ=0.05. The result is not an artefact
of where the tolerance was drawn.

**RX-036 is the reason this is readable.** Before that fix the same run would
have reported `abstain 0.000, parse-fail 0.689` - "the program channel is
broken" - because the program channel's abstention signal was recorded and never
read. The correct reading is "retrieval is not supplying evidence", which is a
different diagnosis pointing at a different subsystem. A measurement defect that
changes the *attribution* while leaving accuracy untouched is the kind that
survives review.

**What it does NOT support.** B3 has no detector and no risk score, so it is
absent from the detection table rather than entered at AUROC 0.5. It is a QA
baseline. It cannot speak to H1 through H5, and all five still read `NOT
TESTABLE`.

**One operational note, recorded because it was mine.** The run first reported
`completed 45, failed 0` with 41 rows on disk. Not a defect in the runner: I ran
`git stash` mid-run to check whether some new tests failed without their fix,
and `results.jsonl` is git-tracked *and* being appended to, so git reverted it to
the committed 15-row state while the campaign carried on from row 20. The four
lost questions were exactly the next four in config order. `--resume` recovered
them and re-spent nothing else. Reconcile rows against `config.json`'s
`question_ids`; the printed `completed` count is an in-memory counter and cannot
see a file that moved underneath it.

---

## RX-036 - A principled refusal reported as a parser bug, visible only on an arm with no natural channel

**Date:** 2026-09-01 · **Status:** fixed and verified · **Outcome:** measurement
defect in `abstained`; B3 re-run · **Cost:** ~35k NVIDIA tokens, 0 Groq

**How it surfaced.** Running B3 (RAG + program channel, no natural channel) as a
baseline. The first 9 rows analysed as `abstain 0.000, parse-fail 0.667`, and a
67% parse-failure rate on a working channel is not a believable number.

**First suspect was my own setup.** I ran `run_campaign.py` directly rather than
through the supervisor, which sets `LLM_MAX_TOKENS=4096` explicitly, and RX-032
is exactly the story of a too-small completion budget. Checked before
investigating further: `settings.py` already defaults to 4096, and the run's
`config.json` confirms the same models and budget as the campaign. Not my error.

**The defect.** `PipelineResult.abstained` consulted only the natural channel:

```python
if self.natural is not None and not self.natural.sufficient:
    return True
```

The program channel makes the same decision. `program_channel.py` returns
`failure_reason="program executed and reported the evidence as insufficient"`
when a program runs and prints `{"value": null}`, and its own comment calls that
"an abstention... recorded as such". Nothing downstream read it.

| outcome | first 9 rows | recorded as |
|---|---:|---|
| executed, returned a value | 3 | correct |
| executed, evidence insufficient | 2 | **parse failure** |
| crashed (`RUNTIME_ERROR`) | 1 | parse failure |
| ungraded / other | 3 | — |

**Why fourteen prior runs never showed it.** Every arm run before this one had a
natural channel, which abstains on the same thin evidence and sets the flag
anyway. The program channel's identical signal was redundant and its being
dropped changed nothing. B3 is the first arm where the program channel is the
*only* channel — and the bug is not that a rare case was mishandled, it is that
a whole branch was never reached.

**It is the documented failure mode, inverted.** `AnswerRecord`'s docstring
says: *"Collapsing them would report a parser bug as principled caution."* This
collapsed them the other way and reported principled caution as a parser bug.
Accuracy is unaffected — EVALUATION.md §3 counts both as incorrect — but the
attribution is the entire reason the two rates are reported apart, and
`parse_failure_rate` is the number that would have sent someone to debug a
parser that was working.

**Fix.** `abstained` now also returns True when the program channel executed
cleanly and produced no value, discriminating on
`ProgramChannelResult.executed` (`execution.ok`) rather than on the failure
text — so a timeout, a crash or a non-JSON final line all stay failures. Three
tests pin the three cases.

**Second-order fix.** `executed` was not in the run artifact at all;
`_channel_record` wrote only `failure_reason` prose. So the existing rows could
not be re-analysed without matching on wording, which is the habit RX-033 was
written to break. `_channel_record` now records `executed`, and a future run
whose abstention rule changes can be re-analysed instead of re-run.

**Consequence.** `campaign_20260901T064310Z` is VOID at 10 rows (its `VOID.md`
carries the detail) and B3 restarted under one consistent rule. Verified on the
new run: `FI19b399ca` records `executed=True, abstained=True` where it
previously recorded a parse failure.

---

## RX-035 - The obvious fix for RX-034 does not work: cross-encoder reranking scores 0.240 against 0.280

> **RX-035-original · Status: INVALIDATED (contaminated) · kept for audit.**
> Its four gold sets were later found to draw 43 of their 100 source questions from
> the sealed test split while labelled `validation` (RX-053), so this result is
> partly a measurement on held-out data. It was re-run on validation-only gold as
> **RX-035-revalidated** (2026-09-13), and **its direction did not reproduce**: on
> clean gold the reranker scored 0.156 against a 0.125 baseline, not below it. The
> decision not to adopt reranking stands, on different grounds — see that entry.
> Nothing below this box has been edited.

**Date:** 2026-09-01 · **Status:** verified · **Outcome:** NEGATIVE — reranking
is not adopted · **Cost:** 0 API requests (local CPU model, ~36 min compute)

**Question.** RX-034 measured the headroom and called it "the profile a
cross-encoder reranker exists to fix": the gold chunk is in the top 10 for 0.280
of evidence groups but within rank 70 for 0.720. Does a reranker collect it?

**Method.** `scripts/measure_reranking.py` (new). `cross-encoder/
ms-marco-MiniLM-L-6-v2`, 22M parameters, CPU, over a 100-candidate fused pool.
**Paired by construction:** the pool is retrieved once per question and both arms
are scored over that identical list, so the only variable is the ordering
function. Retrieving separately per arm would let pool depth leak into the
comparison and turn a recall effect into a reranker result. Same four gold sets
and 100 evidence groups as RX-034.

### Result — it is worse, and the loss is not noise about a null

| Company | baseline | reranked | change |
|---|---:|---:|---:|
| Reliance Industries | 0.280 | **0.440** | **+0.160** |
| Tata Motors | 0.040 | 0.000 | −0.040 |
| Sun Pharmaceutical | 0.440 | 0.320 | −0.120 |
| HDFC Bank | 0.360 | **0.200** | **−0.160** |
| **all four (100 groups)** | **0.280** | **0.240** | **−0.040** |

The baseline reproduces RX-034's 0.280 exactly, from a separately written
harness. That is the strongest evidence available here that the measurement is
of the reranker and not of a bug in the instrument.

**It is not doing nothing — it is doing the wrong thing.** Of the 75 groups
inside the pool, 30 moved up, 43 moved down and 2 held. Seven were lifted into
the top 10 and eleven were pushed out. A reranker that had no effect would be
recorded as harmless; this one churns the ordering vigorously and lands net −4.

**The per-company spread is the finding.** +0.160 on Reliance against −0.160 on
HDFC, at n=25 each, is the signature of a model whose behaviour on this corpus
is close to arbitrary — not of a technique that helps some documents. Reporting
the Reliance column alone would have been a publishable-looking +57% relative
gain, and it would have been an artefact of which quarter of the data was shown.

**One hypothesis was tested and eliminated.** These questions are ~80%
scaffolding ("For Tata Motors Limited, as reported for the year ended March 31,
2024, what were X (X as reported in the consolidated financial statements)?"),
and every word of that appears in every chunk of the document, so the query form
was the first suspect and was flagged as such in the code before the run.
Ablated with `--strip-question`: Tata scores 0.000 either way. The query form is
not the cause.

**Standing explanation, untested.** MS MARCO is a web-prose passage benchmark
and these passages are financial tables. Whether a table-aware or
financially-tuned cross-encoder behaves differently is open, and 25 groups
never enter the 100-pool at all — a recall problem no reranker can address.

**Consequence.** `HybridRetriever.reranker` stays `None` and `RERANK_MODEL`
stays unset. The code and the harness are kept, because "we tried the obvious
fix and measured it not working" is a result the case study needs, and because
the harness makes the next candidate cheap to test. The 0.720 ceiling from
RX-034 stands unclaimed.

---

## RX-034 - Retrieval's problem is ordering, and the ceiling is 0.720 not 0.280

> **Historically affected by RX-053 · assessed 2026-09-13.** Measured on the same
> mixed-split gold (57% test-derived). **Decision impact: one** — its headroom
> figure motivated RX-035, the reranker experiment; no component was adopted or
> removed on its own basis. Because it fed a decision, it was **revalidated** on
> validation-only gold as RX-054b: top 10 **0.125** (this entry: 0.280), within rank
> 70 **0.406** (0.720), within rank 300 0.719, not ranked within 300 0.281. The
> ordering-not-coverage reading survives only partly: the headroom is real but much
> deeper, and over a quarter of the evidence is not a ranking problem at all.

**Date:** 2026-09-01 · **Status:** verified · **Outcome:** the headroom is
quantified and reranking-shaped · **Cost:** 0 API requests

**Question.** RX-028 named Tata Motors at 0.080 "the case to understand first"
and reported every miss as `ranked_too_low` rather than `absent_from_index`. But
that verdict only asks whether the evidence exists **anywhere in the corpus**;
everything present is filed under `ranked_too_low` whether it came 11th or
3,000th. Those need opposite fixes. Where does the right chunk actually rank?

**Method.** `scripts/diagnose_retrieval_ranks.py` (new, so the figures are
reproducible from the repository rather than from a deleted scratch file).
Retrieve 300 deep per question, find the first hit satisfying each gold evidence
group, and band the rank. Document-filtered plain hybrid, four generated gold
sets, 100 evidence groups.

### Result — the right chunk is a candidate 87% of the time and reaches the top 10 28% of the time

| Company | in top 10 today | ≤20 | ≤70 | ≤300 | never ranked |
|---|---:|---:|---:|---:|---:|
| Sun Pharmaceutical | 0.440 | 0.560 | **0.960** | 1.000 | 0 |
| HDFC Bank | 0.360 | 0.400 | 0.520 | 0.720 | 7 |
| Reliance Industries | 0.280 | 0.520 | 0.720 | 0.920 | 2 |
| Tata Motors | **0.040** | 0.280 | **0.680** | 0.840 | 4 |
| **all four (100 groups)** | **0.280** | **0.440** | **0.720** | **0.870** | 13 |

**The worst document has the largest headroom.** Tata Motors goes from 0.040 to
0.680 by rank 70 — the chunks are not merely present, they are *close*. That is
the profile a cross-encoder reranker exists to fix, and reranking is currently
DEFERRED.

**87% of evidence is reachable within 300 candidates.** Coverage is not the
binding problem; ordering is. RX-028's diagnosis was right, and it now has a
number attached to it rather than a category.

**These are not RX-028's numbers and do not replace them.** This runs
document-filtered plain hybrid; RX-028 ran corpus-wide planned retrieval, which
is what the campaign does. Different instrument, different absolute values — the
rank *distribution* is what transfers, not the level.

**Cumulative recall is a ceiling, not a promise.** It says what evidence accuracy
would become if the right chunk could always be lifted from that band into the
top 10. A reranker still has to pick correctly against near-duplicates, and on
these documents the near-duplicates are the same label in a dozen notes.

**Infosys is excluded deliberately.** Its gold is hand-built and carries no
`source_qid`, while the other four are generated from validated answer gold — so
including it would mix two gold-construction methods in one mean. That difference
is also an untested candidate explanation for the transfer gap RX-028 could not
account for; recorded in `datasets/retrieval_eval/README.md`.

### Two traps found on the way, both worth more than the measurement

**The `*_recent` gold sets are stale and nothing said so.** Four of the nine sets
in `datasets/retrieval_eval/` were built pre-D42, and **not one of their
`source_qid`s resolves** against the current dataset — 0/13, 0/18, 0/16, 0/16.
Their evidence pages are the defective provenance RX-026/RX-027 corrected: Tata's
"finance cost" evidence points at a defined-benefit-obligation note whose
`Interest expense` row is not the consolidated finance cost line. The first run
of this diagnosis used one of them by mistake and produced a confident wrong
answer. They are kept because RX-017 and RX-020's control (0.492, n=63 = exactly
their sizes) were measured on them, and are now labelled in a README.

**My own probe read `SearchHit.page`, which does not exist.** `page` lives in
`SearchHit.payload`. `getattr(hit, "page", None)` returns `None` for every hit,
every span comparison then fails, and the probe reported that **all 23 Tata
misses were unreachable within 300** — flatly contradicting RX-028 and pointing
at a filtering bug that does not exist. The correct reading gives ranks of 11,
17, 27, 69. The instrument was broken, not the system, and the broken version was
more interesting, which is exactly why it needed checking rather than writing up.
`evaluate_retrieval.to_items` was the existing correct conversion the whole time.

**Deliberately not tuned.** RX-028's ordering stands: validate gold, run the
campaign, *then* tune retrieval against a number with a confidence interval.
Reranking against this measurement, on the day it was taken, is the mistake
RX-004 and RX-015 were both written to stop.

---

## RX-033 - A campaign reported `completed 180, failed 0` with Channel A dead for 166 of them

**Date:** 2026-08-31 · **Status:** verified · **Outcome:** the run is VOID; three
defects fixed · **Cost:** ~68,000 Groq tokens and 574,003 NVIDIA tokens, spent
for nothing

**What happened.** The first four-arm H1 campaign
(`campaign_20260831T184152Z`) ran to apparent completion: 45 questions × 4 arms,
`completed 180, skipped 0, failed 0`. Nothing in its summary said otherwise.

| | |
|---|---:|
| rows | 180 |
| **Channel A unavailable, `DailyQuotaExhausted`** | **166** |
| Channel A actually ran | 10 |
| rows carrying any answer | 35 |
| verdicts | 131 `UNCERTAIN`, 4 `AGREE`, 45 none |

Read at face value it says the dual-channel system is uncertain on 97% of
questions. **That is a rate limit wearing the costume of a research finding** —
and unlike a crash, it produces an artefact shaped exactly like a real one.

### Defect 1 - two quota exceptions, one handler

`campaign.py` stops on `QuotaExhaustedError`: the *provider* refusing mid-call.
The refusal that fires first in practice is `DailyQuotaExhausted`: the *local
limiter* declining to make the call at all, before any request is sent. They
were unrelated classes — `RuntimeError` and `RateLimitError` — so the campaign's
stop handler never saw the one that actually happens.

`orchestrator._raise_if_quota_exhausted` exists solely to prevent this, and its
docstring describes the outcome in advance: *"it writes rows in which every
channel is unavailable. Those are indistinguishable in the artifact from genuine
abstentions, so they corrupt the abstention rate, the parse-failure rate and the
detection labels of a run that looks complete."* It compared
`error_type == QuotaExhaustedError.__name__` — one name, string equality. The
guard was right, the mechanism was built, and it was matching the wrong door.

**The tests passed the whole time** because they inject `QuotaExhaustedError`.
The client-side path had no end-to-end test. That is the actual lesson: the
class that never fires in production was the only one under test.

Fixed by making `DailyQuotaExhausted` a subclass of `QuotaExhaustedError` — they
are one event seen from two sides — and by deriving the guard's name set from
the class hierarchy, computed per call so it cannot depend on import order.

### Defect 2 - the artefact could not tell a dead channel from an abstention

A recorded channel carried `available` and a prose `failure_reason`, but not the
exception class, so the only machine-readable difference between "the provider
was out of quota" and "the model declined for lack of evidence" was the first
word of a message. The codebase matches on classes everywhere else for exactly
this reason. `_channel_record` now records `error_type`.

### Defect 3 - the analysis ran against a different run

`run_h1.py` selected `sorted(glob("*/metrics.json"))[-1]` — alphabetical, not
chronological. `qu-llm_20260829T181529Z` sorts after `campaign_20260831T184152Z`,
so a stale run from two days earlier was analysed and its empty hypothesis table
printed as though it were the campaign's. **Analysing the wrong run is worse
than analysing none**, because the output is shaped like the right answer. The
run is now identified by being new relative to a snapshot taken before launch.

### Why the run cannot be resumed, only abandoned

`recorder.completed()` treats any recorded `(arm, question_id)` as done, so
`--resume` on this id would **skip the 166 dead rows** and fill in the rest,
yielding a campaign that reports complete and rests on them. The run carries a
`VOID.md` and a new run must be started. This also corrects advice given before
the artefact was inspected: resuming this id was recommended, and it is wrong.

### The sequencing error underneath, which was mine

It was launched with ~68,000 of the day's 200,000 Groq tokens left; the rest had
gone on RX-032 and provider health checks. Had the stop worked, that would have
been harmless — two questions completed, a clean stop, a resumable run. It is
recorded because the bug is what made it expensive, not what made it happen.

**Not counted as evidence about anything.** No parse-failure rate, no abstention
rate, no verdict distribution from this run enters any result. 14 rows had a
live Channel A; 10 questions across 4 arms is not a balanced prefix.

---

## RX-032 - `max_tokens=1024` does not work, and the cheap campaign it bought does not exist

**Date:** 2026-08-31 · **Status:** verified · **Outcome:** negative — the plan's
central cost assumption was wrong · **Cost:** ~16 Groq requests, ~67,000 tokens

**Question.** D43 scoped the campaign at `LLM_MAX_TOKENS=1024` because RX-023
showed the reserved completion budget is 70% of a call, making it the dominant
lever. D43 recorded the scope as *conditional on a measurement not yet taken*:
whether Channel A can emit a parseable answer inside 1024 tokens. If it cannot,
the allowance is spent measuring output length rather than reasoning.

**Method.** `measure_channel_reliability.py --limit 6 --compare-max-tokens 1024
4096`. Paired: each question asked at both budgets back to back, so the
comparison is within-question and the discordant pairs carry the evidence.

**Result — 1024 fails, unanimously in direction.**

| | 1024 | 4096 |
|---|---:|---:|
| answered | 1 | 3 |
| abstained | 2 | 3 |
| **parse_failure** | **3** | **0** |

Discordant pairs: **3 fixed by raising, 0 broken by raising.** Half the sample
returned unreadable output at 1024 and readable output at 4096.

**n=6 is not a significant sample and this does not rest on a p-value.** A sign
test on 3-0 gives p=0.25. The decision rests on the mechanism instead, which is
not statistical: the bound model emits `<think>` reasoning before its answer, so
a 1024-token ceiling truncates the reply mid-structure and what arrives is not
JSON. That is a deterministic property of the format, not a coin that landed
three times.

**What it costs.** Per call goes 2,804 → 5,876 tokens; the four-arm question
goes 33,648 → 70,512. Against 1,732,302 tokens of allowance before the 2026-09-09
deadline, the forecast falls from 45 questions to about 24.

**What was nearly done about it, and why it was not.** The limiter records
*actual* usage (`record_token_usage`), so a probe was run to test whether the
reserved budget is really charged: one call at `max_tokens=4096` returning 255
tokens cost **272**, not 5,876. Reserved is not charged — the planner's model is
an upper bound. But that probe used a 17-token prompt and a one-word answer, and
generalising from it to a campaign call would have been the error it looked like
it was preventing: today's real 4096-budget calls averaged close to their
reservation, because the model's reasoning tokens fill it. **The upper-bound
model stands. The probe is recorded because it was nearly acted on.**

**What actually recovered the scope, and it was not a budget lever.** The
campaign is question-major and resumable — an interruption leaves every finished
question with every arm, and `--resume` re-spends nothing (both properties have
tests). So `--limit` never needed to be set to what today's forecast can afford:
`run_h1.py` now attempts all 45 and lets the allowance decide how far it gets,
with `affordable` printed as a forecast rather than imposed as a cap. If calls
come in under their reservation the run simply gets further. Truncating the plan
could only have lost questions.

---

## RX-031 - The grader was inverted on 45 of 115 gold answers, and the cause was the unit field

**Date:** 2026-08-31 · **Status:** verified · **Outcome:** two defects in the
correctness predicate's inputs, found before any evaluation ran · **Cost:** 0 API
requests

**Context.** The owner completed the test split - 59 rows judged, 54 validated,
5 rejected - taking the dataset to 115 usable gold answers. Before importing
them, every validated row was re-read for the RX-030 failure shape: an answer
whose *value* is right and whose *metadata* destroys it. Two were found, and the
second is larger than the first.

### Defect 1 - a scale word on a quotient (1 row)

`FI9e9260be`, Reliance's consolidated debt-to-equity ratio, was validated as
**`0.41 crore`**. The figure is right; the validator's own note says "0.41
times". But `crore` multiplies the gold by 10⁷, and the consequence is not a
near-miss, it is an inversion:

| the model answers | graded |
|---|---|
| `0.41` | **wrong** (magnitude, gold reads 4,100,000) |
| `0.41 times` | **wrong** |
| `0.41 crore` - an answer that misunderstands what a ratio is | **correct** |

**Resolved by transcription**, as RX-030 was: the unit was set to dimensionless
because the validator had already written "times", the figure `0.41` is
untouched, and the change is recorded in the row's own note. No judgment was
made that the validator had not already made.

`review_gold.py` now refuses a scale word on a question naming a ratio, margin,
return-on, yield or coverage, and says why. A sweep of all 192 rows finds no
other instance.

### Defect 2 - a gold that states no unit kind rejected the most complete answer (44 rows)

`8,415.03 crore` parses to scale CRORE with unit kind **UNKNOWN** - `crore` names
a scale, not a currency - and that is what the generator writes whenever the
source table says "in crore" without a ₹ symbol. 44 of the 115 validated answers
are in this form.

`judge()` compared dimensions symmetrically, so an UNKNOWN gold was treated as a
dimension of its own rather than as *unstated*:

| gold `8415.03 crore` | model answers | was graded | now |
|---|---|---|---|
| | `8415.03 INR crore` | **wrong** (`unit_kind`) | correct |
| | `8415.03 crore` | correct | correct |
| | `84150300000` | correct | correct |
| | `8415.03` | wrong (magnitude) | wrong (magnitude) |
| | `8415.03 million` | wrong (magnitude) | wrong (magnitude) |

The rejected answer is the **most complete one a model can give** - currency and
scale both stated. The accepted one says strictly less. The predicate was
grading a model on how little it said, on 38% of the gold set, and the artefact
would have surfaced in the write-up as a units-failure rate: a believable
finding about financial LLM reasoning, produced entirely by a label format.

The prediction side already had this rule - a bare number is read under the gold
record's convention - and the gold side had no counterpart. It does now: an
UNKNOWN gold kind does not reject a stated prediction kind, the assumption is
written into the judgment trace, and the magnitude test still runs on canonical
values, so a percentage cannot slip past a crore-scaled gold.

**Not fixed by editing gold.** Rewriting 44 units to `INR crore` would have
worked and would have been the wrong repair: it edits gold labels at scale to
change scores, leaves the predicate broken for anyone else, and buries the
finding. The 44 labels are untouched.

**Reach beyond FinVerify-IND.** `correctness.judge` is the predicate under QA
accuracy, the detection label `y`, every ablation comparison and every error
class. Any gold record naming a scale without a currency was affected, whatever
its source.

---

## RX-028 - Retrieval was being flattered by gold that pointed at the easy pages

> **Historically affected by RX-053 · assessed 2026-09-13.** Its corpus-wide
> figures were measured on gold later found to be 57% test-derived. **Decision
> impact: none** — it corrected a published number and explicitly deferred
> tuning (TODO: "Do NOT tune retrieval against RX-028. Reranking stays DEFERRED"),
> so no component was chosen, adopted or removed on its basis. Re-measured anyway on
> validation-only gold in RX-054, because it is the figure the status table quotes:
> planned evidence accuracy @10 HDFC 0.333, Sun 0.333, Reliance 0.182, Tata 0.167
> (n=32 pooled; different question sets, so direction only).

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** a large downward revision
of every retrieval figure · **Cost:** 0 API requests

**Question.** D42 rebuilt the gold so every question is answered from the
statements it names, and the retrieval gold was re-derived from it. All 197 spans
verify against the PDFs. What does retrieval score now?

**Result.** Corpus-wide, planned retrieval, evidence accuracy at K=10:

| Company | corrected gold | previously reported | change |
|---|---:|---:|---:|
| Sun Pharmaceutical | 0.560 | 0.611 | -0.051 |
| Reliance Industries | 0.320 | 0.438 | -0.118 |
| HDFC Bank | 0.280 | 0.538 | **-0.258** |
| Tata Motors | **0.080** | 0.375 | **-0.295** |

**Not comparable question-for-question** - these are different question sets, and
the earlier column was measured on recent-year questions only. The direction and
the size are the finding, not the arithmetic difference.

**Why it dropped.** The old gold pointed disproportionately at front-of-report
material: ten-year highlights, "Summary of Financial Performance", MD&A tables.
Those pages are short, keyword-dense, and carry the metric name beside the figure
with nothing else competing - which is close to an ideal retrieval target. The
corrected gold points into the consolidated financial statements, which are long,
dense, repetitive across notes, and where the same label appears on a dozen
pages. Retrieval was being scored on the easy half of the document.

**Every miss is `ranked_too_low`, not `absent_from_index`.** For Tata Motors all
23 misses are figures the index contains, usually in several chunks. This is a
ranking problem, not a coverage problem, and the two have opposite fixes.

**Tata Motors at 0.080 is the case to understand first.** Filtered to its own
document it is 0.080 as well, so this is not corpus-wide dilution: retrieval
ranks that filing's own statement pages below its own other pages. Tata Motors is
the largest document in the corpus (530 pages) and the one whose statements sit
furthest from the front.

**The question wording changed too, and it is not the cause.** D42's honest
definitions reword a question only where the source row is not itself a total -
5 of Tata's 25, 2 of Sun Pharma's, 4 of Reliance's, 6 of HDFC's. Too few to move
a figure by 0.295, and Sun Pharma has the fewest rewordings and the smallest drop.

**What this bounds.** Every retrieval figure this project has reported was
measured against the old gold: RX-004, RX-012, RX-015, RX-017, RX-020. **All of
them are superseded.** H2's retrieval-caused stratum is larger than any previous
estimate, and the honest ceiling on end-to-end accuracy is correspondingly lower.

**Deliberately not fixed here.** Reranking is still DEFERRED and the RRF weights
are still unswept. Tuning either against this measurement, on the day the
measurement was taken, would be tuning against an impression again - the mistake
RX-004 and RX-015 were both written to stop. The right order is: validate gold,
run the first campaign, then tune retrieval against a number that has a
confidence interval on it.

---

## RX-027 - The wrong-section answers were a symptom; two extraction defects were the cause

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** two defects fixed, one of
them silent data corruption · **Cost:** 0 API requests

**Question.** RX-026 established that 35 of 268 gold candidates were read from the
wrong set of financial statements. Filtering by section would have removed them.
Why was the generator reaching for those pages at all, when the right pages were
in the same document?

**Defect 1 - a split table loses its column years.** The chunker repeats a
table's preamble in every part (caption, unit banner, rule) but not the header
row that names the years. HDFC Bank's consolidated balance sheet splits exactly
there: part 1 carries the header and the liabilities, part 2 carries Advances and
Investments and names no year at all. Every figure in part 2 fell back to the
document's fiscal year with `year_is_stated` False - and the generator requires a
stated year, so it skipped the consolidated balance sheet and took its answers
from a front-of-report summary table instead.

**That inverts the selection criterion.** Requiring a stated year does not merely
reduce coverage; it *biases* selection toward tables with clean headers, which are
summary and note tables rather than the primary statements. Reliance's borrowings
are the clearest case: the correct figures on the consolidated balance sheet
(p110) had no stated year, while an Interest Rate Swaps note (p140) did - so the
note won, and a validator rejected it.

Fixed by carrying an earlier part's column years to later parts of the same
table. The inheritance is one-directional and weak: a part that names its own
years always wins, and a different table never inherits.

**Defect 2 - two-panel balance sheets leaked figures across the panels.** Indian
balance sheets are frequently printed as two panels side by side, and extraction
flattens both into one row:

    | Goodwill |  | 14,989 | 15,270 | Total Equity |  | 9,25,788 | 8,28,881 |

The extractor read `cells[0]` as the label and then took every numeric cell to
the end of the row. On Reliance p110 that gave `goodwill` four facts, **all four
marked `year_is_stated`**: 14,989 and 15,270, which are goodwill, and 9,25,788
and 8,28,881, which are total equity.

**This is the worst shape a defect can take in this project.** The wrong facts
carried a correct page, a correct row label, a correct column index, a year read
from the table's own header, and the stated-year flag. Nothing downstream could
distinguish them from the right ones, and the error is **62x**. It was found only
by chasing a validator's rejection three levels down.

Fixed by segmenting a row on its label cells. The threshold is three consecutive
letters, so HDFC Bank's schedule references ("18 (4)") do not cut a row before its
figures. The fix also **unlocks the right panel**, which was never extractable:
equity, borrowings and payables all live there.

**Measured, whole corpus:**

| | before | after |
|---|---:|---:|
| facts mined | 1,273 | **1,825** |
| gold candidates | 268 | **193** |
| candidates citing a page on a basis they do not name | 35 | **0** |
| cited pages that could not be placed in a section | 164 | **0** |
| retrieval-gold questions whose evidence is entirely on the unasked basis | 27 / 91 | **0 / 100** |
| largest company's share of the dataset | 47.4% | **23.8%** |

**The owner's eight rejections, resolved one by one.** This is the check that
matters, because the rejections were made independently and before any of this:

| | rejection | outcome |
|---|---|---|
| 4 (Tata, Sun borrowings) | "this is the standalone figure" | question now says standalone (D42) |
| 1 (HDFC advances) | "1,600,585.9 is standalone; consolidated is ~16,61,949" | now **1,661,949.29**, from p412 |
| 2 (Reliance borrowings) | "read from an Interest Rate Swaps note, not the balance sheet" | now read from p110, the consolidated balance sheet |
| 1 (Infosys CWIP) | note describes Reliance's borrowings - pasted from another row | **unresolved**; needs re-judging |

**Residual, stated rather than claimed fixed.** The consolidated balance sheet
carries two rows labelled `Borrowings` - current and non-current - and the
definition names the line item without disambiguating which. Reliance's FY2024
candidate is 2,22,712 (non-current) where total borrowings are 3,24,622. The
question is now answerable and truthful about its source, but a validator still
has to decide whether the line read is the line meant.

**Method note.** The first version of the retrieval half of this audit counted
"the evidence is standalone" as a defect without checking whether the question
*asked* for standalone, and reported 52% for a Sun Pharma set that was entirely
correct. The check now compares against the question's own wording. That is the
third time in two experiments that a measurement produced a confident wrong
number before being corrected against something read by hand.

---

## RX-026 - Gold answers read from the wrong financial statements

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** a dataset defect, found by
the owner's first validation pass · **Cost:** 0 API requests

**How it surfaced.** Not from a hypothesis. The owner judged 9 worksheet rows and
rejected 8 of them, every rejection naming the same thing: the candidate figure
is real, is printed where the generator said, and answers a different question.
`precheck_gold.py` had passed all 9 as `on_cited_page`, which is exactly what
RX-016 said that check could and could not establish.

**The defect.** An Indian annual report states most metrics twice - once in the
STANDALONE statements (the parent alone), once in the CONSOLIDATED statements
(the group). FinVerify-IND's questions name the basis: *"as reported on the
consolidated balance sheet"*. Nothing verified the cited page was on that basis.
For Tata Motors the two differ by seven times: total borrowings are 13,771.04
crore standalone (p449, the standalone capital-management note) against
62,148.53 + 36,351.56 = 98,500.09 crore consolidated (p280, the consolidated
balance sheet). The generator took the standalone figure for both FY2023 and
FY2024.

**Why the generator prefers the wrong page, which is the part that generalises.**
Under Ind AS the consolidated balance sheet splits borrowings into current and
non-current and prints **no total line**. The standalone capital-management note
prints a single row labelled `Total borrowings`. A binder matching row labels
therefore finds an exact string match in the wrong section and no match at all
in the right one. The defect is not sampling noise; it is what label matching
does to this document structure, and it will recur for every metric Ind AS
splits.

**Measured** (`scripts/check_statement_basis.py`, reproducible, no API cost):

| | count | share |
|---|---:|---:|
| asks consolidated, cites a **standalone** page | **35 / 268** | 13.1% |
| cited page could not be placed in either section | 164 / 268 | 61.2% |
| retrieval gold: **every** acceptable evidence page in the wrong section | **27 / 91** | 29.7% |

The second row is why the first is a **lower bound** and not a total. Unknown is
not wrong - a page outside every financial-statements section, such as HDFC
Bank's *"Summary of Financial Performance"* on p213, is a different defect that
needs a person to adjudicate.

**The retrieval half is the more expensive one.** The 91-question retrieval gold
is derived from these same questions (`source_qid`), so it inherits the defect,
and on 27 of 91 a retriever returning the CORRECT consolidated page is scored as
a miss. Per company: HDFC Bank 13/25, Sun Pharma 9/25, Tata Motors 5/16,
Reliance 0/25. **Those two companies' retrieval figures are unsafe in an unknown
direction** and must not be quoted until the evidence is re-derived.

**It does not explain the Infosys transfer gap, and saying so matters.** The
tempting story - retrieval scores badly off Infosys because the gold points at
the wrong section - does not survive the numbers. Infosys and Reliance both have
**0%** wrong-section questions and evidence accuracies of 0.909 and 0.438. The
three suspects from RX-015 stand; this is a fourth, independent problem.

**Method note: the classifier was wrong twice before it was right, in both
directions.** Keying on any mention of "standalone" anywhere in a page's text
put 23 of Reliance's 25 retrieval questions in the wrong section, because a
consolidated note cross-references the standalone statements in its body; the
first reported figures were 58/268 and 50/91. Restricting the rule to short
running-header lines dropped Reliance to 0 and the totals to 35/268 and 27/91.
The Reliance PDF held here (159 pages) contains **only** consolidated statements
(p110-159), so 0 is correct. The corrected classifier is pinned to five pages
established by reading them, `check_statement_basis.py` re-checks them before
printing anything and **refuses to report if they fail**, and the same check runs
in `tests/test_statement_basis.py`. A heuristic that silently drifts would
produce precisely the confident wrong number this experiment exists to find -
which it twice did.

**What follows.**

1. **The 8 rejected rows are correct rejections, and their notes are not all
   reliable.** FI0001's note describes Reliance's borrowings and the figure
   135,702 on an Infosys capital-work-in-progress question - a pasted note from
   another row. Several notes also assert figures from memory (HDFC Bank's
   consolidated advances "approximately Rs 16,61,949 crore"). The verdicts are
   sound; the notes need checking against the filings before any of them is
   quoted.
2. **The generator needs a basis filter before regenerating anything.** Restrict
   candidate pages to the section the question names, and for split metrics
   either sum the two Ind AS rows or stop asking for a total that is not printed.
3. **The retrieval gold must be re-derived after that**, and every retrieval
   figure reported so far re-measured.
4. **D41 stands and gains a sharper reason.** Generating 232 more questions
   toward spec §34's 500 would have added roughly 30 more of these.

---

## RX-025 — Operand-binding accuracy cannot yet be measured, and the reason is structural

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** neutral (a commitment found to be blocked) · **Cost:** 0 API requests

**Question.** D6 recorded that the deterministic verifier is exact only *given*
`(operation, operands)`, and required operand-binding accuracy to be **measured
as its own metric rather than folded into verifier accuracy**. It never had been.
Discharge it.

**Method.** `scripts/measure_operand_binding.py`, all 268 questions. Retrieval is
local and binding runs no model, so this costs nothing — which is the point,
since the channel being measured is the one that consumes no quota.

**The first attempt reported a number about nothing.** Pooled across all 268
questions it gave 40% coverage and 36% agreement. Both figures are meaningless,
because the deterministic channel **abstains on lookup questions** by design —
re-reading a figure is structural corroboration, not an independent check — and
**256 of 268 questions are lookups**. The measurement was scoring a channel on
questions it never runs on. Split properly:

| stratum | n | bound | comparable | agreement |
|---|---:|---:|---:|---:|
| **channel engages** (non-lookup) | **12** | 6 (50%) | **0** | **—** |
| channel abstains (lookup) | 256 | 113 (44%) | 113 | 20% |

**The commitment cannot be discharged, and the reason is not effort.** The only
questions the channel engages on are the 12 derived-metric questions — and those
carry **no candidate answer on purpose** (D35: a pre-filled arithmetic result
invites a validator to wave it through). So the stratum that matters has nothing
to compare against, by a deliberate design decision made elsewhere.

**Operand-binding accuracy is therefore blocked on the same human validation as
everything else**, and specifically on those 12 questions. That is worth stating
plainly rather than substituting the lookup stratum, which is available, large,
and irrelevant.

**A lead, not a finding.** In the lookup stratum the binder agrees with the
generator on only 23 of 113. Reading the disagreements, they are not arithmetic
errors — they are *selection* errors, and three distinct kinds:

- **wrong entity** — FI0054 bound `Deposits` from p.448 *"Direct Subsidiary"*
  where the question asks the consolidated figure;
- **wrong statement** — FI0186 bound `Other Expenses` from a cash-flow
  discontinued-operations table rather than the P&L;
- **wrong column** — FI0114 and FI0115 ask about different years and both bind
  22,395.68, because `bind_operands(column=0)` always takes the reporting year.

None of these reaches an answer today, because the channel abstains on every one
of them. They matter only if the channel is ever extended to lookups, and they
say what that extension would have to solve first.

**A latent hazard, sized and left alone.** `orchestrator.py:501` calls
`run_deterministic_channel` without a `column`, so it always reads the reporting
year — while 72% of FinVerify-IND asks about a prior year (D36). The blast radius
today is **zero**: of the 12 questions where the channel engages, none asks about
a prior year. It is a hazard rather than a bug, and the proportionate response is
a guard that fails if such a question enters the dataset, not a fix to code that
is currently correct.

---

## RX-024 — Groq's token allowance is a rolling window, not a daily one

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** negative (a planning assumption was wrong) · **Cost:** 4 requests

**Question.** RX-023 left an open observation: a 429 said *"try again in
23m51s"* and a call succeeded ~45 minutes later, well before UTC midnight, which
is not what a daily reset looks like. One data point, so nothing was changed on
it. Waiting for the boundary settled it.

**Method.** Wait for UTC midnight. Attempt a call. Read the provider's own figure
out of the refusal.

**Result.**

| UTC | provider's own message |
|---|---|
| 22:30 (Aug 29) | `tokens per day (TPD): Limit 200000, Used 199170` · *"try again in 23m51.216s"* |
| **00:11 (Aug 30)** | `tokens per day (TPD): Limit 200000, Used 199188` · *"try again in 23m46.464s"* |

**Midnight passed between those two and the counter did not move.** ~100 minutes
apart, both at ~199,200 used, both quoting ~24 minutes. A UTC-day reset would
have zeroed at 00:00; a rolling window would look exactly like this.

**The refill rate corroborates it.** If ~24 minutes frees enough for one
4,114-token call, that is ~170 tokens a minute, or ~247,000 a day — the same
order as the 200,000 cap. A window aging out continuously, not a bucket emptying
at a boundary.

**What this changes.** The limiter models `utc_day()`. For Groq that is wrong in
the unsafe direction: at midnight it zeroes its counter while the provider keeps
counting, so it becomes **optimistic** exactly when a scheduler is most likely to
launch work. `sync_daily_tokens` repairs it on first contact with a 429 — the
system fails safe by repair rather than by design, which is weaker and is stated
here rather than left implied.

**What it changes for the campaign, which is the point.** *You cannot wait for
midnight and burst.* Throughput is ~200,000 tokens per rolling 24 hours,
continuously available. RX-023's "A + B5 × 50q ≈ 4.9 days" is therefore five days
of **steady** running, not five overnight batches — and a campaign runner that
sleeps until a boundary that does not exist would idle for nothing.

**A correction to my own diagnosis, recorded because the fix would otherwise be
credited to the wrong symptom.** The local counter reading 199,866 was first
diagnosed as a double-count. It was not: the provider's own 429 says 199,188, so
the figure was right and had arrived correctly through `sync_daily_tokens`. Acting
on the wrong diagnosis, the counter was reset to zero — and the next 429 put it
straight back to 199,188, which is the safety net doing precisely its job.

**The double-count bug is nevertheless real**, found while looking for the wrong
thing. `record_token_usage` read its pre-call estimate back out of `_token_events`
— the per-**minute** window, whose entries age out after 60 seconds. Any call
slower than a minute (Qwen routinely runs 47-136s) found it empty and added the
real cost *on top of* the estimate already charged. Demonstrated directly in
`TestTheDailyCounterIsNotDoubleCharged`, and fixed by giving the estimate its own
slot with the lifetime of the call. Two true things, one of which was not the
cause of the other.

**Still unmeasured.** Whether NVIDIA behaves the same way. It publishes no
rate-limit headers at all (RX-014), so neither its cap nor its reset semantics
are observable without exhausting it deliberately.

---

## RX-023 — What a call actually costs, and which lever moves it

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** neutral (quantifies RX-022) · **Cost:** 0 API requests

**Question.** RX-022 established that the campaign is bounded by tokens per day
and put the full ablation at 98 days, from a `TOKENS_PER_CALL` extrapolated off a
single 429. Two things were unquantified: what a call actually costs, and which
part of it can be reduced.

**Method.** `scripts/measure_prompt_cost.py`. Prompts assembled exactly as the
channels assemble them — same retrieval, same `evidence_from_results`, same
templates — and **measured without being sent**. Zero API cost. Estimated at 4
characters per token, deliberately the same crude rule the provider adapter paces
with, so the figure describes the system that is actually running.

**Result, 40 campaign questions.**

| | median | p90 | max |
|---|---:|---:|---:|
| evidence blocks | 1,515 | 1,770 | 1,781 |
| Channel A prompt | 1,782 | 2,037 | 2,047 |
| Channel B prompt | 1,793 | 2,048 | 2,058 |
| **completion, RESERVED** | **4,096** | 4,096 | 4,096 |
| **per call** | **5,878** | | |

**The reserved completion budget is 70% of a call.** `max_tokens` is therefore
the dominant lever — and a near-linear one, not a magic one, because the prompt
is a floor:

| `LLM_MAX_TOKENS` | tokens/call |
|---:|---:|
| 4096 | 5,878 |
| 2048 | 3,830 |
| 1024 | 2,806 |
| 512 | 2,294 |

Trimming evidence would move the smaller half of the bill, and RX-017 measured
the gloss those blocks carry as worth **0.250** evidence accuracy — so cutting
context is not the cheap lever it looks like.

**A second correction to RX-022, in the opposite direction.** RX-022 pooled every
call against Groq's limit. They do not all land there: Channel B runs on NVIDIA
and Channel A on Groq (D21). Counting per provider — which is how the limits
work — changes both the number and the diagnosis.

Full 13-arm × 150 questions, at `max_tokens=4096`:

| provider | calls | tokens | days |
|---|---:|---:|---|
| groq | 3,600 | 21,153,600 | **105.8** |
| nvidia | 1,200 | 7,051,200 | cap UNOBSERVED |

**Groq carries three calls in four.** It serves Channel A, the arbiter, *and*
B5's self-consistency samples; NVIDIA serves only Channel B. The bottleneck is an
allocation imbalance, not a total.

**Feasibility, per provider, worst provider shown.**

| campaign | `max_tokens`=4096 | =1024 |
|---|---:|---:|
| Full 13-arm × 150q | 105.8 days | 50.5 days |
| A + B5 × 150q (H1) | 30.8 days | 14.7 days |
| A + B5 × 50q | 10.3 days | **4.9 days** |

**What this makes possible.** A first real H1 result is **five days** of free-tier
quota at 50 questions and `max_tokens=1024` — not the three months RX-022's pooled
figure implied, and not the third of a day the request counter implied before
either. The full ablation remains out of reach on this tier at any `max_tokens`,
because the prompt floor alone is ~1,780 tokens × 4,800 calls.

**One lever deliberately not pulled.** Moving the **arbiter** off Groq would shift
1,200 calls and roughly a third of the load. It is not done here because it is a
research-validity question, not an engineering one: an arbiter sharing a vendor
and model family with Channel B could systematically favour B's answer, and the
arbiter's whole job is to resolve a disagreement without knowing which channel
said what (D19). Measuring that bias costs more than the rebalancing saves, so it
is recorded as an option with its risk attached rather than taken.

**Also unobserved, and left that way.** NVIDIA's daily token cap. It sends no
rate-limit headers (RX-014), so it is unenforced rather than guessed — the same
rule that governs its request limit. Any campaign leaning harder on NVIDIA is
leaning on an unmeasured limit, and that has to be said rather than discovered.

---

**Addendum, same day — the `max_tokens=1024` column is not yet known to be
usable, and the table must not be read as though it were.**

A paired test on 8 questions (`--compare-max-tokens 1024 4096`) ran while Groq's
allowance was still ~197,500 of 200,000 spent, so 6 of 8 came back `unavailable`
and the run settles nothing. Of the two pairs that produced a reading, **both
favoured 4096**: parse failure at 1024, not at 4096; zero the other way.

n=2 discordant pairs is not evidence. But the direction is what the mechanism
predicts — a reasoning model truncated at 1024 emits no JSON at all — and the
consequence is asymmetric: if Channel A cannot answer at 1024, the "4.9 days"
cell is not a campaign, it is 4.9 days of parse failures. **Measure this on a
fresh allowance before planning against that column.**

**Two things the same run did establish.**

1. **The corrected classifier works.** Quota failures landed in `unavailable`
   where the previous version filed them as `parse_failure`. That misattribution
   is what invalidated RX-019, and it is now visibly gone.
2. **The 429 sync works in production.** `experiments/.rate_limit_state.json`
   carries `"tokens": 197546`, adopted from a live refusal mid-run. The limiter
   now refuses locally instead of discovering the wall again.

**An open observation about the reset boundary.** The limiter models a UTC-day
rollover (`utc_day()`). Groq's 429 said *"Please try again in 23m51.216s"* and a
call did succeed ~45 minutes later, well before UTC midnight — which is what a
**rolling window** looks like, not a daily reset. One data point, so the model is
not changed on it. The direction of the error matters and is worth stating: at
UTC midnight the limiter resets its counter to zero while a rolling provider
would still be counting the preceding day, making the limiter **optimistic**, not
conservative. That is exactly the failure the 429 sync now corrects on first
contact — so the system fails safe by repair rather than by design, which is
weaker and should be resolved by measuring the boundary directly.

---

## RX-022 — The campaign was budgeted at 0.30 days. It is 98.

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** negative (plan-invalidating defect) · **Cost:** the day's Groq allowance

**How it surfaced.** While trying to confirm whether an unread `LLM_MAX_TOKENS`
was causing Channel A's parse failures (RX-019), a paired A/B returned 19 of 20
questions failing at *both* budgets — far worse than the 35% measured an hour
earlier. A direct call returned the reason:

> `groq: quota exhausted: Rate limit reached ... on **tokens per day (TPD)**:
> Limit 200000, Used 199170, Requested 4143`

**The defect.** `RateLimit` modelled `requests_per_minute`, `requests_per_day`
and `tokens_per_minute`. It did not model **tokens per day**, and on Groq's free
tier that is the limit that ends a day's work. At the moment of refusal the
client-side counter reported **14,293 of 14,400 requests still free**.

**Consequence, which is the point.** `estimate_requests` counted requests only,
so `--budget-only` reported the campaign as a third of a day:

| campaign | requests | days by requests | tokens | **days by tokens** |
|---|---:|---:|---:|---:|
| A + B5, 150 questions (H1, the minimum real result) | 1,200 | 0.08 | 5,400,000 | **27** |
| Full 13-arm ablation, 150 questions | 4,350 | 0.30 | 19,575,000 | **98** |

A ~90–330× underestimate, in the direction that strands a campaign part-way
through rather than failing it at the start. D14 recorded that "rate limits are
the binding constraint, not money" and was right about the shape and wrong about
the unit.

**Why the token cost is what it is.** The 429 reports `Requested 4143` for a call
whose `max_tokens` was 4096 and whose prompt was short. That arithmetic strongly
suggests Groq **reserves the full completion budget** against TPD rather than
charging the reply's actual length — which would make `max_tokens` the dominant
term and lowering it a roughly linear lever on campaign length. Stated as
suggested, not established: it rests on one 429, and confirming it needs a
controlled pair of calls on a fresh allowance.

**Two further defects found in the same thread.**

1. **The measurement script misclassified the quota failure as a parse failure.**
   `classify_natural` matched on message text, and `"groq: quota exhausted: …"`
   contains neither "provider" nor "unavailable", so 19 of 20 exhausted calls
   were filed under `parse_failure` — the exact misattribution the script exists
   to prevent, committed by the script. Both channels already record the
   exception *class* in metadata for precisely this reason (the comment beside it
   says so); the classifier ignored it. Now keyed on the class.

2. **RX-019's numbers are contaminated and are withdrawn.** The 35% Channel A
   parse-failure rate was measured while the day's token allowance was being
   consumed by the measurement itself. Two runs with identical seeds, identical
   questions and temperature 0 gave 35% and 45%, and the third gave 95% — a
   monotone trend in quota consumption, not variance. **No parse-failure rate is
   currently established**, and the earlier figure must not be quoted. Re-measure
   on a fresh allowance, with the corrected classifier, and record the token
   spend alongside.

**Fixed.** `tokens_per_day` added to `RateLimit` and enforced *before* the call
so a campaign sees the wall coming; Groq set to 200,000 (OBSERVED from the 429,
not from documentation — the registry already carried one published figure that
was wrong by 75×); `record_token_usage` corrects the day's total with the real
figure; `estimate_requests` reports tokens and days-by-tokens per provider;
`--budget-only` prints both and says which one binds. 8 tests.

**Still unmodelled.** NVIDIA's daily token limit. It sends no rate-limit headers
(RX-014), so it is UNOBSERVED, and unobserved means unenforced rather than
guessed — the same rule that governs its request limit.

---

## RX-021 — Nine of ten declared settings were read by nothing; thirty paths were relative to the working directory

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** negative (defects found), repaired · **Cost:** 0 API requests

**Trigger.** A validation session recorded zero verdicts and left the worksheet
byte-identical. Reproducing it found `review_gold.py`'s default path was relative
to the working directory: run from anywhere but the repository root, it printed
"no worksheet", exited, and never touched the real file — indistinguishable in
the data from a validator who reviewed nothing.

That is a *class*, not an incident, so the whole repository was swept.

**Finding 1 — configuration.** Ten `LLM_*` and `SANDBOX_*` variables are declared
in `.env.example`. **Nine were read by nothing.** Every call ran on whatever
default its own function signature carried.

| variable | consequence |
|---|---|
| `LLM_MAX_TOKENS=4096` | lost to Channel A's own `max_tokens=2048` |
| `LLM_TEMPERATURE` | knob dead; behaviour correct only because both defaults are 0 |
| `LLM_MAX_RETRIES`, `LLM_TIMEOUT_SECONDS` | dead knobs |
| `SANDBOX_*` (×4) | defaults match `.env` exactly, so nothing ran unlimited — but an operator tightening a limit got no tightening **and no error** |
| `ABLATION_SAME_MODEL` | vestigial; **deleted rather than wired** |

`ABLATION_SAME_MODEL` was deleted because wiring it would have been the wrong
repair: arms are `ArmConfig` objects over one graph (D26) and
`same_model_both_channels` lives there. A second source of truth in the
environment could disagree with the arm actually running, and the run record
would name one configuration while the code executed another.

**Finding 2 — paths.** Thirty-odd constants across eleven scripts and four
library modules, plus seven bare `load_dotenv()` calls, all resolved against the
working directory. Three mattered beyond inconvenience:

- **`TEST_ACCESS_LOG`** is the audit trail for the sealed test split. The guard's
  entire value is that bypassing it *leaves evidence in the repository* (spec
  §16). Written relative to the working directory, the evidence lands elsewhere —
  or nowhere — and the seal is decorative.
- **`RUNS_ROOT`** decides where a campaign resumes from. A resume that finds no
  prior artifacts does not fail; it starts again, spending a day of quota.
- **`load_dotenv()`** searches upward from the working directory. Run from
  elsewhere and every API key silently goes missing, which reads as "no providers
  configured" rather than as a path problem.

**Finding 3 — the API cannot say which build is answering.** `/health` reports on
the database and the vector index, both external, and was reporting `status: ok`
from a container whose code predated `backend/core/paths.py` by an hour. A health
check that cannot detect "I am running old code" is D-1 one layer out.

**Fixed.** `backend/core/paths.py` anchors everything to the repository;
`backend/services/llm/settings.py` resolves call settings at call time and raises
on a malformed value; `SandboxConfig.from_env()`; `BUILD_REF` stamped into the
image and echoed by `/health`, defaulting to the literal `"unknown"` rather than
to anything plausible. Recorded as D38, D39, D40.

**The guard that matters more than any of the individual fixes.**
`tests/test_settings_are_wired.py` walks `.env.example` and fails the build if a
declared variable is consumed nowhere, and fails on any CWD-relative path
constant or bare `load_dotenv()`. Every defect above was invisible to a green
test suite for weeks; the class is now something the suite can see.

---

## RX-020 — D36 fixed 72% of the empty-retrieval bug and left 43% of it

**Date:** 2026-08-30 · **Status:** verified · **Outcome:** negative (defect found), then repaired · **Cost:** 0 API requests

**How it surfaced.** Not by looking for it. `measure_channel_reliability.py`
(RX-019) prints the evidence-block count per question, and its first three-question
smoke run showed `blocks=0` on two of them. Both channels abstained correctly —
they were handed nothing and said so — so every downstream signal looked healthy.
Only the block count gave it away.

**The defect.** D36 established that a chunk's `fiscal_year` is the **document's**
year and widened the filter from an exact match to `{Y, Y+1}`. Its docstring then
claimed:

> Widening further would start admitting documents that cannot contain the figure
> at all.

**That is backwards.** The constraint is one-directional: a report can state any
year up to and including its own, and none after it. A *later* report can always
restate an *earlier* year — Indian listed companies publish a ten-year highlights
summary under SEBI's listing obligations. It is an *earlier* report that cannot
hold a *later* figure.

So the `Y+1` window silently returned **zero chunks** for every question reaching
back more than one year:

| year asked about | questions | retrieved |
|---|---:|---|
| 2013-14 … 2021-22 | **115** | **nothing** |
| 2022-23 | 78 | ok |
| 2023-24 | 75 | ok |

**115 of FinVerify-IND's 268 (43%)**, because HDFC Bank's ten-year table and
several multi-year comparatives put questions in the set going back to FY2013-14.
D36 recovered the 72% that ask about the immediately preceding year and left this.

**Two tests asserted the bug.** `test_it_does_not_widen_to_documents_that_cannot
_hold_the_figure` pinned `acceptable_document_years("2019-20") == ("2019-20",
"2020-21")` and passed continuously. Its docstring — "Comparatives go back one
year, not five" — was the false premise, written down and then defended by a
green test. Replaced, with the reason in the docstring.

**Fix.** The window runs forward from the year asked about to a bounded horizon
(`_RESTATEMENT_HORIZON_YEARS = 11`, a ten-year summary plus a year of margin).
Bounded rather than open because a filter that admits everything is not a filter;
the *direction* carries the meaning and the horizon only limits it.

**Result.** On the full gold sets (91 questions, all years):

| company | n | D36 (`Y+1`) | corrected | retrieved nothing |
|---|---:|---:|---:|---|
| HDFC Bank | 25 | 0.280 | 0.280 | 12 → **0** |
| Sun Pharma | 25 | 0.440 | 0.440 | 7 → **0** |
| Reliance | 25 | 0.280 | **0.480** | 9 → **0** |
| Tata Motors | 16 | 0.375 | 0.375 | 0 → 0 |
| **weighted** | **91** | **0.341** | **0.396** | **28 → 0** |

On the recent-year sets — the control, where no question could have been affected
— **0.492 before and after, exactly**. RX-015's headline numbers are untouched,
which is how it should be and is worth stating because it is the evidence that
this changed only what it was supposed to.

**What did not improve, and why that is informative.** HDFC Bank gains nothing in
accuracy despite twelve questions going from *nothing retrieved* to *something
retrieved*. Those are the decade-summary rows, and they are genuinely hard —
which corroborates RX-015's finding rather than contradicting it. The change is
in the **diagnosis**, not the score: those twelve move from `absent_from_index`
(nothing was searched) to `ranked_too_low` (the right chunk lost). Those need
opposite fixes, and until now the first was masquerading as the second.

**The pattern this is the third instance of.** D-1 (wrong collection), D36 (exact
year match) and now this are all the same failure: a filter that is *valid*,
answered *normally*, and returned an empty set indistinguishable from "the corpus
does not contain this". None raised. Each was found by looking at a count that
should not have been zero — never by a test, because the tests asserted the
behaviour rather than the outcome.

---

## RX-017 — Is the transfer gap a question-phrasing artifact? No.

**Date:** 2026-08-29 · **Status:** verified · **Outcome:** negative (hypothesis refuted) · **Cost:** 0 API requests

**Question.** RX-015 left one thing open: *why* retrieval scores 0.909 on Infosys
and 0.375–0.611 on the other four filings. It named three untested causes —
statement hints, chunk granularity, RRF weighting — and ruled out vocabulary
coverage.

A fourth suspect appeared while exercising Module 7 (RX-018), and it is not about
the documents at all. The two gold sets differ in **question form** as well as in
document. Infosys was hand-written short:

> How much were trade payables as at March 31, 2024?

The four generated sets carry FinVerify-IND's template:

> For HDFC Bank Limited, as reported for the year ended March 31, 2023, what were
> advances (total advances as reported on the consolidated balance sheet)?

`strip_question_boilerplate` removes dates and stopwords, so the second reduces to
`HDFC Bank Limited, reported advances total advances reported consolidated balance
sheet` — the issuer's own name, plus a restatement of the metric, searched as
though they were content terms. RX-015 could not separate document from phrasing
because the two vary together.

**Method.** `scripts/measure_query_formulation.py`. Four arms over the same 63
recent-year questions, varying **only the query text**; `company=` stays a
metadata filter in every arm, so the filter is constant.

| arm | what is removed |
|---|---|
| `asis` | nothing (current behaviour) |
| `no_company` | the issuer's name from the query text |
| `no_gloss` | the trailing parenthesised definition |
| `both` | both |

**Result.** Evidence-retrieval accuracy@10:

| company | n | `asis` | `no_company` | `no_gloss` | `both` |
|---|---:|---:|---:|---:|---:|
| HDFC Bank | 13 | 0.538 | **0.769** | 0.462 | 0.462 |
| Sun Pharma | 18 | 0.611 | 0.389 | **0.667** | 0.556 |
| Reliance | 16 | 0.438 | **0.500** | 0.062 | 0.438 |
| Tata Motors | 16 | 0.375 | 0.250 | 0.125 | 0.375 |
| **weighted mean** | **63** | **0.492** | 0.460 | 0.333 | 0.460 |

Paired bootstrap against `asis`, 10,000 resamples, questions resampled together
because every arm answers the same 63:

| arm | difference | 95% CI | p |
|---|---:|---|---:|
| `no_company` | −0.042 | [−0.167, +0.083] | 0.798 |
| `no_gloss` | **−0.250** | [−0.417, −0.083] | **0.004** |
| `both` | −0.042 | [−0.208, +0.125] | 0.835 |

**The hypothesis is refuted.** No arm beats the baseline, and the two that touch
the issuer's name are indistinguishable from it. FinVerify-IND's phrasing is not
what costs retrieval off Infosys, and the per-company swings — `no_company` gains
+0.231 on HDFC Bank and loses −0.222 on Sun Pharma — are large, opposite, and
cancel. RX-015's three original hypotheses remain untested and are now the only
ones standing.

**A real finding, and not the one being looked for.** Removing the parenthesised
definition costs **0.250** evidence accuracy (p=0.004) — on Reliance it collapses
retrieval from 0.438 to 0.062. The gloss is load-bearing. D22 introduced it to
pin a metric's *definition* so ambiguity could be scored separately; it turns out
to be carrying most of the retrieval signal as well, because it names the
statement the figure sits on.

**That is a limitation the write-up must state.** FinVerify-IND questions carry
their own definition. A user asking a bare "what were advances?" would not, and
would retrieve substantially worse than these numbers suggest. The measured
retrieval figures are an **upper bound conditioned on the question format**, not
a property of the retriever alone.

**Not over-read.** `both` (0.460) beats `no_gloss` (0.333) by +0.127, i.e.
removing the company name *rescues* the gloss-free arm. A plausible mechanism is
that without the gloss the issuer's name dominates a short query. At n=63 that is
an observation, not a result, and it is recorded rather than explained.

---

## RX-018 — Module 7's LLM path, live for the first time: it works, and it gets the fiscal year wrong

**Date:** 2026-08-29 · **Status:** verified · **Outcome:** negative (latent defect found) · **Cost:** 35 requests (groq)

**Question.** `understand_question`'s LLM refinement has been written and
unit-tested against a stub since Module 7 and has **never run against a real
model**. Does it work, and what does it do to a spec?

This matters more than an ordinary untested path. Question understanding is
upstream of both channels, so a systematic error here is the common-mode failure
D19 names: a mis-parse makes both channels wrong **identically**, and their
agreement then measures nothing.

**Method.** `scripts/measure_question_understanding.py`, 30 questions drawn
company-balanced from FinVerify-IND, against `groq/openai/gpt-oss-20b`. Each
question parsed both ways; source, metrics and fiscal year compared.

**Result — mechanically, it is sound.**

| | |
|---|---|
| refinement used | **30/30** (never fell back) |
| raised (documented as impossible) | **0/30** |
| metrics agreement with the rule parser | 1/30 |
| latency p50 / p90 / max | 2.03s / 46.6s / **49.2s** |

The 29 metric disagreements are the LLM being **better**: it returns
`other equity` where the deterministic parser returns
`HDFC Bank Limited, reported other equity other equity reported consolidated
financial statements`. Neither is gold, and RX-017 then showed that longer blob
is not costing retrieval anything.

**Result — the fiscal year is wrong on 7 of 30, and always the same way.**

| deterministic | LLM |
|---|---|
| 2022-23 | 2023-24 |

Every disagreement is a question reading *"as reported for the year ended March
31, 2023"*. The Indian fiscal year ending 31 March 2023 is **2022-23**. The model
answers 2023-24 — the standard off-by-one on Indian FY labelling — on **every**
prior-year question it was given, and never on a current-year one. The
deterministic parser is right in all seven.

**Why this is serious rather than a curiosity.** D36 established that 193 of
FinVerify-IND's 268 questions (72%) ask about a prior year. Enabling this path
would put a wrong fiscal year on the majority of the dataset, in the one field
D36 just proved load-bearing, in the one component whose errors both channels
inherit identically.

**Why it is not currently a live bug.** `backend/agents/orchestrator.py:313`
calls `parse_question`, the deterministic parser — not `understand_question`. The
LLM path is unreachable from the campaign. It was off by construction rather than
by decision; it is now off by decision (D37).

**Also observed.** Two of 30 calls took ~47–49s against a 2.03s median, on a
provider whose median has been ~1.7s all week. Campaign budgeting that assumes a
2s question-understanding call would be wrong about its tail. Not reportable as a
latency finding — n=2 — but recorded so a timeout is chosen from data.

---

## RX-016 — Every candidate figure located in its filing, and the line it sits on

**Date:** 2026-08-29 · **Status:** verified · **Outcome:** positive (no defect found) · **Cost:** 0 API requests

**Question.** FinVerify-IND's 268 candidates each name a figure and a page. Are
those figures actually printed where the generator says they are? And can the
*searching* be taken off the validator, leaving only the *judging*?

The second half is the reason this ran. Validation had stalled at 0/268 with the
owner unable to find where to start, and a 268-row CSV checked against five
filings totalling 1,665 pages is a genuinely bad interface, not a matter of
effort.

**Method.** `scripts/precheck_gold.py` opens each cited PDF page through
PyMuPDF's own text layer — not the chunk cache, which retrieval indexes and
which grading against would measure self-consistency — and searches for the
candidate figure in every grouping the corpus uses: western (`135,702`), Indian
(`1,35,702`), bare (`135702`), with and without a trailing fraction, and
parenthesised for negatives. The row label is searched too, and the raw line is
written back into the worksheet.

**Result.**

| Pre-check | n | % |
|---|---:|---:|
| `on_cited_page` | 250 | 93.3% |
| `short_figure` (unsearchable, row found by label) | 6 | 2.2% |
| `no_candidate` (derived metrics, blank by design) | 12 | 4.5% |
| **`not_found`** | **0** | **0%** |

**What this establishes.** No candidate names a figure absent from its filing.
That is a statement about the *generator*'s page attribution, and a reassuring
one: the D34 lexicon extension and the table-column labelling in Module 4 are
placing figures on the right pages.

**What this does NOT establish, and must never be reported as though it did.**
Nothing about whether a candidate answers its question. A machine can establish
that a number is printed on a page. It cannot establish that it is the *right*
number — the right metric, the right year's column, the right consolidation
basis, the right scale. Those four are precisely where financial QA breaks, they
are why spec §16 requires a person, and 93.3% is not a validation rate. A
`precheck` column exists in the worksheet; a `verdict` column remains empty, and
`ValidationStatus.VALIDATED` is still unreachable from code.

**A false alarm, caught and fixed.** The first run reported 6 figures
`not_found`, five of them HDFC Bank dividend-per-share rows on p197. All six
were the check's own minimum-digit gate: `4.00` looks like three digits and
reduces to the single variant `4`, which was filtered as insufficiently
discriminating — correctly, since a one-digit needle matches every page of every
filing. The gate was measuring the raw string rather than the searchable
variants. Reporting those as absent would have sent a validator hunting for a
generator defect that did not exist; they are now `short_figure`, meaning *the
check cannot decide this, read the row yourself*, with the row printed beside
it. The distinction between "absent" and "unverifiable" is the same one that
made `build_retrieval_gold.py` drop 6 of 97 spans rather than guess (RX-015).

**Also corrected.** The excerpt initially showed the *first* line on the page
carrying the row label. On Tata Motors p449 that is "Total borrowings includes
all long and short-term borrowings as disclosed in notes 22 and 23" — a
definition of the row, not the row. The label line nearest the figure is now
chosen, so the validator reads `Total borrowings  //  18,872.44`.

**Consequence.** `scripts/review_gold.py` presents one question per screen with
that line beside the candidate, so no PDF need be open. It has **no default
answer** — Enter re-prompts — because a keypress that means "yes" when you meant
"next" turns a validation pass into a rubber stamp, and a rubber-stamped gold
set is indistinguishable from an unvalidated one in the data but not in what it
claims. A test pins that (`tests/test_gold_review.py`, 30 tests).

**Still open.** 0 of 268 validated. This experiment did not move that number and
was not capable of moving it.

---

## RX-015 — Retrieval measured on all five filings: a total filter failure, and a number that does not transfer

**Date:** 2026-08-29 · **Status:** verified · **Outcome:** negative (two defects) · **Cost:** 0 API requests

**Question.** RX-012 re-measured retrieval corpus-wide but on Infosys questions
only, because Infosys was the only company with a retrieval gold set. Every
retrieval figure this project has ever quoted describes **one document out of
five**. What happens on a bank, a conglomerate, a pharmaceutical company and an
automaker?

**Method.** Built retrieval gold for the other four filings
(`scripts/build_retrieval_gold.py`), deriving evidence spans from FinVerify-IND's
recorded page and row label and then **verifying every span against the PDF's own
text layer** — both the row label and the figure had to be found on the stated
page, and the whole document was scanned so every page carrying the same pair is
listed as an alternative.

This produces gold *evidence locations*, never gold *answers*. A gold answer
asserts what a metric means and needs a human (spec §16). A gold span asserts
that two strings appear on a page, which is decidable by reading the file. 6 of
97 candidates could not be confirmed and were **dropped, not guessed at**.
`--validate-gold` then re-verified independently: **184/184 spans confirmed**,
completeness confirmed on all four sets.

### Finding 1 — the fiscal-year filter returned zero chunks for 72% of the dataset

The first run returned an *empty result list* for questions about the prior year.
Not a poor ranking — nothing at all.

A chunk's `fiscal_year` payload is the year of the **document**. The filter
matched it against the year the **question** asked about. Every chunk in this
corpus carries `2023-24`:

```
fiscal_year=2023-24  ->  22930 chunks
fiscal_year=2022-23  ->      0 chunks
```

An annual report states its own year beside the previous one, so FY2023 figures
exist only inside the FY2023-24 filing. **193 of FinVerify-IND's 268 questions
(72%) ask about a prior year.** All of them retrieved nothing, both channels
would have answered from an empty context, and the campaign would have recorded
that as the method failing.

Nothing errored. `build_filter` was valid, Qdrant answered normally, and an
empty result set is indistinguishable from "this report does not contain that".
The docstring beside the filter even warned that "guessing a year would silently
exclude the right evidence" — the filter did exactly that, and the warning did
not save it.

**Fixed** (D36): a question about year Y now admits documents for Y and Y+1, via
`MatchAny` rather than `MatchValue`. Effect on HDFC Bank, planned arm:

| | R@10 | Evidence acc.@10 |
|---|---:|---:|
| before | 0.120 | 0.120 |
| after | **0.280** | **0.280** |

### Finding 2 — the headline retrieval number describes the development document

With the filter fixed, and restricted to recent-year questions so the comparison
is like-for-like with the hand-built Infosys set:

| Company | Sector | R@10 | MRR | Evidence acc.@10 |
|---|---|---:|---:|---:|
| **Infosys** (hand-built, development set) | IT services | **0.932** | 0.616 | **0.909** |
| Sun Pharmaceutical | Pharma | 0.611 | 0.336 | 0.611 |
| HDFC Bank | Banking | 0.538 | 0.195 | 0.538 |
| Reliance Industries | Conglomerate | 0.438 | 0.191 | 0.438 |
| Tata Motors | Automotive | 0.375 | 0.050 | 0.375 |

**0.909 is a development-set figure.** Every retrieval decision on this project —
chunking parameters, the metric lexicon, statement hints, RRF weights, the
6-row header window — was measured on Infosys and only Infosys. Off that
document, evidence accuracy is roughly **0.38–0.61**, against 0.909 on it.

MRR degrades hardest, and Tata Motors' 0.050 is the number to worry about: when
the evidence is found at all it ranks near the bottom of the top-10, so at the
pipeline's `top_k=8` the channels will frequently never see it.

**Two honest caveats.** The Infosys set was hand-built and hand-completed
(RX-005); these four were generated and machine-verified, so part of the gap may
be construction rather than retrieval. And historical decade-summary rows are
genuinely harder than current-year rows — including them drops HDFC from 0.538
to 0.280 — which is why the table above excludes them.

**Consequence for the campaign.** H2 predicts a retrieval-caused stratum. On
Infosys that stratum is ~9% of questions; corpus-wide it is closer to **half**.
That is not a reason to stop — H2 exists to measure exactly this — but the
write-up must report per-company retrieval and must not quote 0.909 as a
property of the system.

**What this does NOT establish.** Why retrieval transfers poorly. The lexicon
covers all the failing metrics (`deposits`, `advances`, `borrowings` are all
present), so the cause is not vocabulary coverage. Statement hints, chunk
granularity on much longer filings, and RRF weighting are all untested
hypotheses.

---

## RX-052 - A module-by-module audit against the §43 checklist, and what it cost the status table

**Date:** 2026-09-12 · **Status:** verified · **Outcome:** NEGATIVE for the status
table, POSITIVE for the record — 6 modules marked COMPLETE were not

**Question.** The status table said 29 COMPLETE, 5 PARTIAL, 1 BLOCKED, audited
2026-09-06. Does it survive an independent check against the spec §43 checklist
rather than against its own prose?

**Method.** Six auditors, one per module group, each instructed to verify by reading
code and artifacts and to return CANNOT_VERIFY rather than guess. Then an adversarial
stage per group, told to *refute* each reported discrepancy and to default to
REFUTED when it could not reproduce one.

**What actually ran, because it bounds the result.** Four of six audit groups
completed. **The two covering modules 8-17 — the reasoning channels and the
verification core — died on a session quota limit, and so did all six adversarial
verifiers.** 23 of 33 modules were therefore audited, single-auditor. Every
discrepancy recorded below was reproduced by hand before being written down; that
hand-checking is what the dead stage was supposed to do, and it is not a substitute
for six independent refutation attempts.

**Result: 6 of 15 audited COMPLETE rows did not survive, all flattering.**

| Module | Was | Is | Why |
|---|---|---|---|
| 3 Document intelligence | COMPLETE | **PARTIAL** | OCR has no production caller |
| 1 Dataset & benchmark | COMPLETE | **PARTIAL** | §9's `gold_calculation`/`gold_program` absent; 22 rows carry no evidence |
| 7 Question understanding | COMPLETE | **PARTIAL** | its only acceptance figure was superseded by RX-028 |
| 23 Baselines | COMPLETE | **PARTIAL** | B3 never ran on the held-out split |
| 27 Case study | COMPLETE | COMPLETE | generated half was 11 days stale — fixed same day |
| 31 Deployment | COMPLETE | COMPLETE | the project's own verifier was failing — fixed same day |

**The finding that matters most: a module can pass every test it has and still not
run.** `backend/documents/ocr.py` passes 22 tests against real Tesseract. D32 says
"every OCR'd page carries source='ocr' and its confidence into the extraction quality
report". And `extraction.py` contains the string `ocr` **zero times**. A repo-wide
grep for `ocr_document|ocr_page|needs_ocr` returns the module, its tests, and an
unrelated enum value — no caller anywhere.

This is visible in the corpus rather than inferred: the 10 HDFC Bank pages the
registry flags as scanned inserts hold **~187 characters each** (pages 200-209 of
`40f73920a8b153ec.chunks.jsonl`), which is the injected unit banner plus a fragment
of a spaced-out "ASSURANCE STATEMENT" heading. The OCR stage spec §11 requires is
unexercised on real data, and the tests could never have told us, because they test
the function and the gap is in the wiring.

**Not fixed, deliberately.** Re-extracting those pages changes the chunk set that the
22,930-point index was built from, which cascades into facts, retrieval, and every
campaign artifact. Under a deadline that is how a corpus gets quietly desynchronised
from the runs that cite it. Recorded as a PARTIAL with its consequence instead.

**A second defect, found by fixing the first.** Regenerating the stale CASE_STUDY.md
surfaced that `evaluation/case_study.py` read `record[channel]["tokens"]`, a key the
recorder abandoned in 2026-09. Every row of every modern campaign therefore read as
zero and the document printed *"no row recorded token usage"* over an artifact where
all 61 rows record it. **This is the identical defect already found and fixed in
`evaluation/metrics/efficiency.py`** — same wrong key, one module over, fix never
propagated. A generated document asserting data is absent when the data is present is
worse than a wrong number: it reads as an honest admission.

Fixed to prefer `tokens_used`, the row's own total, which also captures the
verification agent's spend that per-channel blocks miss. The case study now reports
**6445 tokens/question against the efficiency report's 6444.85** — two independent
readers over one artifact, agreeing for the first time. 5 tests pin it.

**One downgrade was wrong, and it is the reason the dead verification stage mattered.**
An auditor reported module 7's "wrong fiscal year on 7 of 7 prior-year questions" as
unsupported. Checking `qu-llm_20260829T181529Z` directly: 30 rows, `year_agree` False
on exactly 7, and on all 7 the deterministic year is `2022-23` while the LLM returned
`2023-24`. The claim is correct and was left alone. Had the refutation stage run, it
would have caught that without a human check; without it, the only protection was
checking every finding by hand.

**Also corrected, each confirmed against the repository before the edit:**

- `--faint` measured **2.95:1** on a live table header, under the 4.5:1 AA floor,
  and it colours `.undefined-value`, `.risk-UNSCORED` and the chart confidence
  intervals — this project's caveats, in its least legible colour. Now 4.61:1.
- `build_ref` reported `unknown` against a matching HEAD. It is a Docker *build arg*,
  so `--force-recreate` cannot carry it, and DEPLOYMENT.md set neither it nor
  `--build`. The deployment document produced a stack that failed the project's own
  verifier, on the one field that exists to catch stale code.
- RAG.md presented RX-001's Infosys-only figures as *(current)*, ending at 0.909
  evidence accuracy, with no mention of RX-028 — which re-measured the same
  pipeline corpus-wide at **0.08-0.56**. Sixteen days stale in the flattering
  direction.
- TESTING.md attributed 54 sandbox-escape attempts to `test_sandbox.py` and said they
  "execute real containers". They are `test_code_validator.py`, pure AST analysis, no
  container — a claim of containment evidence from a suite that starts none.
- DATABASE.md claimed "all sixteen entities spec §26 names". §26 names
  seventeen; sixteen are built. `reports` is folded into `documents`, now recorded in
  D49.1 rather than glossed.
- The test count read 1,374 in four current-state documents against 1,406 passing.
  The two *historical* mentions — "every one was invisible to 1,374 passing
  tests" — were left exactly as written: they describe a past moment, and
  bumping them would falsify the record.

**The procedural lesson.** Every one of these was a claim about the project rather
than a claim the project makes to a user, and not one was reachable by any test in
the suite. `test_source_hygiene.py` and the new `test_frontend_contract.py` and
`test_frontend_contrast.py` now assert a handful of them mechanically — the
screen count must match the router, every endpoint the frontend calls must exist in
the API, the contrast floor must hold on both themes. A status table is the one
artifact in a research project with no test behind it, and this is the second time an
audit has found it drifting in the direction that flatters.

---

## RX-053 - The test-set seal had a hole in the retrieval lane, and 57% of the sealed split went through it

**Date:** 2026-09-12 · **Status:** verified · **Outcome:** NEGATIVE — a
research-validity defect, found while trying to do something else

**How it surfaced.** The §43 audit (RX-052) said module 6's headline retrieval
figures were prose-only and should be persisted as committed artifacts, and that
module 7's acceptance figure should be re-measured on the corrected post-D42 gold.
Both are re-runs of `evaluate_retrieval.py`, which costs no LLM quota. Before
running anything, the gold sets were checked for test-split content. They have it.

**The finding.** Each of the four generated retrieval gold sets declared:

    "split": "validation"

and carried, in its own notes:

    "VALIDATION split. Must never be reported as a test result."

Against the benchmark, their source questions break down as:

| Gold set | test | train | validation |
|---|---:|---:|---:|
| `40f73920` HDFC Bank | **10** | 8 | 7 |
| `cca3bdde` Sun Pharmaceutical | **13** | 9 | 3 |
| `d8e3739d` Reliance Industries | **9** | 10 | 6 |
| `db02424e` Tata Motors | **11** | 7 | 7 |

**43 of the benchmark's 76 sealed test questions — 57% — appear as source
questions in sets labelled validation.** Verified question-by-question, not by a
count: retrieval qid `R04` of the HDFC set is byte-identical in question text to
benchmark qid `FI1a3114db`, whose split is `test`.

**Why nothing caught it.** `scripts/evaluate_retrieval.py` gates the seal on the
set's own declared label:

    if meta["split"] == "test" and os.environ.get("FINVERIFY_ALLOW_TEST") != "1":
        refuse

That gate is correct and it works — it refuses, with exit code 2, the moment the
label says `test`. The defect is one layer up: `build_retrieval_gold.py` **hardcoded**
`"split": "validation"` in the set's metadata rather than deriving it from the source
questions' splits, which were right there in the same loop as `source_qid`. So the
gate read a label that did not describe the contents, and permitted every run.

**Consequence, stated at its actual size rather than at its most alarming.**

What went through the hole is **evidence-span locations and retrieval performance**
on 43 test questions — not gold answers. The answer-level campaign used
`evaluation.dataset.build_split`, which does enforce the flag and does append to
`experiments/test_set_access.log`; that log's 7 entries are all campaign
evaluations, 61 questions each, and are unaffected.

What is nonetheless true:

1. **Three experiments ran on test-bearing gold, unflagged and unlogged:** RX-028's
   corpus-wide re-measurement, RX-034's rank decomposition, RX-035's reranker test.
   `experiments/test_set_access.log` has no entry for any of them, because the
   logging lives behind the gate they never tripped.
2. **RX-035 is the one that matters most**, because it is the only one that made a
   DECISION. It measured a cross-encoder reranker at 0.240 against a 0.280 baseline
   and rejected it. That rejection is partly informed by test-derived data, which
   makes it a tuning decision on the held-out split — the precise thing the seal
   exists to prevent. The decision's direction happens to be conservative (it
   *declined* to adopt something), which limits the damage but does not change what
   it rests on.
3. **Module 6's figures cannot be called validation figures.** The 0.08–0.56
   evidence-accuracy range is measured on mixed-split gold.

**Fixed.**

- `build_retrieval_gold.py` now records `source_split` on every built question and
  **derives** the set-level label from those splits, with a `split_composition` block
  stating the breakdown so the claim is checkable instead of asserted. A set with any
  test content is labelled `test`.
- The four existing sets were relabelled to `split: "test"` with their composition.
  **No question, evidence span or answer note was touched** — verified field by
  field against `HEAD`. The change moves strictly in the unflattering direction: the
  sets are now gated, so their figures can no longer be presented as free validation
  results. That is the intended effect, and it is why this is a relabel rather than a
  rebuild.
- `tests/test_retrieval_gold_split.py` (20 tests) is the guard that did not exist: a
  set claiming `validation` may not contain test questions; its prose may not
  contradict its split field; no part of the sealed split may be reachable through a
  set labelled `validation`; and the builder may not hardcode the label again. The
  last one matters because the original defect was a single literal in a dict, and
  every other test would still pass on the current files while the next generated set
  carried the same untruth.
- Confirmed after the fix: `evaluate_retrieval.py` now refuses all four sets without
  `FINVERIFY_ALLOW_TEST=1`, exit code 2.

**NOT fixed, and left open deliberately.** The module 6 and 7 re-measurements that
prompted this are now blocked behind a one-shot: running them means spending a test
evaluation on a split already recorded as spent against a VOID freeze (D48). That is
a decision for the owner, not a cleanup task, and it is recorded in TODO 0f rather
than taken quietly. A clean alternative exists and costs nothing: rebuild the
retrieval gold from validation-split questions only, and re-measure on that.

**The lesson, which generalises past this project.** A seal that reads a
self-declared label is only as strong as whoever wrote the label. D48 already
recorded the same shape of failure one level up — a precondition written as prose
in a decision entry, which blocked nothing, until `run_campaign.py` was given a gate
that actually checked. This is that lesson again, one lane over: the gate existed,
and it was reading a field that nobody computed.

---

## RX-054 - Retrieval re-measured on validation-only gold: planning is worth +0.188, not +0.409

**Date:** 2026-09-12 · **Status:** verified · **Outcome:** POSITIVE for the mechanism,
NEGATIVE for the published figure — module 7's acceptance number was 2.2x too high

**Why this exists.** RX-052 found that module 7's only acceptance figure, +0.409 R@10
for planned retrieval, rests on `retrieval-eval-infosys-fy24-v1`: 22 hand-built
questions, one company, and gold that RX-028 superseded. RX-053 then found that the
four corpus-wide gold sets are 57% test-derived, so the obvious re-measurement would
have spent a one-shot test evaluation.

**Method — the free route, and the reason it was available.** `build_retrieval_gold.py`
gained a `--split` filter, so gold can be built from validation questions only. A
split-filtered build gets its own `set_id` and its own filename, because overwriting
the mixed-split sets would destroy the evidence three recorded experiments were
measured against.

Built: **32 questions across 4 companies** (HDFC 12, Reliance 11, Tata 6, Sun 3),
every one drawn from the validation split, `split_composition` confirming it. All
**58 gold spans re-verified against the source PDFs** before use. Infosys is absent
because the builder skips it — its set is hand-built, not generated.

Then: corpus-wide (the whole index, which is what the campaign actually does),
planned and hybrid arms scored from the same pass so the contrast is planning and
nothing else. **No test data touched, no LLM quota spent, four committed reports
under `evaluation/reports/`.**

**Result — evidence retrieval accuracy at K=10:**

| Company | n | planned | hybrid | lift | RX-028 (mixed gold) |
|---|---:|---:|---:|---:|---:|
| HDFC Bank | 12 | 0.333 | 0.167 | **+0.167** | 0.280 |
| Sun Pharmaceutical | 3 | 0.333 | 0.000 | **+0.333** | 0.560 |
| Reliance Industries | 11 | 0.182 | 0.000 | **+0.182** | 0.320 |
| Tata Motors | 6 | 0.167 | 0.000 | **+0.167** | 0.080 |
| **pooled** | **32** | **0.250** | **0.062** | **+0.188** | 0.08–0.56 |

**Three readings, in order of how much they should change what anyone says.**

1. **Module 7's claim survives in direction and collapses in magnitude.** Planned
   retrieval beats hybrid in **all four companies**, with no company showing a
   negative lift — that consistency is the robust part of this result and it is
   what the mechanism actually earns. But the pooled lift is **+0.188, against the
   +0.409 the status table has carried**. The old figure was measured on one company,
   22 questions, and gold the project itself retired; this one is on four companies
   and gold that can be called validation without qualification. **The +0.409 should
   not be quoted again.**

2. **Unplanned hybrid retrieval finds essentially nothing corpus-wide: 0.000 on three
   of four companies.** That is a harder statement than anything in RX-028 and it
   deserves care, because it is easy to over-read. Scored corpus-wide, a bare
   question competes against 22,930 chunks from five filings, and the financial
   statements are long, repetitive and numerically dense — many chunks are near
   duplicates of the right one. Planning supplies the company filter and the
   statement hint that make the target reachable at all. The honest summary is not
   "hybrid retrieval is broken" but **"corpus-wide retrieval without question
   planning is not a working configuration on this corpus"**, which is consistent
   with RX-012 and is now measured on clean gold.

3. **n=32, and one cell is n=3.** Sun Pharmaceutical rests on three questions: a
   single question changing outcome moves it by 0.333, which is most of the reported
   lift. Per-company numbers here are direction, not magnitude. The pooled figure is
   the one to cite, and 32 questions still gives an interval wide enough that +0.188
   should be read as "clearly positive, size uncertain".

**What this does NOT do.** It does not re-measure RX-028's absolute accuracy figures
or RX-034's rank decomposition — those were computed on the mixed gold and remain
compromised in the RX-053 sense. It does not re-run RX-035's reranker test, which is
the one compromised measurement that made a decision (D50, Decision 3). Those need
their own runs against this gold, and the gold now exists for them.

**Module 6 and 7 both change.** Module 6's figures are no longer prose-only for the
planned-vs-hybrid contrast: four reports are committed. Module 7's acceptance figure
is replaced by one that is defensible. Neither becomes COMPLETE: module 6 still has
RX-028's absolute figures on compromised gold and RAG.md's superseded table, and
module 7 still has spec §15's unimplemented question types.

### RX-054b — the rank decomposition on the same clean gold, and it moves against the reranker

`diagnose_retrieval_ranks.py` gained the `--out` flag RX-052 said it needed, and was
run corpus-wide over the same 32 validation-only questions. Five committed artifacts,
one per company plus a pooled one, where RX-034 had prose.

| | RX-034 (mixed gold) | clean validation gold |
|---|---:|---:|
| in the top 10 | 0.280 | **0.125** |
| within rank 70 | 0.720 | **0.406** |
| within rank 300 | — | **0.719** |
| not ranked at all | 0.130 not a candidate | **0.281** |

**The headroom is real but it sits much further down the ranking than reported.** The
0.72 figure that RX-034 placed at rank 70 needs **rank 300** on clean gold, and
**28% of evidence groups are not ranked within 300 at all** — against RX-034's
0.130 "not a candidate". Those groups are not a reranking problem under any
definition: nothing that reorders a 100-candidate pool can reach them.

**CORRECTED 2026-09-13.** This paragraph originally argued that the deeper headroom
*strengthens* RX-035 — that a smaller share of right chunks in the pool made the
reranker's failure unsurprising and its rejection more defensible. **The clean re-run
refuted that.** RX-035-revalidated measured the reranker on this exact gold at
**0.156 against a 0.125 baseline (+0.031)**: it did not fail, it gained one evidence
group net. The ceiling argument predicted the direction and got it wrong.

The original paragraph also said, correctly, that "the conclusion would probably
hold" is a prediction and that this project does not publish predictions as results.
It is left recorded here as the reason the re-run was worth doing: had the prediction
been written into the status table as a finding, it would now be a false one.

**One limit, stated because it bounds everything above.** 32 evidence groups. The
bands hold 4, 1, 8, 10 and 9 groups; a single group moving band shifts a cumulative
figure by 0.031. The shape of the distribution is the finding — most of the
evidence is reachable but deep, and a fifth of it is not reachable at all — not any
individual number in it.

**The smaller lesson.** The blocked item and its solution were the same distance
away the whole time. Re-measuring "properly" looked like it required spending the
test split, and the thing that made it free was a three-line `--split` filter on a
script that already did everything else. The contamination existed *because* that
filter was missing, so the fix for the breach and the unblock for the measurement
were one change.

---

## RX-035-revalidated - On clean gold the reranker does not hurt; it still is not adopted

**Date:** 2026-09-13 · **Status:** valid · **Split:** validation only ·
**Supersedes:** RX-035-original (invalidated, RX-053) · **Git commit:** `3d98787`
· **Cost:** 0 API requests, 797.9 s local CPU

**Why it exists.** RX-035-original rejected cross-encoder reranking at 0.240 against
a 0.280 baseline, on gold RX-053 found to be 57% test-derived. It is the only
compromised retrieval experiment that made a *decision*, so D50.3 left it to be
re-run once validation-only gold existed. RX-054 built that gold.

**Configuration — held equal to the original wherever possible.**

| | RX-035-original | RX-035-revalidated |
|---|---|---|
| script | `scripts/measure_reranking.py` | same, unchanged |
| reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | same |
| embedding / collection | `intfloat/e5-base-v2` / `finverify_e5` | same |
| pool / cutoff | 100 fused candidates / K=10 | same |
| pairing | pool retrieved once, both arms scored over it | same |
| scope | four company gold sets | corpus-wide, four company sets |
| gold | `*_retrieval_v1.json` — 100 groups, **57% test-derived** | `*_retrieval_v1_validation.json` — 32 groups, **0 test questions** |
| metric | recall@10 over evidence groups | same |

The one unavoidable difference is the gold: 32 validation groups instead of 100
mixed ones, because that is how many validation questions the corpus yields once
test and train questions are excluded (HDFC 12, Reliance 11, Tata 6, Sun 3).

**Result.**

| arm | recall@10 |
|---|---:|
| hybrid (baseline) | 0.125 |
| hybrid + reranking | **0.156** |
| difference | **+0.031** |

2 evidence groups lifted into the top 10, 1 pushed out — **net +1 of 32**. Artifact:
`evaluation/reports/rerank_validation_only_corpuswide.json`, per-group ranks before
and after.

**Reading.**

1. **The original negative result does not reproduce.** RX-035-original's headline
   was that reranking made retrieval *worse*. On clean gold it made it marginally
   better. What the original measured, at least in part, was the test split.
2. **+1 of 32 is not a finding either way.** A single evidence group is the entire
   difference. This measures "no detectable effect", not "a small gain".
3. **The decision is unchanged: reranking is not adopted — but the reason is
   different, and the reason is what gets recorded.** Not "it hurts", which is now
   unsupported, but: (a) no measurable benefit on the only clean gold available, and
   (b) **24.9 s of CPU per evidence group**. The live question path already spends
   ~25 s in the program channel; reranking would roughly double user-facing latency
   for an effect indistinguishable from zero.

**What would change the decision:** a validation set large enough to separate a
+0.03 effect from noise, or a GPU host where the latency term disappears. Neither is
in scope for this project.
