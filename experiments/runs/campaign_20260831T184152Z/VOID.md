# This run is VOID. Do not analyse it. Do not resume it.

**Run:** `campaign_20260831T184152Z` · **Date:** 2026-08-31 · **Void because:**
Channel A was rate-limited for 166 of its 180 rows and the campaign did not stop.

Preserved rather than deleted, because `experiments/runs/` is append-only and a
failed run is evidence (spec §46.7). Recorded as **RX-033**.

## What it looks like, and why that is the danger

It reports `completed 180, skipped 0, failed 0`. All 45 questions, all 4 arms.
Nothing in the summary says anything went wrong.

| | |
|---|---|
| rows | 180 (45 questions x A, B5, G, H) |
| **Channel A unavailable — `DailyQuotaExhausted`** | **166** |
| Channel A actually ran | 10 |
| rows with any answer | 35 |
| verdicts | 131 `UNCERTAIN`, 4 `AGREE`, 45 none |

Read naively, this run says the dual-channel system is uncertain on 97% of
questions. That finding is entirely manufactured by a rate limit.

## Why it happened

Two failures, and only the second is a bug.

**Sequencing.** The run was launched with ~68,000 of the day's 200,000 Groq
tokens left — the rest had gone on the RX-032 measurement and provider health
checks. That alone was harmless: the campaign should have completed about two
questions and stopped for resumption.

**The stop did not fire.** `campaign.py` stops on `QuotaExhaustedError`, which is
the *provider* refusing mid-call. The refusal that actually fires first is
`DailyQuotaExhausted`, the *local limiter* declining to make the call at all —
and the two were unrelated exception classes. `_raise_if_quota_exhausted` in the
orchestrator, whose docstring predicts this exact outcome in this exact wording,
matched only the provider-side class name. So each refusal fell through to the
ordinary channel-failure path, which records an unavailable channel and
continues, by design.

The tests passed throughout because they simulate `QuotaExhaustedError`. The
client-side path was never exercised end to end.

## Why it must not be resumed

`recorder.completed()` treats any recorded `(arm, question_id)` pair as done.
Resuming this run id would **skip all 166 dead rows** and fill in only the
missing ones, producing a campaign that reports as complete and is built on them.

Start a new run instead. The fixes are in `backend/services/llm/ratelimit.py`
(`DailyQuotaExhausted` is now a `QuotaExhaustedError`), `orchestrator.py` (the
guard derives its class names from the hierarchy, and `_channel_record` now
records `error_type` so a dead channel is distinguishable from an abstention
without parsing prose), and `scripts/run_h1.py` (it analysed a stale run from
two days earlier, chosen by an alphabetical sort).

## The 14 live rows

They are real and are not thrown away, but they are not a result: 10 rows with a
working Channel A across 4 arms is not a balanced prefix of anything.
