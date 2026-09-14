# FinVerify-IND

A numerical QA dataset over Indian annual reports, built for one purpose: to
label an answer *correct* or *incorrect* precisely enough that "did the system
detect the incorrect ones?" is a measurable question.

**Status: 192 candidates judged, 115 validated, 29 rejected, 48 left pending by
choice.** `evaluation.dataset.build_split` refuses to serve a PENDING question to
an evaluation, so the usable evidence base is **45 validation + 61 test
(spent) + 2 train** — quoting 192 as the benchmark size would overstate it by 77
rows.

The validation rate is itself evidence: **44% over the first 45 rows, 83% over
everything judged after D42, RX-027 and RX-029 landed.** Same validator, same
corpus, same rubric — the defects that drove the early rejections are gone.

**Two defects remain open in the gold** and are listed under Known limitations:
22 corrected rows whose evidence anchors were dropped and need re-derivation
(RX-042), and two answers that appear on **0 pages** of their own filing.

---

## This project uses no external benchmark

`datasets/benchmark/` is **empty**, and no module imports FinQA, TAT-QA or
ConvFinQA. They appear in `RESEARCH.md` §2 only as the datasets *prior work* was
validated on — cited, never ingested. Every number this project reports comes
from FinVerify-IND.

That is worth stating plainly because the project plan scheduled them, and
`TODO`/`PROJECT_STATUS` carried *"licences unverified"* as an open blocker long
after the path was abandoned — a blocker on a road nobody took.

**The licences were verified anyway on 2026-09-06**, from the primary sources
rather than from memory or a search summary, so the question is closed whichever
way a future author goes:

| dataset | licence | verified from |
|---|---|---|
| FinQA | **MIT**, © 2021 Zhiyu Chen | `czyssrs/FinQA` `LICENSE` |
| ConvFinQA | **MIT**, © 2022 Zhiyu Chen | `czyssrs/ConvFinQA` `LICENSE` |
| TAT-QA | **CC BY 4.0** | `NExTplusplus/TAT-QA` `README.md` |

**One thing could not be confirmed, and is recorded rather than assumed.** FinQA
and ConvFinQA derive their documents from IBM's **FinTabNet**, whose licence is
widely reported as CDLA-Permissive — but IBM's Data Asset eXchange page for it is
deprecated and states no terms, and no other primary source was reached. So the
repository licences above are confirmed; **the terms of the underlying filings
are not**, and anyone redistributing derived content should settle that first.
This follows the standard `LITERATURE_REVIEW.md` set: a figure that cannot be
confirmed against its source is marked unconfirmed, not quietly cited.

---

## The corpus

Five Indian listed companies, five sectors, FY2023-24, 1,665 pages.

| Company | Sector | Pages | Candidates |
|---|---|---:|---:|
| HDFC Bank Limited | Banking | 585 | 56 |
| Tata Motors Limited | Automotive | 530 | 15 |
| Sun Pharmaceutical Industries Limited | Pharmaceuticals | 312 | 39 |
| Reliance Industries Limited | Energy / conglomerate | 159 | 17 |
| Infosys Limited | IT services | 79 | 33 |

Chosen for **structural diversity rather than size**. A bank's balance sheet, a
conglomerate's segment reporting and a pharmaceutical company's inventory notes
break extraction in different ways; five IT companies would measure one document
template five times.

Every report is a public filing obtained from the company's own
investor-relations site. URL, retrieval date and SHA-256 are in
`documents/registry.json`. **The PDFs are not redistributed** — `documents/raw/`
is gitignored, and the hash is what lets a reader confirm the file they fetch
from the publisher is the file this evaluation used.

The counts are uneven, and visibly so **before** the campaign runs, which is what
D23 asked for when it said thin cells should be thin by plan. Reliance and Tata
Motors are thin because the metric lexicon is IT-centric; a bank's balance sheet
says *deposits* and *net interest income*.

---

## The record

```python
DatasetQuestion(
    qid, question, company, fiscal_year, document_id,
    answer=GoldAnswer(text, unit, source_page, source_note),
    definition,        # D22: the metric definition the question PINS
    ambiguous,         # D22: deliberately unpinned, scored separately
    question_type, difficulty,
    evidence=(...),    # groups: ALL must be covered, ANY span covers a group
    validation=ValidationRecord(status, validator, validated_on, pass_number, …),
    split, provenance,
)
```

**Gold is parsed by the same parser as predictions.** Different rules on the two
sides would let a systematic scale bug cancel on one and not the other, and the
correctness predicate would be comparing two different readings of the same
notation.

**`canonical` is derived, never hand-entered.** A hand-typed canonical value is
one more place a scale error can hide.

---

## Definitional ambiguity is a designed subset (D22)

RX-007 found all three channels splitting on *return on equity* because each used
a different standard definition — closing equity, average equity, owners' share.
Each is defensible. The detector flagged the question as risky, which is correct
by its own lights and **wrong for a research question about hallucination**.

So the dataset has two parts:

- **The main set (150).** The metric definition is pinned in the question text
  and carried in `definition`. A question with no pinned definition is a
  malformed record and the audit says so.
- **The ambiguity subset (10).** Deliberately *unpinned*, `ambiguous=True`,
  scored separately and **never pooled into a headline figure**. Pooling would
  make that figure a property of how many ambiguous questions someone wrote.

The subset is a finding in its own right: *how often does a consistency-based
detector mistake definitional ambiguity for a numerical error?* That
false-positive mode is unquantified in the literature.

---

## How candidates are generated

```bash
scripts/build_finverify_ind.py generate    # mine candidates
scripts/build_finverify_ind.py audit       # malformed records + stratum counts
scripts/build_finverify_ind.py export      # the validation worksheet
scripts/build_finverify_ind.py import --csv … --validator "Name"
scripts/build_finverify_ind.py split       # seeded, stratified
scripts/build_finverify_ind.py manifest    # SHA-256
```

**Every candidate answer is mined from a printed table cell, never composed.**
The generator delegates to Module 4, so it has no path to a number the document
does not contain. Questions are generated per (metric, **stated year**), so each
one names the year it asks about — asking without naming the year is asking an
ambiguous question, and that belongs in the ambiguity subset by design rather
than in the main set by accident.

**Derived-metric questions carry no candidate answer, on purpose.** A validator
is far likelier to wave through a number already in the box than to notice one
that is missing, so the eleven ratio questions arrive blank and the validator
computes and signs for them. The audit counts them as *awaiting computation*
rather than as defects — an audit that flags intended states is one people learn
to ignore.

---

## Validation (spec §16)

A CSV round-trip, not a review UI. The validator is one person working through
268 questions with a PDF open beside them; a web app would be a week of frontend
work to replace a spreadsheet that already does the job.

Each row carries the candidate answer, its source page and its evidence anchors,
so the check can be made without cross-referencing another file.

**Corrections are recorded, never applied silently.** A corrected answer keeps
what it replaced in `provenance.generated_answer` and in the import report. The
rule that gold labels are never altered to improve a score is only auditable if
every alteration has a record; *"the gold says 3,956"* is worth nothing if nobody
can see it once said 3,596.

A **blank verdict leaves the question PENDING**, so a validator who got through
80 rows can submit the sheet.

### One validator, and the word that must not be used

D23 specifies a second pass over a **seeded** ~20% subset — seeded so the subset
is fixed by the seed rather than by which questions the system got wrong.

That yields **intra-annotator consistency**: how stable this person's judgments
are. It is **not inter-annotator agreement**, one validator cannot establish
that, and the write-up must not use the stronger term. No kappa is reported
either — kappa corrects for chance agreement between two annotators, and there is
only one, so quoting it would be a statistic that *sounds* like it addresses the
limitation while not addressing it.

**Validator:** to be recorded here on the first import. The `--validator` flag is
required; an unattributed gold label is not gold.

---

## Splits and the seal

| Split | n | Use |
|---|---:|---|
| train | 48 | prompt development, few-shot selection |
| validation | 48 | model/config selection, threshold tuning, calibration |
| **test** | **64** | **final evaluation only, once, after the freeze** |

Stratified on (question_type, ambiguous) with seed 20260826, so the test split
cannot end up without a multi-hop question by chance — which on 268 questions is
not a remote possibility.

**Splits are assigned before validation**, so the split is not a function of
anything the validator saw.

The test split requires `FINVERIFY_ALLOW_TEST=1` **and** a stated reason, appends
to `experiments/test_set_access.log`, and may never be evaluated against
unvalidated gold. The guard is bypassable by anyone with the repository; the
point is that bypassing it **leaves evidence** rather than going unnoticed.

---

## Known limitations

1. **Nothing is validated yet.** The dataset is a set of proposals.
2. **One validator.** See above.
3. **Coverage is uneven by sector**, because the metric lexicon is IT-centric.
4. **Only 32% of table chunks yield a stated year** (RX-009), so the generator
   draws from a third of the corpus. The rest is not lost — it is excluded
   because attributing a figure to an unstated year is the error Module 4 exists
   to prevent.
5. **Lookups dominate** (256 of 268). Multi-hop and computed questions are where
   dual-channel verification should matter most, and eleven of them is a thin
   basis for a claim about that stratum.

---

*Methodology: [`EVALUATION.md`](EVALUATION.md) · Decisions:
[`DECISIONS.md`](DECISIONS.md) D22, D23 · Corpus:
[`CASE_STUDY.md`](CASE_STUDY.md)*


---

## Coverage, and the imbalance it created (D34)

The metric lexicon was built on the Infosys vertical slice and stayed there. It
contained `cost of technical sub-contractors` and no banking vocabulary at all,
which is why the first generation left Reliance at 17 candidates and Tata Motors
at 15 — barely at the case study's reporting floor.

Extending it from **terms confirmed present as table row labels in the corpus**
(never from a textbook — see D34 for the nine rejected guesses) took the set from
160 to 268.

| Company | Sector | Before | After | Split: train / val / test |
|---|---|---:|---:|---|
| HDFC Bank | Banking | 56 | **127** | 39 / 34 / 54 |
| Sun Pharmaceutical | Pharma | 39 | 48 | 16 / 16 / 16 |
| Infosys | IT services | 33 | 45 | 12 / 13 / 20 |
| Reliance | Energy / conglomerate | 17 | 29 | 9 / 11 / 9 |
| Tata Motors | Automotive | 15 | 19 | 5 / 7 / 7 |
| **Total** | | **160** | **268** | 81 / 81 / 106 |

**The good half.** Every company now clears the 15-question floor `MIN_CELL`
enforces, with margin. Per-sector cells are reportable for the first time.

**The unwelcome half, stated plainly.** HDFC Bank is now **47% of the dataset**.
A 585-page bank filing with dozens of schedules yields far more year-labelled
columns than a 79-page IT report, and the generator makes one question per
(metric, stated year).

> **Any pooled detection metric over this dataset is substantially a statement
> about one bank.** Results must be stratified by company, and the write-up must
> not lead with the aggregate.

This is not capped deliberately. Discarding real, validatable questions to
balance a table trades gold labels for a cosmetic property, and every analysis
path here already stratifies. The imbalance is written down so it is argued with
rather than discovered in review.

**Rebuilt 2026-08-30 to 193 candidates (D42, RX-026, RX-027).** The first nine
human verdicts found that answers were being read from the wrong set of financial
statements, and chasing that found two extraction defects underneath. Every
question now names the statements its answer came from and is answered from them:

| | before | after |
|---|---:|---:|
| candidates | 268 | **193** |
| citing a page on a basis the question does not name | 35 | **0** |
| cited page unplaceable in either section | 164 | **0** |
| retrieval gold graded on the unasked basis | 27/91 | **0/100** |
| largest company's share | 47.4% | **23.8%** |

Composition: 180 lookup / 13 multi-hop; 174 consolidated / 19 standalone;
Reliance 46, Infosys 45, HDFC Bank 37, Tata Motors 36, Sun Pharma 29.

**Question ids are content-derived** (`FI` + 8 hex of a hash over document,
metric and year), so a regeneration no longer renumbers every question. The
previous counter meant one rebuild silently invalidated every recorded verdict.

**Frozen at 268 against spec §34's ~500 (D41, owner-approved 2026-08-30).**
The same section says not to optimise for size at the expense of gold-label
quality, and the binding constraint is one human validator, not generation.
Another 232 questions from the same generator against the same five filings
would deepen HDFC Bank's share rather than correct it, since D34's lexicon gains
were concentrated in banking vocabulary.

**The count is not the deviation that matters.** §34 names twelve question
categories — revenue, profit, growth, margin, ratio, percentage, comparison,
trend, CAGR, multi-step, cross-table, cross-year. This set is **256 lookup and
12 multi-hop**, so every derived category rests on those 12 and several are not
represented at all. Reporting "268 of ~500" without saying this understates the
limitation by describing the wrong one: the consequence is not a smaller
dataset, it is that H1 is tested mostly on single-figure retrieval, that the
deterministic channel engages on 12 questions (RX-025), and that the program
channel is exercised on the same 12. Any later extension should add *derived*
questions on the under-represented filings, after a first result rather than
before one.

## Validation order (D35)

The worksheet is **interleaved round-robin by company, smallest first**. Grouped
by company — the natural generation order — the first 40 rows would be 40 HDFC
Bank questions, and a first result from that prefix would describe one bank.
Interleaved, 40 rows is 8 per company across all five.

Verified on the exported sheet:

| Prefix | Composition |
|---|---|
| first 20 | 4 from each of the five companies |
| first 40 | 8 from each |
| first 80 | 16 from each |

Same principle as the campaign runner's question-major ordering: *make the prefix
of an interrupted job representative, because the job will be interrupted.*
