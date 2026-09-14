# Case study: Indian annual reports

## The corpus

Five Indian listed companies across five sectors, FY2023-24, 1,665
pages. Chosen for structural diversity rather than size: a bank's
balance sheet, a conglomerate's segment reporting and a
pharmaceutical company's inventory notes break extraction in
different ways, and five IT companies would measure one document
template five times.

| Company | Sector | Year | SHA-256 | Extracted | Candidates | Validated |
|---|---|---|---|---|---:|---:|
| Infosys Limited | IT services | 2023-24 | `f356fc75d6d6…` | yes | 45 | 29 |
| HDFC Bank Limited | Banking | 2023-24 | `40f73920a8b1…` | yes | 37 | 22 |
| Reliance Industries Limited | Energy / conglomerate | 2023-24 | `d8e3739d2e06…` | yes | 45 | 27 |
| Sun Pharmaceutical Industries Limited | Pharmaceuticals | 2023-24 | `cca3bdde342a…` | yes | 29 | 13 |
| Tata Motors Limited | Automotive | 2023-24 | `db02424e0ed1…` | yes | 36 | 24 |

5 of 5 reports extracted and chunked.

Every report is a public filing obtained from the company's own
investor-relations site; URL, retrieval date and SHA-256 are recorded
in `documents/registry.json`. The PDFs are **not** redistributed —
the hash is what lets a reader confirm the file they fetch from the
publisher is the file this evaluation used.

**Candidates are not gold.** 115 of 192 candidate questions have passed human validation (spec 16); `evaluation.dataset` refuses to serve a PENDING or REJECTED question to an evaluation. The Validated column is the one that gates the results below.

Arm **A** over **61** graded questions from 5 companies.

## Headline

| | |
|---|---|
| Numerical accuracy | 36.1% |
| Channels did not agree (disagreed, or one gave no figure) | 68.9% |
| Channels disagreed on a figure (verdict DISAGREE) | 3 of 61 (4.9%) |
| Abstention rate | 41.0% |
| **Both channels agreed and both were wrong** | 5 of 61 |
| Latency p50 / p95 | 72.94s / 190.64s |
| Tokens per question | 6445 |

The both-agree-wrong row is the method's blind spot and is placed in the headline rather than an appendix: it counts the questions where the detector was confident and wrong, which is the number a reader deciding whether to trust this system actually needs.

## By company

| Company | Sector | n | Accuracy | Did not agree | Both wrong | |
|---|---|---:|---:|---:|---:|---|
| HDFC Bank Limited | Banking | 11 | 27.3% | 72.7% | 0 | **thin cell** |
| Infosys Limited | IT services | 13 | 92.3% | 30.8% | 0 | **thin cell** |
| Reliance Industries Limited | Energy / conglomerate | 14 | 7.1% | 92.9% | 1 | **thin cell** |
| Sun Pharmaceutical Industries Limited | Pharmaceuticals | 10 | 50.0% | 80.0% | 1 | **thin cell** |
| Tata Motors Limited | Automotive | 13 | 7.7% | 69.2% | 3 | **thin cell** |

A cell below 15 questions is marked. A per-sector accuracy computed over a handful of questions is an artefact of which questions happened to be written, not a finding about the sector.

## Which question categories are hard

| Type | n | Accuracy | Did not agree | |
|---|---:|---:|---:|---|
| multi_hop | 1 | 0.0% | 100.0% | **thin cell** |
| lookup | 60 | 36.7% | 68.3% |  |

## Error taxonomy

Two axes, because they answer different questions: where the error entered the pipeline, and what it looks like. One label cannot serve both (D25).

| Provenance | n | | Kind | n |
|---|---:|---|---|---:|
| retrieval | 29 | | unsupported_claim | 25 |
| question_understanding | 6 | | unclassified | 9 |
| undetermined | 4 | | sign_error | 2 |
|  |  | | wrong_unit | 2 |
|  |  | | wrong_scale | 1 |

## Verification effectiveness

The arbiter was triggered on 4.9% of questions. It resolved 3 and abstained on 0.

An abstention is not a failure. When the evidence does not settle a disagreement, declining to resolve it leaves the answer flagged as risky, which is the safe outcome; a resolution manufactured from inadequate evidence would remove the flag without removing the risk.

## Caveats

- 5 company cell(s) hold fewer than 15 questions (HDFC Bank Limited, Infosys Limited, Reliance Industries Limited, Sun Pharmaceutical Industries Limited, Tata Motors Limited); their rates are reported but must not be read as findings about those sectors
- 11 of 39 error label(s) are SUGGESTED or need a human: comparing two numbers cannot separate a wrong metric from a wrong formula from an arithmetic slip

Absolute figures here are free-tier-model figures and must never be set beside published frontier-model results as though the setups matched.
