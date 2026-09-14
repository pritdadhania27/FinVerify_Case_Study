# Research Questions

Derived from the gap in `LITERATURE_REVIEW.md` §6. Each question states what
would count as an answer *and* what observation would make the answer negative —
a question that cannot come back "no" is not a research question.

**Central question (spec §10):**

> Can independent dual-channel reasoning and consistency verification reliably
> detect numerical hallucinations in agentic financial document QA?

"Reliably" is not left as a feeling. It is operationalised in `EVALUATION.md` as
detection AUROC with confidence intervals, measured against a labelled
correctness ground truth, and compared against named baselines.

---

## RQ1 — Does cross-modality disagreement beat same-model consistency?

Is disagreement between a natural-language reasoning channel and an *executed
program* channel a better detector of numerical error than repeated sampling from
a single model in a single modality (the SelfCheckGPT premise)?

- **Comparison:** dual-channel disagreement vs. n-sample self-consistency at
  matched API cost. Matching on cost matters — a method that wins only by
  spending 3× more has not been shown to be better, only more expensive.
- **Answered by:** AUROC for each detector over the same question set and the
  same correctness labels.
- **Negative result looks like:** self-consistency matches or exceeds
  cross-modality AUROC at equal cost. That is a publishable finding and will be
  reported as such.

## RQ2 — Which components actually carry the signal?

The architecture has four potential error-detection sources: the NL channel, the
program channel, the deterministic verifier, and the verification agent. Which
produce measurable improvement, and which are decoration?

- **Answered by:** the 7-arm ablation (spec §31), each arm scored on the same
  frozen test set.
- **Negative result looks like:** removing a component leaves detection
  performance statistically unchanged — that component is not earning its cost
  and the paper should say so rather than describe it as a contribution.

## RQ3 — Does the signal survive the shared-evidence problem?

Both channels read the *same* retrieved evidence. When retrieval surfaces the
wrong number, both channels compute faithfully from it and **agree on a wrong
answer**. So agreement should be blind to retrieval-caused error while remaining
informative about reasoning-caused error.

- **Answered by:** stratifying detection metrics by error provenance —
  retrieval-caused vs. reasoning-caused — using gold evidence spans to assign the
  label.
- **Why this is the most important question here:** an unstratified headline
  AUROC would silently average over a regime where the method is expected to be
  near-blind. Reporting one combined number would overstate the contribution.
- **Negative result looks like:** detection is no better on reasoning-caused
  errors than on retrieval-caused ones, which would undercut the causal story
  behind the whole design.

## RQ4 — What does the reliability cost?

Spec §33: how much additional reliability does dual-channel verification buy
relative to its computational cost?

- **Answered by:** detection performance plotted against measured cost (tokens,
  USD, latency, agent calls) for every arm — a curve, not a point.
- **Negative result looks like:** the cost-matched curve shows a cheaper
  configuration dominating the full system.

## RQ5 — Does deterministic verification add anything beyond the two LLM channels?

The deterministic calculator is fully external — no model in the loop once
operands and operation are fixed (see D6 for the honest scope limit). Does it
contribute signal the two LLM channels do not already provide?

- **Answered by:** the "without deterministic verifier" ablation arm, plus
  reporting **operand-binding accuracy** and **calculation correctness**
  separately, since only the latter is genuinely LLM-free.
- **Negative result looks like:** the deterministic verifier's verdicts are
  redundant with the program channel's — plausible, since both execute
  arithmetic, and worth knowing either way.

---

## Explicitly out of scope

Named so that absence reads as a decision rather than an oversight:

- Non-numerical financial hallucination (narrative claims, forward-looking
  statements). The subject here is numerical error.
- Multilingual reports. FinVerify-IND uses English-language Indian filings.
- Real-time market data. The corpus is static published reports.
- Fine-tuning. All channels use off-the-shelf models; the contribution is
  architectural, not a training result.
