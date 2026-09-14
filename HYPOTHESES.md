# Hypotheses

Each hypothesis states a **directional prediction**, the **measurement**, and the
**observation that would falsify it**. Thresholds are fixed *before* experiments
run and are frozen with the evaluation methodology (spec §46.9). Moving a
threshold after seeing results is the failure mode these numbers exist to
prevent.

Statistical protocol for all tests: bootstrap 95% CIs (10,000 resamples) over
questions; paired comparisons on identical question sets; Holm–Bonferroni
correction across H1–H5. "Supported" requires the CI to exclude the null, not
merely a favourable point estimate.

---

## H1 — Cross-modality disagreement detects numerical error better than same-model self-consistency

**Prediction.** At matched API cost, dual-channel disagreement achieves higher
detection AUROC than n-sample self-consistency over a single model and modality.

**Rationale.** Homogeneous samples share the model's systematic biases, so a
confidently-held wrong belief yields consistent wrong samples (SelfCheckGPT's
structural limit). An executed program routes the arithmetic through an
interpreter that holds no beliefs — external feedback in the sense Huang et al.
show is necessary.

**Measurement.** AUROC on the frozen test set, error label = numerical mismatch
against gold beyond tolerance (`EVALUATION.md`).

**Falsified if.** Self-consistency AUROC ≥ dual-channel AUROC at equal measured
cost, or the CI on the difference includes zero.

**Prior belief:** moderate. The mechanism is sound, but both channels still share
one retrieval step and one question interpretation — a large shared-failure
surface that could dominate.

---

## H2 — Detection power is materially higher for reasoning-caused than retrieval-caused errors

**Prediction.** Detection AUROC on reasoning-caused errors exceeds AUROC on
retrieval-caused errors by a margin whose 95% CI excludes zero.

**Rationale.** Channels sharing an evidence set cannot disagree about evidence
they both accepted. Given the wrong number, both compute correctly and agree.
Agreement should therefore be near-blind to retrieval error by construction.

**Measurement.** Stratify by provenance using gold evidence spans: if the
retrieved set omits the gold span, the error is retrieval-caused; if the span was
retrieved and the answer is still wrong, it is reasoning-caused.

**Falsified if.** The two strata show statistically indistinguishable AUROC.

**Why this hypothesis is load-bearing.** If H2 fails, the causal story behind the
architecture is wrong even if H1 succeeds — H1 could then be passing for reasons
unrelated to the claimed mechanism. **H2 is the mechanism check on H1**, and a
positive H1 with a failed H2 must be reported as an unexplained correlation, not
as a validated design.

---

## H3 — Deterministic verification contributes signal beyond the two LLM channels

**Prediction.** Removing the deterministic verifier reduces detection F1 by a
margin whose CI excludes zero.

**Rationale.** It is the only component with no model in the arithmetic path.

**Falsified if.** Ablating it leaves detection performance unchanged — likely if
the program channel already catches every arithmetic slip the calculator would.

**Prior belief:** low-to-moderate. Program execution and deterministic
calculation may substantially overlap. Recorded here *before* running, so that a
null result reads as a prediction tested rather than a disappointment reframed.

---

## H4 — Cross-vendor channel diversity contributes beyond prompt diversity

**Prediction.** Running the two channels on models from *different vendors*
(Gemini vs a Groq-served open model) yields higher detection AUROC than running
both on the same model with the same two prompts.

**Rationale.** This is the direct empirical test of decision D1 — the central
validity assumption. If it fails, "independence" reduces to prompt/modality
difference alone, and the claim must be narrowed to exactly that.

**Falsified if.** Same-model and cross-model arms are statistically
indistinguishable.

**Note.** Either outcome is informative, and the failure case is the more useful
one: it would say modality independence does the work and model diversity is an
unnecessary cost.

**Strengthened by D8a, then narrowed again by D44 — and D44 is what ran.**

D8a's free-provider split meant the two channels could come from *different
organisations*: different training corpora, architectures and failure modes, a
substantially stronger form of independence than one vendor's checkpoints. That
is what this section used to claim, in the present tense.

**D44 (2026-09-01) gave it up.** To finish the campaign in a day rather than
eight, both channels moved onto one provider, and the independence claim narrowed
from cross-organisation to cross-*model*. Every campaign this project reports ran
under D44. So H4 as tested asks whether two models from one provider, given
structurally disjoint prompts, fail differently enough for disagreement to carry
signal — a weaker question than the one written above, and the weaker one is the
one with data behind it.

The stronger form is not refuted. It is untested, and it would need a
cross-provider arm nobody has run.

---

## H5 — The reliability gain is not cost-free, and the curve is not flat

**Prediction.** The full system attains the highest detection AUROC *and* the
highest per-question cost; at least one reduced configuration achieves ≥80% of
the full detection gain at ≤50% of the cost.

**Rationale.** Verification-agent invocations are triggered by disagreement, so
cost scales with the disagreement rate. Some components will not pay for
themselves.

**Falsified if.** No reduced configuration reaches the 80%/50% point, i.e. the
gain degrades roughly linearly with spend.

**Purpose.** Forces an honest cost accounting instead of reporting only the
best-performing arm (spec §46.5).

---

## Pre-registration

These hypotheses, the thresholds above, and the metric definitions in
`EVALUATION.md` were **intended to be frozen before the final test-set
evaluation**, with the freeze recorded as a git tag so the commit history shows
the predictions predate the results.

> **That freeze is VOID, and this section must not be read as though it holds.**
> `methodology-freeze-v1` is annotated VOID (D46): it froze a methodology onto a
> model the provider retired two days later. No v2 was ever cut, so
> `live_freeze_tags()` returns empty and `run_campaign.py` now refuses
> `--split test` outright. The test split had already been spent against the
> voided tag — seven accesses logged in `experiments/test_set_access.log`
> between 2026-09-03 and 2026-09-05.
>
> The consequence, stated plainly: **the held-out figures this project reports
> are not pre-registered.** They are honest measurements on a split that was
> used once, but the mark that would prove the predictions came first is not
> there. D48 records why it cannot be repaired — backdating a tag onto a spent
> split would place a pre-registration mark on a decision that was not
> pre-registered, which is the precise thing pre-registration exists to rule
> out. This is why Module 0 is PARTIAL and will stay PARTIAL.

Any change after the freeze must be documented in `DECISIONS.md` with its date
and reason, and results must then be reported both ways — under the frozen
methodology and under the revised one.
