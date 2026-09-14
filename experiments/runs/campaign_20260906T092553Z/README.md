# Arm O on the REPAIRED dataset (RX-043)

Validation, 45 questions, 135 requests. The first oracle run whose evidence is
correct — RX-040's resolved through anchors citing figures the validator had
rejected (RX-042).

Restricted to the **37 questions the oracle built for**:

| | |
|---|---:|
| correct | **35 of 37** |
| accuracy | **0.946** |
| errors | **2** |
| blind spot | **1 of 33** |

Against arm A's 0.400 with real retrieval, same models and prompts.

**Do not quote the pooled all-45 AUROC of 0.943.** It includes the 8 questions
whose anchors the repair dropped, which the oracle handed nothing — closed-book
rows, 0 of 8 correct and all high-risk, inflating AUROC for a reason unrelated
to reasoning.

Supersedes `campaign_20260906T005652Z`. Full analysis: RX-043.
