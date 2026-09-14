# VOID — never started, 0 rows

**Run:** `campaign_20260903T160937Z` · **test split** · arms A B5 G H B1 B2 B4
**Stopped:** 2026-09-03, during startup, before a single row was written
**Reason:** Channel A's model had died eight hours earlier.

`nvidia/openai/gpt-oss-120b` reached **end of life at 2026-09-03T08:00:00Z** and
returns:

```
HTTP 410 Gone
"The model 'openai/gpt-oss-120b' has reached its end of life on
 2026-09-03T08:00:00Z and is no longer available."
```

D44 bound Channel A to it on 2026-09-01. It lasted two days.

## What stopped this from becoming 427 bad rows

The run was caught in its startup phase — loading the embedding model and
rebuilding the BM25 cache — before its first API call, so **nothing was
written**. It surfaced because a DNS wobble against huggingface.co produced a
wall of retry noise in the console, which prompted a check of whether the
provider was reachable at all. The DNS errors were harmless (the embedding model
loaded from cache); the check they triggered was not.

410 is deliberately absent from `_RETRYABLE_STATUS`, so the adapter would have
failed fast rather than burning retries. It would still have written a full test
split of rows with a dead Channel A.

## The listing cannot be trusted, and this is the second proof today

`GET /models` **still lists `openai/gpt-oss-120b`** after it started returning
410. It also lists `qwen/qwen3.6-27b`, which returns `404 page not found`.

CLAUDE.md's rule — *never hard-code a model id; discover with `--list`* — is
half of what is needed. The listing is not evidence a model is servable. Only a
real completion is, which is what `verify_llm_providers.py` does and why it
exists.

## What replaces it

`nvidia/openai/gpt-oss-20b`, verified by live call: 6.5s, correct answer, and
still a different lab from Channel B's Nemotron, so D1 independence holds at the
model level exactly as D44 left it. Probed and rejected: `qwen/qwen3.6-27b`
(404), `deepseek-ai/deepseek-v4-pro-0813` and `mistralai/mistral-large-2-instruct`
(no response inside 400s each).

## Why the test split cannot simply be re-pointed at the new model

`campaign_20260901T105355Z` — the 315-row validation campaign, RX-038 — ran on
gpt-oss-120b, and so did the threshold frozen from it as D45. Evaluating the test
split on gpt-oss-20b would compare a held-out result against a validation
baseline produced by a **different Channel A model**, which measures the model
swap rather than the system.

`methodology-freeze-v1` is therefore void as a basis for test evaluation: it
freezes a binding the provider no longer serves. Validation has to be re-run on
gpt-oss-20b, the threshold re-selected from it, and a new tag cut, before the
test split is touched.
