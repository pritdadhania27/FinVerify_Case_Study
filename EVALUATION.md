# Evaluation Methodology

**Status:** DRAFT — frozen at git tag `methodology-freeze-v1` before final
test-set evaluation (spec §46.9).

This document defines every metric's exact computation *before* any experiment
runs. A metric defined after seeing results is a metric chosen to flatter them.

---

## 1. Data splits and test-set protection

| Split | Use | Access |
|---|---|---|
| train | prompt development, few-shot selection, threshold tuning | unrestricted |
| validation | model/config selection, retrieval tuning, calibration | unrestricted |
| **test** | **final evaluation only** | **sealed until freeze** |

**Protection mechanism, as built.** This paragraph described a stronger mechanism
than existed until 2026-09-12, in three specific ways. Corrected, because a
methodology document that overstates its own safeguards is worse than one that
admits their limits.

The test split does **not** live in `datasets/benchmark/test/` — that directory
holds only `.gitkeep`. It is a `split` field on each question inside the single
`datasets/finverify_ind/finverify_ind_v1.json` (58 train / 58 validation / 76 test),
and there is **no separate SHA-256 manifest** for it. What enforces the seal is
`evaluation.dataset.build_split`, the only supported way to obtain questions for an
evaluation: it raises `SealedSplitError` unless `FINVERIFY_ALLOW_TEST=1` is set
*and* a reason is given, refuses `include_pending` on test, and appends to
`experiments/test_set_access.log`.

Each log entry now carries timestamp, reason, question count, **the git commit, and
the invoking script**. Until 2026-09-12 it carried only the first three — this
document had promised the commit all along, and the code has been brought up to the
promise rather than the promise brought down to the code. An access that cannot be
tied to a state of the code is most of the way to no evidence at all.

> **The seal was breached, and the breach did not leave evidence (RX-053).** The
> sentence that used to close this paragraph — "any accidental leakage leaves
> evidence in the repository rather than going unnoticed" — is the one this
> project most needs to be true, and it was false for three weeks.
>
> `build_split` guards the *answer* lane. The **retrieval** lane has its own gold
> sets under `datasets/retrieval_eval/`, and they never go through `build_split`.
> `scripts/evaluate_retrieval.py` has its own gate, which reads the set's
> self-declared `split` field — and `scripts/build_retrieval_gold.py` hardcoded
> that field to `"validation"` while drawing source questions from every split.
> **43 of the 76 sealed test questions (57%) sat inside sets labelled validation**,
> each carrying the note "must never be reported as a test result".
>
> So RX-028, RX-034 and RX-035 all ran on test-bearing gold with no flag, no reason,
> and no log entry. What was exposed is evidence-span locations and retrieval
> performance, not gold answers; every answer-level figure went through
> `build_split` and is unaffected. But **RX-035 rejected a reranker on that data**,
> which makes it a tuning decision informed by the held-out split.
>
> Now: the label is derived from the source questions and published as
> `split_composition`, the four sets are relabelled `test` and are correctly
> refused, and `tests/test_retrieval_gold_split.py` asserts that no part of the
> sealed split is reachable through a set claiming to be validation.
>
> **The generalisable lesson:** a seal that reads a self-declared label is only as
> strong as whoever wrote the label. D48 recorded the same shape one level up — a
> precondition written as prose, which blocked nothing. Both gates existed. Both
> were reading something nobody computed.

Test set is evaluated **once** per frozen methodology version. Re-running after
seeing test results converts it into a validation set, and the write-up would
have to say so.

---

## 2. Correctness labelling — the foundation everything else rests on

Every downstream metric depends on deciding whether one numeric answer is
"correct". Done loosely, the whole evaluation is unsound.

### 2.1 Normalisation, applied before comparison

Applied to both prediction and gold, in this order:

1. **Unit and scale resolution** to a canonical base unit. Indian filings mix
   ₹ crore (10⁷), ₹ lakh (10⁵), millions, and billions, frequently within one
   document. A scale error is a 10×–100× wrong answer that looks entirely
   plausible, so this step is where silent failure concentrates.
2. **Sign normalisation.** Accounting parentheses `(1,234)` mean −1234.
3. **Percentage convention.** `25%` and `0.25` are the same magnitude; the gold
   record's `unit` field fixes the expected convention.
4. **Separator and artefact stripping.** Thousands separators, currency symbols,
   footnote markers glued to numerals.

Every transformation is logged with its input, output, and rule id, so a
correctness judgment can always be traced back to the exact steps that produced
it (spec §13, "all transformations must be traceable").

### 2.2 The match predicate

With `p` = normalised prediction, `g` = normalised gold:

```
correct(p, g) = same_sign(p, g) AND same_unit(p, g) AND rel_err(p, g) <= TAU
rel_err(p, g) = |p - g| / max(|g|, EPSILON)      EPSILON = 1e-9
```

**TAU = 0.005 (0.5%)** as the primary threshold. Chosen so that gold values
rounded to published precision (a report stating "12.4%" for 12.4372%) still
match, while a genuine arithmetic error does not.

- `g == 0` → require `|p| <= EPSILON` (relative error is undefined at zero).
- **Sign and unit mismatches are always incorrect**, regardless of magnitude
  proximity. A profit reported as a loss is not a near-miss.

**Sensitivity is reported, not assumed.** All headline results are re-computed at
TAU ∈ {0.001, 0.005, 0.01, 0.05} and the table is published. If conclusions flip
between tolerances, that fact is the finding and gets stated prominently.

---

## 3. QA metrics (spec §30)

| Metric | Computation |
|---|---|
| **Exact Match** | `correct(p, g)` with TAU = 0 after normalisation |
| **Numerical Accuracy** | fraction with `correct(p, g)` at TAU = 0.005 — the headline QA number |
| **Execution Accuracy** | fraction where the program channel executed without error *and* produced a correct result. Failure-to-execute counts as incorrect, never as "excluded" |

Non-answers ("cannot determine") are scored as **incorrect** for accuracy, and
tracked separately as an abstention rate. Abstaining is safer behaviour than
guessing, so it deserves its own number rather than being silently rewarded or
punished by the accuracy metric alone.

---

## 4. Retrieval metrics (spec §14)

Against gold evidence spans, at K ∈ {1, 3, 5, 10}:

- **Recall@K** — fraction of gold evidence spans present in the top-K.
- **Precision@K** — fraction of top-K that are gold evidence.
- **MRR** — mean reciprocal rank of the first gold span.
- **Evidence retrieval accuracy** — fraction of questions where *all* required
  gold spans appear in the retrieved set. This is the one that matters for
  multi-step questions: retrieving one of two needed numbers yields a confident,
  wrong answer.

**Span matching (amended by decision D18).** A gold evidence span is identified
by `(page number in the source PDF, literal anchor strings)` and is satisfied by a
retrieved chunk when the chunk comes from that page and contains every anchor,
after whitespace collapsing and case folding. Digit separators are deliberately
*not* normalised.

The original rule here — chunk-overlap ≥ 50% on character offsets in the
extracted text — was written before the chunker existed and cannot be computed:
table chunks are re-rendered as pipe tables from Camelot cells, so they are not
substrings of the page text and have no offsets into it. The only text an offset
rule could use is the chunker's own output, which would make the gold labels a
function of the system under test. See D18 for the full argument and the cost of
the weaker claim.

**Evidence is grouped.** Each question carries evidence *groups*: all groups must
be covered, any span within a group covers it. Recall@K is over groups. This is
what lets one figure have several legitimate locations (Infosys' trade payables
appear on the balance sheet *and* in note 2.14) while a multi-hop question still
requires every distinct figure it needs.

---

## 5. Hallucination detection metrics — the core of the contribution

### 5.1 Task definition

This must be stated precisely, because "detecting hallucination" is ambiguous
until the label and the score are pinned down.

- **Label** `y ∈ {0,1}`: `y = 1` iff the system's final answer is incorrect per
  §2.2. This is the event to be detected.
- **Score** `s ∈ [0,1]`: the system's **continuous risk score** — higher means
  more likely wrong.

**The confidence module must emit a continuous score, not only HIGH/MEDIUM/LOW.**
AUROC is undefined over three ordinal buckets in any useful sense, and AUROC is
the primary metric. Discrete bands are a presentation layer *derived* from the
continuous score, never the underlying representation. (This constraint on
Module 15 is why the methodology is written before the module.)

### 5.2 Metrics

| Metric | Definition |
|---|---|
| **AUROC** | primary; threshold-free ranking quality of `s` against `y` |
| **AUPRC** | reported alongside, because errors are the minority class and AUROC flatters imbalance |
| Precision / Recall / F1 | at the operating threshold selected **on validation**, never on test |
| **FPR** | correct answers wrongly flagged — the cost of distrust |
| **FNR** | wrong answers passed as trustworthy — the cost that matters most in finance |
| **Risk–coverage curve** | accuracy on the retained subset as low-confidence answers are abstained on; summarised by AURC |

The operating threshold is fixed on validation and **frozen**. Tuning it on test
would inflate precision/recall by construction.

### 5.3 Stratification by error provenance (RQ3 / H2)

Reported **separately** for:

- **retrieval-caused** — a required gold span was absent from the retrieved set;
- **reasoning-caused** — all gold spans were retrieved and the answer is still
  wrong.

Both channels read the same evidence, so agreement is expected to be near-blind
to retrieval-caused error. A single pooled AUROC would average across a regime
where the method cannot work and would overstate the contribution. The pooled
number is still reported, but always beside the stratified pair.

### 5.4 Disagreement as its own object of study

Independent of detection quality:

- disagreement rate by question type;
- agreement–correctness contingency table (both-agree-correct,
  both-agree-**wrong** ← the dangerous cell, disagree-one-correct,
  disagree-both-wrong);
- verification-agent resolution accuracy when it is triggered.

The both-agree-wrong cell is the honest measure of the method's blind spot and is
reported prominently rather than buried.

> **How "disagree" is actually counted — clarified 2026-09-13.** The table is built
> from each record's `agreed` field, and `agreed` is `verdict == AGREE`. So the
> "disagree" row is really **"did not agree"**: it pools **DISAGREE** (both channels
> produced figures that differ) with **UNCERTAIN** (one channel produced no figure).
> On the held-out arm A those are very different sizes — **AGREE 19, UNCERTAIN 39,
> DISAGREE 3** over 61 questions — so 39 of the 42 "disagree" rows are a channel
> abstaining, and the rate of genuine numerical disagreement is **3/61 = 4.9%**, not
> the 68.9% the "did not agree" share gives. This is the same fact RX-048 found from
> the other direction: most of what the detector ranks is abstention. Anyone reading a
> disagreement rate from this project should read it as "did not agree" unless it says
> DISAGREE.

---

## 6. Efficiency metrics (spec §33)

Per question and aggregated: wall-clock latency (p50/p95), input/output tokens by
channel, number of model calls, number of sandbox executions, retrieval time, and
verification-agent trigger rate.

**Cost needs care here, because the runtime is a free tier (decision D14).** The
dollar figure is 0.00, and reporting only that would make spec §33's cost analysis
vacuous — it would read as "dual-channel verification is free", which is true of
this deployment and false of the method. Three figures are therefore recorded:

| Figure | Meaning |
|---|---|
| `cost_usd` | actual spend: **0.00** on the free tier |
| `equivalent_cost_usd` | what the same tokens would cost at the provider's published **paid** rates — the number that transfers to another deployment |
| quota consumed | requests against the daily free-tier allowance — the constraint that actually limits throughput |

Efficiency comparisons use tokens, latency and call counts as primary, since
those are provider-agnostic; `equivalent_cost_usd` carries the monetary framing.
The free-tier zero is never presented as evidence that the method is cheap.

All detection comparisons are additionally reported **cost-matched**: a method
that wins by spending more has not been shown to be better.

---

## 7. Systems compared (spec §30)

| Arm | Description |
|---|---|
| B1 | LLM only — no retrieval, closed book |
| B2 | RAG — retrieval + natural-language reasoning |
| B3 | RAG + programmatic reasoning (PoT-style, single channel) |
| B4 | Agentic RAG — iterative retrieval, single reasoning channel |
| B5 | **Self-consistency** — n samples, one model, one modality (the SelfCheckGPT-style comparator for H1) |
| **P** | **FinVerify-AI** — full dual-channel + deterministic + consistency + verification agent |

B5 is not in the spec's list but is required: it is the comparator that makes H1
falsifiable. Without it, the claim "cross-modality disagreement detects errors"
has nothing to be better *than*. Recorded as an addition in `DECISIONS.md`.

## 8. Ablation arms (spec §31)

A full system · B −natural channel · C −program channel · D −consistency engine ·
E −verification agent · F −hybrid retrieval (semantic only) · G −deterministic
verifier · **H same-model both channels** (tests H4 / decision D1).

---

## 9. Statistical protocol

- Bootstrap 95% CIs, 10,000 resamples over questions.
- Paired comparisons on identical question sets (paired bootstrap for AUROC
  differences).
- Holm–Bonferroni across the H1–H5 family.
- **Run-to-run variance is measured, not assumed.** Since temperature is not
  settable on current models (decision D7), a 50-question sample is run n=5 times
  and the observed spread is reported. Without this, an improvement smaller than
  the noise floor could be reported as real.
- Effect sizes always accompany p-values.

---

## 10. What gets reported regardless of outcome

Per spec §46: every arm that was run appears in the results, including failures;
raw per-question outputs are committed under `experiments/runs/`; failed and
abandoned experiments are recorded in `EXPERIMENTS.md` with the reason; baseline
and proposed-system results are always distinguished; and negative results for
H1–H5 are reported with the same prominence as positive ones.

---

## 11. Known limitations of this methodology

Stated here so they reach the write-up rather than being discovered by a reviewer:

1. **Gold labels are human-validated but not infallible.** FinVerify-IND gold
   answers come from human validation (spec §16); annotator disagreement rate
   will be reported.
2. **Correctness ≠ hallucination.** A wrong answer may stem from a document
   ambiguity rather than a fabrication. "Numerical error detection" is the
   defensible claim; "hallucination detection" is the looser term the field uses,
   and the write-up should not silently trade on the gap between them.
3. **No temperature pinning** (D7) — configuration-reproducible, not
   bit-reproducible.
4. **Shared retrieval is an irreducible common-mode failure** for both channels;
   §5.3 measures it rather than resolving it.
5. **Free-tier models are weaker than frontier models.** Absolute accuracy will
   be lower than published frontier-model numbers on these benchmarks. The
   research question concerns *relative* detection performance across arms on
   identical inputs, which this does not invalidate — but absolute figures must
   be reported as free-model figures and never set beside frontier-model results
   as though the setups matched.
6. **Model identity can drift.** Free providers may re-point a model id at a new
   checkpoint without notice. The **resolved** id returned by the API is recorded
   per call, so a substitution is visible in the record rather than silent.
7. **Throughput is quota-bound**, not money-bound (D14). A full run needs an
   estimated 6,000–8,000 requests against a combined ~15,900/day allowance, so
   the evaluation must be resumable across days.
