# VOID — 22 test-split rows, every one with a dead Channel A

**Run:** `campaign_20260903T161320Z` · **test split** · arms A B5 G H B1 B2 B4
**Stopped:** 2026-09-03 after 22 of 427 rows
**Reason:** `nvidia/openai/gpt-oss-120b` had reached end of life eight hours
earlier and returned HTTP 410 to every Channel A call.

`config.json` records `natural_model: openai/gpt-oss-120b`. Every row carries
`natural.error_type: ProviderUnavailableError`. Three rows show a non-null
answer, produced by the program channel alone with Channel A dead — which is
arm A degraded into a single-channel system, not arm A.

This is the RX-033 failure shape exactly: rows that exist, look complete, and
measure the provider rather than the method.

## Why it got past the check that was supposed to stop it

The model preflight in `run_campaign.py` — one real completion per binding
before the slow startup — was written **in response to this run**, not before
it. An earlier attempt, `campaign_20260903T160937Z`, was caught during startup
and wrote nothing. This one, started minutes later, got through.

The preflight now exists and refuses with exit 2, naming the dead binding. It
would have stopped this in seconds.

## This does NOT consume the one-shot test evaluation

The test split is evaluated once per **frozen methodology version**, and the
rule protects against one specific thing: fitting choices to test data. Nothing
here could do that. Every Channel A call failed, no metric was computed, no
threshold or design decision was informed by these rows, and no analysis was
run. There is nothing to leak.

`experiments/test_set_access.log` records the access, which is the point of
keeping that log — the count is auditable rather than asserted.

What the rows do establish, and it is worth keeping them for: a provider can
retire a model mid-project and the pipeline will keep writing rows that look
structurally fine. That is why they are preserved rather than deleted (D3).

## What has to happen before the test split is evaluated

Not simply a re-run on the new binding. `methodology-freeze-v1` froze Channel A
to a model that no longer exists, and the validation campaign and threshold both
came from it. Per D46:

1. Re-run **validation** on `nvidia/openai/gpt-oss-20b` — 315 rows, 765 requests.
2. Re-select the operating threshold from that run.
3. Cut `methodology-freeze-v2`.
4. Then evaluate the test split, once, against that tag.
