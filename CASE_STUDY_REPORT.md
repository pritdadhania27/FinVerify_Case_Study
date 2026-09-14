# FinVerify-AI — a technical case study

Numerical question answering over Indian annual reports, with dual-channel
verification and hallucination detection.

**Status as of 2026-09-08. Five campaigns complete.** Validation (315 rows,
7 arms x 45), a held-out test evaluation run once (427 rows, 7 arms x 61, zero
provider failures), the oracle diagnostic (45), the B–F ablation (225) and arm A
rebased to the current binding (45).

**The mechanism now has support, on validation, in one family.** With arm A on
the same binding as arms B–F, the project has its first clean single-field
ablation, and **over all rows** removing the **executed-program channel** costs
the detector **+0.102 AUROC [0.028, 0.196]**, surviving Holm across the four
contrasts — while removing the *natural* channel costs +0.066 and does not. On
this evidence it is specifically the program channel that carries the signal
(§11.7). Two limits are permanent. It is **validation only**, because the test
split was spent before these arms ran, and every earlier held-out claim in this
project weakened on test. And the **committed-answers family — the one a
hallucination claim needs — holds three errors**, so it cannot corroborate the
asymmetry in either direction; both contrasts clear Holm there and the figures
are reported flagged as underpowered rather than read as a second confirmation.

**The headline detection number is a triage flag.** Arm A's risk
score reaches **AUROC 0.885** on held-out data and beats self-consistency at
**43% of its token cost** — but §11.6 shows what that number is made of: the
score is a 3-level flag (7 distinct values over 61 questions, 3 covering 93%),
and 64% of the errors it ranks (25 of 39) are the system's own abstentions, which feed the
score. Over committed answers only, held-out AUROC is **0.679 [0.500, 0.839]**,
an interval reaching chance.

**None of the spec's numbered hypotheses is supported**, and the mechanism
evidence above is an *ablation contrast*, not H1: H1 is defined against arm B5
(self-consistency), which exists only on a retired model binding, so H1, H3 and
H4 remain NOT TESTABLE on one binding. The oracle diagnostic built to settle H2
instead exposed a defect in the import path — a corrected gold answer left its
evidence anchors citing the figure the validator had rejected. What is measured cleanly, and it is the study's central result, is
that **retrieval is the entire story**: handed the right evidence the system is
correct **35 times in 37 (0.946)** against **0.400** with real retrieval, same
models and prompts. §11 reports every figure, §15 states what that does and does
not license, §13 bounds both.

Nothing here is estimated, projected or filled in from a template.

**Corrected 2026-09-13 against the committed artifacts.** The retrieval and reranking
figures in §11 are replaced by the validation-only re-measurements (RX-053, RX-054,
RX-054b, RX-035-revalidated); abstentions are 25 of the 39 held-out errors (64%), not
24 (62%); the committed-only interval's upper bound is 0.839; the test split's use is
described as it happened; and cost is stated in tokens, because no paid rate is
established. The faculty submission is `docs/FinVerify_AI_Final_Case_Study.docx`.

**Live generated results:** [`CASE_STUDY.md`](CASE_STUDY.md) is written by
`scripts/write_case_study.py` from whatever run artifacts exist at the time, and
supersedes any figure quoted here if the two disagree.

---

## 1. Introduction

A language model asked "what were Tata Motors' borrowings as at 31 March 2024?"
will answer. It will produce a specific figure, in a plausible unit, with a
fluent derivation — and when it is wrong, nothing about the output distinguishes
that case from the right one. In finance the failure is not embarrassing but
expensive, and its most common form is not invention from nothing: it is a real
number taken from the wrong year, the wrong entity, the wrong scale, or the
wrong statement.

FinVerify-AI is a research system that answers such questions through two
independent reasoning processes and measures whether their disagreement is a
usable signal that the answer is wrong.

## 2. Problem statement

Retrieval-augmented generation reduces fabrication but does not make numerical
answers trustworthy, for three reasons this project takes as given and then
tests against:

1. **Self-checking is circular.** A model asked to verify its own arithmetic
   brings the same biases to the check. Repeated sampling of one model inherits
   the systematic error rather than exposing it.
2. **Retrieval failure is invisible downstream.** If the wrong chunk is
   retrieved, a perfectly sound reasoning step produces a confidently wrong
   number, and nothing in the answer marks the difference.
3. **Financial documents are adversarial to naive extraction.** Scale stated
   once in a header and omitted from every cell beneath; `(1,234)` meaning
   −1234; footnote markers glued to numerals; the same metric for the same year
   restated differently in two reports.

**The question the whole system serves:** can independent dual-channel reasoning
and consistency verification reliably detect numerical hallucinations in agentic
financial document QA?

## 3. Objectives

1. Answer numerical questions over real annual reports with provenance — every
   figure traceable to a document, page and span.
2. Produce a continuous risk score per answer, not a label, so detection can be
   measured by AUROC rather than by accuracy on a threshold chosen after the
   fact.
3. Measure whether cross-modality disagreement beats same-model self-consistency
   at matched cost (H1), and whether each component earns its place (H2–H5).
4. Report negative results with the same prominence as positive ones.

## 4. Related approaches, and the gap

| Approach | What it does | Why it is not sufficient here |
|---|---|---|
| Plain RAG | retrieve, then answer in natural language | no check on the arithmetic; retrieval failure is silent |
| Program-of-Thought | generate and execute code | removes arithmetic slips, but a wrong operand binding executes just as cleanly |
| Self-consistency / SelfCheckGPT | sample one model n times, measure agreement | homogeneous samples share the model's biases; a confidently-held wrong belief yields consistently wrong samples |
| LLM-as-judge | a second model reviews the answer | still a language model reasoning about language; no independent computation |

**The research gap.** Each of these checks a system against *itself* — the same
model, the same modality, or the same reasoning process in a second costume.
What is missing is a check whose failure modes are structurally uncorrelated
with the first channel's: an executed program routes the arithmetic through an
interpreter that holds no beliefs about revenue. Whether that independence
survives the fact that both channels still read the *same retrieved evidence*
is an empirical question, and it is the one this project exists to answer.

*(Kept deliberately separate from the conclusion — see §15.)*

## 5. The proposed system

Two channels answer the same question without either being able to see the
other:

- **Channel A — natural language.** Reads the retrieved evidence and reasons to
  a figure in prose.
- **Channel B — program.** Writes a Python program over the same evidence. The
  program is AST-validated against an allowlist and then executed in a fresh
  container with CPU, memory and timeout limits and no network.

A **deterministic verifier** recomputes the arithmetic where operands can be
bound. A **consistency engine** compares the channels in canonical units. On
disagreement a **verification agent** arbitrates, and may abstain. A
**confidence model** emits a continuous risk score.

**Independence is the whole contribution, and it is enforced by construction.**
The program channel never receives Channel A's answer, reasoning or confidence —
there is no parameter through which it could arrive, and tests assert it. The
channels run different models from different labs (gpt-oss on Channel A,
Nemotron on Channel B), both served by NVIDIA since D44, and their prompts are
structurally disjoint. Same-model
operation exists only as ablation arm H, which is the direct test of whether
vendor diversity contributes beyond prompt and modality diversity (H4).

## 6. Architecture

```
Question
   ↓
Question Understanding (rule-based, no model call)
   ↓
Hybrid Retrieval — semantic (E5) + BM25, reciprocal-rank fusion, metadata filters
   ↓
Evidence
   ├──────────────────────────┬──────────────────────────┐
   ↓                          ↓                          │
Channel A: NL reasoning   Channel B: program → AST → Docker sandbox
   └──────────────────────────┴──────────────────────────┘
   ↓
Deterministic numerical verification
   ↓
Consistency engine  ──AGREE──→ answer
   │
   └──DISAGREE──→ Verification agent ──→ resolved | abstained
   ↓
Confidence / risk score  →  evidence-grounded answer
```

There is no edge from Channel A to Channel B. That absence is the design.

Around it: PostgreSQL for the projection the API reads, Qdrant for 22,930
indexed chunks, FastAPI, and a React dashboard. Run artifacts on disk remain the
source of truth; the database is a projection of them, one-way.

## 7. Dataset

Five Indian listed companies, FY2023-24, five sectors, 1,664 pages. Chosen for
structural diversity rather than volume — a bank's balance sheet, a
conglomerate's segment reporting and a pharmaceutical company's inventory notes
break extraction in different ways, and five IT companies would have measured
one template five times.

| | |
|---|---:|
| Filings | 5 |
| Pages indexed | 1,664 |
| Tables extracted | 2,780 |
| Indexed chunks | 22,930 |
| Questions generated and judged | 192 |
| **Validated as gold** | **115** |
| Rejected in validation | 29 |
| Left pending | 48 |
| Evidence spans | 198 |

**115 is the benchmark size, not 192.** The dataset layer refuses to serve an
unvalidated question to an evaluation. Quoting the judged total would overstate
the evidence base by 77 rows.

Every filing is a public document from the company's own investor-relations
site, with URL, retrieval date and SHA-256 recorded. The PDFs are not
redistributed; the hash lets a reader confirm the file they fetch is the file
this evaluation used.

**Validation was done by a human, and it changed things.** The validation pass
rejected 29 candidates outright and drove four separate defect fixes in the
generator (RX-026 through RX-031). The pass rate rose from 44% on the first 45
rows to 92% on the last split as those landed — the rising rate is a measure of
the generator improving, not of standards slipping.

## 8. Methodology

- **Splits** are frozen. Evaluations use the non-ambiguous validated questions:
  45 validation, 61 test, 2 train. The test set is sealed behind an access log
  requiring `FINVERIFY_ALLOW_TEST=1`. It was evaluated once, by
  `campaign_20260905T112212Z`, after a voided attempt (2026-09-03, retired model)
  and a superseded partial one (2026-09-04); it has not been used since. The
  retrieval-lane leak RX-053 found is described in `EVALUATION.md` §1.
- **Correctness** is a relative tolerance of 0.5% on canonical values, with sign
  and unit-kind treated as categorical rather than tolerable. A loss reported as
  a profit is never correct at any tolerance.
- **Detection** is measured by AUROC over a continuous risk score, with
  bootstrap confidence intervals and Holm-Bonferroni correction across
  hypotheses. An arm with no detector is absent from the detection table rather
  than entered at 0.5, which would read as a measurement.
- **Temperature is pinned at 0**, and each run records its model bindings in
  `config.json`, because free-tier providers retire model ids without notice.
  The provider-returned id is captured on each response but persisted per call
  only for the arbiter.
- **Cost** is reported as measured tokens and calls. `equivalent_cost_usd` is
  null in every report, because no provider in the registry carries a published
  paid rate, and the free tier's `cost_usd` of 0.00 is never offered as evidence
  the method is cheap.

## 9. Implementation

**Backend** — FastAPI over PostgreSQL and Qdrant. Ten endpoints, all backed by
real data: health, corpus statistics, documents, upload, ask, answers (list and
detail), verification, evidence, experiments, evaluation results. Live question
answering is **disabled by default**: each request runs both channels and can
trigger the arbiter, spending quota the campaign is paced against, and an open
endpoint is a way to lose a day of budget to a crawler.

**Sandbox** — generated code is AST-validated against an allowlist, then run in
a fresh container per execution with CPU, memory and timeout limits and no
network. If Docker is unavailable, program execution is **blocked**; it never
falls back to the host.

**Frontend** — a React dashboard: corpus statistics, document explorer with
provenance, QA interface, a verification view listing recorded answers with
their arbiter decisions, and a research view. Two rules run through it. A null
risk score renders as "not scored", never as 0.5, because arms B1–B4 have no
detector. An ungraded answer renders as "not graded", never as incorrect. And
where a metric has not been computed, the screen says *"Not yet measured"* in
words rather than showing a zero that reads like a result.

## 10. Experimental setup

Seven arms, of which four are running:

| arm | configuration | tests |
|---|---|---|
| **A** | full system — both channels, deterministic verifier, arbiter | the proposal |
| **B3** | RAG + program channel only | PoT-style baseline |
| **B5** | self-consistency, 5 samples, one model one modality | H1's comparator |
| **G** | A minus the deterministic verifier | H3 |
| **H** | same model on both channels | H4 |

**The binding constraint is quota, not money** — 200,000 Groq tokens a day, and
one question across the four campaign arms costs about 51,000 of them, of which
B5's five samples alone are 26,304. That is roughly five questions a day, and it
is why the campaign is a week of wall-clock rather than an afternoon.

That constraint shaped a real decision. **B3 was run and B2 was not.** B3 is the
program channel alone, which runs on NVIDIA — not the exhausted provider — and
question understanding is rule-based, so a B3 row makes exactly one API call and
none of it is Groq. It ran while the campaign was blocked, at zero cost to it.
B2 is plain RAG on the natural channel, and it must use arm A's natural model or
the comparison confounds architecture with model choice, which puts it on the
provider that is already the bottleneck. It waits.

## 11. Results

Two complete campaigns: **validation** (`campaign_20260901T105355Z`, 315 rows,
7 arms x 45 questions, RX-038) and **test** (`campaign_20260905T112212Z`, 427
rows, 7 arms x 61 questions, RX-039), the latter evaluated once and never
revisited. Zero provider failures on either channel in the test run.

### Headline — the held-out numbers

| | validation | **TEST (held out)** |
|---|---:|---:|
| Arm A detection AUROC | 0.907 | **0.885** [0.801, 0.954] |
| Arm A numerical accuracy | 0.400 | 0.361 |
| Plain RAG (B2) accuracy | 0.400 | 0.328 |
| Self-consistency (B5) AUROC | 0.600 | 0.606 |
| B5 token cost vs arm A | 2.4x | **2.3x** |

**Detection generalises.** Nothing was tuned between the two runs; AUROC moved
by 0.022, well inside its interval.

### Question answering, test split (n=61)

| arm | accuracy | abstains |
|---|---:|---:|
| **A** (full system) | **0.361** | 0.410 |
| **H** (same model both channels) | **0.361** | 0.377 |
| G (no deterministic verifier) | 0.344 | 0.410 |
| B2 (plain RAG) | 0.328 | 0.426 |
| B5 (self-consistency) | 0.328 | 0.475 |
| B4 (agentic RAG) | 0.311 | 0.443 |
| B1 (closed book) | **0.000** | 1.000 |

Arm A leads plain RAG by 0.033 — **two questions out of 61**, far too small to
be a claim. On validation the five retrieving arms returned identical answers;
here they separate slightly. Reported as a first sign, not as evidence.

B1 abstains on all 61. Closed-book has no access to the filings' numbers and
declines rather than inventing them, which is the correct behaviour and the
cleanest baseline result in the study.

### Detection, test split

| arm | AUROC | 95% CI | precision | recall | F1 |
|---|---:|---|---:|---:|---:|
| **A** | **0.885** | [0.801, 0.954] | 0.810 | 0.872 | 0.840 |
| H | 0.857 | [0.749, 0.945] | 0.914 | 0.821 | 0.865 |
| G | 0.855 | [0.736, 0.947] | 0.800 | 0.900 | 0.847 |
| B5 | 0.606 | [0.479, 0.736] | — | — | — |

Arm A catches **34 of 39** wrong answers and wrongly flags 8 of 22 correct ones.
B1, B2 and B4 produce no risk score and are absent by design rather than entered
at AUROC 0.5, which would read as "detects nothing" instead of "is not a
detector". B5's threshold columns are omitted: the operating point was selected
on arm A's score distribution and B5's is on a different scale.

Threshold-dependent columns carry one caveat. 0.51125 was frozen on validation
before any test data existed — no leakage in sequence — but that validation ran
on `gpt-oss-120b`, which the provider retired mid-project (D46), while the test
ran on `gpt-oss-20b`. **AUROC is threshold-free and unaffected**; precision,
recall and F1 are indicative until validation is re-run on the current binding.

### The hypotheses — none supported

| | comparison | point | 95% CI | p | after Holm |
|---|---|---:|---|---:|---|
| H1 | AUROC(A) − AUROC(B5) | +0.159 | [−0.034, 0.338] | 0.100 | not significant |
| H3 | AUROC(A) − AUROC(G) | +0.030 | [−0.062, 0.136] | 0.580 | not significant |
| H4 | AUROC(A) − AUROC(H) | +0.027 | [−0.068, 0.139] | 0.641 | not significant |

Every interval crosses zero. **The full system cannot be distinguished from its
own ablations at n=61.** All three point estimates now favour the full system,
where H3 and H4 favoured the ablations on validation — consistent with a mild
real effect, and equally consistent with noise at this sample size.

### H2 — the mechanism check, testable at last, and it fails

Validation had **no** reasoning-caused errors, so this could not run. The test
split has four:

| stratum | questions | errors | AUROC |
|---|---:|---:|---:|
| retrieval-caused | 32 | 27 | **0.837** |
| reasoning-caused | 21 | 4 | **0.787** |
| unknown | 8 | 8 | undefined |

H2 predicted detection would be *far better* on reasoning-caused errors, because
that is the failure dual-channel disagreement exists to catch. It is **worse**.

**These are the post-repair figures (RX-044).** RX-039 reported the
retrieval-caused stratum as n=40, 35 errors, AUROC **0.851**, computed before the
anchor repair landed the following day. The repair dropped the stale anchors from
12 test rows, and the stratifier reads evidence rather than answers, so 8
questions became *undecidable* and left the stratum. Nothing else in the run
moved — pooled AUROC, accuracy, precision, recall, F1 and every hypothesis are
bit-identical. The 8 are all errors and are held in `unknown` rather than
assigned: folding them into reasoning-caused is the direction that would make H2
look supported, which is precisely why the analyser refuses to do it.

H2's own rationale sets the consequence: a positive H1 with a failed H2 is an
unexplained correlation, not a validated design. H1 is not positive either, so
the reading is plainer still — **the risk score ranks errors well, and this study
provides no evidence that it does so by the mechanism the system was built
around.** Four errors is a small denominator and the comparison is unpaired; it
is not a strong negative, but it is the opposite of the predicted direction.

### Oracle retrieval — retrieval is the entire story (RX-043)

H2 cannot be tested end to end because reasoning errors are too rare. Arm O
removes the retrieval term by handing each question the chunks containing its
gold evidence, so every remaining error is a reasoning error by construction.

This is the second run of that arm. The first (RX-040) resolved its evidence
through anchors that cited figures the validator had rejected — a defect in the
import path, recorded as RX-042 in [`EXPERIMENTS.md`](EXPERIMENTS.md) — and so
handed the channels the wrong chunk on up to 10 of 43 questions. Those anchors were dropped and the arm re-run.

Restricted to the **37 questions the oracle built for**:

| | arm A, real retrieval | **arm O, oracle** |
|---|---:|---:|
| numerical accuracy | 0.400 | **0.946** |
| errors | 27 of 45 | **2 of 37** |
| blind spot (agreed and wrong) | 1 of 14 | **1 of 33** |

**Handed the right evidence, the system answers correctly 35 times in 37.**
Same models, same prompts, same temperature — the only change is what reaches
the reasoner. **Every accuracy figure in this study is a retrieval measurement.**

**What the repair changed**, and why the first run's conclusion was withdrawn:

| | stale anchors | repaired |
|---|---:|---:|
| accuracy on built questions | 0.860 | **0.946** |
| errors | 6 | **2** |
| blind spot | 5 of 33 | **1 of 33** |

Four of the six "reasoning errors" were the oracle's own wrong chunk. The
blind-spot count falls for the same reason: those were both channels correctly
reading a chunk that was wrong, which is indistinguishable from a shared
misreading and is not one.

**The two remaining errors.** One is an abstention the detector flagged (risk
0.551). One is the failure D1 exists to worry about — both channels returning
25,018.28 against gold 12,798.00, agreeing, risk 0.0. **n=1.** The error
analyser declines to call either a confirmed reasoning error, because question
understanding and extraction were not separately checked.

**H2 is unreachable on a benchmark this size, and the arithmetic says by how
much.** At P(error | correct evidence) = 2/37 = 0.054, a stratum of 20 reasoning
errors needs ~370 questions *with retrieval working*, and nearer 1,000 end to
end. This benchmark has 45. That is a property of the system being accurate, not
a flaw in the experiment.

The pooled all-45 AUROC of 0.943 is not quoted: it spans the 8 questions whose
anchors the repair dropped, which the oracle handed nothing — 0 of 8 correct and
all high-risk, inflating AUROC for a reason unrelated to reasoning.

### Cost — the claim that replicates

| arm | AUROC | tokens/question |
|---|---:|---:|
| B1 | — | 526 |
| B2 | — | 2,466 |
| B4 | — | 2,485 |
| H | 0.857 | 5,391 |
| **A** | **0.885** | **6,445** |
| G | 0.855 | 6,464 |
| **B5** | **0.606** | **14,816** |

**Self-consistency costs 2.3x the full system and detects far worse** — 14,816
tokens for AUROC 0.606, against 6,445 for 0.885. Validation measured 2.4x in the
same direction. This is the only quantitative claim here that holds both its
size and its sign out of sample.

### A validation finding that did not replicate

Arm H's blind-spot rate — both channels agreeing and both wrong — was twice arm
A's on validation (0.150 against 0.071), the direction D1 predicts for same-model
channels corroborating each other's mistakes. On test the three arms are
indistinguishable: A 0.263, G 0.250, H 0.269. **The signal did not survive.**
Reporting the validation figure alone would have been reporting noise, and this
is the ordinary reason held-out evaluation exists.

### Supporting results

**B3, single-channel program reasoning (n=45, RX-037).** Numerical accuracy
0.289 [0.156, 0.422]; accuracy *when it answers* 0.929 (13 of 14); abstains
0.489. Not a weak reasoner but a precise one that declines most of the time —
the retrieval limitation seen from the far end of the pipeline. Flat across
τ=0.001–0.01.

**Retrieval, on validation-only gold (RX-054b).** RX-034's rank decomposition ran
on gold RX-053 found to be 57% test-derived, so its figures are withdrawn. Over
32 validation-only evidence groups, hybrid retrieval corpus-wide:

| in top 10 | ≤20 | ≤70 | ≤300 | not ranked within 300 |
|---:|---:|---:|---:|---:|
| 0.125 | 0.156 | 0.406 | 0.719 | 0.281 |

Most of the evidence is reachable but deep, and 28% is not ranked within 300 at
all, which no reranker over a candidate pool can reach.

**Reranking: the original negative result did not reproduce.** RX-035-original
reported a cross-encoder at 0.240 against a 0.280 baseline on the mixed gold, and is
invalidated. On the clean gold the same script scores **0.156 against 0.125**, net +1
of 32 groups, an effect indistinguishable from zero (RX-035-revalidated). Reranking
stays unadopted, now because there is no measurable benefit and it costs 24.9 s per
evidence group on this CPU.

### 11.6 What AUROC 0.885 is actually made of (RX-048)

The headline number is correctly computed and it describes the artefact badly.
Two properties, both recoverable from the committed run artifacts at zero cost,
neither reported until they were looked for.

**The score is a three-level flag.** Arm A's risk score takes **7 distinct values
over 61 held-out questions, and 3 of them cover 57 (93%)**:

| risk | n | errors | error rate | facet signature |
|---|---:|---:|---:|---|
| 0.0 | 19 | 5 | 0.263 | everything VERIFIED |
| 0.51125 | 14 | 6 | 0.429 | agreement PARTIAL, evidence PARTIAL |
| 0.550625 | 24 | **24** | **1.000** | agreement PARTIAL, evidence PARTIAL |
| 4 singletons | 4 | 4 | 1.000 | agreement FAILED |

`EVALUATION.md` §5.1 — written before Module 15 existed — forbids precisely this:
*"The confidence module must emit a continuous score, not only HIGH/MEDIUM/LOW.
AUROC is undefined over three ordinal buckets in any useful sense."* The module
emits a float, so the constraint passes on inspection and fails in fact. RX-007
caught the identical defect in arm **B5** and the check was never applied to the
primary arm.

Note that rows two and three share an **identical facet signature** yet differ in
error rate 0.429 against 1.000 — the separation comes from a continuous sub-term
(`coverage`), not from the four facets the system reports. The score carries real
signal; it does not carry it where the output says it does.

**And the always-wrong bucket is the system abstaining.** That third row is 21 of
21 wrong on validation and 24 of 24 on test — perfect prediction, replicated
across two splits and two Channel A models. It is the abstention bucket: 20 of
21 and 24 of 24 of those rows declined to answer. §3 grades an abstention as
incorrect, and abstention is itself an *input* to the risk score (`coverage`,
`usable_channels`), so the detector substantially detects the system's own
refusal, which the grader then counts against it — **25 of the 39 held-out errors
(64%)**: the 24 in this bucket and one abstention scored 0.55625.

**The number that answers the research question.** A numerical hallucination is a
*committed wrong figure*; a refusal is its opposite. Restricted to answers the
system committed to:

| arm A | all rows (as §5.1 specifies) | committed answers only |
|---|---:|---:|
| validation | 0.907 | **0.802** [0.591, 0.960] |
| **TEST (held out)** | **0.885** [0.801, 0.954] | **0.679 [0.500, 0.839]** |

**The held-out interval reaches 0.500 — chance.** Arms G (0.692) and H (0.717)
land in the same band, so this is a property of the approach rather than of one
arm. Among committed answers the score takes two values on 33 of 36 questions.

**What this does and does not do to the result.** Nothing is retracted: every
published figure was computed exactly as the frozen methodology specifies, and
0.885 stays valid as a **deployment triage** number — *should I trust this
output?* — because the abstentions it flags genuinely are outputs not to trust.
It is simply not a hallucination-detection number, and the research question asks
for the latter. The committed-only figure carries its own weakness, stated so it
is not oversold: it conditions on abstention, a post-treatment variable driven by
the same evidence quality that drives correctness, so it is a selection effect
and possibly a collider. Neither is a clean causal estimand. Both belong in the
report, labelled.

It also explains the operating threshold. D45 froze 0.51125 because all three
candidate objectives landed on one point, *"which is what makes it a plateau
rather than a value fitted to noise"*. With three mass points there are only
about two distinguishable cut points, so agreement between objectives is
**arithmetic, not robustness**. The operating point means exactly *"flag unless
all four facets verified"*, which reproduces the reported precision and recall to
the digit on both splits.

### 11.7 The clean ablation, and the one result that survives correction (RX-049)

Every ablation arm is *"arm A minus exactly one field"*, so each is interpretable
only against arm A — and until 2026-09-08 the arms B–F had run on
`gpt-oss-20b` while arm A had only ever run on `gpt-oss-120b`, which the provider
retired. Re-running arm A on the current binding gives the project its **first
genuine single-field ablation**: one binding, one split, one set of 45 questions.

**The swap changed the risk score but not one answer.**

| | arm A on gpt-oss-120b | arm A on gpt-oss-20b |
|---|---|---|
| correct answers | 18 | **the identical 18** |
| detection AUROC | 0.907 | **0.981** |

Nine arm-configurations across two models return the same 18 questions. So the
confound was **null on accuracy and real on the risk score** — which is what
every hypothesis is tested on. `analyse_campaign.py` now refuses to pool runs
whose split or bindings differ, because the confounded table looks entirely
reasonable.

| arm | removes | accuracy | AUROC | 95% CI |
|---|---|---:|---:|---|
| **A** | nothing | 0.400 | **0.981** | [0.951, 1.000] |
| B | the natural channel | 0.311 | 0.916 | [0.817, 0.986] |
| **C** | **the program channel** | 0.400 | **0.880** | [0.780, 0.963] |
| D | consistency engine + arbiter | 0.400 | — | no risk score by construction |
| E | the verification agent | 0.400 | 0.952 | [0.883, 0.995] |
| F | hybrid retrieval → semantic only | 0.178 | 0.983 | [0.946, 1.000] |

Paired over the same questions, Holm-corrected across the family:

| contrast | all rows (27 errors) | **committed answers only (3 errors)** |
|---|---|---|
| A − B (minus natural) | +0.066 [−0.004, 0.164] p=0.082 | +0.417 [0.233, 0.567] · underpowered |
| **A − C (minus program)** | **+0.102 [0.028, 0.196] p=0.0020** | +0.361 [0.235, 0.500] · underpowered |
| A − E (minus arbiter) | +0.030 [−0.012, 0.091] p=0.238 | +0.259 [−0.032, 0.605] p=0.108 |
| A − F (minus hybrid) | −0.002 [−0.037, 0.039] p=0.873 | −0.102 [−0.318, 0.118] p=0.272 |

**Over all rows — the only adequately powered family — A − C survives Holm and
nothing else does.** That asymmetry is the finding: removing the *natural*
channel does not survive correction, removing the *program* channel does. On this
evidence it is not "two channels beat one" but specifically the executed program
carrying the detection signal, which is this project's thesis. Arm A's blind spot
on this binding is **0 of 12** agreed answers.

**The committed-only family cannot corroborate it, and says so.** A numerical
hallucination is a *committed* wrong figure (§11.6), so that family is the one
the research question is really about — and it contains **three errors**. Both
A − B and A − C clear their Holm thresholds there, A − B by the larger margin. An
AUROC ranks errors above correct answers, so three errors cannot separate two
arms whatever the p-value reads; the figures are reported flagged rather than
withheld. **The supported claim lives in the all-rows family, where most of what
is flagged is the system's own abstention.** Earlier drafts of this table carried
+0.139 for A − B committed and concluded that nothing but A − C survived
anywhere; that cell was a transcription error from a scratch script, corrected in
RX-050 and now generated by `evaluation/ablation/contrasts.py` under test.

**The limits, and they are permanent.** This is **validation only**: the test
split was spent on 2026-09-05 and these arms ran afterwards, so they can never be
held out — and every earlier held-out claim in this project weakened on test.
n=45, and 21 for the committed contrast. Part of the effect is structural, since
arm C has one reasoning channel by construction — which is what an ablation is
for, but it means the result reads *"the second channel contributes detection
signal"*, not *"cross-modality specifically does"*.

## 12. Error analysis

The instructive finding so far is about *where* the errors were. Of the defects
found on this project, the large majority were in gold data, graders, or the
measuring instruments — not in the system under test. Three worth reading:

- **RX-031 — the grader punished completeness.** The correctness predicate
  treated "unit unstated" as a dimension of its own, so a gold of `8,415.03
  crore` rejected a prediction of `8,415.03 INR crore` — the most complete
  answer a model can give — while accepting the barer form. It affected 44 of
  115 gold answers and would have shipped as a units-failure rate: a believable
  finding about financial LLM reasoning, manufactured entirely by a label
  format.
- **RX-033 — a campaign that reported success while dead.** A run reported
  `completed 180, failed 0` with Channel A dead for 166 of them, because two
  quota exceptions were unrelated classes and the campaign caught only one.
- **RX-036 — a refusal counted as a bug.** The program channel prints
  `{"value": null}` when evidence is insufficient and records that as an
  abstention; nothing downstream read it. Invisible for fourteen runs because
  every prior arm had a natural channel that abstained on the same evidence.
  It surfaced on the first arm that had none.

The system-level error taxonomy (wrong scale, wrong year, wrong entity, wrong
statement, retrieval-caused vs reasoning-caused) is implemented and will be
populated by the campaign.

## 13. Limitations

Stated plainly, because each one bounds what any eventual result can claim.

1. **The benchmark is single-hop.** Of the 45 usable validation questions, **44
   are lookups and one is multi-hop**. Whatever this system turns out to do, it
   will have been demonstrated almost entirely on single-figure retrieval and
   comparison. No conclusion about complex multi-step financial reasoning is
   supported.
2. **Retrieval is the weakest link.** At 0.08–0.56 evidence accuracy, there are
   questions where neither channel can be right, and on those their agreement
   measures nothing.
3. **The channels share a retrieval step and a question interpretation.** That
   is an irreducible common-mode failure surface. It is measured by
   stratification, not solved.
4. **Free-tier models.** Absolute figures are free-tier figures and must never
   be placed beside published frontier-model results as though the setups
   matched.
5. **Sample size.** 45 validation questions gives wide confidence intervals; 61
   test answers will not narrow them much. This is a case study, not a
   benchmark paper.
6. **Single human validator.** A second independent pass on a seeded subset is
   designed and not yet run.
7. **NVIDIA latency is not reportable** — identical prompts returned in 0.42s
   and 12.5s, and the endpoint sheds load with HTTP 503. Token and request
   counts are unaffected.
8. **The operating threshold was sourced from a different model.** 0.51125 was
   frozen on validation before any test data existed, so there is no leakage in
   sequence — but that validation ran on `gpt-oss-120b`, which the provider
   retired mid-project, and the test ran on `gpt-oss-20b`. AUROC is
   threshold-free and unaffected; precision, recall and F1 are indicative until
   validation is re-run on the current binding.
9. **Channel independence is same-provider.** Since D44 both channels are served
   by NVIDIA, bound to models from two different labs. Different corpora,
   architectures and failure modes are preserved; correlated availability and
   any shared edge transformation are not. The second is unmeasured and is the
   real cost.
10. **The mechanism is unvalidated, not refuted.** H2 fails on 4 reasoning-caused
    errors — a very small denominator, unpaired across different question sets,
    and with no interval on the difference. It is evidence against the mechanism
    story, not proof the mechanism is absent.

## 14. Future scope

- Complete the campaign and fix the operating threshold on validation before the
  single sealed test evaluation.
- Attack retrieval ordering with something other than an MS MARCO cross-encoder
  — the passages here are financial tables and the model is trained on web prose.
  25 of 100 evidence groups never enter the candidate pool at all, which is a
  recall problem no reranker addresses.
- Run B2 once quota allows, to support a "better than plain RAG" claim the
  current arms cannot make.
- Widen the multi-hop share of the dataset, which is the limitation that most
  constrains what the work can say.
- Teach the rate limiter the provider's rolling window; it currently models a
  calendar day and gives up roughly a fifth of the daily allowance.

## 15. Conclusion

*(What was demonstrated — deliberately separate from the research gap in §4.)*

Both campaigns are complete, the test split was evaluated once on 61 held-out
questions across seven arms, and an oracle-retrieval diagnostic then isolated
the reasoning step. The answer to the project's own research question is **no**,
and unlike a null result it comes with the mechanism attached.

### What the evidence supports

**The detector works and it generalises.** Arm A's risk score reaches AUROC
**0.885** [0.801, 0.954] on held-out data against 0.907 on validation, with
nothing tuned between the two. It catches 34 of 39 wrong answers. A verifier
that ranks its own errors this well is a useful artefact independent of why it
works.

**It beats self-consistency on both axes at once.** 6,445 tokens per question
for AUROC 0.885, against 14,816 for 0.606 — better detection for 43% of the
cost. Validation measured 2.4x and test 2.3x. This is the only quantitative
claim in the study that holds its size *and* its sign out of sample, and B5 is
the SelfCheckGPT-style comparator the design was written to beat.

**Retrieval is the binding constraint.** On validation-only gold the right chunk
reaches the top 10 for 4 of 32 evidence groups and is not ranked within 300 for 9
(RX-054b). Cross-encoder reranking, re-run on that gold, moved one group net and
is not adopted (RX-035-revalidated). B3 says the same from the far end — the program channel is right on 13
of the 14 questions it commits to and declines on nearly half.

### What the evidence does not support

**No hypothesis is supported.** H1 p=0.100, H3 p=0.580, H4 p=0.641; every
interval crosses zero and none survives correction. At n=61 the full system
cannot be distinguished from its own ablations. Removing the deterministic
verifier, or putting one model on both channels, produces detection this study
cannot tell apart from the complete design.

**The mechanism is not demonstrated, and the attempt to settle it found
something else.** H2 was untestable on validation and failed on test (0.787 on
reasoning-caused errors against 0.837 on retrieval-caused), both on tiny strata.
Arm O was built to settle it and instead showed the question is out of reach.
Handed correct evidence the system makes **2 errors in 37**, so
P(error | correct evidence) = 0.054 and a stratum of 20 reasoning errors would
need **~370 questions with retrieval working** — nearer 1,000 end to end,
against this benchmark's 45. The one clean instance of the failure D1 worries
about (both channels returning 25,018.28 against gold 12,798.00, agreeing, risk
0.0) is **n=1**.

Arm O also exposed two defects that had been invisible to every metric. The
import path left corrected answers pointing at the anchors they had superseded,
which is what made the arm's first run wrong (RX-042). And two components are
inert: the deterministic verifier is applicable on 1 question of 45 and produced a
value on none — it abstains on
lookups and 44 of 45 are lookups — which is why arm G scores the same as arm A
and why H3 tests nothing; and `scale` is `None` on every evidence block, so the
`unit` facet reports VERIFIED on every error.

**A validation finding did not replicate.** Arm H's blind-spot rate was twice
arm A's on validation, in exactly the direction D1 predicts for same-model
channels corroborating each other's mistakes. On held-out data the three arms
are indistinguishable. Had the project stopped at validation, that number would
have been reported as support for its central design decision, and it would have
been noise.

### The honest summary

This built a numerical-answer verifier whose risk score ranks errors well and
cheaply. **Whether it does so because of dual-channel disagreement is not
established**, and three attempts to establish it — H2 on validation, H2 on
test, and the oracle diagnostic — each ran out of measurable errors before they
ran out of method.

What is established, and cleanly: **retrieval is the binding constraint.**
Supplying the evidence takes accuracy from **0.400 to 0.946**, with nothing
about the reasoner changed. Every claim this project can make about reasoning is
bounded by the fact that its reasoner sees the right evidence about a third of
the time — and by the fact that when it does, it is almost never wrong.

The bound on the rest is honest and unflattering: 45 and 61 questions, strata of
four to six errors, intervals wide enough to contain almost anything, a
single-hop benchmark, and gold data that contradicts itself on roughly one
lookup in ten. The architecture is neither validated nor refuted. Saying which
would need a benchmark whose errors are reasoning errors and whose labels agree
with their own evidence, and §14 sets out what that would take.

### A methodological result, learned repeatedly and at cost

In a system like this **the instruments fail more often than the subject**, and
a green test suite says nothing about whether the thing being measured is the
thing you think.

- 1,311 tests passed while the evidence matcher could not match a number: gold
  anchors stored `12232`, filings print `12,232`, and the matcher preserved
  commas by design. It reported evidence retrieved on 1 of 45 questions where
  the true figure was 13, and emptied the stratum H2 is tested on. The tell was
  17 questions answered correctly while the predicate claimed their evidence had
  never been retrieved.
- Every efficiency figure the project ever produced was 0.0, because the reader
  looked for a key the recorder never wrote. It failed *politely* — its own note
  said the zero was "by absence rather than by measurement", which reads as
  scrupulous and was false.
- A provider retired Channel A's model mid-project, with two days' notice and
  none given. `/models` kept listing it for hours after it began returning 410.
  A test run reached row 22 against it before this was noticed.

Every significant defect here was found by **running** the system, never by
testing it. The safeguards that now exist — a live per-binding preflight before
any campaign spends, `NOT TESTABLE` as a first-class verdict, run artifacts
marked VOID rather than deleted — are all retrofits paid for by a failure.

---

*Generated results, regenerated from run artifacts:*
[`CASE_STUDY.md`](CASE_STUDY.md) · *experiment log:*
[`EXPERIMENTS.md`](EXPERIMENTS.md) · *live walkthrough:* [`DEMO.md`](DEMO.md)
