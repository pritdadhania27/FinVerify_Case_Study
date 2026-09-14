# Retrieval

How evidence gets from a PDF into a reasoning channel's context, and — the part
that took longest to get honest — how well that actually works.

Retrieval quality bounds everything downstream. Both reasoning channels read the
same retrieved evidence, so a question whose evidence never reaches the context
cannot be answered correctly by either channel, and their agreement on a wrong
answer is not a detector failure but a retrieval failure wearing its clothes.
That is why this module is measured before the reasoning modules are built.

---

## 1. Chunking — tables are chunked as tables

`backend/rag/chunking.py`

A character-count chunker applied to a financial statement destroys the thing
being retrieved. A table cut in half leaves rows whose column headers are in a
different chunk, so `2,071` arrives with no indication that it is equity share
capital, in crore, for FY2024.

So:

- one chunk per table wherever it fits;
- oversized tables split **by row**, with the header repeated in every part;
- the unit declaration rendered into the chunk text as a banner, including the
  carry-forward from an earlier page (D13) — a continuation page's units live on
  a page that will not be retrieved alongside it;
- provenance on every chunk, so any retrieved evidence cites document, page,
  section and table.

**Header detection is multi-row, and this was a content-integrity bug, not a
ranking one.** Financial statements carry a unit caption, then column names, then
the fiscal years, as three separate Camelot rows. Taking only row 0 kept
`(In crore, except equity share and per equity share data)` and discarded
`Year ended March 31, / 2024 2023` — so part 2 of the split profit and loss
statement read `Profit for the year | 26,248  24,108` with **nothing in the chunk
saying which column is which year**. A figure whose period cannot be recovered is
worse than a missing figure, because it still looks like an answer.

**Budgets are set from measured tokens-per-character, not the ~4 chars/token rule
of thumb.** Pipe tables run about 1.4 chars/token — digits, commas and `|` all
tokenize densely. At the original 1,800-character budget, 20 of 234 chunks
exceeded the model's 512-token window and lost their trailing rows from the
vector with no error raised. The table budget is 700 characters.

**Table figures are indexed once.** Page text from PyMuPDF repeats table cells as
loose runs, and a run like `Current tax 2.17 8,390 9,287 Deferred tax ...` clears
both the alpha-ratio and word-count prose tests. Indexed as text it competes with
— and beats — the structured table chunk covering the same numbers, because a
bare `8,390` is a tighter match for a numeric query than a full table is.
`is_table_spillover` drops a paragraph whose figures are already in a table on the
same page **and** which contains no sentence; real narrative quoting figures has
sentences and survives. On the Infosys filing it removed 65 paragraphs, all
inspected and confirmed to be flattened table content.

## 2. Embedding

`backend/rag/embedding.py`

Local `sentence-transformers` (D2), so retrieval is reproducible and independent
of any API. The model is **`intfloat/e5-base-v2`**, chosen by measurement rather
than preference (D2a): on identical chunks with an identical BM25 leg it beats
`bge-base-en-v1.5` on every retrieval metric — Recall@10 0.568 vs 0.477
standalone, 0.682 vs 0.568 in fusion. It costs about 60% more embedding time on
this host.

Two details that fail silently if wrong:

- **Query-instruction prefixes** are applied to queries only. BGE and E5 both
  expect one; omitting it degrades recall with no error raised. E5 additionally
  needs `passage: ` on the indexed side.
- **Truncation is counted and reported.** The model cuts at 512 tokens without
  complaint.

Throughput on this CPU-only host is ~1.1 chunks/s, so the full 1,665-page corpus
is roughly an hour to embed once. That is a real constraint on iteration speed,
which is why extraction output is cached (`documents/processed/*.chunks.jsonl`)
and only the embedding pass re-runs when the model changes.

## 3. Vector index

`backend/rag/indexing.py` — Qdrant in server mode (D5), cosine over normalised
vectors.

Payload indexes exist on exactly the fields spec §19 requires filtering on:
document, company, fiscal year, section, kind, page, table index. Filtering is
what makes a multi-company corpus usable — a question about Infosys must not
retrieve HDFC Bank's balance sheet — and filter isolation is verified against the
real service rather than a stub, because filter semantics are the part most
likely to differ between the two.

Point ids are `uuid5` of the chunk id, so re-indexing a document replaces its
points instead of duplicating them.

## 4. Hybrid retrieval and fusion

`backend/retrieval/hybrid.py`

Two legs over the same filtered candidate set, so they never disagree about what
the corpus is:

- **semantic** — Qdrant vector search;
- **keyword** — BM25Okapi, with a tokenizer that keeps numerals intact, because
  `2,071` is a highly selective query term when checking whether a specific
  figure appears in the corpus, which is exactly what verification needs to do.

Fusion is **Reciprocal Rank Fusion** (K=60), combining ranks rather than scores:
cosine similarity (~0.5–0.6, tightly bunched) and BM25 (unbounded,
corpus-dependent) are not on comparable scales, and choosing a normalisation
would make it a tuned parameter nobody reported.

**Fusion earns its place, but only once the query is clean** — see §5a and §6.
On raw questions RRF lost to BM25 alone and a weight sweep was monotonically
bad; on scaffolding-stripped questions every non-zero weight beats BM25 alone
and the sweep is flat across all of them. Plain RRF (w = 1.0) is therefore
correct with **no hyperparameter to tune**. `semantic_weight` /
`keyword_weight` are retained as an ablation handle only.

## 5a. Query preparation — the largest single retrieval gain

`backend/retrieval/query.py`

A financial question is mostly boilerplate, and the boilerplate matches
everything. For *"What were total assets as at March 31, 2024?"*, measured across
the 906 chunks: `total` appears in 16% of them, `assets` in 25%, and
`as`/`at`/`march`/`31,`/`2024` in 27-57%. Every term a reader would call *the
question* is among the corpus's most common terms. BM25 sums term contributions,
so chunks dense in filler outscore the one chunk that answers, and the dense leg
embeds a sentence whose content is swamped by date scaffolding shared with the
rest of the document.

Stripping that scaffolding moved evidence-retrieval accuracy from 0.636 to 0.818
and MRR from 0.341 to 0.551 (RX-004) — no re-indexing, no new model, no new
component.

Two details that keep it honest:

- The stopword list is **not** a general English one. `other`, `total`, `net`,
  `current`, `basic`, `diluted` are all kept, because they are exactly what
  separates "other equity" from "equity" and basic EPS from diluted. A general
  list would trade one retrieval failure for a worse one.
- The **date is dropped from the search text but not discarded.** Which fiscal
  year is being asked about is real information; it belongs in the `fiscal_year`
  metadata filter, which the index already supports. `extract_fiscal_year` does
  the deterministic part; full extraction is Module 7's job.

A `kind="table"` filter was tested in the same pass and **rejected** — alone it
gained nothing, and combined with stripping it lost ground, because several
evidence groups are legitimately satisfied by narrative chunks.

## 5. How retrieval is measured

`evaluation/metrics/retrieval.py`, `scripts/evaluate_retrieval.py`

Metrics per `EVALUATION.md` §4: Recall@K, Precision@K, MRR, and evidence
retrieval accuracy (the fraction of questions where *every* required evidence
group is present — the one that matters for multi-hop questions, since retrieving
one of two needed figures yields a confident wrong answer).

**Gold spans are located by `(page, literal anchors)`** (D18). Table chunks are
re-rendered pipe tables, not substrings of the page text, so they have no
character offsets into it; an offset rule could only be computed against the
chunker's own output, which would make the gold labels a function of the system
under test.

Three things the harness does that a plain metric script would not:

| Mode | Why it exists |
|---|---|
| `--validate-gold` | Re-checks every anchor against the source PDF, **and audits completeness** — a group that lists fewer pages than state its figure is reported. The first gold set listed one location per figure; the filing states each on up to four pages, so 29 of 62 spans were missing and retrieval was being penalised for finding evidence it had found. |
| `--arms` | Scores semantic-only, keyword-only and fused from one pass over one candidate set, so a difference between them is fusion and nothing else. |
| `--diagnose` | Separates *ranked too low* from *absent from the index*. These need opposite fixes and are indistinguishable from a score. |

## 6. Measured results

> ### These figures are SUPERSEDED. Read this box before the table.
>
> Everything in this section was measured on **Infosys only, 22 questions, against
> gold that was later corrected**. RX-028 re-measured the same pipeline against
> the corrected gold across all five companies, and the numbers fell hard:
>
> | Company | corrected | as reported below | change |
> |---|---:|---:|---:|
> | Sun Pharmaceutical | 0.560 | 0.611 | −0.051 |
> | Reliance Industries | 0.320 | 0.438 | −0.118 |
> | HDFC Bank | 0.280 | 0.538 | **−0.258** |
> | Tata Motors | **0.080** | 0.375 | **−0.295** |
>
> Corpus-wide evidence accuracy is **0.08–0.56**, not the 0.909 the table below
> ends on. **And those corrected figures are themselves measured on MIXED-SPLIT
> gold** — RX-053 found that the four retrieval gold sets declared
> `split: validation` while 43 of the 76 sealed test questions appeared among
> their source questions, so RX-028/034/035 ran without the test flag and without
> an access-log entry. They are not validation figures and must not be described
> as such. The cause is not a regression: the old gold pointed at front-of-report
> summary tables — short, keyword-dense, metric name beside the figure — which are
> close to an ideal retrieval target. The corrected gold points into the
> consolidated financial statements, which are long, repetitive and numerically
> dense. The easier target was the measurement artefact, not the harder one.
>
> Two later results complete the picture and neither is in the table below:
>
> - **RX-034** decomposed the misses by true rank. The right chunk is in the top 10
>   for **0.280** of evidence groups, within rank 70 for **0.720**, and a candidate
>   at all for **0.870**. Coverage is not the binding problem; ordering is.
> - **RX-035** tested the reranker that headroom implies, and it **FAILED** — a
>   cross-encoder over a 100-candidate pool scores **0.240 against the 0.280
>   baseline**, lifting 7 groups into the top 10 and pushing 11 out, with a
>   per-company spread from +0.160 to −0.160 that reads as arbitrary rather than
>   selective. Not adopted.
> - **Both bullets above are withdrawn (RX-053):** their gold was 57% of the sealed
>   test split. On validation-only gold the right chunk is in the top 10 for 0.125
>   of groups and not ranked within 300 for 0.281 (RX-054b), and the reranker scores
>   0.156 against 0.125 — no measurable effect — so it stays unadopted for cost
>   (RX-035-revalidated).
>
> **The clean replacement (RX-054, 2026-09-12).** Gold rebuilt from validation
> questions only — 32 questions, 4 companies, all 58 spans re-verified against
> the PDFs — and measured corpus-wide: **planned 0.250, hybrid 0.062, lift
> +0.188 pooled**, positive in every company. That is the figure to quote for the
> planning contrast. The absolute per-company numbers above remain on compromised
> gold and have not been re-measured.
>
> **What below is still true:** the mechanism sections (chunking, the E5-vs-BGE
> choice, query stripping, fusion) describe how the system works and none of that
> changed. The *table* is a historical record of RX-001, kept because
> `experiments/runs/` is append-only and because the query-stripping effect it
> documents is real and large. It is not the current state of retrieval.
>
> This section carried no such warning for sixteen days after RX-028 landed, and
> its closing row was marked *(current)*.

Infosys FY2023-24 consolidated statements, 906 chunks, 22 validation questions,
62 evidence spans. Full entry and limits: `EXPERIMENTS.md` RX-001.

| Arm | R@5 | R@10 | MRR | Evidence acc.@10 |
|---|---|---|---|---|
| semantic, bge-base, raw query | 0.318 | 0.477 | 0.251 | 0.455 |
| semantic, e5-base, raw query | 0.432 | 0.568 | 0.290 | 0.545 |
| keyword (BM25), raw query | 0.591 | 0.705 | 0.355 | 0.682 |
| hybrid, bge-base, raw query | 0.455 | 0.568 | 0.349 | 0.545 |
| hybrid, e5-base, raw query | 0.523 | 0.682 | 0.341 | 0.636 |
| keyword (BM25), **stripped query** | 0.682 | 0.795 | 0.544 | 0.773 |
| **hybrid, e5-base, stripped query** *(RX-001 best)* | **0.773** | **0.864** | **0.551** | **0.818** |
| **+ Module 7 planning** *(RX-001; superseded by RX-028)* | **0.841** | **0.932** | **0.616** | **0.909** |

Three readings, in order of how much they change what happens next:

1. **The deficit was mostly the question, not the retriever.** Two rounds of work
   were aimed at fusion and reranking by a number whose real cause was
   scaffolding in the query text. A measured deficit is not evidence about the
   component you happened to be looking at.
2. **Every miss is `ranked_too_low`, never `absent_from_index`.** Extraction and
   chunking are placing the evidence in the index; ranking fails to surface it.
   All remaining effort belongs on ranking. This is the opposite of where the
   eyeball evidence pointed, since the extraction defects were the visible ones.
3. **Evidence accuracy of 0.818 is better, and still not enough.** Roughly one
   question in five cannot be answered correctly by any downstream component.
   The three remaining failures are not retrieval problems: two are queries with
   no selective term at all (*"total assets"*), one is a multi-hop question
   needing two figures from two statements. Both classes belong to **question
   understanding (Module 7)** — decomposition and metadata routing — not to a
   reranker.

## 7. Not built

- **Reranking.** Deferred: RX-004 showed the apparent need for it was largely a
  query-formulation artifact. Reconsider once Module 7 lands and the residual
  failures are re-measured.
- **OCR path** for scanned pages — 10 of HDFC Bank's 585 pages have no text layer.
- **Cross-document retrieval evaluation.** The gold set covers one document in
  one sector; a bank's balance sheet is structurally different.

---

*Current project state: [`PROJECT_STATUS.md`](PROJECT_STATUS.md) · Next actions:
[`TODO.md`](TODO.md) · Measurements: [`EXPERIMENTS.md`](EXPERIMENTS.md)*

---

## Scope is worth more than fusion (RX-012)

Every retrieval figure quoted before 2026-08-27 was measured with the search
filtered to a single document. The corpus is five filings and the campaign
filters by nothing, so those figures described a task the system does not
perform.

Re-measured on the same 22 gold questions against the full 22,930-chunk index:

| Arm | Scope | R@10 | MRR | Evidence acc.@10 |
|---|---|---:|---:|---:|
| semantic | corpus | 0.386 | 0.131 | 0.364 |
| keyword | corpus | 0.500 | 0.297 | 0.500 |
| hybrid | corpus | 0.523 | 0.293 | 0.500 |
| planned, no company | corpus | 0.750 | 0.357 | 0.727 |
| **planned, company known** | corpus | **0.932** | **0.616** | **0.909** |
| planned, company known | one document | 0.932 | 0.616 | 0.909 |

Three things follow.

**Knowing the issuer is worth more than any retrieval tuning done here.**
Company-scoping moves Recall@10 by 0.409. Fusion moves it by 0.023 over keyword
alone once the corpus is realistic. The reranking deferred in RX-004 would be
competing for the smaller of those two prizes.

**The single-document slice understated Module 7 sixfold.** Planned retrieval
read as +0.068 R@10 there and is +0.409 here. A component measured under
easier-than-real conditions can look like a rounding error and be the load-
bearing part.

**An unknown company must produce no filter, never a guessed one.** A wrong
company filter returns another issuer's balance sheet, correctly labelled, with
no signal that anything was assumed — the same shape as D33, arriving by a
different route. A test pins this.

### What is still unmeasured

The gold evidence spans cover **Infosys only** — 22 questions. These numbers say
what happens to *those* questions among five filings. They say nothing about
retrieval on HDFC Bank, Reliance, Sun Pharma or Tata Motors questions, which are
223 of the 268 candidates and structurally different: a bank's balance sheet
says *deposits* and *net interest income*, and the metric lexicon is IT-centric.

**No per-sector retrieval claim is available**, and 0.909 should not be assumed
to generalise off Infosys.
