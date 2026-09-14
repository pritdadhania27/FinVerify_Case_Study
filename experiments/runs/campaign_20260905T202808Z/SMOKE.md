# SMOKE TEST — 1 row, not a campaign

**Run:** `campaign_20260905T202808Z` · arm O (oracle retrieval) · validation · 1 question

Proves the oracle path works end to end before spending 135 requests on it.

| | |
|---|---|
| retrieve node | `mode: oracle, blocks: 1, oracle_available: True` |
| question | FI0560ee2f |
| natural / program | 12232 / 12232 |
| risk | 0.0 |

Confirms the arm reaches the oracle branch rather than the closed-book one, and
that both channels reason over the injected chunk.

It also shows the distractor confound D47 records: the oracle supplied **1**
block where real retrieval supplies 8.

Do not analyse or quote this run.
