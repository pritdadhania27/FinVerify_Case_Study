# VOID — do not resume, do not analyse, do not quote

**Run:** `campaign_20260901T104422Z` · arms A, B5, G, H, B1, B4 · validation split
**Stopped:** 2026-09-01 after 1 of 45 questions (6 rows)
**Reason:** a cold-start provider failure landed on question 1 and `--resume`
cannot repair it. Kept, not deleted, because `experiments/runs/` is append-only
(D3).

## What happened

First run under D44, which moved Channel A from Groq to
`nvidia/openai/gpt-oss-120b` and left Channel B on
`nvidia/nvidia/nemotron-3-ultra-550b-a55b`. On the first question, both arms that
call Nemotron failed their program channel:

| arm | program channel | result |
|---|---|---|
| A | nemotron-3-ultra | `ProviderUnavailableError` — HTTP 404, empty body |
| G | nemotron-3-ultra | `ProviderUnavailableError` — HTTP 404, empty body |
| H | gpt-oss-120b (same-model arm) | executed, value 12232 |
| B5, B1, B4 | no program channel | fine |

## It is a cold start, not the D44 provider move

Three things were checked before concluding, because "our change broke it" and
"this endpoint was always like this" call for opposite responses:

1. **Nemotron is not retired.** It is still in `--list`, and an isolated call
   right after the failure returned `25.00` in 24.9s.
2. **It is not contention between the two channels.** A probe alternating
   gpt-oss-120b and nemotron against the one provider with campaign-sized
   prompts ran 3/3 pairs clean, Nemotron at 32–40s.
3. **It predates D44.** B3's completed 45-row run
   (`campaign_20260901T071345Z`, RX-037) was single-channel Nemotron with no
   contention at all, and carried 2 `ProviderUnavailableError` rows of 45 —
   a 4.4% baseline for this endpoint.

The registry's own note on this provider already says a *cold first request*
triggers it. That is what this was: the campaign's first Nemotron call of the
session.

## Why resuming could not fix it

`completed_pairs()` keys on `(arm, question_id)` and reads what is on disk. A row
whose program channel failed is still a row, so `--resume` counts it done and
never retries it. The two failures would have stayed as permanent holes in arms A
and G — and on question 1 specifically, which makes one question systematically
worse for exactly the two dual-channel arms whose disagreement is the measurement.

A 2-in-90 hole is within this endpoint's measured 4.4% failure rate and would not
have invalidated the campaign. It is voided because the failure is
*systematic to the run's first question* rather than randomly distributed, and
because six calls is a trivial price to avoid arguing about that later.

## What replaces it

The same six arms, restarted against a now-warm endpoint. Nothing else changed.
