# Arm O — the oracle-retrieval diagnostic (RX-040)

**Validation split, 45 questions, 135 requests.** Each question was handed the
chunks containing its gold evidence, so every remaining error is a reasoning
error by construction. The oracle built for 43 of 45.

| | arm A (real retrieval) | arm O (oracle) |
|---|---:|---:|
| accuracy | 0.400 | **0.822** |
| abstention | 0.444 | **0.067** |
| AUROC | 0.907 | **0.655** [0.435, 0.876] |
| AUROC, reasoning stratum | — | **0.541** |

**Not an end-to-end measurement.** It has been told where to look. RX-039
remains the end-to-end result; this isolates the reasoning step.

**Validation only** (D47): the arm was designed after seeing the test result, so
running it on test would be leakage.

Full analysis: RX-040 in EXPERIMENTS.md.
