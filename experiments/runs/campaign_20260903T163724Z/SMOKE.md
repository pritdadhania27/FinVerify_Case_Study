# SMOKE TEST — 1 row, not a campaign

**Run:** `campaign_20260903T163724Z` · arm A · validation · 1 question
**Purpose:** prove `nvidia/openai/gpt-oss-20b` works through the whole pipeline
before asking for 765 requests on it.

D46 rebound Channel A after `gpt-oss-120b` reached end of life mid-flight. A
health check proves a model answers a bare prompt; it does not prove the model
survives retrieval, a program channel, the consistency engine and the arbiter.
This does.

| | |
|---|---|
| question | FI0560ee2f |
| answer | 12232 |
| natural channel | 12232, no error |
| program channel | 12232, executed, no error |
| agreed | yes, risk 0.0 |
| tokens | 5,491 |

Same answer the retired gpt-oss-120b produced on the same question in
`campaign_20260901T105355Z`, which is reassuring but is one question and is not
evidence the two models are equivalent. That is what the re-run of validation is
for.

Do not analyse or quote this run.
