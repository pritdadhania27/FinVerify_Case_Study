# Completion Guide

**Written:** 2026-08-26 · **Updated:** 2026-08-27 · **Method:** every claim
below came from running a command on this machine, not from reading
`PROJECT_STATUS.md`.

> ## ⚠ Its numbers are a snapshot from 2026-08-27 and several are now wrong
>
> Kept as written rather than rewritten: the defect register below, and the
> account of *how each defect hid*, is the useful part and it is history. The
> counts are not. Corrected as of **2026-09-06**:
>
> | this document says | actually |
> |---|---|
> | 268 questions, **0 validated** | 192 judged, **115 validated**, 29 rejected, 48 pending |
> | 1,245 facts | **1,793** (53 orphans pruned 2026-09-06) |
> | 999 tests | **1,464 passing, 1 skipped** |
> | retrieval gold for Infosys only | all five filings, 100 evidence groups |
> | B-1: gold awaiting validation | **resolved** — validation is done |
> | the campaign is live at 4 of 180 rows | **five campaigns complete**: validation 315, test 427 (spent), oracle 45, ablation 225, arm-A rebase 45 |
> | nothing is `COMPLETE` | **29 COMPLETE, 5 PARTIAL, 1 BLOCKED** (audited 2026-09-08 against spec §43) |
>
> **[`PROJECT_STATUS.md`](PROJECT_STATUS.md) is the current state.** Read this
> one for the defect register and the method, not for the figures.

**Status: D-1 through D-7 are all resolved.** D-6 was found by carrying out
this guide's own Step 0b, and it was the worst of them — the campaign would have
retrieved across all five filings without knowing which company each question
was about, at 0.727 evidence accuracy instead of 0.909.

What remains is §3 — three things only you can do — plus one newly-exposed
measurement gap (retrieval gold exists for Infosys only). The defect register in
§2 is kept rather than deleted: how each one hid is more useful than the fact
that it is gone.

**D-6 and D-7 were both found by carrying out this guide's own steps.** D-6 came
out of Step 0b, which was meant to refresh a stale number. D-7 came out of a
single live run. Neither would have surfaced by reading the code or running the
suite — which is the argument for the steps below being *done* rather than
reviewed.

---

## 0. The audit behind this document

| Check | Command | Result |
|---|---|---|
| Working tree | `git status --short` | clean, 18 commits |
| Lint | `ruff check .` | **All checks passed** |
| Tests | `pytest -q` | **1014 passed, 0 skipped**, 316s |
| Environment | `scripts/verify_environment.py` | **32/34 VERIFIED**, exit 0 |
| Services | `docker compose ps` | 4 containers up, 3 healthy |
| Vector index | Qdrant REST + manifest | `finverify_e5` **22,930 pts, 5/5 companies**, E5-built |
| Corpus | `documents/processed/*.chunks.jsonl` | 5 files, **22,930 chunks**, 13,559 table |
| Dataset | `build_finverify_ind.py audit` | **268 questions**, **0 validated**, 0 problems |
| Database | `psql` row counts | 5 docs, **1,245 facts, all 5 companies**, 1,664 pages, 537 sections, 2,780 tables |
| Campaign gate | `run_campaign.py --budget-only` | correctly **refused** — no validated gold |
| Frontend | `npm run typecheck` | clean |
| Quota | `verify_llm_providers.py --quota` | groq 14,396 left, nvidia 998, gemini 20 |

Zero skips because `DATABASE_URL` was exported and Docker, PostgreSQL,
Tesseract and the source filings were all present. Without them the count drops
and each skip names what was absent — `test_the_schema_applies_to_real_postgres`
skips rather than passes when no PostgreSQL is reachable, so a green run on a
bare machine never reads as "the schema was verified".

---

## 1. What is complete

### The honest headline

**~~Nothing is `COMPLETE` in the spec's sense~~ — superseded 2026-09-06.
The tally is 29 COMPLETE, 5 PARTIAL, 1 BLOCKED.**

Spec §43 requires eleven boxes ticked, the last being *acceptance criteria
passed*. For most modules that means evidence from a run against validated gold,
and when this section was written no campaign had run. Three now have. The
sentence stayed here — and in `PROJECT_STATUS.md` — for five days after it
stopped being true, which is its own small lesson: **a status written as a
principle outlives the fact it was describing.** It reads as a standard being
upheld long after it has become a stale claim.

The Tier A/B/C split below still holds and is still the useful frame. What has
changed is that Tier A is mostly discharged. What has *not* changed is the
principle: marking a module `COMPLETE` on the strength of unit tests is exactly
the failure `ENGINEERING_RULES.md` forbids, which is why five modules were left PARTIAL and
one demoted to BLOCKED in the same audit that promoted twenty-nine.

But "not COMPLETE" hides an important distinction. There are three different
reasons a module is not complete, and they need completely different work:

| Tier | Meaning | Work remaining |
|---|---|---|
| **A** | Built, tested, documented. Waiting **only** on a run. | Run the campaign |
| **B** | Built, but with a **known gap in scope** | Named code/config work |
| **C** | **Blocked on the owner** — I cannot do it | Your decision or labour |

### Module-by-module

| # | Module | Tier | Built? | What is actually missing |
|---|---|:--:|:--:|---|
| — | Environment gate | **B** | 32/34 | Ghostscript absent → camelot `lattice` unavailable; `OPENROUTER_API_KEY` unset. Both optional |
| 0 | Research foundation | **C** | yes | Methodology **not frozen** (tag pending); literature currency sweep before submission |
| 1 | Dataset & benchmark | **B** | yes | FinQA / TAT-QA / ConvFinQA **licences unverified** — they are designed in but not cleared |
| 2 | Document acquisition | **A** | yes | Registry, hashing, dup detection, content validation all work. Needs run evidence |
| 3 | Document intelligence | **A** | yes | OCR verified at 95.3% digit confidence. 103 of 13,559 tables flagged low-accuracy (<1%) |
| 4 | Knowledge structuring | **A** | yes | `facts.py` produces all spec §12 fields. **1,245 facts, all five companies** |
| 5 | Normalisation | **A** | yes | 86 tests, 3 real bugs already fixed |
| 6 | Hybrid RAG | **B** | yes | 0.909 / 0.932 / 0.616 were measured on the **906-chunk Infosys slice**. The index is now 22,930 chunks — **re-measure before quoting** (RX-011) |
| 7 | Question understanding | **B** | yes | The **LLM refinement path has never run**. Written, untested against a live provider |
| 8 | Natural channel | **B** | yes | No self-consistency sampling inside the channel (exists only as arm B5); parse-failure rate unmeasured |
| 9 | Program channel | **B** | yes | No repair-on-failure retry |
| 10 | Deterministic verification | **A** | yes | 10 operations, deterministic operand binding, 44 tests. Binding accuracy unmeasured (D6) |
| 11 | Consistency engine | **A** | yes | Verdict + continuous score, `base_score`/`coverage` separate |
| 12 | Disagreement detection | **A** | yes | 8 types incl. scale-mismatch |
| 13 | Verification agent | **A** | yes | Resolution accuracy needs gold |
| 14 | Hallucination taxonomy | **A** | yes | Two orthogonal axes (D25), 28 tests. Never exercised on labelled data |
| 15 | Confidence & risk | **B** | yes | Reports `calibrated=False` and will until fitted. **Brier and ECE must not be quoted** until then; AUROC is rank-based and valid now |
| 16 | Explainability | **A** | yes | Derived, never generated (D29), 19 tests |
| 17 | Orchestration | **A** | yes | LangGraph, arms as configs (D26), 44 tests, verified live (RX-010) |
| 18 | Database | **A** | yes | Every entity but `users` populated: 1,664 pages, 537 sections, 2,780 tables, 1,245 facts, 268 questions |
| 19 | Vector database | **A** | yes | 22,930 chunks, 5/5 companies verified. Model/collection pairing enforced by manifest (D33) |
| 20 | Backend API | **B** | yes | All 9 endpoints, 25 tests. No TLS, no auth, no rate limiting |
| 21 | Frontend | **B** | yes | 6 screens, strict typecheck, nginx-served. **No component or E2E tests** |
| 22 | Evaluation framework | **A** | yes | 56 tests against synthetic data. No real data to run on |
| 23 | Baselines | **A** | yes | B1–B5 as configs, validated, never run |
| 24 | Ablation | **A** | yes | Arms A–H, each differing in exactly one field, never run |
| 25 | Error analysis | **A** | yes | H2 stratifier fails to `unknown`, 31 tests, never run |
| 26 | FinVerify-IND | **C** | 268/500 | **0 of 268 validated.** Every company now clears the 15-question floor (D34), but HDFC Bank is 47% of the set |
| 27 | Case study | **A** | corpus | Corpus written; results section refuses to render without data |
| 28 | Experiment management | **A** | yes | Append-only, resumable, question-major, budgeted |
| 29 | Security | **A** | yes | 54 escape attempts against real containers, no host fallback |
| 30 | Testing | **B** | yes | 999 tests. **No CI**, no frontend tests, no load testing |
| 31 | Deployment | **A** | yes | 4-container stack verified; API rebuilt and confirmed from inside the container |
| 32 | Documentation | **B** | yes | Every §40 file written and figures corrected. Module docs under `docs/` still thin |

**Count after the 2026-08-27 fixes:** 22 modules in Tier A (waiting only on a
run), 9 in Tier B, 3 in Tier C (yours). Four moved A←B by being fixed; Module 6
moved B←A because RX-011 showed its headline number no longer describes the
index it is quoted against — an honest downgrade, not a regression.

---

## 2. Defects found in this audit

**All five are fixed** (2026-08-27). Kept in full rather than deleted: *how each
one hid* is the transferable part, and every one of them hid behind a green
suite. Recorded as **D33** and **RX-011**.

Ranked by what they would have cost.

### D-1 — CRITICAL — ~~The deployed stack reads a 906-chunk Infosys-only index~~ **FIXED**

**Evidence.**

```
$ curl -s localhost:6333/collections/finverify_e5     → points_count: 22930
$ curl -s localhost:6333/collections/finverify_chunks → points_count:   906
$ curl -s .../finverify_chunks/points/scroll          → every point: "Infosys Limited"
$ grep QDRANT_COLLECTION .env                         → finverify_chunks
$ docker exec finverify-api sh -c 'echo $QDRANT_COLLECTION' → finverify_chunks
```

The full 22,930-chunk corpus went into `finverify_e5`. `.env` names
`finverify_chunks`, which holds one document. `docker-compose.app.yml`
interpolates `${QDRANT_COLLECTION:-finverify_e5}` from `.env`, so the **running
API container is querying the 906-point collection right now**.

**Why it is dangerous rather than merely wrong.** Qdrant does not error on a
small collection — it returns the nearest 8 Infosys chunks for a question about
HDFC Bank's deposits. Both channels then reason over confidently-retrieved,
completely irrelevant evidence. They may even *agree*, because they share the
evidence (H2's common-mode failure). The result would read as *the method fails
on banking questions*, when the truth is *the config pointed at the wrong
collection*. `/health` reports `vector_index: true` throughout, because it
checks reachability, not contents.

**Second-order problem: five scripts disagree on the fallback.**

| File | Fallback if `QDRANT_COLLECTION` unset |
|---|---|
| `scripts/index_corpus.py:112` | `finverify_chunks` ← wrong |
| `scripts/evaluate_retrieval.py:191` | `finverify_chunks` ← wrong |
| `scripts/run_campaign.py:61` | `finverify_e5` |
| `scripts/run_slice.py:85` | `finverify_e5` |
| `backend/api/main.py:140,291` | `finverify_e5` |

A re-index run without `--collection` would write into the small collection.

**Third-order problem: the campaign is protected only by accident.**
`run_campaign.py` builds its argparse defaults at line 61 and calls
`load_dotenv(".env")` at line 67 — *after*. So `.env`'s value never reaches the
default and the campaign happens to get `finverify_e5`. That is luck, not
design. Export `QDRANT_COLLECTION` in a shell and the campaign silently reads
Infosys only.

**Fix — all four parts, none optional.**

```powershell
# 1. Point .env at the collection that holds the corpus
#    QDRANT_COLLECTION=finverify_chunks   ->   QDRANT_COLLECTION=finverify_e5

# 2. Restart the API so the container picks it up
docker compose -f docker-compose.app.yml up -d --force-recreate api

# 3. Confirm from inside the container, not from the host
docker exec finverify-api sh -c 'echo $QDRANT_COLLECTION'   # must print finverify_e5

# 4. Confirm the collection actually spans the corpus
curl -s localhost:6333/collections/finverify_e5 | findstr points_count   # 22930
```

Then unify the two stale fallbacks in `index_corpus.py:112` and
`evaluate_retrieval.py:191` to `finverify_e5`, and move `load_dotenv(".env")`
**above** the `ArgumentParser` construction in `run_campaign.py` and
`run_slice.py` so `.env` is authoritative rather than accidentally ignored.

**Add the guard that would have caught this.** A retrieval config that cannot
see the whole corpus should fail loudly at startup, not quietly at question
time:

> before the first question, count distinct `company` values in the collection
> and compare against `documents/registry.json`. Fewer companies in the index
> than in the registry is a fatal config error, not a warning.

**The root cause, found after the guide was first written.** This was not
carelessness — it was **D2a applied to one variable of two**. D2a switched the
embedding model from BGE to E5 on measured evidence and re-indexed into
`finverify_e5`, updating `EMBEDDING_MODEL` and not `QDRANT_COLLECTION`.

Which makes the defect worse than "wrong collection". `.env` paired an **E5
model** with a **BGE-built index**. Both are 768-dimensional, so Qdrant accepts
the cross-model query and returns a *different page*. Measured (RX-011) on
*"total equity attributable to owners of the company"*:

| Query model | Collection | Top score | Top hit |
|---|---|---:|---|
| e5 | `finverify_chunks` | 0.5403 | Infosys **p.67** |
| bge | `finverify_chunks` | 0.7761 | Infosys **p.12** — the balance sheet |
| e5 | `finverify_e5` | 0.8865 | the right page |

**Resolved 2026-08-27.** `.env` → `finverify_e5`; both stale fallbacks unified;
`load_dotenv` moved above the argparse block in all four scripts that read env
defaults; `documents/index_manifest.json` + `backend/rag/manifest.py::preflight`
now **raise** on a model/collection mismatch or a corpus the index does not
cover, before a campaign, slice, retrieval evaluation or API question.
`docker-compose.app.yml` passes `EMBEDDING_MODEL` explicitly and mounts
`documents/` read-only so the manifest tracks the live index rather than build
time. 14 tests. Verified by asking an HDFC question corpus-wide and getting HDFC
pages, and by restoring the broken config to confirm `/health` reports `false`.

This was the seventh of eleven defects on this project found by **running** the
system rather than by a test, and it fits the pattern exactly: every unit test
passed, because no test asserted that the configured collection covers the
corpus or was built by the model querying it.

---

### D-2 — HIGH — ~~The database projection is stale and partially unpopulated~~ **FIXED**

**Evidence.**

```
documents        5      financial_facts  526     companies    5
questions       21      evidence          39     experiments 11
pages            0      sections           0     tables       0
answers          0

facts by company:  Sun Pharma 219 · Reliance 128 · Infosys 100 · HDFC 79 · Tata Motors 0
```

Three problems in one:

1. **Tata Motors has zero facts.** It was the last document to finish indexing,
   after the ingest ran. The DB is a snapshot of a four-document corpus.
2. **`pages`, `sections`, `tables` have never held a row.** Spec §26 names all
   three as major entities. `backend/database/ingest.py` exposes
   `ingest_registry`, `ingest_dataset`, `ingest_facts`, `ingest_run`,
   `ingest_report` — none writes them. The schema is complete; the projection
   is not.
3. **`questions` holds 21 against the dataset's 160.** A stale projection of a
   file that has since grown.

**Why it matters less than D-1.** D4/D28 make files the source of truth and the
database a projection, so no research result depends on these rows. But the
research dashboard reads the database, and §47 asks for a working dashboard —
so a reviewer opening it sees a four-company corpus and 21 questions.

**Resolved 2026-08-27.** Re-ingested, and `ingest_structure` written for the
three entities that had none: **1,664 pages, 537 sections, 2,780 tables, 680
facts across all five companies**. `tables.parsing_accuracy` finally has
somewhere to live. `pages.text` stays NULL rather than reassembled from
overlapping chunks — NULL says *not captured*; a reconstruction would say *this
is the page* and be wrong. `users` is now the only table empty by design, and
`DATABASE.md` says so. 7 tests.

1,664 page rows against the registry's 1,665 pages is not a rounding artefact:
one page produced no extractable content, and the gap is the only record of it.

**The fix, for reference.**

```powershell
.\.venv\Scripts\python.exe scripts\ingest_to_database.py --all
docker exec finverify-postgres psql -U finverify -d finverify -c `
  "select c.name, count(f.id) from companies c
     left join documents d on d.company_id=c.id
     left join financial_facts f on f.document_id=d.id group by 1 order by 2 desc;"
```

Expect five non-zero rows. Then make an explicit decision about
`pages`/`sections`/`tables`: either write an ingest path for them, or record in
`DATABASE.md` that they are schema-only, present for spec conformance and
deliberately unpopulated because chunks carry provenance directly. **Either is
defensible; silence is not** — an empty table with no note reads as a bug.

---

### D-3 — MEDIUM — ~~Three figures in the documentation are stale~~ **FIXED**

The corpus finished indexing after `PROJECT_STATUS.md` was last written.

| Claim | Where | Actual |
|---|---|---|
| "16,060 chunks indexed across 4 companies; 5th indexing" | `PROJECT_STATUS.md` §19 | **22,930 across all 5** |
| "538 facts ingested from 2 filings" | `PROJECT_STATUS.md` §4, §18 | **526 across 4**, Tata 0 |
| "978 passing, 0 skipped" | `PROJECT_STATUS.md`, `TESTING.md`, `README.md` | 978/0 **with `DATABASE_URL` set**; 977/1 without |

The third is technically true and already carries its caveat; it is listed so
nobody re-derives it and thinks a test broke. Fix by rewriting the three lines
after step 2 of §4 below, when the numbers are final anyway.

---

### D-4 — LOW — ~~Five chunks on disk are not in the index~~ **WITHDRAWN — my arithmetic**

`documents/processed/` holds 22,935 chunks; Qdrant holds 22,930. The indexer
also reported `4 chunks were truncated by the embedder - retrieval on those
chunks cannot see their tail`.

**There was no discrepancy. The error was mine.** I counted every JSON line
in the chunk files as a chunk, including each file's cache-header line. Five
files, five headers: 22,935 − 5 = **22,930**, exactly what is indexed.

Checked properly: **22,930 chunk ids on disk, 22,930 indexed, 0 missing, and 0
UUID5 point-id collisions** across all of them. The collision check is the one
that would have mattered — a collision means one chunk silently overwrote
another — and it is clean.

Recorded rather than deleted because a **withdrawn defect is still a
measurement**, and because the retraction is the honest half of a register that
otherwise only accumulates confirmed problems. The four truncated chunks are
real: those chunks exceed 512 tokens and retrieval cannot see their tail.

---

### D-5 — LOW — Ghostscript absent, so camelot `lattice` is unavailable **— now quantified**

103 of 13,559 table chunks (0.76%) were flagged low parsing accuracy, several
below 40 — concentrated in HDFC Bank and Tata Motors, the two most complex
layouts. D10a chose camelot `stream` on measured evidence, but that measurement
compared `stream` against **pdfplumber**, never against `lattice`, because
Ghostscript was missing then and still is.

**Now quantified**, because populating the `tables` entity gave parsing
accuracy somewhere to live:

| Company | Tables | Mean accuracy | Below 80 | Below 50 |
|---|---:|---:|---:|---:|
| HDFC Bank | 918 | 96.2 | **49** | **9** |
| Tata Motors | 960 | 96.8 | 29 | 3 |
| Sun Pharmaceutical | 486 | 95.8 | 25 | 2 |
| Infosys | 129 | 97.5 | 3 | 0 |
| Reliance | 287 | 98.2 | **0** | **0** |

The bank has the worst tail and Reliance has none — the shape D10a would
predict, since ruled, densely-nested schedules are where `stream` is weakest.

**Still an unmeasured opportunity, not a known defect.** D10a compared `stream`
against *pdfplumber*, never against `lattice`, because Ghostscript was missing
then and still is. Worth an hour *only if* the first campaign shows HDFC
underperforming:

```powershell
winget install ArtifexSoftware.GhostScript
.\.venv\Scripts\python.exe scripts\verify_environment.py     # ghostscript -> VERIFIED
# then re-measure on the flagged pages before changing D10a
```

Do not switch extractors on intuition — D10a was written because reasoning
about extractors produced two wrong conclusions that measurement overturned.

---

### D-6 — CRITICAL — ~~The campaign searched five filings without knowing which~~ **FIXED**

**Found by running Step 0b of this guide.** Re-measuring retrieval after D-1 was
supposed to be bookkeeping — a stale number refreshed. It exposed the largest
defect on the project.

**The chain.**

1. `evaluate_retrieval.py` filtered *every* call to `meta["document_id"]`, so
   every retrieval figure ever recorded described a single-document search.
2. `run_campaign.py` builds **one** `PipelineDeps` for a whole campaign, so its
   `company` and `document_id` were necessarily `None`.
3. `parse_question` does **not** extract the company from the question text — it
   returns `None` even for *"For HDFC Bank Limited, as reported for the year
   ended March 31, 2024, what were total deposits?"*
4. So `retrieve_for_spec` added no company filter, and every campaign question
   would have searched all 22,930 chunks across five filings.

**Measured** on the 22 gold questions (RX-012):

| Condition | R@10 | MRR | Evidence acc. | Misses |
|---|---:|---:|---:|---:|
| company known | 0.932 | 0.616 | **0.909** | 2/22 |
| company unknown | 0.750 | 0.357 | **0.727** | 6/22 |
| hybrid, no planning | 0.523 | 0.293 | 0.500 | — |

MRR falls hardest. The right evidence, when found at all, ranks far lower — and
at `top_k=8` the channels would frequently never see it. **The entire result
would have been computed on evidence the system failed to retrieve, and the
failure would have read as a limitation of the method.**

**Fixed.** The company travels with the question from the dataset, which knows
it authoritatively, rather than being parsed back out of the question text —
that would be a second inference to get wrong. `run_question` takes `company`
and `document_id`; `PipelineState` carries them; per-question overrides the
campaign default. Three gates, including one pinning that an unknown company
yields **no filter rather than a guessed one**.

**The finding worth keeping.** Module 7's planned retrieval read as **+0.068**
R@10 on the single-document slice and is **+0.409** corpus-wide. A component
measured under easier-than-real conditions looked like a rounding error and was
the load-bearing part. Corpus-wide and unscoped, hybrid (0.523) barely beats
keyword alone (0.500) — **knowing which company is worth more than every
retrieval tuning measured on this project combined.**

---

### D-7 — MEDIUM — ~~A compound scale token was silently discarded~~ **FIXED**

**Found by running the pipeline live on HDFC Bank, and it looked like a
success.** Both channels independently returned 23,79,786 for total deposits and
agreed at risk 0.000, band HIGH. The figure is correct — HDFC's own narrative on
p.217 reads *"Total Deposits rose by 26.4 per cent to ₹ 23,79,786 crore"*.

The evidence came from the FY24 highlights page, which heads its column
**`Deposits (K Cr)`**. The parser matched `cr`, **discarded the `K` without
comment**, and produced the right number **without reasoning about the pair**.

| Input | Read as | Correct | |
|---|---|---|---|
| `23,79,786 K Cr` | 2.379786 × 10¹³ | 2.379786 × 10¹³ | right, by luck |
| `1 thousand crore` | 1 × 10³ | 1 × 10¹⁰ | **wrong by 10⁷** |

Frequency, measured: **29 compound scale expressions in the corpus, all in HDFC
Bank's filing.** `thousand crore` occurs zero times.

**Fixed by flagging, not by re-arithmetic.** `AMBIGUOUS_COMPOUND_SCALE` fires
when a second scale-shaped token survives beside the one used. The arithmetic is
unchanged on purpose: promoting bare `k` to a scale would turn the only case
that actually occurs into a 1000× error, against documentary evidence, and
changing compound handling for a phrase no document uses is how D10 reached two
conclusions measurement overturned.

**The transferable point.** A green agreement between two independent channels
at risk 0.000 is *not* evidence the reasoning was sound — here both channels
read the same correct digits past the same undiscussed scale token. Agreement
measures corroboration, not correctness, and this is what that distinction looks
like in practice.

---

## 3. Blockers that are yours, not mine

### B-1 — 268 candidate gold answers, 0 validated

Spec §16 requires human validation of custom gold data and nothing substitutes
for it. The dataset layer enforces this structurally — I verified it refuses:

```
$ run_campaign.py --arms A B5 --split validation --budget-only
the validation split has no VALIDATED questions.
  160 question(s) exist, 160 are pending human validation (spec 16).
```

**~40 questions is enough for a first real result**, and the worksheet is now
interleaved by company (D35) so those 40 are 8 from each filing rather than 40
from HDFC Bank. All 268 is what the frozen test-set evaluation needs.

The 11 derived-metric questions have **no candidate answer on purpose** — a
pre-filled arithmetic result invites a validator to wave it through. Those need
the figure worked out and signed for.

### B-2 — ~~No second vendor~~ **RESOLVED 2026-08-29 (RX-014)**

**NVIDIA NIM passed all four D21 steps.** Channel B moved off Groq:

```
NATURAL_CHANNEL_MODEL=groq/qwen/qwen3.6-27b
PROGRAM_CHANNEL_MODEL=nvidia/nvidia/nemotron-3-ultra-550b-a55b   <-- different vendor
VERIFICATION_AGENT_MODEL=groq/qwen/qwen3.6-27b
QUESTION_UNDERSTANDING_MODEL=groq/openai/gpt-oss-20b
```

`channels_are_independent()` now reports **cross-provider**. Verified end to end
on RX-010's question: both channels independently returned INR 88,461 crore,
AGREE, band HIGH — making that figure's fourth independent derivation, the first
on another vendor's hardware.

An earlier candidate failed the same checklist at step 2 (HTTP 402 on every
completion, RX-008), which is exactly why step 2 demands a real completion
rather than a reachable endpoint.

**It landed before the campaign, so nothing needs re-running.** Two caveats now
travel with every result:

- **The daily limit is UNOBSERVED.** NVIDIA sends no rate-limit headers and no
  daily refusal has fired. `requests_per_day=1000` is a pacing placeholder
  labelled as such, not a measurement — quoting it as one would repeat the
  Gemini error (registry said 1,500/day, provider enforced 20).
- **Latency on this provider is not reportable.** Identical prompts returned in
  0.42s and 12.5s; the free 550B endpoint sheds load with HTTP 503 under
  contention rather than 429 under quota. 503 was already retryable, so no code
  changed. Request and token counts are unaffected, so the cost analysis stands.

**One piece of good news from the quota check.** Groq's ceiling is 14,400
requests/day and the full 13-arm × 150-question campaign is 4,350. Gemini's
20/day looked like the binding constraint but is not on the critical path,
because nothing is bound to Gemini. **The full campaign fits inside a single
day of Groq quota.**

### B-3 — The conference and its date

D24 cut scope against a deadline whose date is unrecorded. The ship list is
ordered but not scheduled.

### B-4 — FinVerify-IND is 160 against a ~500 target

Spec §34 sets "approximately 500 high-quality, human-validated questions" and
also says *do not optimise for size at the expense of gold-label quality*. 160
is a defensible interpretation of that trade-off, but it is a **deviation from a
numeric target in the spec**, and §1's mandatory rule requires it be documented
and approved rather than absorbed.

Coverage is uneven and visible before any run — HDFC 56, Sun Pharma 39, Infosys
33, Reliance 17, Tata Motors 15 — so a per-sector claim on the thin cells is not
available. The cause is a metric lexicon that is IT-centric; a bank's balance
sheet says *deposits* and *net interest income*.

**Your call, and it changes the write-up either way.** Extend the lexicon and
regenerate toward 500, or record 160 as a deliberate quality-over-size decision
in `DECISIONS.md` and state the limitation in `DATASET.md`.

---

## 4. The guide — in dependency order

Each step names how you know it worked. Do not skip a verification: six of the
seven defects on this project were invisible to a green test suite.

### Step 0 — Fix the collection wiring — **DONE 2026-08-27**

All four parts, plus the guard, plus the container. Verified by asking about a
company that is not Infosys and getting its pages, and by deliberately restoring
the broken config to confirm `/health` can report `false`. See **D-1**.

### Step 0b — Re-measure retrieval — **DONE 2026-08-27, and it found D-6**

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --validate-gold
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --arms --planned --corpus-wide --diagnose
```

62/62 gold spans verified. Company-scoped, RX-005 reproduces exactly: 0.932 R@10
/ 0.616 MRR / 0.909 evidence accuracy. **Unscoped it is 0.750 / 0.357 / 0.727** —
which is what the campaign would have had. See **D-6**.

**New blocker this exposed:** gold evidence spans exist for **Infosys only**.
These numbers describe 22 Infosys questions among five filings. Retrieval on the
other **223 of 268** candidate questions is unmeasured, and no per-sector
retrieval claim is available. ~5 evidence-span questions per filing would settle
whether 0.909 generalises or is an Infosys artefact. This is separate work from
validating answer gold — spans, not answers.

### Step 1 — Validate the gold answers (the long pole)

```powershell
.\.venv\Scripts\python.exe scripts\build_finverify_ind.py export
#   fill `verdict`: validated | rejected | needs_review
#   leave `corrected_answer` blank when the candidate is right
.\.venv\Scripts\python.exe scripts\build_finverify_ind.py import `
    --csv datasets\finverify_ind\worksheet.csv --validator "Your Name"
.\.venv\Scripts\python.exe scripts\build_finverify_ind.py audit
```

Done when `audit` reports ≥40 validated. Then the seeded 20% second pass
(`export --pass 2`) for intra-annotator consistency — **not** inter-annotator
agreement, and the write-up must not use the stronger term.

### Step 2 — Re-ingest the database — **DONE 2026-08-27**

Per **D-2** and **D-3**. All five companies show non-zero facts; pages, sections
and tables populated; the stale figures corrected across `PROJECT_STATUS.md`,
`TESTING.md`, `README.md`, `API.md` and `DATABASE.md`.

Re-run `ingest_to_database.py --all` after each campaign to project its results
into the database.

### Step 3 — Smoke the campaign before spending quota

```powershell
.\.venv\Scripts\python.exe scripts\run_campaign.py --arms A B5 --split validation --budget-only
.\.venv\Scripts\python.exe scripts\run_campaign.py --arms A --split validation --limit 3
```

Three questions is enough to catch a config error before 4,350 requests do.
Read the run artifact by hand — check the cited pages belong to the right
company. **This is the step that catches the next D-1.**

### Step 4 — The H1 campaign

```powershell
.\.venv\Scripts\python.exe scripts\run_campaign.py --arms A B5 --split validation
```

A vs B5 *is* H1, and it is the cheapest thing that produces a real finding. The
runner is resumable and question-major, so an interruption leaves a balanced
prefix rather than a biased one.

### Step 5 — Fix the operating threshold, then freeze

```powershell
.\.venv\Scripts\python.exe scripts\analyse_campaign.py --run <run_id>
git tag methodology-freeze-v1 && git push --tags
```

`analyse_campaign.py` refuses to select a threshold on the run it is reporting —
that refusal is the guard against selecting and reporting on the same data.
**Tag before any test-set evaluation. Non-negotiable.**

### Step 6 — The full ablation A–H

This tests H4 and carries the most research value of anything remaining. 4,350
requests, one day of Groq quota.

### Step 7 — The test set, once and only once

```powershell
$env:FINVERIFY_ALLOW_TEST=1
.\.venv\Scripts\python.exe scripts\run_campaign.py --arms A B5 --split test `
    --reason "frozen methodology v1 final evaluation"
```

Needs the flag *and* a stated reason, and logs every access. Evaluated once per
frozen methodology version.

### Step 8 — Write it up

```powershell
.\.venv\Scripts\python.exe scripts\write_case_study.py --run <run_id>
```

The corpus section is already written; this fills the results section. Then the
hypothesis verdicts (H1–H5), the error analysis tables, and the cost-matched
comparison.

### Step 9 — Housekeeping before submission

- **Rotate the API keys** pasted into a chat transcript during development
  (Groq, Gemini, NVIDIA, api-ninjas).
- Verify FinQA / TAT-QA / ConvFinQA licences permit this use, or drop them from
  the write-up.
- Literature currency sweep — specifically for work pairing program execution
  with NL reasoning *as a detector*, which would narrow the gap claim.
- Decide whether `documents/raw/` PDFs are redistributable.
- Decide B-4: extend toward 500, or record 160 as deliberate.

---

## 5. What must not be claimed, even after all of the above

Listed here because the pressure to overclaim arrives exactly when the numbers
finally exist.

1. ~~Not cross-vendor independence~~ — **cross-vendor is now claimable** (D21,
   RX-014). But **not NVIDIA's latency**: identical prompts returned in 0.42s
   and 12.5s on a contended free endpoint, so no latency figure measured there
   describes the method. Token and request counts are fine.
2. **Not calibrated confidence** — AUROC is rank-based and valid on an
   uncalibrated score. **Brier and ECE are not** and must not be quoted until
   the risk score is fitted on real data.
3. **Not inter-annotator agreement** — one validator yields intra-annotator
   consistency.
4. **Not "cheap"** — `cost_usd` is 0.00 because the tier is free.
   `equivalent_cost_usd` would be the number that transfers, but it is null in
   every report: no provider carries a published paid rate, so cost is stated in
   tokens and calls.
5. **Not comparable to frontier-model results** — the claim is about *relative*
   detection across arms on identical inputs. Free models are weaker; absolute
   figures must never be set beside frontier results as though the setups
   matched.
6. **Not container-escape-proof** — the 54 escape attempts test the
   configuration, not the Docker or kernel isolation boundary.
7. **Not a per-sector claim on the thin cells** — Reliance 17 and Tata Motors 15
   are below the 15-question floor the case study enforces.
8. **Not a retrieval claim off Infosys** — gold evidence spans exist for one
   filing. 0.909 describes 22 Infosys questions searched among five filings; it
   is not known to hold for the other 127 candidate questions, whose documents
   are structurally different and whose vocabulary the metric lexicon does not
   cover (RX-012).
9. **Not "hybrid retrieval works"** — corpus-wide and unscoped, hybrid scores
   0.523 against keyword-alone's 0.500. What works is company-scoped planned
   retrieval. The honest sentence names the scoping, not the fusion.

---

*State: [`PROJECT_STATUS.md`](PROJECT_STATUS.md) · Next actions:
[`TODO.md`](TODO.md) · Reasoning: [`DECISIONS.md`](DECISIONS.md) ·
Measurements: [`EXPERIMENTS.md`](EXPERIMENTS.md)*
