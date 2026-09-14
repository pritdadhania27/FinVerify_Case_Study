# Project Status

**Updated:** 2026-09-13

> ## THE PROGRAM CHANNEL EARNS ITS PLACE — THE FIRST SUPPORTED RESULT (RX-049)
>
> Arm A has been re-run on the current binding (`campaign_20260908T164126Z`, 45
> rows, 0 failed), giving this project its **first genuine single-field
> ablation**: one binding, one split, one set of 45 questions.
>
> **The model swap changed the risk score but not one answer.** Predicted in
> RX-047, now measured: arm A on gpt-oss-120b and on gpt-oss-20b return the
> **identical 18 correct questions**, while detection AUROC moves **0.907 →
> 0.981**. Nine arm-configurations across two models, one answer set. So the
> confound was null on accuracy and **real on the risk score** — which is what
> every hypothesis is tested on. Refusing to pool the campaigns was right, and
> this is the proof rather than the assumption.
>
> | arm | removes | accuracy | AUROC |
> |---|---|---:|---:|
> | **A** | nothing | 0.400 | **0.981** |
> | B | the natural channel | 0.311 | 0.916 |
> | **C** | **the program channel** | 0.400 | **0.880** |
> | D | consistency engine + arbiter | 0.400 | — |
> | E | the verification agent | 0.400 | 0.952 |
> | F | hybrid retrieval → semantic only | 0.178 | 0.983 |
>
> Paired, with Holm across the family:
>
> | contrast | all rows (27 errors) | **committed answers only (3 errors)** |
> |---|---|---|
> | A − B (minus natural) | +0.066 [−0.004, 0.164] p=0.082 | +0.417 [0.233, 0.567] · underpowered |
> | **A − C (minus program)** | **+0.102 [0.028, 0.196] p=0.0020** | +0.361 [0.235, 0.500] · underpowered |
> | A − E (minus arbiter) | +0.030 [−0.012, 0.091] p=0.238 | +0.259 [−0.032, 0.605] p=0.108 |
> | A − F (minus hybrid) | −0.002 [−0.037, 0.039] p=0.873 | −0.102 [−0.318, 0.118] p=0.272 |
>
> **Over all rows — the only adequately powered family — A − C survives Holm and
> nothing else does.** That asymmetry is the point: removing the *natural* channel
> does not survive correction, removing the **executed program** does, so on this
> evidence the program channel carries detection signal the natural channel does
> not. It is the first measurement in the project that separates the thesis from
> noise at all. Arm A's blind spot on this binding is **0 of 12** agreed answers.
>
> **The committed-only family cannot corroborate it.** It holds **three errors**,
> and an AUROC ranks errors — so although both A − B and A − C clear Holm there
> (A − B by more), nothing follows from it in either direction. That family is the
> one the research question is really about, which is the honest limit of the
> result. Corrected in RX-050: an earlier scratch-script table put A − B committed
> at +0.139 and concluded nothing but A − C survived anywhere.
>
> **The limits are severe and permanent.** This is **validation only** — the test
> split was spent on 2026-09-05 and these arms ran after it, so they can never be
> held out, and every earlier held-out claim in this project weakened on test. It
> is **not H1**, which is defined against arm B5 on the retired binding; H1, H3
> and H4 remain NOT TESTABLE here. n=45, and 21 for the headline contrast, whose
> interval is [0.235, 0.500]. And part of the effect is structural: arm C has one
> reasoning channel, so it has less information by construction — which is what an
> ablation is for, but it means the result reads *"the second channel contributes
> detection signal"*, not *"cross-modality specifically does"*.

> ## THE HEADLINE NUMBER MEANS LESS THAN IT LOOKS (RX-048)
>
> **AUROC 0.885 is correctly computed and it is not the answer to the research
> question.** Two properties of the risk score, both measurable from committed
> artifacts at zero cost, neither ever reported:
>
> **1. The score is a three-level flag.** Arm A takes **7 distinct values over 61
> held-out questions, 3 of them covering 57 (93%)**. `EVALUATION.md` §5.1 —
> written before Module 15 — forbids exactly this: *"The confidence module must
> emit a continuous score, not only HIGH/MEDIUM/LOW. **AUROC is undefined over
> three ordinal buckets in any useful sense**."* The module emits a float, so the
> constraint passes on inspection and fails in fact. RX-007 caught this same
> defect in **B5** and nobody applied the check to the primary arm.
>
> **2. 64% of what it "detects" is the system's own abstention** (25 of 39 errors; corrected from 24 on 2026-09-13). The
> always-wrong bucket (21/21 validation, 24/24 test) is **the abstention
> bucket** — 24 of 24 held-out rows in it declined to answer. Abstention is
> graded as an error *and* is an input to the risk score (`coverage`,
> `usable_channels`), so the detector substantially detects the system's own
> refusal, which the grader then counts against it.
>
> **The number that answers the research question.** A numerical hallucination is
> a *committed wrong figure*; a refusal is the opposite of one. Restricted to
> committed answers:
>
> | arm A | all rows (as §5.1 specifies) | **committed answers only** |
> |---|---:|---:|
> | validation | 0.907 | **0.802** [0.591, 0.960] |
> | **TEST (held out)** | **0.885** [0.801, 0.954] | **0.679 [0.500, 0.839]** |
>
> **The held-out interval reaches chance.** Arms G (0.692) and H (0.717) land in
> the same band, so it is a property of the approach, not of one arm.
>
> **Nothing is retracted.** Every figure was computed exactly as the frozen
> methodology specifies, and 0.885 remains valid as a *deployment triage* number —
> the abstentions it flags are genuinely outputs not to trust. What changes is the
> interpretation, and the write-up must carry both numbers labelled. The
> committed-only figure has its own weakness, stated so it is not oversold: it
> conditions on abstention, a post-treatment variable driven by the same evidence
> quality that drives correctness.
>
> It also explains **D45's "plateau"**: with three mass points there are only
> about two distinguishable cut points, so all three selection objectives landing
> together is arithmetic, not robustness. The operating point means exactly *"flag
> unless all four facets verified"*.

> ## THE ABLATION IS COMPLETE, AND ITS ARMS CANNOT BE COMPARED TO ARM A (RX-047)
>
> `campaign_20260906T221521Z` — **225 rows, arms B C D E F × 45 validation
> questions, 0 failed.** The gap the audit named is closed.
>
> | arm | removes | accuracy | abstains | accuracy when it answers |
> |---|---|---:|---:|---:|
> | B | the natural channel | 0.311 | 0.533 | 0.667 |
> | C | the program channel | **0.400** | 0.489 | 0.783 |
> | D | consistency engine + arbiter | **0.400** | 0.467 | 0.750 |
> | E | the verification agent | **0.400** | 0.511 | 0.818 |
> | F | hybrid retrieval → semantic only | **0.178** | **0.711** | 0.615 |
>
> **C, D and E return the byte-identical 18 correct questions** — and so do arms
> A, B5, G, H and B2 **on the other Channel A model**. Every arm that keeps the
> natural channel returns the same answers regardless of what else is removed and
> regardless of which model runs it. The mechanism reproduces RX-038 exactly on
> the new binding: **13 of 13 correct where retrieval delivered every gold group,
> 2 of 25 where it did not, 0 complete-evidence failures.**
>
> **Arm F degrades safely.** It still receives 8 blocks — it retrieves *worse*,
> not *less* — and its collapse is **abstention** (0.489 → 0.711), not
> fabrication. When it commits it is still right 0.615 of the time.
>
> **But every arm is "A minus one field", and the A they need ran on a different
> model.** `campaign_20260901T105355Z` used gpt-oss-120b; this one used
> gpt-oss-20b, and 120b reached end of life (D46). So no same-binding A-vs-B–F
> comparison exists. On *accuracy* the confound is empirically null (the identical
> 18); on the *risk score* it is unproven. **`RUN_ARM_A_REBASE.bat` closes it for
> 135 requests.**
>
> The arbiter fired **once in 225 rows** — B and C each lack a channel, D and E
> disable it — so RX-045's fix is still unmeasured. All 225 rows **do** carry an
> `explanation`, which is Module 16's missing acceptance evidence.

> ## THE MODULE AUDIT: 21 COMPLETE, 11 PARTIAL, 1 BLOCKED — AND THREE THINGS IT FOUND
>
> Audited 2026-09-06 against the spec §43 checklist. This file had been claiming
> *"Nothing is `COMPLETE` … there has not been [a run]"* since before the first
> campaign finished, and six module rows still read **"Not yet run"** about arms
> that had run twice. Three campaigns exist: validation (315 rows), test (427),
> oracle (45).
>
> Promoting the stale rows was the small half. The audit found three things that
> were not previously written down anywhere:
>
> **1. Five of the eight ablation arms have never been run.** ~~Every campaign
> ran A, G and H plus the five baselines.~~ **CLOSED 2026-09-07** by
> `campaign_20260906T221521Z` (225 rows, RX-047). The row had read *"Not yet
> run"* while three campaigns had run, and that stale label hid a live gap for
> five days. Running it then exposed the next layer: the five new arms are on a
> different Channel A model from arm A, so they still cannot be compared to it.
>
> **2. The test split was evaluated against a freeze that had been voided two
> days earlier** (D48). D46 set four preconditions; steps 1–3 — re-run validation
> on the new binding, re-select the threshold, cut `methodology-freeze-v2` — were
> never done, and step 4 went ahead on 2026-09-05. All seven access-log entries
> name the voided tag. **AUROC 0.885 and every between-arm comparison survive
> intact** (threshold-free, and all arms shared one Channel A); precision, recall,
> F1 and FPR at 0.51125 are **indicative**. The threshold is deliberately *not*
> re-selected: it was chosen before any test row existed, so it carries no
> leakage today, and re-choosing it now with the results read would add some.
> `run_campaign.py` now refuses `--split test` without a live freeze tag — the
> check that was missing, and worth more than the entry recording its absence.
>
> **3. The arbiter always saw the natural channel first** (RX-045). Chasing
> Module 13's last unmeasured acceptance item — resolution accuracy, which
> `EVALUATION.md` §5.4 has asked for since the methodology was written, which
> cost nothing, and which was computable from the artifacts all along — turned up
> a research-validity defect. The prompt takes deliberate care to hide *which
> channel* produced each answer, and then passed the natural channel to
> `CANDIDATE 1` on every arbitration this project has ever run. Position is a
> perfect proxy for identity, first-position preference is well documented in
> pairwise LLM judging, and this sits in the one component D1 permits to see both
> answers. The measurement fits: it fired 17 times, and on the 6 distinct
> questions where a channel actually held the right answer it chose correctly
> **2 of 2** when that channel was the natural one and **1 of 4** when it was the
> program. Order is now decided per question by a hash of the question text —
> deterministic, because temperature is pinned at 0. **The fix has not been run**,
> so 3-of-7 remains the old arbiter's number.
>
> Also corrected: the anchor repair had moved one published number without
> anyone re-running the analysis. Retrieval-caused detection on test is **0.837**,
> not the 0.851 RX-039 reported (RX-044). Nothing else in that run moved, and the
> H2 conclusion is unchanged in sign and substance.

> ## HANDED THE RIGHT EVIDENCE, THE SYSTEM IS RIGHT 35 TIMES IN 37 (RX-043)
>
> The oracle diagnostic, re-run on the repaired dataset (arm O, validation, 135
> requests). The first version whose evidence is actually correct: RX-040's
> resolved through anchors citing figures the validator had rejected (RX-042),
> handing the channels the wrong chunk on up to 10 of 43 questions.
>
> Restricted to the **37 questions the oracle built for**, where every error is
> a reasoning error by construction:
>
> | | arm A, real retrieval | **arm O, oracle** |
> |---|---:|---:|
> | numerical accuracy | 0.400 | **0.946** |
> | errors | 27 of 45 | **2 of 37** |
> | blind spot (agreed and wrong) | 1 of 14 | **1 of 33** |
>
> **Retrieval is the entire story.** Same models, same prompts, same
> temperature. Every accuracy figure this project has reported is a retrieval
> measurement wearing a reasoning measurement's clothes.
>
> **RX-040's mechanism claim is void.** Four of its six "reasoning errors" were
> the oracle's own wrong chunk, and its AUROC of 0.541 — "indistinguishable from
> chance" — was measuring a broken oracle. The blind-spot count falls from 5 to
> 1 for the same reason: those were both channels correctly reading the wrong
> chunk, which looks identical to a shared misreading and is not one.
>
> **H2 is not merely untestable here — it is unreachable.** At
> P(error | correct evidence) = 2/37 = 0.054, a stratum of 20 reasoning errors
> needs ~370 questions with retrieval working, and nearer 1,000 end to end. The
> benchmark has 45. That is a good property of the system, not a flaw in the
> experiment.
>
> **The two errors.** One is an abstention the detector flagged (risk 0.551).
> One is the failure D1 exists to worry about — both channels returning 25,018.28
> against gold 12,798.00, agreeing, risk 0.0. **n=1.** The error analyser refuses
> to call either a confirmed reasoning error: question understanding and
> extraction were not separately checked.
>
> Do not quote the pooled all-45 AUROC of 0.943: it includes 8 questions the
> oracle handed nothing after the anchor repair, 0 of 8 correct and all
> high-risk.

> ## THE HELD-OUT RESULT IS IN (RX-039)
>
> `campaign_20260905T112212Z` — **427 rows, 61 test questions x 7 arms, zero
> provider failures on either channel.** Evaluated once. The test split is spent.
>
> | | validation | **TEST** |
> |---|---:|---:|
> | arm A detection AUROC | 0.907 | **0.885** [0.801, 0.954] |
> | arm A accuracy | 0.400 | 0.361 |
> | plain RAG (B2) accuracy | 0.400 | 0.328 |
> | self-consistency (B5) AUROC | 0.600 | 0.606 |
> | B5 tokens vs arm A | 2.4x | **2.3x** |
>
> **What holds.** The detector generalises — 0.885 held out against 0.907 on
> validation, nothing tuned between them. It beats self-consistency on both axes
> at once: better detection for 43% of the tokens, replicated across both splits.
> That is the project's one claim that keeps its size and sign out of sample.
>
> **Read that with RX-048.** 0.885 is a *deployment triage* number: 64% of the
> errors it ranks are the system's own abstentions, and the score is a 3-level
> flag. Restricted to committed answers — the only place a hallucination can
> occur — the held-out figure is **0.679 [0.500, 0.839]**.
>
> **What does not.** No hypothesis is supported (H1 p=0.100, H3 p=0.580,
> H4 p=0.641; none survives Holm). All three now point the right way, where H3
> and H4 pointed the wrong way on validation — consistent with a mild real effect
> and with n being far too small to show it, and evidence of neither.
>
> **H2 became testable and FAILED.** Validation had no reasoning-caused errors at
> all; the test split has 4. Detection is **worse** on them (0.787) than on
> retrieval-caused errors (**0.837**), where H2 predicted far better. Whatever
> makes the risk score work, this campaign gives no evidence it is dual-channel
> disagreement over reasoning.
>
> *Retrieval-caused was 0.851 when RX-039 was written; the anchor repair landed
> the next day and moved that stratum from 40 questions to 32 (RX-044). Nothing
> else in the run changed — pooled AUROC, accuracy, precision, recall, F1 and
> every hypothesis are identical. Quote 0.837.*
>
> **A validation finding did not replicate**: arm H's blind-spot rate was twice
> arm A's on validation (0.150 vs 0.071) and the three arms are indistinguishable
> here (0.269 / 0.263 / 0.250). Reporting the validation figure alone would have
> been reporting noise. This is the ordinary reason held-out evaluation exists.
>
> **Caveat on the threshold-dependent numbers.** 0.51125 was frozen before any
> test data existed, so there is no leakage in sequence — but it was selected on
> validation running `gpt-oss-120b`, which the provider retired on 2026-09-03,
> and this test ran on `gpt-oss-20b`. AUROC is threshold-free and unaffected;
> precision/recall/F1 should be read as indicative until validation is re-run on
> the current binding. The access log's `reason` string still names
> `methodology-freeze-v1`, which D46 voided — the runs are correctly recorded,
> the label on them is stale.
>
> **The defensible claim is narrower than the motivation**: this built a
> numerical-answer verifier whose risk score ranks errors well and cheaply, and
> it did not demonstrate that the dual-channel design is why.

> **2026-09-03 — Channel A's model died mid-project.**
> `nvidia/openai/gpt-oss-120b`, bound by D44 on 2026-09-01, reached end of life
> at 2026-09-03T08:00:00Z and returns HTTP 410. It is **still listed** by
> `/models`. Channel A and the arbiter are rebound to
> `nvidia/openai/gpt-oss-20b`, verified by live call and by a 1-row pipeline
> smoke test.
>
> **Consequence (D46): the validation campaign below and the threshold frozen
> from it were both produced on a model that no longer exists.** Evaluating the
> test split now would compare held-out results against a baseline from a
> different Channel A. Validation must be re-run on gpt-oss-20b, the threshold
> re-selected, and `methodology-freeze-v2` cut, before test data is touched.
> `methodology-freeze-v1` is retained as a record and marked VOID.
>
> RX-038 is **not withdrawn**: it is a complete, correctly measured campaign of a
> real configuration. It simply cannot serve as the validation baseline for a
> test evaluation any more.
>
> `run_campaign.py` now refuses to start unless every binding answers a real
> call — the safeguard that would have caught this before startup rather than
> after a test run had begun.

**Every module in the spec now has an implementation, tests and documentation.
The engine is built. Human validation is DONE - 192 candidates judged, 115
usable as gold. The first H1 campaign is VOID (RX-033); the defect is fixed and
verified. Since D44 removed the Groq bottleneck, a seven-arm campaign over all 45
validation questions is COMPLETE - 315 rows, seven arms, every question carrying
every arm. No hypothesis is supported, and the reason is structural: not one of
the 27 errors is a reasoning error, so the benchmark has no numerical
hallucination for a dual-channel method to detect (RX-038).**

That sentence is the whole status. Everything below is detail. The blocker that
stood here since the project began is cleared: the owner worked through the
whole worksheet, and the validation rate rose from 44% to 83% across the fixes
(RX-026 through RX-031).

**2026-09-01 (later) · the Groq bottleneck was removed by moving Channel A off
Groq entirely (D44), and a seven-arm campaign was started.** Groq's 200,000
tokens/day was the constraint that made the campaign an eight-day job and left
B1/B2/B4 unrunnable, because a baseline must share arm A's natural model. Channel
A and the arbiter now bind to `nvidia/openai/gpt-oss-120b`; Channel B stays on
`nvidia/nvidia/nemotron-3-ultra-550b-a55b`. Both channels are on one provider,
bound to models from two different labs.

**This narrows the independence claim and D44 records the cost in full.** D1 had
recorded cross-vendor binding as "strictly stronger than the original plan"; this
steps back to the original plan. What is lost is correlated availability and any
shared serving-layer transformation — the latter unmeasured, and the real
limitation. `channels_are_independent()` reports "same provider, different
models" and `config.json` records that string per run, so no artifact inherits a
stronger claim than the binding that produced it.

**Campaign `campaign_20260901T105355Z` — COMPLETE.** Seven arms (A, B5, G, H,
B1, B2, B4) x 45 validated questions = **315 rows, every question carrying every
arm.** Channel B failed 1 of 135 program calls (0.7%). Full write-up: RX-038.

**What it found, conservatively:**

1. **Five arms return the same 18 correct answers.** A (full system), B2 (plain
   RAG), B5, G and H all score 0.400 numerical accuracy. The dual-channel
   machinery changes the risk score, not the answer — they share Channel A at
   temperature 0 over the same evidence, and the arbiter fires on 4 of 45.
   B1 (closed book) scores 0.000 and abstains on 97.8%.
2. **The risk score ranks errors well** — AUROC 0.907 (A), 0.959 (G), 0.936 (H).
3. **No hypothesis is supported after Holm correction.** H1 p=0.172, H3 p=0.368,
   H4 p=0.708; every interval crosses zero. H3 and H4 point the *wrong way* —
   the ablations score slightly higher than the full system.
4. **The full system is cheaper AND better than self-consistency**: 5,783
   tokens/question at AUROC 0.907 against B5's 13,709 at 0.600. This is the
   clearest supported claim the campaign produced.
5. **Retrieval is the bottleneck, measured directly.** On the **13** questions
   where retrieval delivered every gold evidence group the system was correct
   **13 times out of 13**. On the **32** where it did not, it was wrong 27
   times. **100% accuracy when the evidence arrives, 16% when it does not.**
6. **H2 is NOT TESTABLE, and that is the headline.** The reasoning stratum has
   13 questions and **zero errors**, so AUROC is undefined there — the system
   never once failed on a question whose evidence it actually had. With that
   denominator the honest statement is that reasoning failures are *rare*
   (0 of 13, consistent with up to ~25% at 95% confidence), not *absent*.

Points 5 and 6 reframe the remaining work: retrieval, not reasoning, is where
this system loses. Widening the benchmark's multi-hop share is no longer an
external-validity nicety — it is the precondition for the research question
being answerable at all.

**Both figures above are post-correction.** The first analysis put
`all_evidence_retrieved` at 1 of 45, because gold anchors store bare numerals
(`12232`) and filings print grouped ones (`12,232`), and the matcher preserved
commas by design — so a numeric anchor could never match and every error was
attributed to retrieval by construction. The tell was 17 questions answered
correctly while the predicate claimed none of their evidence had been retrieved.
Fixed in `EvidenceSpan.satisfied_by`; see the correction at the end of RX-038.

**The retrieval figures were re-measured and are CONFIRMED, not stale.** The
shared matcher raised the worry; the re-run settled it. `measure_reranking.py`
over all four gold sets returns 0.280 baseline / 0.240 reranked / net −4 of 100 —
identical to RX-035 in every figure, and the baseline reproducing 0.280 confirms
RX-034's decomposition too. The reason the defect devastated the campaign and
barely touched retrieval is that the two gold sets use different anchor
conventions: **96% of campaign evidence groups carry a bare numeral against 13%
of retrieval ones.** The campaign gold took the number from the answer, the
retrieval gold from the printed page.

**The operating threshold is FROZEN at 0.51125** (D45), chosen on this
validation run and fixed before the test split is touched. All three candidate
objectives — F1-optimal, recall floor 0.90, recall floor 0.95 — land on the same
point, which is what makes it a plateau rather than a value fitted to noise.

Arm A at that threshold: **TP 26, FP 5, TN 13, FN 1 — precision 0.839, recall
0.963, F1 0.897, FPR 0.278.** It catches 26 of 27 wrong answers and wrongly
flags 5 of 18 correct ones. G reports 0.730/1.000 and H 0.960/0.889. B5 reports
0.000 across the board because the threshold was chosen on arm A's score
distribution and B5's is on a different scale — a scale mismatch, not a
measurement of B5.

An earlier attempt the same hour, `campaign_20260901T104422Z`, is VOID: a
cold-start `ProviderUnavailableError` killed the program channel on question 1
for arms A and G, and `--resume` keys on `(arm, question_id)` so it would never
have retried them. Three checks established this was a cold start and **not**
caused by D44 — Nemotron is still listed and answers in isolation; a probe
alternating both models over one provider ran 3/3 clean; and B3's pre-D44
single-channel run already carried 2 such failures in 45 rows, a 4.4% baseline
for this endpoint.

Earlier the same day, before D44, everything that moved was chosen because it
did not touch the Groq allowance:

- **B3 baseline is COMPLETE — the first arm this project has finished** (RX-037,
  `campaign_20260901T071345Z`, n=45). It ran on NVIDIA at **zero Groq cost**:
  Channel B only, and question understanding is rule-based, so a B3 row makes
  exactly one API call and none of it is Groq.

  | | value | 95% CI |
  |---|---:|---|
  | Numerical accuracy | **0.289** | [0.156, 0.422] |
  | **Accuracy when it answers** | **0.929** (13/14) | [0.786, 1.000] |
  | Abstains | **0.489** | |
  | Execution failure | 0.200 | |

  The shape is the finding: of the 14 questions where it committed to a figure,
  13 were right. It is a precise reasoner that declines on half the questions
  because the retrieved evidence does not support an answer — **the retrieval
  limitation measured from the far end of the pipeline**, agreeing with RX-028
  and RX-034 by a different route.

  **B2 is deliberately not run** — plain RAG must use arm A's natural model or
  the comparison confounds architecture with model choice, which puts it on the
  exhausted provider.
- **RX-035** built the reranker RX-034's headroom implied, measured it on local
  CPU, and **rejected it**: 0.240 against a 0.280 baseline. (Invalidated by RX-053;
  on clean gold RX-035-revalidated measured 0.156 against 0.125 and it stays
  unadopted for a different reason.)
- **RX-036** found a measurement defect that only a program-only arm could
  expose — the program channel's abstention was recorded and never read, so
  clean runs that correctly declined were counted as parse failures.
- The evaluator was **audited empirically** against the spec's normalization
  list. Three of four flagged cases were the probe being wrong, not the grader;
  the fourth applies to one row where the current behaviour is correct.
- `GET /stats` and `GET /answers`, a dashboard that counts rows, a demo guide
  ([`DEMO.md`](DEMO.md)) and the written case study
  ([`CASE_STUDY_REPORT.md`](CASE_STUDY_REPORT.md)).

Status vocabulary is used strictly (spec §43): `COMPLETE` requires the full
acceptance checklist, which for most modules includes evidence from a run.
Anything short is `PARTIAL` or `BLOCKED`.

---

## Read this first

**The retrieval misconfiguration is FIXED and verified** (D-1 / D33 / RX-011).
`.env` now names `finverify_e5`, the deployed API was rebuilt and confirmed from
inside the container, and a preflight refuses to run when the collection's
embedding model disagrees with the configured one. The check was proved to fail
by restoring the broken config on purpose.

The root cause was **D2a applied to one variable of two**: it switched the
embedding model and left `QDRANT_COLLECTION` naming the superseded BGE index.
Both models are 768-dimensional, so every check in the system passed while
retrieval returned the wrong page.

**[`COMPLETION_GUIDE.md`](COMPLETION_GUIDE.md) carries the defect register and
the audited module tiers**, and is worth reading for *how each defect hid*. Its
counts are a 2026-08-27 snapshot and several are now wrong — it says 268
questions and 0 validated — so it opens with a correction table. **This file is
the current state.**

---

## The blocker is cleared

**192 of 192 candidates have been judged by the owner.** Spec §16 required human
validation and no amount of generation quality substituted for it. It is done.

| | validated | rejected | pending | **usable as gold** |
|---|---:|---:|---:|---:|
| validation | 48 | 10 | 0 | **45** |
| test | 65 | 11 | 0 | **61** (sealed) |
| train | 2 | 8 | 48 | 2 |
| **total** | **115** | **29** | **48** | **108** |

*Usable* excludes the D22 ambiguity subset, which is scored separately.

**The validation rate is itself evidence.** The first 45 rows judged came back
44% validated; after D42, RX-027 and RX-029 the rate over the remaining rows was
83%. Same validator, same corpus, same rubric - the defects that drove the early
rejections are gone.

**Two defects were found in the completed gold before it was imported**, both of
the RX-030 shape where the value is right and the metadata destroys it: a
debt-to-equity ratio carrying `crore` (which would have graded the correct
answer wrong and the wrong answer correct), and - larger - a gold that states a
scale but no currency rejecting the most complete answer a model can give, on 44
of 115 rows. Both fixed, the second in the correctness predicate rather than by
editing 44 gold labels. See RX-031.

### The campaign: first attempt VOID, re-run pending allowance

**`campaign_20260831T184152Z` is void and must not be resumed** (RX-033). It
reported `completed 180, skipped 0, failed 0` while **Channel A was
rate-limited for 166 of those 180 rows**. Read naively it says the system is
uncertain on 97% of questions; that is a rate limit wearing the costume of a
finding. `--resume` on it would skip the dead rows and build a "complete"
campaign on them, so a new run is required. It carries a `VOID.md`.

The cause: the campaign stops on `QuotaExhaustedError` - the provider refusing
mid-call - while the refusal that fires first is `DailyQuotaExhausted`, the
local limiter declining to call at all. Unrelated classes; the guard written for
exactly this outcome matched only one name. **Every test passed throughout,
because they inject the class that never fires in production.**

Fixed and **verified end to end against a genuinely exhausted allowance**: the
identical condition now stops the run with `0 rows written` and names its resume
command.

**Do not resume `campaign_20260831T211058Z`.** It was created with `--limit 2`,
so its `config.json` pins two questions; it is proof the stop works, not a
campaign to continue.

**`campaign_20260831T235851Z` (4 of 180 pairs) is SUPERSEDED, not resumed.**
It ran with Channel A on Groq. D44 rebound Channel A, and result rows do not
record the model that produced them — only `config.json` does, once per run — so
resuming it would mix two Channel A models inside one run with nothing in the
data to separate them. Its four rows are preserved under the append-only rule.

**The run to continue is `campaign_20260901T105355Z`:** seven arms (A, B5, G, H,
B1, B4, B2) over all 45 validation questions, 315 rows, 765 requests, all on
NVIDIA. Start Docker first, then:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts\run_campaign_unattended.py `
    --resume campaign_20260901T105355Z --arms A B5 G H B1 B4 B2
```

Run it from a real terminal, not from inside a Claude Code session: anything a
session launches dies with that session, which is what stopped this run at 4
rows and again at 79. Never run two at once — both append to one
`results.jsonl`.

B2 (plain RAG) was **approved by the owner on 2026-09-01** and runs as the
seventh arm of the same run — 45 more requests, 765 for the day, 315 rows. It is
the comparator a "better than plain RAG" claim needs, and with it every baseline
in EVALUATION.md now has data: B1 closed-book, B2 plain RAG, B3 program-only,
B4 agentic RAG, B5 self-consistency.

**There IS a midnight to wait for, and this file said otherwise until
2026-09-01.** RX-024 measured Groq's own window as rolling, and that is true of
the provider — but no call reaches the provider without passing `RateLimiter`,
which rolls its counters only when the UTC day changes. A run that spends the
day's 200,000 tokens at 05:00Z is refused locally for nineteen hours regardless
of what the provider has refilled. The supervisor now sleeps to the reset
instead of polling nine times through it.

`LLM_MAX_TOKENS=4096`, not the 1024 D43 planned: RX-032 measured them head to
head and 1024 returned unreadable output on half the sample. At that cost the
allowance reaches **about 22 of the 45 questions** before the 2026-09-09
deadline. The run is question-major, so what completes is a balanced prefix -
every finished question has every arm - in the dataset's own seeded order.

**The test split now has 61 gold answers and stays sealed** until the
methodology is frozen at `methodology-freeze-v1`. It is larger than the
validation split, and it was validated before any result existed - so no
judgment on it could have been influenced by seeing how the system performed.

---

## Still open with the owner

1. ~~A second vendor that serves free completions.~~ **RESOLVED 2026-08-29
   (RX-014).** NVIDIA NIM passes all four D21 steps, including the real-call
   step an earlier candidate failed (RX-008). Channel B is now
   `nvidia/nvidia/nemotron-3-ultra-550b-a55b`
   and independence is **cross-vendor**. It landed before the campaign, so
   nothing needs re-running. Two caveats: NVIDIA's daily limit is **unobserved**
   (no rate-limit headers), and its **latency is not reportable** — identical
   prompts returned in 0.42s and 12.5s.
2. **The conference and its date.** D24 cut scope against a deadline whose date
   is still unrecorded. The ship list is ordered but not scheduled.

---

## Module status

**21 COMPLETE · 11 PARTIAL · 1 BLOCKED**, re-audited 2026-09-12 and 2026-09-13 against the spec
§43 checklist. Status vocabulary is used strictly: `COMPLETE` means every
applicable item passed, including *acceptance criteria passed*, which for most
modules means evidence from a run. Anything short is `PARTIAL` or `BLOCKED`.

> **The 2026-09-12 re-audit: what it covered, and what it did not.**
>
> Six independent auditors were run against the §43 checklist, each verifying a
> group of modules by reading the code and the artifacts rather than the status
> claims. **They downgraded 6 modules that had been marked COMPLETE** (1, 3, 7, 23,
> and — before same-day fixes — 27, 30, 31), every one in the flattering
> direction. Three of those were closed the same day; the other three stand.
>
> Two limits on that result, stated because they change how much weight it carries:
>
> 1. **Modules 8–17 were audited in a second pass on 2026-09-13.** Its adversarial
>    verifiers again died on a quota limit for two of its three groups, so every
>    figure written into rows 8–17 was reproduced from the artifacts by hand; the
>    third group's findings were confirmed by its own verifier. Of the eight rows
>    that had been carried as COMPLETE unchecked, **two did not survive (12 and
>    14)**. The rest keep COMPLETE with corrected figures and five spec functions
>    scoped out explicitly in D52.
> 2. **Every adversarial verifier died too**, so the findings are single-auditor.
>    That is not a formality: one downgrade was wrong. An auditor reported the
>    7-of-7 fiscal-year figure in module 7 as unsupported; checking the run
>    artifact directly showed exactly 7 prior-year rows with the LLM returning the
>    filing year on all 7, so the claim was correct and was left alone. Every other
>    downgrade recorded here was reproduced by hand before being written down.

| # | Module | Status | Evidence |
|---|---|---|---|
| — | Environment gate | **COMPLETE** | **32/34 VERIFIED**, exit 0, and the two gaps are recorded as MISSING rather than glossed: Ghostscript (camelot `lattice`, unused - D10a chose `stream`) and an OpenRouter key (no provider binds to it). The gate's own acceptance rule is verified-or-recorded, and it is met |
| 0 | Research foundation | **PARTIAL** | **there is no live methodology freeze.** `methodology-freeze-v1` is annotated VOID (D46) and v2 was never cut, so the test split was evaluated against a voided freeze (D48). Now gated: `run_campaign.py` refuses `--split test` without a live tag. **The literature currency sweep is DONE (2026-09-06) and the gap claim survives**: the two closest 2026 items were read at source, and neither uses cross-modal disagreement as a detector — Chen et al. (arXiv 2605.31064, KDD 2026) executes programs in financial QA but to *produce* the answer, and Kovács et al. (arXiv 2607.00895) detects spans across evidence *types* rather than reasoning *channels*. Chen et al. is the closest published neighbour and should be cited. **PARTIAL for one structural reason only**: the freeze cannot be fixed retroactively, because backdating a tag onto a spent split would put a pre-registration mark on a decision that was not pre-registered (D48) |
| 1 | Dataset & benchmark | **PARTIAL** | 5-company cross-sector corpus, 1,665 pages, all extracted (1,664 yielded content). **The FinQA/TAT-QA/ConvFinQA licence blocker was a blocker on a road nobody took**: `datasets/benchmark/` is empty and no module imports them — they appear in `RESEARCH.md` §2 only as the datasets *prior work* validated on. **Downgraded from COMPLETE on 2026-09-12 by a §43 audit, for two reasons the row never stated.** Spec §9 names `gold_calculation` and `gold_program` as record fields; neither exists anywhere outside the spec, so the 9 multi_hop questions carry no gold program and no gold calculation — FI112f618b records 7.26 percent with `source_page` null and an empty `source_note`. And **22 validated rows now carry zero evidence groups** (RX-042 moved their stale anchors to `provenance.superseded_evidence`), so every evidence-based metric is undecidable on them. Verified anyway on 2026-09-06 from the primary sources, so the question is closed either way: FinQA **MIT** (© 2021 Zhiyu Chen), ConvFinQA **MIT** (© 2022), TAT-QA **CC BY 4.0**. **One thing is recorded as unconfirmed rather than assumed**: FinQA and ConvFinQA derive their documents from IBM's FinTabNet, whose licence is widely reported as CDLA-Permissive, but IBM's page for it is deprecated and states no terms — so the repository licences are confirmed and the underlying filings' terms are not |
| 2 | Document acquisition | **COMPLETE** | **Spec §10's "version management" is NOT implemented and is now explicitly scoped out in D49.1** rather than silently absent: the corpus is five filings all covering FY2023-24, one report each, so a supersession chain has no pair to connect. The restatement trap itself is handled by a stronger mechanism — provenance records which document every number came from, so the same metric for the same year from two filings stays two distinguishable facts. Reverses the moment a second filing covers a year already covered. Otherwise: registry, hashing, dup detection, content validation - exercised on the real corpus: 5 filings, 1,665 pages acquired and registered, 1,664 yielding content |
| 3 | Document intelligence | **PARTIAL** | Camelot `stream` (D10a), provenance, scale carry-forward and multi-row headers are all verified against the real corpus: 1,664 of 1,665 pages extracted, 2,780 tables. **But OCR HAS NO PRODUCTION CALLER, and the row claimed "OCR built and verified" without saying so.** `backend/documents/ocr.py` passes 22 tests against real Tesseract; `extraction.py` contains the string `ocr` zero times, and a repo-wide grep for `ocr_document|ocr_page|needs_ocr` finds only the module itself, its tests, and an unrelated enum value. The consequence is in the corpus, not hypothetical: the **10 HDFC Bank pages the registry flags as scanned inserts were never OCR'd** — pages 200-209 of `40f73920a8b153ec.chunks.jsonl` carry ~187 characters each, which is the injected unit banner plus a fragment of "A S S U R A N C E  S T A T E M E N T". D32 further states that "every OCR'd page carries source='ocr' and its confidence into the extraction quality report"; `ExtractionQuality` has no such field. So spec §11's "OCR if required" stage is unexercised on real data. **Deliberately not fixed under deadline** (2026-09-12): re-extracting those pages changes the chunk set the 22,930-point index was built from, which cascades into facts, retrieval and every campaign artifact, and a hurried version of that is worse than the admitted gap |
| 4 | Knowledge structuring | **COMPLETE** | `facts.py`: spec §12 fields, year-labelled columns, merged-cell splitting. **1,825 facts across all 5 companies** (680 before D34's lexicon extension, 1,245 before the RX-027 extraction fixes) |
| 5 | Normalisation | **COMPLETE** | parser + 80 tests, 4 real bugs fixed. Compound scale (`K Cr`, `thousand crore`) now flagged rather than silently resolved (RX-013) |
| 6 | Hybrid RAG | **PARTIAL** | **RX-054 re-measured the planned-vs-hybrid contrast on clean validation-only gold and committed the artifacts**: pooled evidence accuracy @10 is **0.250 planned against 0.062 hybrid** over 32 questions, four reports under `evaluation/reports/`. So that contrast is no longer prose-only. **RX-054b also re-ran the rank decomposition** on the same gold with the `--out` flag the script had been missing, so that is committed too — and it moved: the right chunk is in the top 10 for **0.125** of evidence groups (RX-034 said 0.280), within rank 70 for **0.406** (said 0.720), within rank 300 for 0.719, and **0.281 are not ranked within 300 at all**. The headroom is further down the ranking than reported. **RX-035 was then re-run on the same clean gold (RX-035-revalidated): 0.156 reranked against 0.125 baseline, net +1 of 32 groups** — the original "reranking hurts" did not reproduce. Reranking is still not adopted, now because the effect is indistinguishable from zero and costs 24.9 s CPU per evidence group. **RX-053: the RX-028, RX-034 and RX-035-original figures below are measured on MIXED-SPLIT gold and cannot be called validation figures.** The four retrieval gold sets each declared `split: validation` and carried the note "must never be reported as a test result" while **43 of the 76 sealed test questions (57%)** appeared among their source questions — `build_retrieval_gold.py` hardcoded the label instead of deriving it, and `evaluate_retrieval.py`'s seal gate reads exactly that field, so it permitted every run. RX-028, RX-034 and RX-035 therefore ran unflagged and unlogged on test-bearing gold. What leaked is evidence-span locations and retrieval performance, not gold answers; the answer-level campaign used the sealed loader and is unaffected. **RX-035 is the one that made a decision** — it rejected a reranker at 0.240 against 0.280 — so that rejection rests partly on held-out data. Labels corrected, builder fixed, 20 tests added (28 cases once the validation-only sets existed); the sets are now gated, and the clean re-measurement was run on validation-only gold instead (RX-054, RX-054b, RX-035-revalidated — the figures at the start of this row). Figures as measured: Re-measured against corrected gold (RX-028): evidence accuracy **0.08–0.56**, not the 0.38–0.61 previously reported. The old gold pointed at front-of-report summary tables; the corrected gold points into the financial statements. All earlier retrieval figures superseded. **RX-034 decomposed the misses by true rank**: the right chunk is in the top 10 for 0.280 of evidence groups, within rank 70 for **0.720**, and a candidate at all for 0.870. Coverage is not the binding problem — ordering is. **RX-035-original (INVALIDATED by RX-053; superseded by RX-035-revalidated) tested the reranker that headroom implies and reported it FAILED**: a cross-encoder over a 100-candidate pool scores **0.240 against the 0.280 baseline**, lifting 7 groups into the top 10 and pushing 11 out, with a per-company spread from +0.160 to −0.160 that reads as arbitrary rather than selective. Not adopted; the 0.720 ceiling stands unclaimed |
| — | LLM provider layer | **COMPLETE** | one OpenAI-compatible adapter serving **Groq, Gemini and NVIDIA NIM**, persisted quota limiter, verified live on every binding. Two live gates it earned the hard way: a real completion per binding before any campaign starts (D46 — `/models` listed a model for hours after it began returning 410) and no retry on auth or model-not-found. No response cache (listed improvement) |
| 7 | Question understanding | **PARTIAL** | **Planning is worth +0.188 evidence accuracy @10, pooled over 32 validation-only questions across 4 companies (RX-054) — not the +0.409 this row carried.** That old figure came from 22 hand-built Infosys questions on gold RX-028 superseded; the new one is measured corpus-wide on gold built from validation questions only, with all 58 spans re-verified against the source PDFs, at zero quota cost. The **direction is the robust part**: planned beats hybrid in all four companies (+0.167 / +0.333 / +0.182 / +0.167), and unplanned hybrid scores **0.000 on three of the four** — corpus-wide retrieval without question planning is not a working configuration on this corpus. Magnitude is uncertain at n=32, and Sun Pharmaceutical's cell is n=3, so per-company numbers are direction only. Four committed reports under `evaluation/reports/`. Superseded for the record: planned retrieval was reported as **+0.409 R@10 corpus-wide** (0.523 -> 0.932) against the +0.068 the slice suggested (RX-012). Does **not** extract company from text - the dataset supplies it. The LLM refinement path **was** exercised (RX-018) and returned the wrong Indian fiscal year on 7 of 7 prior-year questions, so D37 made the module deterministic by decision rather than by accident of wiring. Ran on all 787 campaign rows. **Downgraded on 2026-09-12: the module's sole acceptance figure is one this project retired.** The +0.409 is measured on `retrieval-eval-infosys-fy24-v1` — 22 hand-built Infosys questions — and RX-028 lists RX-012 among the measurements it supersedes, so the number stands on gold that was later corrected and on one company. Separately, spec §15 names 11 question types to classify; there is no trend, comparison or cross-table class, and the benchmark itself labels only lookup (179) and multi_hop (13), so the classification half is both partly unimplemented and largely unexercised. (The 7-of-7 fiscal-year claim WAS checked against the run artifact and is correct: exactly 7 rows ask about a prior year and the LLM returned the filing year on all 7.) |
| 8 | Natural channel | **COMPLETE** | **Corrected 2026-09-13 (audit of modules 8–17, figures reproduced from the run artifacts):** the parse-failure rate is **0.022 on validation** (arms A, B1 and B4, one each of 45, a provider outage) and 0.000 on test — not "0.000 on both splits". Spec §16's self-reported confidence is not emitted, scoped out in D52.1 because self-assessment is the circular check the method exists to avoid. verified live (RX-006) and exercised across **787 campaign rows** on three campaigns, 0.000 parse-failure rate on both splits. Self-consistency sampling lives in arm B5 rather than in the channel, by design |
| 9 | Program channel | **COMPLETE** | **Corrected 2026-09-13 (audit of modules 8–17, figures reproduced from the run artifacts):** "1 failure in 135 (0.7%)" was the provider-outage count. **Hard failures** — runtime errors, AST-allowlist rejections and that outage — were **21 of 135 (0.156) on validation and 27 of 183 (0.148) on test**; a further 66 and 73 calls abstained on insufficient evidence. **A built-and-never-called defect was fixed**: `SandboxConfig.from_env()` had no production caller, so the `SANDBOX_*` limits were honoured by no campaign ever run. `execute_program` now uses it, and a test asserts a configured limit reaches the docker command line. generation -> AST allowlist -> container, verified live. **1 failure in 135 program calls on validation (0.7%), 0 on test.** RX-036 fixed the abstention accounting a program-only arm exposed. No repair-on-failure retry (listed improvement, not a gap in the spec) |
| 10 | Deterministic verification | **BLOCKED** | **Corrected 2026-09-13 (audit of modules 8–17, figures reproduced from the run artifacts):** it did not "fire on 1 question of 45" — it **produced a value on 0 questions on either split** (applicable on 2 of 90 validation calls and 2 of 122 test calls, bound on none). Across every campaign run: 2 values in 754 calls, both on one question in ablation arms. 63 tests (40 + 23), not 44. The BLOCKED status stands and is, if anything, understated. 10 operations, fully deterministic operand binding, 44 tests - and **inert in practice: it fires on 1 question of 45.** It abstains on lookups by design and 44 of 45 are lookups, so arm G ablates a component that never runs and H3 tests nothing. RX-041 measured the obvious fix and **rejected** it: binding takes the note-reference column, giving 8 agreements against 23 disagreements on the oracle run's best-case evidence. **Both routes out are now measured and closed (RX-046).** The note column: TODO's stated signal - a column of small bare integers - fires on 33 of 634 tables and essentially all are false positives, because the corpus is full of ESG and HR tables whose data genuinely is small bare integers; adding the omitted constraint that a note column sits *before real money* leaves **4 detections, 1 genuine**. The year column: bounded at **0.279** by data that is not there - only 500 of 1,793 facts get a year from a `column_header`, 1,243 fall back to `document_fiscal_year`, which names the filing rather than the column, and **90 of 188 validated questions (47.9%) ask about the comparative year**, so that fallback points at the wrong column on about half the benchmark. **The blocker is upstream**, in chunker-side header recovery (measured ceiling 32.1% at 6 rows, 37.6% at 10) - and that agrees with the 0.279, which is the useful cross-check. Nothing inside `operand_binding` reaches it |
| 11 | Consistency engine | **COMPLETE** | **Corrected 2026-09-13 (audit of modules 8–17, figures reproduced from the run artifacts):** it ran on **363 of the 787** rows, not all of them — the other 424 are baseline arms with the engine switched off. The held-out AUROC it feeds was recomputed independently (0.8846) and holds. The three-channel path, including the deterministic overrule its docstring calls the only defence against "both agree, both wrong", has **never fired on real data**, because module 10 never produced a value; unit tests cover it. Spec §19's evidence and reasoning comparison is scoped out in D52.2 — both channels read identical evidence by construction. verdict + continuous score; `base_score`/`coverage` exposed separately. Ran on all 787 rows and produced the score that reaches **AUROC 0.885 held out** |
| 12 | Disagreement detection | **PARTIAL** | **Downgraded 2026-09-13.** "8 types" counts NONE; 7 are real, and across the 787 pooled rows **only `scale_mismatch` was ever recorded, 4 times**. `report.disagreements` is never serialised, so MAGNITUDE_MISMATCH and CHANNEL_UNAVAILABLE cannot reach an artifact at all, and spec §20's `difference` field is computed and discarded. **The headline contingency table's "disagree" row is mostly not disagreement:** `agreed` is `verdict == AGREE`, so the held-out arm A's 42 "did not agree" rows are **3 DISAGREE and 39 UNCERTAIN** — a genuine disagreement rate of **3/61 = 4.9%**, where the case study reported 68.9% as "Disagreement rate". The generator now reports both, and EVALUATION.md §5.4 says how the table is counted. Previously: 8 types incl. scale-mismatch naming, exercised across three campaigns |
| 13 | Verification agent | **COMPLETE** | **Audited 2026-09-13: every figure in this row reproduced to the digit** (17 triggered, 16 resolved, 3 right, 7 winnable). Two gaps closed or recorded. **No integration test had ever driven a DISAGREE into the arbiter** — and the test named for the agree case passed only because its stubbed program failed the sandbox, so the channels never agreed. Three branch tests now drive AGREE, DISAGREE and UNCERTAIN with the verdict asserted. Spec §21's re-retrieval stage is scoped out in D52.3, with its cost stated: on 9 of the 16 resolutions neither channel held a correct answer. triggers on DISAGREE only; abstention is first-class. **Resolution accuracy is now measured** (RX-045, EVALUATION.md §5.4, at zero API cost — it was computable from the run artifacts all along and nobody had run it): across all three campaigns it fired **17 times**, resolved 16, declined 1, and was **right 3 times**. Read against the denominator that matters: on 9 of the 16 *neither* channel held a correct answer, so there was nothing to choose; on the **7** where one did, it returned it **3** times. Measuring it exposed a research-validity defect — the prompt hid the channel labels but `CANDIDATE 1` was **always** the natural channel, making position a perfect proxy for identity in the one component D1 permits to see both answers. **Fixed**: order is now per-question and deterministic (SHA-256 of the question text, 0.463 program-first), the verdict is translated back into channel terms before anything reads it, and the order shown is recorded so the effect stays measurable; 8 tests. **The fix is now live in an artifact**: `campaign_20260908T164126Z` carries the project's first row with `verification.metadata.candidate_1`, and on it the arbiter was shown the **program** answer first and still chose the natural channel — one data point against a pure first-position effect, and **n=1**, so the fixed ordering remains effectively unmeasured. COMPLETE on the acceptance criterion (§5.4 resolution accuracy, measured) rather than on the mechanism, which needs an arm that actually disagrees often |
| 14 | Hallucination taxonomy | **PARTIAL** | **Downgraded 2026-09-13, confirmed by the audit's adversarial verifier.** Six of the spec's ten minimum kinds — wrong evidence, number, year, formula, metric and arithmetic error — are declared and assigned by no code path: over **1,196 labelled errors** in the committed reports they occur zero times, and only five kinds ever appear. The test that read as coverage asserted enum membership; it is renamed to say it checks declaration. ErrorProvenance REASONING, EXTRACTION and DEFINITIONAL_AMBIGUITY have never been assigned either — the H2 stratum is computed separately. Previously: two orthogonal axes (D25), 28 tests. **Now exercised on labelled data**: `error_analysis.analyse()` calls `classify()` on every graded error across validation, test and oracle runs (RX-038/039/043/044) |
| 15 | Confidence & risk | **PARTIAL** | **Audited 2026-09-13: every figure in this row reproduced independently**, including AUROC 0.8846 and the committed-only 0.679. One addition: that 0.679 — the figure the research question actually turns on — exists in no committed report artifact. continuous risk score, four facets; **AUROC 0.885 [0.801, 0.954] held out**. Still reports `calibrated=False` and will until fitted, so **Brier and ECE cannot be quoted** — AUROC is rank-based and valid uncalibrated, but calibration is this module's stated acceptance criterion and the only thing keeping it out of COMPLETE. All four facets are live on real data. **But the score is not continuous, and §5.1 forbids that** (RX-048): arm A takes **7 distinct values over 61 held-out questions, 3 covering 93%**, so the module satisfies "emits a float" and fails "emits a continuous score". RX-007 caught the same defect in B5 and the check was never applied here. **64% of the errors it ranks (25 of 39) are the system's own abstentions**, which are an input to the score; restricted to committed answers — where a hallucination can actually occur — held-out AUROC is **0.679 [0.500, 0.839]**, an interval reaching chance |
| 16 | Explainability | **COMPLETE** | **Audited 2026-09-13.** Spec §24 asks the response to name its source document and show the calculation. The document is now on every evidence item the API serves (D51). The calculation is scoped out (D52.4): the executed program's source is not recorded in the run artifacts. derived, never generated (D29); 19 tests. `explain()` had been reachable only from the API's live-QA path, which D30 turns off by default, so 787 recorded rows carried no explanation and the module could not be shown to work on anything real. Fixed at source and **the acceptance evidence now exists in two runs**: all 225 rows of `campaign_20260906T221521Z` and all 45 of `campaign_20260908T164126Z` carry one, derived from the same object that produced the verdict so it cannot drift from it |
| 17 | Orchestration | **COMPLETE** | **Corrected 2026-09-13 (audit of modules 8–17, figures reproduced from the run artifacts):** 8 distinct arms across the three campaigns, not 9; 56 tests in `test_orchestrator.py`, not 47; the oracle-ordering gate lives in `test_oracle_evidence.py`. Spec §25's evidence-validation stage does not exist — scoped out in D52.5 — and README's diagram, which drew it, is corrected. The independence barrier was re-verified: no parameter admits Channel A's output into Channel B. LangGraph, branch on disagreement, arms as configs (D26), 47 tests incl. the RX-012 retrieval-scope gates and the oracle-before-closed-book ordering gate. Ran 787 rows across 9 distinct arms |
| 18 | Database | **COMPLETE** | all 16 spec entities + Alembic, migration applied, and the table now matches the artifacts exactly: 192 questions (115 validated / 29 rejected / 48 pending), 198 evidence spans, **1,793 facts**, 1,664 pages, 537 sections, 2,780 tables, 5 documents, and **1,646 graded answers across 14 arms from 42 runs** — repopulated 2026-09-09, when the deployed database was found holding 49 answers over 5 arms from an ingest predating the ablation entirely. The span count fell 231 → 198 in the same pass, which is the table catching up to the corrected gold of `b6a8643`. **Pooled reports (`<run-a>+<run-b>`) were silently skipped** by the per-run loop, and that is the form every ablation analysis takes — the reason the headline result had never reached the database or the UI (RX-050). **Upsert cannot express a deletion**, and this project met that defect twice: D42's id change left all 268 pre-D42 questions beside the new 192 (the table read **460**), fixed by pruning in `ingest_dataset`; the same defect survived one level down because facts arrive one chunk file at a time, so nothing ever saw the whole live set. `prune_facts` accumulates keys across every file and prunes once — **53 orphans removed 2026-09-06**, not the ~21 previously estimated. That estimate compared 1,846 stored against the 1,825 facts extraction *emits*, but 32 of those 1,825 collide on the same cell key and collapse on upsert, so the live set is **1,793** and 1,846 − 1,793 = **53** exactly. `users` is unpopulated by design — there is no authentication in a research system, and the entity exists because spec §18 lists it |
| 19 | Vector database | **COMPLETE** | **Spec §27's financial-metric filter is NOT implemented and is scoped out in D49.2**, because a metric filter on chunks would be wrong rather than merely missing: the indexed unit is a table, and one financial-statement table carries dozens of line items, so labelling it with one metric discards the rest and labelling it with all of them makes the filter useless. Metric-level selection lives on `FinancialFactRow`, which is keyed per cell. **The §27 section filter was a different case — implemented at the index layer and unreachable from the pipeline**, because `QdrantIndex.build_filter` accepted `section` and `HybridRetriever.retrieve()` never passed it; threaded through on 2026-09-12 with 3 tests, one of which pins that `section` reaches the BM25 cache key (a filter missing from that key makes two different filters share one cached candidate set). Otherwise: **22,930 chunks across all 5 filings**, 5/5 companies verified present. Collection/model pairing now enforced by manifest (D33) |
| 20 | Backend API | **COMPLETE** | **`POST /questions/ask` had never worked in the deployed container** — the image is web-only, so every request died on `ModuleNotFoundError: langgraph` as a bare 500, hidden because live QA is off by default — and it rebuilt the embedding model on every request (365 s of wall time against 167 s of pipeline, past the proxy timeout). Fixed under D51: a 503 naming the missing engine in the container; the pipeline cached and warmed on the host API; live answers persisted under run `live-qa` and kept out of `/stats`; every evidence item names its filing. **A 37-check endpoint inventory through the nginx proxy passed**, with every field the frontend's TypeScript declares present. **Stopping the database and the index under the running stack found two more defects, both fixed:** with PostgreSQL stopped every request hung indefinitely (no connect timeout) and every screen sat on "Loading" with no error — now a 503 naming the database in about 10 s, a labelled error on every screen, and a reconnect on restore; with Qdrant stopped, asking returned a bare 500 — now a 503 naming the index in 4 s, before any quota is spent. all 9 spec endpoints plus `/stats` and `/answers`, 42 tests, verified live and in-container at `build_ref` matching HEAD. `/answers/{id}` returned the schema default `UNSCORED` for every recorded answer until 2026-09-09 — "no detector ran" printed exactly where one did; bands are derived from the stored score by `confidence.band_for`, the same function the live path calls. `/stats` is counted per request, never cached, and has no field for a metric nobody has computed |
| 21 | Frontend | **COMPLETE** | **Driven end to end through headless Edge on 2026-09-13**: a live question answered in 157 s, its evidence naming the filing, saved and reopened cold after navigating away, and listed under `live-qa`; every route at 1280 px and 375 px with no overflow and no console errors; with the API stopped, every screen shows a labelled error and keeps its navigation. The Ask screen had sent no filing, so every question asked from the UI ran unscoped retrieval; it now defaults to a filing. **8 screens** (Configurations and the cross-arm question view are new), typechecks strict, builds, served from nginx in-container, and **verified by loading the running stack** rather than by reading the source. Dashboard renders real corpus and campaign counts (1,646 rows, 14 arms, 42 runs) and reports whether metrics exist instead of asserting "Not yet measured" unconditionally — it had gone on calling 1,646 graded rows "the run in progress". Research renders the single-field ablation contrasts with intervals, error counts and the UNDERPOWERED flag in the same cell as the value. Ask states why it is switched off instead of letting a user compose a question and meet a 503. Verification lists recorded answers instead of asking for an integer primary key, and now carries arm/run/text filters in the URL - it could previously reach only the newest 200 of 1,646 answers, leaving 8 of the 14 arms unreachable. **A live probe of the running containers on 2026-09-10 found 20 further defects the unit suite passed through** (RX-051), including an upload path that could not succeed at any file size, an arbiter panel asserting "not triggered" for arms that have no arbiter, and a dashboard figure of mine that was nearly double the truth. Research now plots each arm's AUROC and the ablation contrasts as point-and-interval charts: an arm with no detector is drawn as ABSENT rather than at 0.5, and charts are drawn only within a single run because a shared axis across bindings is RX-047 in visual form |
| 22 | Evaluation framework | **COMPLETE** | correctness/QA/detection/statistics/efficiency, 62 tests. RX-031 fixed the correctness predicate (44 of 115 gold answers affected). **Now run on 787 rows across three campaigns**, producing every figure this project reports. The efficiency reader was reading `record[channel]['tokens']` while the recorder writes `usage.total_tokens`, so every efficiency figure was 0.0; fixed, and it revealed B5 costs 2.4x arm A |
| 23 | Baselines | **PARTIAL** | **All five are run, but not all five on the held-out split, and the row said "all five are RUN" with no qualifier.** B3 program-only ran on validation only (RX-037); `campaign_20260905T112212Z`'s arms are A, B5, G, H, B1, B2, B4, so the **held-out baseline comparison covers 4 of the 5**. Every held-out baseline figure also comes from a split this project records as spent against a VOID methodology freeze (D48), which the row did not mention. Detail: B1 closed-book (0.000 accuracy, abstains 97.8%), B2 plain RAG (0.400 / 0.328), B4 agentic RAG (0.311 on test) and B5 self-consistency (2.4x arm A's tokens for AUROC 0.600) ran as arms of the two 7-arm campaigns; B3 program-only ran separately (RX-037, n=45): 0.289 overall and **0.929 on the 14 questions it answers at all** |
| 24 | Ablation | **PARTIAL** | arms A-H defined, each differing from A in exactly one field, with a test pinning that property. **A-F have now all run on one binding** (RX-047, RX-049): 225 rows for B-F plus a 45-row arm A rebase, same split, same 45 questions. The contrasts are computed by `evaluation/ablation/contrasts.py` under 12 tests, not by hand — RX-050 found that the previous table came from a scratch script and that one of its ten cells was wrong. **Over all rows A − C survives Holm and nothing else does**; the committed-only family carries **3 errors** and is flagged underpowered rather than read as a second confirmation. **PARTIAL because B5, G and H remain on the retired `gpt-oss-120b` binding**, so H1/H3/H4 stay NOT TESTABLE on a single binding (~405 requests to rebase), and because these arms ran after the test split was spent and can never be held out |
| 25 | Error analysis | **COMPLETE** | H2 stratifier + both taxonomy axes, 31 tests. **Run on all three campaigns** (RX-038/039/043/044). Its refusals are load-bearing: `evidence_was_retrieved` returns `None` rather than `False` when undecidable, and 8 test questions sit in `unknown` rather than being folded into the stratum that would make H2 look supported |
| 26 | FinVerify-IND | **PARTIAL** | **192 questions**, all judged by the owner: **115 validated, 29 rejected, 48 train-split pending by choice**. Usable as gold: 45 validation, 61 test (spent), 2 train. Validation rate rose 44% -> 83% -> 92% as D42/RX-027/RX-029 landed. **Two open defects**: 22 corrected rows had their stale anchors dropped (RX-042) and need re-derivation, and FI82a95e99 / FIce2f3063 record answers that appear on **0 pages** of their filings and need a person with the PDF |
| 27 | Case study | **COMPLETE** | **The faculty submission, `FinVerify_AI_Final_Case_Study.docx`, was generated on 2026-09-13** (built outside the repository and gitignored - its title page carries personal identifiers; see [`docs/README.md`](docs/README.md)) on the course template, with every figure re-derived from the artifacts and every screenshot taken from the running application; its fact check corrected stale claims in this file, CASE_STUDY_REPORT.md, DEMO.md, RAG.md, README.md and COMPLETION_GUIDE.md (CHANGELOG 2026-09-13). [`CASE_STUDY_REPORT.md`](CASE_STUDY_REPORT.md) written from real run artifacts - both campaigns, the oracle diagnostic, 10 stated limitations (§13; the row previously said 15) and no projected figures. `write_case_study.py` regenerates [`CASE_STUDY.md`](CASE_STUDY.md) from whatever runs exist and refuses to render without data. **A §43 audit found the generated half 11 days stale and it was regenerated on 2026-09-12**: CASE_STUDY.md still reported "Arm B3 over 45 graded questions" from 2026-09-01, with "Disagreement rate 0.0%" and "both channels agreed and both were wrong 0 of 45" for a SINGLE-CHANNEL arm, predating the test campaign, the oracle run and the ablation — while CASE_STUDY_REPORT.md declares it authoritative over itself. Now arm A over 61 held-out questions, 36.1%, matching the report. Regenerating it exposed RX-052: the token reader used a key the recorder abandoned in 2026-09, so the document printed "no row recorded token usage" over an artifact where all 61 rows record it — the identical defect fixed in `efficiency.py` and never propagated. Now 6445 tokens/question, agreeing with the efficiency report's 6444.85. Two dangling §13.11 references repointed to RX-042, and the limitation count corrected |
| 28 | Experiment management | **COMPLETE** | append-only, resumable, question-major, budget-before-spend, live-binding preflight (D46) and a methodology-freeze gate on the test split (D48). **17 campaign runs preserved** (41 run directories in all), including every void, superseded and smoke run with a marker file saying which and why |
| 29 | Security | **COMPLETE** | AST allowlist + container isolation verified against real containers; no host fallback |
| 30 | Testing | **COMPLETE** | **1,464 passing, 1 skipped.** Includes spec 38 failure cases, 54 sandbox-escape attempts, real-document extraction, real container execution, real PostgreSQL and real Tesseract. Tests needing absent services skip rather than pass. See below |
| 31 | Deployment | **COMPLETE** | full 4-container stack built and verified end to end — **27/27 checks against the running containers**. A §43 audit found that check failing: `build_ref` is a Docker **build arg**, so it is baked at image build and `--force-recreate` cannot carry a new value, and DEPLOYMENT.md's start command set neither `BUILD_REF` nor `--build` (README.md and DEMO.md did). Anyone following the deployment document therefore got a stack reporting `build_ref: unknown` and failing the project's own verifier — on the one field that exists to catch a container serving stale code. DEPLOYMENT.md now documents both, and `scripts/verify_deployed_stack.py`, which until 2026-09-12 was referenced in no document in the repository |
| 32 | Documentation | **COMPLETE** | all **19** documents spec §40 names exist at the repository root and are current as of 2026-09-06. `docs/` is deliberately a pointer rather than a second location — §41 lists the directory, but the §40 filenames are the contract and splitting the set would mean a reader has to know which half holds what; the reasoning is recorded in [`docs/README.md`](docs/README.md). **The audit found real staleness and fixed it**, which is the part worth recording: `AGENTS.md` still claimed **cross-vendor** independence with a ✅ five days after D44 gave it up — the error running in the flattering direction — plus a withdrawn parse-failure rate, a README describing a 4-row campaign, three wrong figure counts and a test count 332 short |

**Re-audited 2026-09-06 against the spec §43 checklist.** This table said
*"Nothing is `COMPLETE`. Most modules are one campaign away — the spec's
acceptance checklists ask for evidence from a run, and there has not been one."*
That premise stopped being true on 2026-09-01. **Three campaigns have now run:**
validation (315 rows), test (427 rows) and the oracle diagnostic (45 rows). Six
rows still said *"Not yet run"* about arms that had run twice.

The promotions below are the checklist honestly applied to evidence that now
exists — not a relabelling. **Five modules were deliberately not promoted, and
one was demoted**, because their own acceptance criteria are still unmet:

| still short | what is actually missing |
|---|---|
| **10 Deterministic verification** → **BLOCKED** | applicable on 1 question of 45, produced a value on none. Both routes out are now measured and closed (RX-041, RX-046); the blocker is upstream in header recovery |
| 0 Research foundation | no live methodology freeze (D48) — structural, and not retroactively fixable |
| 6 Hybrid RAG | evidence accuracy 0.08–0.56; Tata Motors 0.080 undiagnosed |
| 15 Confidence & risk | 3-level score; committed-only held-out AUROC 0.679 [0.500, 0.839] |
| 24 Ablation | A-F clean on one binding (RX-049, contrasts corrected in RX-050); B5/G/H still on the retired one, so H1/H3/H4 stay untestable |
| 26 FinVerify-IND | 22 rows with dropped anchors; 2 unresolvable answers |

**Module 24 is the one that matters for the research claim, and it now has a
result.** A–F have run on one binding over one set of 45 questions, and over all
rows **A − C survives Holm while nothing else does** — removing the executed
program costs the detector 0.102 AUROC where removing the natural channel costs
0.066 and does not survive correction.

**Two things keep it from being more than that.** The committed-only family — the
one a hallucination claim actually needs, since a hallucination is a *committed*
wrong figure — holds **three errors**, so it cannot corroborate the asymmetry in
either direction, and it is flagged underpowered wherever it appears. And B5, G
and H remain on the retired binding, so **H1, H3 and H4 are still NOT TESTABLE**
on a single binding; the spec's hypotheses are not what this result answers.

The earlier claim that A − C survived "in both families, nothing else does" was
withdrawn in RX-050: it came from a scratch script with a wrong cell, and the
contrasts are now generated by tested code.

---

## Environment

| | |
|---|---|
| Python | 3.12.10 venv, 122 packages pinned ✓ |
| PostgreSQL 16 | running on **5433** (D31), schema migrated ✓ |
| Qdrant | `finverify_e5` **22,930 chunks, 5/5 companies** ✓ (`finverify_chunks` 906 = superseded BGE index, recorded in the manifest) |
| Docker | up; API + frontend images built ✓ |
| Tesseract OCR | 5.4.0 ✓ — resolved by path, not PATH (D32) |
| Node / npm | 24.19.0 / 11.17.0, frontend builds ✓ |
| LLM (Groq) | **VERIFIED** by real calls, all four bindings |
| Gemini | key valid; **20 req/day** enforced (RX-006) |
| NVIDIA NIM | **VERIFIED** by real calls; Channel B (D21). Daily limit unobserved |

---

## Test suite

**1,464 passing, 1 skipped** (`pytest`, ~8 min, run 2026-09-13). Lint clean under a pinned
ruff rule set. 637 three sessions ago, 999 two sessions ago, 1,243, then 1,335.
The most recent additions pin the **methodology-freeze gate** (10 tests: void
matched case-insensitively, mixed live-and-void tags, and three ways for git to
fail — all of which must return `None` so the caller can tell *"no live freeze"*
from *"could not find out"* and refuse on both) and that **every recorded row
carries its explanation**, which 787 rows of campaign data did not.

One of those ten failed when first written, and it was the useful one: a
tab-less continuation line from a multi-line tag annotation parsed as a tag name
with an empty subject, which contains no "VOID" and so counted as a **live**
freeze. The gate could have been opened by a sentence inside the void tag's own
body. Fixed in the parser rather than in the test.

The one conditional skip is `test_the_schema_applies_to_real_postgres`, which
skips rather than passes when no PostgreSQL is configured — so a green run on a
machine without it never reads as "the schema was verified". On a machine also
missing Docker, Tesseract or the source filings the count drops further and each
skip names what was absent. That is the point. Includes spec §38
failure cases, 54 sandbox-escape attempts, real-document extraction, real
container execution, real PostgreSQL, and real Tesseract.

Tests that need Docker, PostgreSQL, Tesseract, or the gitignored filings are
**skipped, not passed**, when those are absent — so a green run on a bare
machine never reads as "verified".

---

## Risks

### Research validity

1. **Nothing has been measured end to end yet.** Every metric module is tested
   against synthetic data. The first real campaign will find things the tests
   did not — RX-007 found four research-validity defects in a green suite, and
   every one was found by running the system rather than by a test.
2. **Channel independence is cross-vendor** (D21, RX-014) — Groq vs NVIDIA NIM.
   The residual risk moved rather than vanished: NVIDIA's free 550B endpoint
   sheds load with HTTP 503 under contention, so **latency measured there is not
   a property of the method** and must not be reported. H4 tests whether the
   vendor diversity earns anything, and either outcome is informative.
3. **Retrieval bounds the whole contribution, and 0.909 was a development-set
   number.** Against corrected gold it is **0.08–0.56** (RX-028), so H2's
   retrieval-caused stratum is likely to be *most* of the questions rather than
   the ~9% Infosys implied. This is the single largest threat to the result: on
   a question whose evidence never reaches the channels, neither can be right
   and their agreement measures nothing.
   **No longer Infosys-only.** Gold evidence spans now exist for all five
   filings (100 evidence groups across the four generated sets, plus Infosys'
   hand-built 22). Infosys is excluded from cross-company means because its gold
   is hand-built rather than generated — a difference that is itself an untested
   candidate explanation for the transfer gap.
   **RX-034 bounds the headroom**: the right chunk is a candidate for 87% of
   evidence groups and within rank 70 for 72%, so this is a ranking problem with
   a large ceiling rather than a coverage failure. **RX-035 tried the lever that
   implies and it did not move**: cross-encoder reranking scored 0.240 against a
   0.280 baseline over the same 100 evidence groups. **Superseded 2026-09-13:**
   those groups were 43% test-derived (RX-053); on validation-only gold the reranker
   scored 0.156 against 0.125 (RX-035-revalidated), no measurable effect, and it
   stays unadopted for cost.
4. **Shared evidence is an irreducible common-mode failure** (H2), and one
   `QuestionSpec` feeds both channels (D19). Measured by stratification, not
   solved.
5. **Definitional ambiguity is indistinguishable from hallucination** (RX-007).
   Handled by D22's pinned main set plus a 10-question ambiguity subset scored
   separately.
6. **The methodology was not frozen when the test split was spent** (D48).
   `methodology-freeze-v1` is annotated VOID (D46), v2 was never cut, and the
   test split was evaluated anyway on 2026-09-05 — three of D46's four
   preconditions skipped. AUROC and every between-arm comparison are unaffected;
   precision, recall, F1 and FPR at 0.51125 are **indicative**. The threshold is
   deliberately *not* re-selected now: it was chosen before any test row existed
   and re-choosing it today, with the results read, would add leakage to a number
   that has none. `run_campaign.py` now refuses `--split test` without a live
   freeze tag, which is the check that was missing.
7. **Literature currency** — the gap claim needs a re-sweep before submission.

### Project

8. **Human validation is the binding constraint, and it is one person.** No
   inter-annotator agreement can be computed; D23's double-pass on ~20%
   estimates intra-annotator consistency instead, and the write-up must not use
   the stronger term.
9. **Throughput is quota-bound** (D14). The full 13-arm × 150-question campaign
   is **4,350 requests** at the ceiling. The campaign runner is resumable and
   question-major for exactly this reason.
10. **Free models are weaker than frontier models.** The claim is about
    *relative* detection across arms on identical inputs. Absolute figures must
    never be set beside frontier-model results as though the setups matched.
11. **~~Candidate coverage is uneven~~ — largely resolved by D42.** It was
    HDFC Bank 127 / Sun Pharma 48 / Infosys 45 / Reliance 29 / Tata Motors 19
    (D34), with HDFC at **47%** of the set. Rebuilding so every question names
    the statements it was answered from evened it out: the 192 candidates are
    now Infosys 45, Reliance 45, HDFC 37, Tata 36, Sun Pharma 29 — **largest
    share 23.4%**, and the 108 usable gold answers run 25.9% down to 11.1%.
    Results should still be stratified, but no pooled metric is now
    substantially a statement about one bank.
    **The live constraint is smaller samples, not skew**: the campaign will
    reach roughly 22–40 of the 45 validation questions before the deadline, and
    only **1 of the 45 is multi-hop** — so H1 is tested almost entirely on
    single-figure lookups, and the write-up must say so.
12. **Benchmark licensing** unverified for FinQA / TAT-QA / ConvFinQA.

### Security

13. Sandbox verified against real containers: network unreachable, read-only
    filesystem, non-root uid, timeout kill and cleanup, memory cap, no state
    leakage. No host-execution fallback exists. Residual risk: container escape
    via a Docker or kernel vulnerability is outside this project's control and
    is not claimed to be mitigated.
14. **The API has no TLS, authentication, or rate limiting.** It runs on
    localhost for a research project; exposing it to a network needs all three.
15. CPU-only host: embedding throughput constrains corpus size and reported
    latency.

---

## What changed in this session

Modules 3 (OCR), 4, 16, 17, 18, 20, 21, 22, 23, 24, 25, 27, 28 and 31 went from
NOT STARTED or PARTIAL-stub to implemented, tested and documented. Decisions
D26–D32 recorded.

**Thirteen** defects were found by **running** the system rather than by testing
it. Every one is now fixed. None was caught by a test suite that was green
throughout — and that is the finding, not an aside.

| Found | Consequence had it shipped |
|---|---|
| B5's risk score would have been quantised to 5 values | H1 "supported" partly because the baseline was rounded off |
| Quota exhaustion was silently becoming an abstention | Hours of a campaign writing rows indistinguishable from real abstentions |
| Camelot merges two year-columns into one cell | A prior-year figure returned as the current year, cited and plausible |
| Two PostgreSQL servers on 5432 | An afternoon; `docker compose ps` said healthy throughout |
| A missing dependency reported as an unreachable index | A deployment problem misattributed to a working service |
| Tesseract installed but not on PATH | Every scanned page recorded as unreadable — an environment fact attributed to the document |
| The document registry read with guessed key names | An empty documents screen; found by *looking at the rendered page*, since no fixture set the field |
| **The wrong Qdrant collection wired into the deployed stack** (D33) | Every non-Infosys question answered from Infosys evidence, by a *different* embedding model — a config error published as a method failure |
| **Live QA omitted `url=`, so it could never reach Qdrant in-container** | Every containerised question failing; masked only because live QA is off by default |
| **The console could not print `₹`** | Any script crashing on the first line of real Indian evidence, in a project about Indian filings |
| **`run_slice.py` hard-filtered retrieval to the Infosys document** | Written when the corpus was one filing; it answered questions about any other company from Infosys's pages, filter working correctly on the wrong document |
| **The campaign passed no company, so retrieval searched all five filings** (RX-012) | Evidence accuracy 0.727 instead of 0.909, MRR 0.616 → 0.357 — the whole result computed on evidence the channels largely never saw |
| **A compound scale token silently discarded** (RX-013) | HDFC's `K Cr` read as crore — correct here, and only by luck; a filer meaning thousand-crore would be misread 1000× with no warning |

---

## How to pick up

**[`COMPLETION_GUIDE.md`](COMPLETION_GUIDE.md) first** — it is the audited
picture: module tiers, five open defects, four owner blockers, and the ordered
path to a result. Then `PROJECT_STATUS.md` (here) → `TODO.md` → `DECISIONS.md` →
`EXPERIMENTS.md` RX-001…RX-010 for what has been measured and why the numbers
moved.

**Step 0 of the guide is not optional.** Nothing measured before the collection
wiring is fixed can be trusted.

**Read RX-007 first if you touch the verification layer.** Four
research-validity defects lived there through a green suite, and three pushed
the same direction: they inflated measured disagreement and depressed AUROC,
making the method look *worse* than it is.

**A note for the next audit:** two agents once edited source under a read-only
brief — one left `if False:` dead code in verdict logic. Give audit agents
explicitly read-only instructions.

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts\verify_environment.py
.\.venv\Scripts\python.exe scripts\verify_llm_providers.py     # real calls
.\.venv\Scripts\python.exe scripts\index_corpus.py --status
.\.venv\Scripts\python.exe scripts\build_finverify_ind.py audit
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

---

*Measurements: [`EXPERIMENTS.md`](EXPERIMENTS.md) · Decisions:
[`DECISIONS.md`](DECISIONS.md) · Next actions: [`TODO.md`](TODO.md) · Case
study: [`CASE_STUDY.md`](CASE_STUDY.md)*
