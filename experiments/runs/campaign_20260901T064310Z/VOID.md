# VOID — do not resume, do not analyse, do not quote

**Run:** `campaign_20260901T064310Z` · arm B3 · validation split
**Stopped:** 2026-09-01 after 10 of 45 questions
**Reason:** the abstention rule changed mid-run. Kept, not deleted, because
`experiments/runs/` is append-only (D3) and because the defect it exposed is the
reason the run was worth starting.

## What it found

This was the first arm ever run **without a natural channel**, and that is what
made the defect visible.

`PipelineResult.abstained` consulted only the natural channel. The program
channel makes the same decision — `program_channel.py` prints `{"value": null}`
when the evidence does not support an answer, and its own comment calls that
"an abstention... recorded as such" — but nothing downstream read it.

On every arm that *has* a natural channel the omission is invisible, because
that channel abstains on the same evidence and sets the flag anyway. On B3, the
program channel alone, it was not. Of the first 9 rows:

| outcome | rows | recorded as |
|---|---:|---|
| program executed, returned a value | 3 | correct |
| program executed, reported evidence insufficient | 2 | **parse failure** |
| program crashed (`RUNTIME_ERROR`) | 1 | parse failure |
| other / not yet graded | 3 | — |

The analysis printed `abstain 0.000, parse-fail 0.667`. Two of those six were
programs that ran cleanly in the sandbox and correctly declined — a principled
refusal reported as a parser bug. That is the exact inversion of what
`AnswerRecord`'s own docstring warns against: *"Collapsing them would report a
parser bug as principled caution."*

It does not move accuracy — EVALUATION.md §3 counts both as incorrect — but it
misattributes *why*, which is the entire reason the two rates are reported
separately.

## Why the rows cannot simply be re-analysed

The fix uses `ProgramChannelResult.executed` (i.e. `execution.ok`) as the
discriminator, so a timeout, a crash, or a non-JSON final line all stay
failures, and only a clean run that chose to return nothing counts as a
decision. That field was **not in the artifact** — `_channel_record` wrote
`failure_reason` prose and no execution flag — so recovering it from these rows
would mean matching on provider-adjacent wording, which is the habit RX-033 was
written to break.

`_channel_record` now records `executed`, so a future run whose abstention rule
changes can be re-analysed instead of re-run. These 10 rows predate that.

## What replaces it

A clean re-run of the same 45 questions under one consistent rule. It costs
nothing that matters: B3 is Channel B only, every call goes to NVIDIA, and
NVIDIA is not the provider the campaign is blocked on — Groq stood at 196,887 of
200,000 tokens throughout and was never touched.
