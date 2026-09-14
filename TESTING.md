# Testing

**1,464 passing, 1 skipped**, ~7 minutes. Lint clean under a pinned ruff rule
set.

The one skip is `test_the_schema_applies_to_real_postgres`, which skips rather
than passes when no PostgreSQL is configured — so a green run on a machine
without it never reads as "the schema was verified". Without Docker, Tesseract or
the source filings the count drops further, and each skip names what was absent.
That is the design: **a suite that quietly passes when its dependencies are
missing is measuring the wrong thing.**

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
cd frontend && npm run typecheck
```

---

## What these tests are for

A green suite is not the same as a working system, and this project has the
receipts. RX-007 found **four research-validity defects living behind a green
suite**, and three of them pushed the same direction: they inflated measured
disagreement and depressed AUROC, making the method look *worse* than it is.
Every one was found by running the system, not by a test.

So the tests here are written to pin **the properties conclusions depend on**,
not to cover lines. A metric that is wrong still lands in [0,1], still orders the
arms, and still produces a table a reader will believe.

---

## Skipped, never passed

Tests that need a real dependency are **skipped when it is absent**, with the
reason stated in the skip message:

| Needs | Count | Why it must skip rather than fake it |
|---|---|---|
| Docker | ~20 | A sandbox test that passes without a container has verified nothing about containment |
| PostgreSQL | 1 | A schema test on SQLite has not verified the PostgreSQL schema |
| Tesseract | ~8 | An OCR test that passes without the binary has not read anything |
| `documents/raw/` | ~6 | Third-party filings are gitignored, not redistributed |

**A green run on a bare machine must never read as "verified".** That is the
whole point of the distinction, and it is why the 26 tests in `test_sandbox.py`
start real containers rather than asserting against a mock.

The 54 escape attempts in `test_code_validator.py` are a separate layer and the
distinction matters: they are pure AST analysis, run no container, and finish in
under a second. They prove the allowlist *rejects* hostile code before execution
is ever reached. Containment itself - CPU, memory, timeout, no network - is what
`test_sandbox.py` proves, against Docker. This file previously attributed the 54
to `test_sandbox.py` and said they "execute real containers", which claimed
containment evidence from a suite that never starts one.

---

## The research-validity gates

These are not ordinary unit tests. If one fails, the finding is that the design
has been compromised, and **the fix is the code, never the test**.

| Gate | Where | What it protects |
|---|---|---|
| Channel B cannot see Channel A | `test_channels.py`, `test_orchestrator.py` | D1 — the whole independence claim |
| The program-channel signature admits no Channel-A argument | `test_channels.py` | the same, structurally |
| Risk direction: disagreement scores riskier than agreement | `test_orchestrator.py` | feeding the score in unflipped yields `1 − AUROC` |
| AUROC's two directions are complements | `test_evaluation_metrics.py` | the same, arithmetically |
| Undefined is `None`, not 0.5 | `test_evaluation_metrics.py` | a stratum that could not test the detector must not read as "no better than chance" |
| Unknown never becomes `REASONING` | `test_taxonomy.py`, `test_error_analysis.py` | H2's stratum would absorb every uninvestigated failure |
| The stratifier fails to `unknown` | `test_error_analysis.py` | every wrong answer there flatters H2 |
| Ablations differ in exactly one field | `test_campaign.py` | an ablation that changed two things isolates neither |
| Candidates cannot be served as gold | `test_dataset.py` | spec §16 |
| The test split needs a flag *and* a reason, and is logged | `test_dataset.py` | EVALUATION.md §1 |
| Explanations contain no provider call | `test_explanation.py` | D29 |
| Quota errors are never retried | `test_orchestrator.py` | a retry loop burns a day's free-tier allowance |
| A collection is refused unless the configured model built it | `test_index_manifest.py` | D33 — both models are 768-dim, so a dimension check passes while retrieval returns the wrong page |
| An index missing a company being asked about is refused | `test_index_manifest.py` | questions about that company would be answered from another company's evidence |
| A missing manifest raises rather than passing | `test_index_manifest.py` | absence of a record must never read as absence of a problem |
| The manifest records only what was verified present | `test_index_manifest.py`, `test_database.py` | copying coverage from the registry would assert something nobody checked |
| The question's company reaches retrieval | `test_orchestrator.py` | RX-012 — without it, evidence accuracy is 0.727 rather than 0.909 |
| An unknown company yields no filter, never a guessed one | `test_orchestrator.py` | a wrong company filter returns another issuer's balance sheet, correctly labelled |
| `K Cr` reads as crore, and is flagged anyway | `test_financial_value.py` | RX-013 — asserts the reading the *source document* supports, so an "obvious" fix fails here rather than in a published number |
| No source file contains a control character | `test_source_hygiene.py` | a backspace in a regex renders as nothing and disables the pattern silently |
| Every derived metric is reachable by a question naming it | `test_source_hygiene.py` | present-but-unreachable is the third defect of that shape here |

---

## Failure cases, per spec §38

Every applicable one is covered: malformed PDF, scanned PDF, missing table,
ambiguous question, wrong unit, negative values, zero denominator, conflicting
evidence, missing evidence, model timeout, code execution failure.

Plus the ones this project found for itself: empty model replies (D17), prose
instead of JSON, merged table columns, a partially written JSONL line, an
unreachable index, a missing binary, and a quota error arriving as a channel
failure.

---

## What the tests do **not** cover

Stated because a testing document that lists only its coverage is misleading.

- **The metric modules are tested against synthetic data, and three campaigns
  have since run against real data** (787 rows). The prediction above — that a
  real campaign would find what these tests did not — held: it found the
  efficiency reader keyed on `record[channel]["tokens"]` while the recorder
  writes `usage.total_tokens`, so **every efficiency figure was 0.0** and every
  test passed throughout. Also that `explain()` had never been called on a
  campaign row, and that a corrected gold answer left its evidence anchors
  citing the rejected candidate (RX-042). None was a test failure.
- **Retrieval quality on the full corpus is measured and it is the binding
  constraint** — evidence accuracy 0.08–0.56 by company (RX-028), with Tata
  Motors at 0.080 and every miss `ranked_too_low`. No test covers this and none
  could: it is a measurement, not an assertion. What a test *does* pin is that
  the questions reach retrieval with their company attached (RX-012).
- **Configuration is only tested where a guard exists.** The thirteen defects
  tabulated in `PROJECT_STATUS.md` were all found by running this project, not by
  testing it, and the suite was green for every one — as were the three above.
  D33's preflight, the RX-012 scope gates, D46's live-binding check and D48's
  freeze gate each close one class; they do not close the category.
- **Agreement is not correctness, and no test can make it so.** RX-013 has both
  channels agreeing at risk 0.000 on a correct figure they both read past the
  same undiscussed scale token. The suite cannot distinguish that from sound
  reasoning, because it is not observable from the outputs.
- **No performance or load testing.** The binding constraint is free-tier request
  quota, not throughput.
- **No frontend runtime tests.** It typechecks strict and builds; there are no
  component or E2E tests. The spec calls the UI secondary and this is where that
  is cashed out.
- **No CI.** Tests and lint run locally.
- **Container escape is not claimed to be mitigated.** The 54 escape attempts
  test the configuration, not the Docker or kernel isolation boundary, which is
  outside this project's control.

---

## Layout

All 50 test files, with the number each collects (1,465 collected in total). Generated from
`pytest --collect-only`, not maintained by hand: the previous table listed 30 of
the files and carried per-file counts that had drifted by as much as 2x
(`test_api.py` was written as 25 against 52 actual), which made it read as an
inventory while being a sample.

```
tests/
  test_acquisition.py             registry, hashing, malformed and scanned PDFs       (27)
  test_extraction.py              real PDFs, provenance, quality                      (45)
  test_split_table_headers.py     multi-row header recovery                           (8)
  test_two_panel_tables.py        side-by-side panels split correctly                 (9)
  test_statement_basis.py         standalone vs consolidated                          (13)
  test_ocr.py                     labelling, confidence (real Tesseract; NO caller)   (22)
  test_financial_value.py         parsing, scale, sign, separators                    (80)
  test_facts.py                   Module 4: columns, years, merges                    (26)
  test_generator_basis.py         question generation stays on basis                  (17)
  test_fiscal_year_spans.py       Indian FY boundaries                                (13)
  test_chunking.py                tables stay tables                                  (25)
  test_retrieval.py               hybrid legs, fusion, the section filter             (28)
  test_retrieval_metrics.py       Recall@K, MRR, evidence accuracy                    (32)
  test_retrieval_gold_split.py    RX-053: the seal the gold label bypassed            (28)
  test_rerank.py                  RX-035: the reranker that failed                    (14)
  test_index_manifest.py          D33 preflight gates                                 (14)
  test_fiscal_year_filter.py      year filter isolation                               (11)
  test_query_preparation.py       query scaffolding                                   (24)
  test_query_formulation.py       planned retrieval terms                             (15)
  test_question_understanding.py  deterministic parse, LLM fallback                   (34)
  test_channels.py                both channels + independence gates                  (64)
  test_code_validator.py          AST allowlist: 54 escape attempts, no container     (54)
  test_sandbox.py                 real containers, limits, no network                 (26)
  test_deterministic.py           10 operations, failure cases                        (40)
  test_deterministic_channel.py   operand binding, abstention                         (23)
  test_consistency.py             verdicts, scores, RX-007 guards                     (44)
  test_confidence.py              risk direction, facets, bands                       (26)
  test_taxonomy.py                two axes, tri-state provenance                      (28)
  test_explanation.py             derived, never generated                            (20)
  test_verification_agent.py      arbiter, abstention, RX-045 ordering                (26)
  test_orchestrator.py            graph, arms, quota, independence                    (58)
  test_arm_config_is_light.py     the arm table imports no runtime                    (11)
  test_campaign.py                resumability, budget, arm catalogue                 (32)
  test_unattended_campaign.py     long-run recovery                                   (18)
  test_freeze_gate.py             D48: no test split without a live freeze            (10)
  test_run_h1.py                  H1 harness                                          (16)
  test_oracle_evidence.py         the oracle diagnostic's gold path                   (15)
  test_evaluation_metrics.py      correctness, QA, detection, stats                   (78)
  test_ablation_contrasts.py      paired A-X contrasts, Holm within family            (12)
  test_dataset.py                 gold discipline, sealed split, access log           (43)
  test_gold_review.py             validation workflow, anchor repair                  (54)
  test_error_analysis.py          H2 stratifier, report                               (31)
  test_case_study.py              refusals, token accounting                          (31)
  test_database.py                idempotent ingest, real Postgres                    (28)
  test_api.py                     endpoints, ordering, live-QA guard                  (62)
  test_llm_provider.py            adapter, rate limiter                               (83)
  test_settings_are_wired.py      no setting is decorative                            (24)
  test_source_hygiene.py          control chars, reachability                         (6)
  test_frontend_contract.py       the seams between the two tiers                     (12)
  test_frontend_contrast.py       WCAG AA on both themes                              (5)
```

---

*What running the system found: [`EXPERIMENTS.md`](EXPERIMENTS.md) ·
Methodology: [`EVALUATION.md`](EVALUATION.md)*
