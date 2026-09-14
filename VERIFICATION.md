# Verification

How the system decides an answer might be wrong, and what each part of that
decision is and is not entitled to claim.

---

## The chain

```
three channel answers
        ↓
consistency engine      → verdict + base_score + coverage
        ↓
verification agent      → only on DISAGREE; may abstain
        ↓
risk assessment         → continuous score, higher = more likely wrong
        ↓
explanation             → derived from the above, never generated
```

---

## 1. The consistency engine

`backend/verification/consistency.py`

Compares channel answers pairwise after canonicalisation, and returns a verdict
with a **continuous** score plus the two factors that produced it.

### Verdicts

| Verdict | Meaning |
|---|---|
| `AGREE` | every usable channel produced the same figure within tolerance |
| `DISAGREE` | usable channels produced different figures |
| `UNCERTAIN` | **fewer than two** channels produced a figure — nothing could be cross-checked |

`UNCERTAIN` is not a weak `AGREE`. With one answer there is no corroboration, and
calling that agreement would manufacture confidence out of missing data.

### Disagreement types

`SCALE_MISMATCH`, `SIGN_MISMATCH`, `CURRENCY_MISMATCH`, `UNIT_KIND_MISMATCH`,
`MAGNITUDE_MISMATCH`, `DETERMINISTIC_CONFLICT`, `CHANNEL_UNAVAILABLE`, `NONE`.

Scale mismatch is named separately rather than folded into magnitude because it
is *the* headline error class in Indian filings — a factor-of-10⁷ error that
looks entirely plausible — and folding it in would make it unreportable.

### The two factors, kept apart

```python
report.base_score   # agreement BEFORE the coverage penalty
report.coverage     # how much ACHIEVABLE corroboration was obtained
report.score        # base_score × coverage (× an overrule penalty)
```

They mean different things. `base_score` is **evidence of error** — the channels
actively contradict each other. `coverage` is **absence of corroboration**, which
is uncertainty. Pre-multiplying them discards the distinction and the risk layer
cannot recover it from the product.

### Achievable, not absolute

The coverage penalty counts channels that **could have answered and did not** —
not channels that were inapplicable.

This was a real defect, found by running the system. On a lookup question the
deterministic verifier abstains by design (there is no arithmetic to check), so
two of three channels answered and the score was capped at 0.90, while a computed
question where all three answered reached 1.00. Lookups are the majority of
financial QA questions, so **the score was partly encoding *"is this a lookup?"*
rather than *"is this answer wrong?"***, and it ranked every correct lookup below
every correct computed answer — a systematic confound in the primary metric.

---

## 2. The deterministic verifier

`backend/verification/deterministic.py`, `deterministic_channel.py`

Ten operations over `Decimal`, never float. The only component with **no model in
the arithmetic path**, which is what H3 tests.

### The honest scope of "deterministic" (D6, and stronger than D6 assumed)

D6 recorded that operand binding might originate from an LLM, making the verifier
deterministic only *given* (operands, operation). It did not turn out that way:
`operand_binding.py` binds operands with a regex and the metric lexicon, with no
model involved. **For a question it can bind, this channel cannot hallucinate.**

The price is coverage. It **refuses rather than guesses**, so it applies to a
minority of questions, and `applicable=False` is reported distinctly from
`available=False`:

- `applicable=False` — this question has no arithmetic to check. Not a gap.
- `available=False` — it should have applied and something failed. A gap.

Binding accuracy is not yet measured as its own metric. That is in `TODO.md`.

---

## 3. The verification agent (arbiter)

`backend/agents/verification_agent.py`

Triggered **only on DISAGREE**. It sees the question and the evidence; it never
sees which channel produced which answer.

Three properties, each a deliberate refusal:

- **It cannot adjudicate an absence.** `UNCERTAIN` leaves it alone — one answer
  and one silence is not a disagreement to resolve.
- **It does not know whose answer is whose.** A verifier that can see the
  channels it is checking is anchoring, not verifying (D1).
- **Abstention is a first-class outcome.** When the evidence does not settle the
  question it declines, the answer stays flagged as risky, and `assess()` treats
  the abstention as a risk signal rather than as a resolution. A manufactured
  resolution would remove the flag without removing the risk.

### The second property was half-true until 2026-09-06 (RX-045)

The prompt hides the channel *labels* — deliberately, with a comment saying
why — and then passed the natural channel to `CANDIDATE 1` and the program
channel to `CANDIDATE 2` on **every arbitration this project has ever run**.
Position is a perfect proxy for identity, so "it does not know whose answer is
whose" was false in the way that mattered, and first-position preference is a
documented behaviour in pairwise LLM judging.

Candidate order is now decided per question by a SHA-256 of the question text.
**Deterministic, not random**: runs are pinned at temperature 0 (D7a), and an
arbiter that reordered between runs would make a campaign irreproducible. It
comes out at 0.463 program-first across the 188 validated questions, the verdict
is translated back into channel terms before anything downstream reads it, and
`metadata.candidate_1` records the order actually shown — without which the
effect could never be measured after the fact, which is precisely the position
this project was in until it was.

### Resolution accuracy, measured

`EVALUATION.md` §5.4 asks for it, and it turned out to need no new gold at all —
every run artifact already records whether the arbiter fired, whether it
resolved, and what the final answer was. Across all three campaigns:

| | |
|---|---:|
| triggered | **17** (2.2%–8.2% of questions per arm) |
| resolved / declined | 16 / 1 |
| final answer correct | **3 of 16** |
| **a channel actually held the right answer** | **7 of 16** |
| **returned it when it was there** | **3 of 7** |

**Read the second denominator, not the first.** The arbiter fires on
disagreement, and on 9 of those 16 questions *neither* channel had a correct
answer — there was nothing to choose. "19% accurate" would be a statement about
how hard disagreement questions are.

These are the **old** arbiter's figures, on the fixed ordering. Nothing has been
run since the fix, so no claim is made that it is better.

---

## 4. The risk score

`backend/verification/confidence.py`

A **continuous** score in [0,1], **higher = more likely wrong**. That direction is
fixed by `EVALUATION.md` §5.1 and is the single easiest thing to get wrong: the
consistency score runs the other way, and feeding it in unflipped yields exactly
`1 − AUROC` — a plausible-looking number that inverts every conclusion.

### Signals

| Signal | Weight | Why |
|---|---|---|
| no evidence retrieved | 1.00 | agreement on nothing is agreement about nothing |
| deterministic conflict | 0.90 | the model-free channel contradicts two agreeing ones |
| hard disagreement (scale/sign/currency/unit) | 0.85 | categorical, not a near-miss |
| contradiction (`1 − base_score`) | ≤1.00 | scaled by how much they actually disagreed |
| missing corroboration (`1 − coverage`) | 0.45 | uncertainty, deliberately weighted below error |
| arbiter abstained | 0.55 | silence is not resolution |

Combined with a **damped noisy-OR**: the strongest signal sets a floor, weaker
ones close part of the remaining gap.

Sum was rejected — unbounded, and it lets several weak signals outrank one
certainty. Max was rejected — coarse, and AUROC counts ties as half-discordant,
so a max-based score throws away ranking information exactly where it matters.

### The four facets (spec §23)

`agreement`, `unit`, `evidence`, `arithmetic` — each `VERIFIED` / `PARTIAL` /
`FAILED` / `NOT_APPLICABLE`. Bands (`HIGH`/`MEDIUM`/`LOW`) are a **presentation
layer derived from the continuous score**, never the underlying representation.

### It says it is uncalibrated

`RiskAssessment.calibrated` is `False` and will be until the weights are fitted on
validated data. This matters for which metrics may be quoted:

- **AUROC is valid** — it is rank-based, and any strictly monotone recalibration
  leaves every rank unchanged.
- **Brier score and ECE are not** and must not be reported until calibration
  happens.

---

## 5. The explanation

`backend/verification/explanation.py`

**Derived, never generated** (D29). A test asserts the module contains no
provider call at all.

Asking a model to write *"why is this answer risky?"* would put a language model
in the one place the system claims to be trustworthy and let it produce a fluent
account that does not match the computation. A wrong number with a convincing
justification is more dangerous than a wrong number alone.

Every explanation answers four questions, the fourth being the one that makes it
an explanation rather than a status message: **what would change the verdict?**

---

## What verification cannot do

- **Both channels wrong in the same way.** If the retrieved evidence is wrong,
  both channels read it, both compute correctly, and both agree. This is
  architecturally irreducible; H2 measures it rather than solving it.
- **A mis-parsed question.** One `QuestionSpec` feeds everything (D19), so a
  mis-parse makes both channels wrong identically.
- **Definitional ambiguity.** RX-007 found all three channels splitting on return
  on equity because each used a different standard definition. The detector
  flagged the question as risky — correct by its own lights, wrong for a research
  question about *hallucination*. Handled by D22: the main set pins the
  definition, and a separate subset deliberately does not so the false-positive
  rate can be measured.

---

*Metrics: [`EVALUATION.md`](EVALUATION.md) · Hypotheses:
[`HYPOTHESES.md`](HYPOTHESES.md) · What running it found:
[`EXPERIMENTS.md`](EXPERIMENTS.md) RX-007*
