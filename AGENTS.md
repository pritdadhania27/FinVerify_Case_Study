# Agents

Four agents, and the interesting thing about each is what it is **forbidden** to
see.

| Agent | Sees | Deliberately never sees |
|---|---|---|
| Question understanding | the question | — |
| Channel A (natural language) | question, evidence | Channel B, the program, its output |
| Channel B (program) | question, evidence | **Channel A's answer, reasoning, confidence, or existence** |
| Verification agent (arbiter) | question, evidence, two anonymised answers | which channel produced which answer |

---

## Question understanding

`backend/agents/question_understanding.py`

Deterministic parser plus a metric lexicon. Produces one `QuestionSpec`:
operation, expected unit, fiscal year, company, sub-questions, ambiguities.

Emits `(filters, sub-questions)`, not just intent — fiscal year and company route
to metadata filters, and multi-hop questions decompose per figure. That
decomposition raised evidence-retrieval accuracy from 0.818 to **0.909**
(RX-005), which is the largest single measured gain in the project — **on
Infosys**. RX-015 measured the other four filings at 0.38–0.61, so that figure
describes the document the system was tuned on, not the corpus.

**This is a common-mode failure path, and it is recorded as one (D19).** One
`QuestionSpec` feeds both channels, so a mis-parse makes both wrong identically
and their agreement proves nothing. The taxonomy therefore carries
`QUESTION_UNDERSTANDING` as its own provenance rather than folding it into
reasoning.

An LLM refinement path exists and **has never been exercised against a live
provider**. It is in `TODO.md`, and until it runs, nothing may be claimed for it.

---

## Channel A — natural language

`backend/agents/natural_channel.py`

Reads the evidence and reasons in prose, returning JSON: answer, unit, reasoning,
evidence used, figures used, and `sufficient`.

Robust parsing was earned rather than designed: it handles JSON in prose, in
fences, after a reasoning model's `<think>` block, empty replies (D17 — these
models return HTTP 200 with an empty string when the token budget runs out), and
declined answers.

`sufficient=False` is a **decision** and is scored as an abstention. An
unparseable reply is a **bug** and is scored as a parse failure. Collapsing them
would report a parser defect as principled caution, so the two rates are reported
separately.

The parse-failure rate is now **measured**: **0.022 on validation** (1 question
in 45, on arms A, B1 and B4 — 0.000 on the other four) and **0.000 across all
seven arms on the 427-row held-out test run**. RX-019's earlier 35% is
**withdrawn**: it was measured while the measurement itself was consuming the
day's token allowance, and returned 35%, 45% and 95% on identical inputs.

Read the abstention rate beside it, not instead of it: the same runs abstain on
**0.41–0.49** of questions. Reporting one number for "did not answer" would fold
a 41% principled refusal into a 0% parser defect and lose the distinction this
section exists to make.

That zero is conditional on `LLM_MAX_TOKENS=4096`. At 1024 the rate is not zero:
RX-032 asked six questions at both budgets, paired, and got **3 parse failures at
1024 that 4096 answered, and none the other way**. The bound model emits
`<think>` reasoning before its reply, so 1024 truncates it mid-structure. A
"cheaper campaign" at 1024 would have been a campaign of parse failures.

---

## Channel B — program

`backend/agents/program_channel.py`

Generates Python, validates it against an AST allowlist, executes it in a
disposable container, and parses the result.

```python
def run_program_channel(
    question: str,
    evidence: list[EvidenceBlock],
    *,
    provider, model, temperature=0.0, max_tokens=2048,
    name="program", sandbox_config=None,
) -> ProgramChannelResult:
```

**Note the parameter list.** There is deliberately no way to pass Channel A's
output here. That is decision D1 expressed as code rather than as a convention:
adding such a parameter would make channel agreement a measure of anchoring, and
the research contribution would die quietly.

Two tests defend it — one inspects the signature, one asserts that no Channel-A
payload marker reaches the program prompt through the orchestration graph. **If
either fails, that is a research-validity bug, not a test to update.**

Policy runs before containment: validation failures are **reported, never
executed anyway "just to see"**. If Docker is down, execution is `BLOCKED` — there
is no host fallback, because a verification channel that silently ran untrusted
generated code on the host would be worse than no verification channel.

---

## The verification agent

`backend/agents/verification_agent.py`

Triggered **only on DISAGREE**. Sees the question, the evidence, and two
answers — **without being told which channel produced which**. A verifier that
can see the channels it is checking is anchoring, not verifying.

Three refusals:

- It will not adjudicate an `UNCERTAIN` verdict. One answer and one silence is
  not a disagreement.
- **Abstention is a first-class outcome.** When the evidence does not settle the
  question it declines, the answer stays flagged as risky, and the risk layer
  treats the abstention as a signal rather than a resolution.
- Its result is recorded separately and **never folded into channel accuracy**
  (EVALUATION.md §5.4).

Its resolution accuracy is unmeasured; it needs gold that pins the metric
definition, so it follows validation.

---

## The third opinion: the deterministic channel

Not an agent — it has no model at all — but it sits in the same position.
`operand_binding.py` binds operands with a regex and the metric lexicon, so
**for a question it can bind, it cannot hallucinate**. It refuses rather than
guesses, which is why it applies to a minority of questions and why
`applicable=False` is reported distinctly from `available=False`.

It is the only thing that can overrule two agreeing channels — the
*both-agree-wrong* cell that EVALUATION.md §5.4 calls the method's blind spot.

---

## Independence, stated precisely

| Level | Claim | Status |
|---|---|---|
| Modality | prose reasoning vs executed code | ✅ by construction |
| Prompt | structurally disjoint | ✅ by construction |
| State | no cross-channel reads | ✅ enforced by the signature, test-gated |
| Model | different checkpoints | ✅ |
| Lab | different training corpora and architectures | ✅ gpt-oss vs Nemotron |
| **Vendor** | **different serving organisations** | ❌ **both on NVIDIA NIM since D44** |

**Independence is same-provider, cross-lab — NOT cross-vendor.** It was
cross-vendor from 2026-08-29 (RX-014, Groq vs NVIDIA), and **D44 gave that up on
2026-09-01**: Groq's 200,000 tokens/day was the constraint that made a seven-arm
campaign an eight-day job and left three baselines unrunnable, because a baseline
must share arm A's natural model. Both channels moved to NVIDIA. Channel A is
`openai/gpt-oss-20b` (D46, after the 120b it was first bound to reached end of
life two days later); Channel B is `nvidia/nemotron-3-ultra-550b-a55b`.

**What was actually lost**, stated rather than minimised: correlated
availability, and any transformation the shared serving layer applies to both
channels. The second is **unmeasured**, and it is the real limitation — if one
provider's stack systematically shaped both replies, the channels would be less
independent than every row above suggests and nothing here would detect it.

`channels_are_independent()` returns the exact string *"same provider, different
models"*, and every run's `config.json` records it, so **no artifact can inherit
a stronger independence claim than the binding that produced it.** That is the
mitigation: not a guarantee, an audit trail.

**Write-ups must say "same provider, different models"** — or "cross-lab".
"Cross-vendor" is now false, and it was the stronger claim, so the error would
run in the flattering direction.

H4 still tests whether model diversity contributes beyond prompt and modality
diversity, and a null result there would be the more useful outcome — it would
say modality independence does the work.

---

## Orchestration

`backend/agents/orchestrator.py` — LangGraph over eight nodes, branching to the
arbiter only on a real DISAGREE verdict.

Every arm, baseline or ablation, is an `ArmConfig` over **the same graph** (D26).
A disabled component appears in the node log as `skipped` rather than being
absent, so a reader can confirm from the artifact alone that arm C really ran
without the program channel.

Retry defaults to **off**. `max_transient_retries` retries only
`ProviderUnavailableError` — never a rate limit, never auth, never quota. A retry
loop on top of the adapter's own is how a day's free-tier allowance disappears in
a minute.

---

*Verification: [`VERIFICATION.md`](VERIFICATION.md) · Decisions:
[`DECISIONS.md`](DECISIONS.md) D1, D19, D21, D26 · Architecture:
[`ARCHITECTURE.md`](ARCHITECTURE.md)*
