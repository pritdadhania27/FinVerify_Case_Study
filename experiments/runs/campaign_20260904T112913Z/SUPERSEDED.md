# SUPERSEDED — partial, do not analyse

**Run:** `campaign_20260904T112913Z` · test split · 183 of 427 rows
**Superseded by:** `campaign_20260905T112212Z`, which completed all 427 rows
**Not void.** The rows are valid; there are simply not enough of them, and a
complete run of the same configuration exists.

Same bindings as the final run — Channel A `nvidia/openai/gpt-oss-20b`,
Channel B `nvidia/nvidia/nemotron-3-ultra-550b-a55b` — started after D46
rebound Channel A and stopped partway.

Kept because `experiments/runs/` is append-only (D3), and because the count
matters: `experiments/test_set_access.log` records every read of the test set,
and this run is one of them. An honest audit trail includes the attempts that
did not finish.

**RX-039 reports `campaign_20260905T112212Z` and only that run.** Analysing this
one would produce a second, smaller set of test figures for the same
methodology, which is exactly the ambiguity a one-shot evaluation exists to
avoid.
