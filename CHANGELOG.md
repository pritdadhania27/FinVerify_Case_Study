# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Research-relevant changes (methodology, metrics, prompts, gold data) are called
out explicitly, since they affect result comparability.

---

## [Unreleased]

### Changed - 2026-09-14 - CLAUDE.md is now ENGINEERING_RULES.md, and .claude/ left the repository

The rules file was named for the assistant that read it, which said nothing about what
it contains: the hard rules this project runs on - never fabricate a figure, never touch
the test split without the flag and a reason, never alter gold to improve a score, never
execute generated code outside the sandbox, never wrap the rate limiter in a retry loop,
and the scale traps that make a wrong number look plausible. Renamed to
`ENGINEERING_RULES.md`, with all 17 current citations updated: README, PROJECT_MAP,
DECISIONS, TODO, COMPLETION_GUIDE, docs/README, the code comments in `orchestrator.py`,
`rerank.py`, `validation.py`, `campaign.py`, both campaign scripts, and two test
docstrings.

Entries in this file, in `EXPERIMENTS.md`, in the run artifacts and in the original
specification still say `CLAUDE.md`. They are records of what was true when written and
are not rewritten - the same rule that keeps `experiments/runs/` append-only.

`.claude/` (assistant plugin settings and ruflo's proven-config state) is untracked and
now gitignored as a directory. It is editor configuration, not project source, and it was
published in the first push. The files stay on disk; only git stops carrying them.

### Added - 2026-09-13 - The faculty case study, and the stale claims its fact check found

`FinVerify_AI_Final_Case_Study.docx` (with a Word-rendered PDF) is the case-study
submission, laid out on the course template. It is built outside the repository and
gitignored, because its title page carries the author's name and registration number: title page, abstract, Introduction,
Methodology, The Case Environment (Data Collection; Constraints/ Assumptions), Analysis and
Results, Discussion & Social Impact, Conclusion, References. Every figure in it was
re-derived before being written down - by a fact-check pass over the run artifacts,
reports, dataset file and live API - and every citation was confirmed from a primary
source (three are cited only by their confirmed fields). All sixteen figures are
screenshots of the running application or charts drawn from committed reports.

The fact check disproved several claims the root documents were still making, corrected
in the same pass:

- **"The test set has not been touched"** (CASE_STUDY_REPORT.md §8, DEMO.md §7). It was
  evaluated once, by `campaign_20260905T112212Z`, after a voided and a superseded
  attempt; the seven access-log entries belong to those three attempts.
- **Cost "reported as `equivalent_cost_usd` at published paid rates"** (CASE_STUDY_REPORT.md,
  COMPLETION_GUIDE.md). It is null in every report; no provider carries a paid rate.
  Cost is tokens and calls.
- **Abstentions are 25 of the 39 held-out errors (64%), not 24 (62%)** - one abstention
  sits outside the 0.550625 bucket. And the committed-only interval is **[0.500, 0.839]**,
  not 0.840 (0.83946). RX-048 carries a dated correction note; CASE_STUDY_REPORT.md,
  PROJECT_STATUS.md and README.md are corrected.
- **Evidence spans are 198, not 231** (CASE_STUDY_REPORT.md, DEMO.md): 231 predates
  RX-042's removal of stale anchors.
- **RX-034's rank decomposition and RX-035-original's reranker rejection** were still quoted
  as findings in CASE_STUDY_REPORT.md, DEMO.md, RAG.md and PROJECT_STATUS.md, although
  RX-053 invalidated the gold they ran on. Replaced with the validation-only figures
  (RX-054b: 0.125 in the top 10, 0.281 not ranked within 300; RX-035-revalidated: 0.156
  against 0.125).
- **"The deterministic verifier fires on 1 question of 45"** - it is applicable on 1 and
  produced a value on none.
- **"Every resolved model id is recorded per call"** - the provider-returned id is captured
  on each response but persisted per call only for the arbiter; runs record bindings.
- Test counts (1,461 / 1,359 / 1,296 in various places) now read the measured **1,464
  passing, 1 skipped**; `tests/test_retrieval_gold_split.py` has 28 cases, not 20.

### Fixed - 2026-09-13 - Explanations said the channels "broadly agree" when one had given no figure

The explanation attached to every answer describes each risk facet in a sentence. Two of
those sentences described states the code never assigns. `confidence._facets` sets
**agreement PARTIAL** when the verdict is UNCERTAIN - fewer than two channels produced a
figure - but the prose read *"the channels broadly agree but not within tolerance"*. It
sets **evidence PARTIAL** when a channel that could have answered did not, but the prose
read *"evidence was retrieved but thin"*. Found on live answer 1650, whose program channel
had timed out: the page told the user the channels broadly agreed, when there was nothing
to agree with. UNCERTAIN is the most common verdict in every campaign, so this was the
sentence users read most often.

Both now describe the real state: *"fewer than two channels produced a figure, so the
answer could not be cross-checked"* and *"evidence was retrieved, but a channel that could
have used it produced no figure"*. `tests/test_explanation.py` asserts an UNCERTAIN answer
no longer claims agreement.

**What this does not change.** Explanations are derived once, when an answer is produced,
and ingest copies them from the run artifacts rather than regenerating them. Answers
already recorded - campaign rows and live answers 1647, 1649 and 1650 - keep the
wording they were saved with; the artifacts are append-only and are not rewritten. A live
API process started before this fix keeps the old prose until it is restarted.

### Fixed - 2026-09-13 - A slow live question was reported as the API being down

A live question asked through the dashboard took **691 s**. nginx stopped waiting at
600 s and returned 504; the page then said *"the API is not responding - check the
finverify-api container"*. The API was answering. It finished the pipeline and saved
the result as answer 1649 under `live-qa`, 91 seconds after the user had been told it was
not there. The time went on the program channel: its call to the provider failed with
`ProviderUnavailableError: nvidia: transport error: The read operation timed out`, and a
second attempt at the same question (answer 1650) took 651 s for the same reason. The
600 s limit had been set against a 167 s measurement with a responsive provider.
(The commit for this fix attributed the 691 s to machine load; the stored channel
failure shows it was the provider.)

- `docker/nginx.conf` allows **900 s**, and `LIVE_QUESTION_TIMEOUT_MS` is **920 s**, so
  the client deadline still outlasts the proxy and a slow question ends in the proxy's
  504 rather than a client abort.
- A 504 or a client timeout on `POST /questions/ask` now says the question is probably
  still running and where its saved answer will appear. Every other endpoint keeps the
  "not responding" message, which is right for them.
- `DEPLOYMENT.md` said `proxy_read_timeout` was 300 s. It had been 600 s since
  2026-09-13; it now says 900 s and why.
- `tests/test_frontend_contract.py` asserts the client deadline exceeds the proxy's
  and that the ask timeout carries the still-running message.

Also removed from the repository root: an empty, untracked directory named `-p`, left by
a mis-parsed `mkdir` on 2026-09-11.

### Fixed - 2026-09-13 - The Research detection chart called a validation run "held-out"

The caption under Research's detection chart was the literal *"Held-out detection AUROC
per arm"* for every run. Selecting the pooled single-binding ablation - validation only,
and permanently so, because the test split was spent before those arms ran - put the
project's headline contrasts under a caption claiming the opposite. Found while
capturing screenshots for the case study, which is exactly where that caption would have
been reproduced.

The caption is now built from the run's recorded split: *"on the held-out test split"*
for the test campaign, *"on the validation split, which is not held out"* otherwise. The
AUROC axis is also bounded to [0, 1]; its padded domain ended at 1.060, a value the
quantity cannot take. `tests/test_frontend_contract.py` asserts the literal cannot
return.

### Fixed - 2026-09-13 - The Research run picker offered 35 runs with nothing to show

**Reported from the running app:** Research's Run picker listed 43 runs, most labelled
"(0 answers)", each leading to an empty page.

**Why.** `ingest_to_database.py` registers every `experiments/runs/*` directory as a
run, then skips each record it cannot match to a benchmark question. The 26
development and diagnostic runs - the `slice_`, `channels_`, `qu-llm_` and `budget_`
series - record pipeline measurements, not benchmark answers, so in the database they
became runs with no answers and no evaluation metrics (their own diagnostic figures stay
in their run directories). Nine more recorded answers that were never analysed on their
own. The picker listed all of them with an answer count, on a screen that shows
**metrics**.

**The obvious fix would have broken the page.** Hiding runs with 0 answers would have
removed the pooled ablation report - the project's headline contrasts, 88 metric rows
and no answers of its own, because they belong to the two campaigns it pools - while
still offering the nine analysed-nowhere runs.

**Fixed by picking on the right count.** `/experiments` now reports each run's metric
rows (`results`) beside its answers, computed as correlated subqueries so neither count
multiplies the other. Research offers the **8 runs that carry metrics**, each labelled
with what it holds. A deep link to any other run explains itself instead of showing an
empty page: its pooled report if it was analysed inside one, Verification if it has
answers, "nothing to measure" for a development run, "no run named" for a typo. The
Dashboard's run table lists the **16 of 42** runs with answers or metrics, links each to
the screen that has something for it, names the hidden development series, and no longer
says they "produced no rows". Nothing was deleted from the database: those rows are
faithful records of run directories that exist.

**An adversarial review of the first version** - database truth, code edge cases,
wording honesty, each finding re-checked by a refuter - found the counts exact and
confirmed three minor defects, all fixed before commit: a deep link briefly showed the
generic "run analyse_campaign.py" hint before the right explanation, and kept it if the
run list failed; an unknown run id pointed at a list that may be hidden and could not be
cleared by choosing "all runs"; and the development runs were said to record no metrics
at all, when 11 of them keep pipeline measurements in their own `metrics.json`.

### Fixed - 2026-09-13 - Live questions work end to end, outages fail gracefully, and RX-035 is revalidated

**RX-035 revalidated on clean gold, and its result did not reproduce.** Same script,
cross-encoder, 100-candidate pool and cutoff, on validation-only gold with zero test
questions: **0.156 reranked against a 0.125 baseline, net +1 of 32 evidence groups**.
The original "reranking makes retrieval worse" was partly a measurement on the sealed
test split (RX-053). Reranking is still not adopted, on different grounds: no
measurable benefit, and 24.9 s of CPU per evidence group. RX-035-original is marked
INVALIDATED and kept for audit. RX-054b had predicted clean data would *strengthen*
RX-035; that prediction was wrong and is corrected where it was made. RX-028 (no
decision depended on it) and RX-034 (it motivated RX-035) are assessed and marked as
historically affected.

**Live question answering had never worked, and four separate faults hid behind the
default that switches it off.** (1) The API container is web-only, so every question
died on `ModuleNotFoundError: langgraph` as a bare 500. (2) Each request rebuilt the
embedding model: 365 s of wall time against 167 s of pipeline, past the proxy timeout.
(3) The Ask screen sent no filing, so every question ran unscoped retrieval. (4) A
live answer was never stored, so it vanished on navigation. D51 fixes all four:
`RUN_LIVE_DEMO.bat` runs the API from `.venv` with the dashboard pointed at it; the
container returns 503 naming the missing engine; the pipeline is cached and warmed;
the Ask screen defaults to a filing; live answers are stored under run `live-qa`,
never graded, and kept out of `/stats` and the research views.

**Verified through the real UI in headless Edge**, driven over the DevTools protocol
because the browser-automation server was unavailable: a live question answered in
157 s, evidence naming its filing, the answer saved and reopened cold after
navigating away, listed under `live-qa`, and both question pages loading - with no
console errors. A 37-check endpoint inventory through the nginx proxy passed with
every field the frontend's TypeScript declares present.

**The method caught its own wrong answer live.** HDFC Bank diluted EPS, gold 82.27:
Channel A answered 78.89, the executed program 82.27, the consistency engine said
DISAGREE, the arbiter declined to choose, and the answer was flagged HIGH risk
(0.912). Asked again, the generated program failed the AST allowlist, the verdict was
UNCERTAIN at 0.511, and malformed code was never executed. Both are the system
behaving as designed.

**Outages now fail gracefully instead of hanging.** With PostgreSQL stopped, requests
never returned and every screen sat on "Loading" with no error: there was no connect
timeout. Now a 503 names the database in about 10 s, every screen shows a labelled
error, pooled connections reconnect on restore, and every frontend request has a
deadline. With Qdrant stopped, asking returned a bare 500; now a 503 names the index
in 4 s, before any quota is spent.

**A security setting nothing read.** `SandboxConfig.from_env()` was written and tested
and had no production caller, so the `SANDBOX_*` limits were honoured by no campaign
ever run. `execute_program` now uses it, and a test asserts a configured limit
reaches the docker command line.

**The audit of modules 8-17** (figures reproduced from the artifacts by hand, because
two of three verifier groups died on a quota limit) took rows 12 and 14 to PARTIAL
and corrected five more. The consequential one: **the case study's headline
"Disagreement rate" of 68.9% was "did not agree"**. `agreed` is `verdict == AGREE`, so
it pooled 3 genuine disagreements with 39 questions where a channel gave no figure.
The real disagreement rate on held-out arm A is **3/61 = 4.9%**. The generator now
reports both and CASE_STUDY.md is regenerated; EVALUATION.md 5.4 says how the table
is counted. Also corrected: parse failures 0.022 on validation, not 0.000; program
hard failures 0.156 and 0.148, not 0.7%; the deterministic verifier produced a value
on 0 questions, not 1 of 45; the consistency engine ran on 363 rows, not 787.
D52 scopes out five spec functions explicitly.

**Two tests that passed for the wrong reason.** The arbiter's agree-case test stubbed
a program the sandbox rejects, so the channels never agreed - the verdict is now
asserted, and a DISAGREE is driven into the arbiter for the first time. The taxonomy
"coverage" test asserted enum membership while six spec kinds are never assigned; it
is renamed to say so.

**Documentation that described what does not work.** DEPLOYMENT.md told readers to
enable live QA on the API container; README's architecture diagram drew an evidence
validation stage the system has never had. Both corrected.

Status table: 23 COMPLETE / 9 PARTIAL / 1 BLOCKED -> **21 / 11 / 1**.
Tests: 1,437 -> **1,459 passing, 1 skipped**.

### Fixed - 2026-09-12 - A §43 audit, and six COMPLETE rows that were not

**The status table lost 6 modules.** Six independent auditors checked all 33 modules
against the spec §43 checklist by reading code and artifacts rather than status
prose. Six rows marked COMPLETE did not survive, every one flattering: **23 COMPLETE
· 9 PARTIAL · 1 BLOCKED**, down from 29/5/1. Recorded as RX-052.

**Two limits on that, stated rather than buried.** The two auditors covering modules
8-17 — the reasoning channels and the verification core — died on a session
quota limit, so **8 rows still carry COMPLETE on this project's own assessment and no
independent check**. Every adversarial verifier died too, so findings are
single-auditor and were reproduced by hand. That mattered: one downgrade was **wrong**
(module 7's 7-of-7 fiscal-year figure checks out exactly against the run artifact) and
was rejected.

**A module can pass every test it has and still never run.** `ocr.py` passes 22 tests
against real Tesseract. `extraction.py` contains the string `ocr` zero times. There is
no caller anywhere, so spec §11's OCR stage is unexercised on the real corpus
— visibly: HDFC Bank's 10 scanned pages hold ~187 characters each. Not fixed under
deadline, because re-extracting them desynchronises the chunk set from the 22,930-point
index and every artifact downstream of it. Module 3 is PARTIAL with its consequence
stated.

**The same wrong key, one module over.** Regenerating an 11-day-stale CASE_STUDY.md
(it still reported arm B3 on 45 questions, with "Disagreement rate 0.0%" for a
single-channel arm, while CASE_STUDY_REPORT.md declares it authoritative over itself)
exposed that `case_study.py` read `record[channel]["tokens"]` — abandoned by the
recorder in 2026-09. The document printed *"no row recorded token usage"* over an
artifact where all 61 rows record it. The identical defect was fixed in
`efficiency.py` and never propagated. Now 6445 tokens/question against the efficiency
report's 6444.85: two readers, one artifact, agreeing for the first time.

**The caveats were rendered in the least legible colour on the page.** `--faint`
measured **2.95:1** on a live table header, under the 4.5:1 AA floor — and it
colours `.undefined-value`, `.risk-UNSCORED`, every stat and form label, and the chart
confidence intervals. The project's rule is that a caveat travels with its number;
that rule was enforced in the markup and undone in the stylesheet. Now 4.61:1 on the
binding ground in both themes, pinned by `test_frontend_contrast.py`.

**The deployment document produced a stack that failed the project's own verifier.**
`build_ref` is a Docker *build arg*, so `--force-recreate` cannot carry it, and
DEPLOYMENT.md set neither it nor `--build`. Now documented, along with
`verify_deployed_stack.py` itself, which until today appeared in no document in the
repository. 27/27 against the running containers.

**Other corrections, each verified against the repository first.** RAG.md presented
RX-001's Infosys-only figures as *(current)*, ending at 0.909 evidence accuracy, with
no mention of RX-028 re-measuring the same pipeline corpus-wide at **0.08-0.56**.
TESTING.md credited 54 sandbox-escape attempts to `test_sandbox.py` as "executing real
containers" when they are `test_code_validator.py`, pure AST, no container. DATABASE.md
claimed all sixteen entities §26 names; §26 names seventeen. API.md omitted
`/stats`, `/answers` and `/arms`. HYPOTHESES.md still presented a VOID freeze as
governing and H4 still claimed cross-organisation channels after D44 gave that up.

**New tests: 1,406 → 1,417 passing, 1 skipped.** `test_frontend_contract.py` (8)
covers the seams a browser probe catches only by luck — every endpoint `api.ts`
calls exists in `main.py`, every route resolves, no page is orphaned, no screen
hardcodes an arm list, the screen count matches the router.
`test_frontend_contrast.py` (5) pins WCAG AA on both themes.
`test_retrieval.py` gains 3 for the §27 section filter, which `build_filter`
accepted all along and `HybridRetriever.retrieve()` never passed — an index-layer
capability no caller could reach. One of the three pins that `section` reaches the BM25
cache key, because a filter missing from that key makes two different filters share one
cached candidate set.

**One of the new tests immediately caught a bug I had just introduced.** Verifying
that the endpoint-contract test actually fails when the contract breaks meant
mutating `api.ts` to call `/statistics`. The restore did not take, the mutation was
still sitting in the file, and the suite failed on exactly that test. A test whose
first catch is its own author is a test worth having.

**D49 records three spec functions as scoped out rather than silently absent:**
document version management (§10 — one fiscal year, nothing to supersede),
the vector index's financial-metric filter (§27 — a table chunk carries dozens
of line items, so the filter belongs on `FinancialFactRow`), and frontend component
tests (§43 — covered by contract plus contrast plus live probe, with the
reasoning stated).

### Fixed - 2026-09-10 - Twenty defects found by driving the running stack, and the ablation made visible

Eight finders run against the RUNNING containers rather than the source. The
adversarial verification stage was cut short by a session limit, so these are
reproduced-by-hand rather than panel-verified; the two finder dimensions that
died (database truth, error paths) were covered manually and came back clean.

**Every one was invisible to 1,374 passing tests** - they live in the seams:
nginx config, a mount mode, image directory ownership, a truncation width.

Upload could not succeed at any file size, for three stacked reasons: nginx's
1 MB body default (four of five filings are 9.2-20.6 MB), then a read-only
corpus mount, then a root-owned volume under a non-root process. Fixed at all
three layers; verified with a 2.9 MB upload.

The arbiter panel asserted "not triggered" for arms that have NO arbiter, and
for failed fetches. A scale disagreement rendered as two identical numbers.
"70 arm-strata" counted rows across runs where 36 distinct exist. "192 judged"
contradicted the tile beside it. The research table stacked eight runs under one
heading with duplicate keys. The question column collapsed 45 questions into 6
displayed strings. Plus: stale data across a dep change, no catch-all route or
error boundary, 42 dead run links, Ask defaulting live QA to ENABLED when health
was unknown, a silent no-file upload, an out-of-range id 500, discarded 422
reasons, raw JSON anchors, sideways page scroll at 360px, and an undisplayed
build_ref.

**Added, to serve the research question rather than decorate it:** a
Configurations screen showing what each arm removes; a cross-arm question view
grouped BY RUN with each binding stated (a shared view across bindings is
RX-047); Module 16 explanations for recorded answers (new nullable column - 270
of 1,646 rows carry one, and NULL reports as "this run recorded none"); filters
on Verification; and point-and-interval charts on Research where an arm with no
detector is drawn ABSENT rather than at 0.5.

Full detail in RX-051.

### Fixed - 2026-09-09 - The ablation table now has code behind it, and one cell was wrong

**Research-relevant: this weakens the project's only supported result.**

`evaluation/ablation/` was an empty directory. RX-049's ten-cell contrast table
had been computed in a scratch script that no longer exists, so the headline
result had no committed code path that regenerated it. Writing
`evaluation/ablation/contrasts.py` reproduced nine cells to three decimals and
refuted the tenth: **A − B committed is +0.417 [0.233, 0.567], not +0.139**.
Confirmable without a bootstrap — over the 17 questions both arms committed,
AUROC(A) = 0.8810 and AUROC(B) = 0.4643.

So *"A − C survives Holm in both families; nothing else does"* is false. A − B
survives the committed family too, by a larger margin. The asymmetry the
interpretation rested on holds over all rows and reverses over committed answers.

**And both committed contrasts rest on three errors.** An AUROC ranks errors, so
the error count is the effective sample size; the module now flags such a
contrast `underpowered` and carries the warning into the database note and onto
the research screen, in the same cell as the value. The supported claim is the
all-rows one, in the family where 62% of what is flagged is the system's own
abstention (RX-048).

Also fixed: `ingest_to_database.py` silently skipped **pooled** reports
(`<run-a>+<run-b>`), which is the form the ablation analysis takes — the reason
the result had never reached the database or the UI. 12 tests added. Full detail
in RX-050; corrected in `EXPERIMENTS.md`, `CASE_STUDY_REPORT.md`,
`PROJECT_STATUS.md`, and the superseded entry below.

### Changed - 2026-09-06 - Literature currency sweep: the gap claim survives, narrower

`LITERATURE_REVIEW.md` §5 carried a standing warning that the gap claim needed a
re-sweep before submission, *"particularly anything pairing program execution with
natural-language reasoning as a detector"*. Done, with both close items read at
source rather than from a search summary.

**Nearest published neighbour, and it should be cited:** Chen et al., *Fighting
Numerical Hallucinations via Data-centric Compilation for Online Financial QA*
(arXiv 2605.31064, 29 May 2026; KDD 2026 ADS). Same domain, same failure mode,
and it does execute programs — but to **produce a correct answer**, not as a
second opinion whose disagreement is the signal.

**Further away:** Kovács et al., *Beyond Document Grounding* (arXiv 2607.00895,
1 Jul 2026) detects hallucinated spans across evidence *types* including code,
not reasoning *channels*. Multi-agent debate manufactures disagreement between
same-modality agents, which is the homogeneity the review already identifies as
the limitation.

**Qualification for the write-up:** executable-program approaches to financial
numerical hallucination are an active 2026 line. The contribution is
*disagreement as a detector*, not *program execution in finance*, and the second
must not be claimed.

### Changed - 2026-09-06 - The deterministic verifier's blocker is upstream (RX-046)

TODO 0c named two steps to unblock Module 10 and said the second *"decides
whether the rest is worth it"*. Both are now measured, offline and at zero cost,
exactly as that entry predicted — and **neither is reachable from inside
`operand_binding`.** Nothing was shipped, which is the correct outcome.

**The note column.** TODO's signal — *"a column of small bare integers in a
consistent position"* — fires on **33 of 634** table chunks, essentially all
false positives: the corpus is full of ESG and HR tables whose data genuinely is
small bare integers. Adding the constraint the description omits — a note column
sits *before real money* in the same table — cuts it to **4 detections, 1
genuine** (Reliance p67, exactly RX-041's case). ~25% precision on n=4, feeding a
component that can overrule two agreeing channels.

**The year column.** Bounded at **0.279**: of 1,793 facts only 500 get a year
from a `column_header`; 1,243 (69.3%) fall back to `document_fiscal_year`, which
names the filing rather than the column. And **90 of 188 validated questions
(47.9%) ask about the comparative year**, so that fallback points at the *wrong*
column on about half the benchmark.

The blocker is therefore **chunker-side header recovery** — a known separate
limitation whose ceiling is 32.1% at a 6-row search and 37.6% at 10, which agrees
with the 0.279 above. Two independent measurements of the same missing thing.
Module 10 stays BLOCKED, now priced rather than vague; H3 remains untestable for
a located reason.

### Fixed - 2026-09-06 - The benchmark-licence blocker guarded a road nobody took

`datasets/benchmark/` is **empty** and no module imports FinQA, TAT-QA or
ConvFinQA — they appear in `RESEARCH.md` §2 only as the datasets prior work was
validated on. `TODO` and `PROJECT_STATUS` had carried *"licences unverified"* as
an open blocker on Module 1 long after that path was abandoned.

Verified anyway, from primary sources rather than search summaries: FinQA
**MIT** (© 2021 Zhiyu Chen), ConvFinQA **MIT** (© 2022), TAT-QA **CC BY 4.0**.

**Recorded as unconfirmed rather than assumed:** FinQA and ConvFinQA derive their
documents from IBM's FinTabNet, reported everywhere as CDLA-Permissive, but IBM's
page for it is deprecated and states no terms and no other primary source was
reached. Repository licences confirmed; the underlying filings' terms are not.

`DATASET.md`'s header also said *"193 candidates, 0 validated"* — it has been 192
judged and 115 validated since 2026-08-31. Module 1 → COMPLETE.

### Added - 2026-09-08 - The program channel earns its place (RX-049)

**RESEARCH-RELEVANT, and it is the project's first supported result.** Arm A
re-run on the current binding (`campaign_20260908T164126Z`, 45 rows, 0 failed)
gives the first genuine single-field ablation: one binding, one split, one set of
45 questions.

**The model swap changed the risk score but not one answer.** Arm A on
gpt-oss-120b and on gpt-oss-20b return the identical 18 correct questions, while
detection AUROC moves 0.907 -> 0.981. Nine arm-configurations across two models,
one answer set. The confound was null on accuracy and real on the risk score,
which is what every hypothesis is tested on — so refusing to pool the campaigns
was right, and this is the proof rather than the assumption.

Paired, with Holm across the family:

| contrast | all rows | committed answers only |
|---|---|---|
| A - B (minus natural) | +0.066 [-0.004, 0.164] p=0.082 | ~~+0.139 [-0.094, 0.375] p=0.233~~ |
| **A - C (minus program)** | **+0.102 [0.028, 0.196] p=0.0020** | **+0.361 [0.235, 0.500] p<0.0001** |
| A - E (minus arbiter) | +0.030 [-0.012, 0.091] p=0.238 | +0.259 [-0.032, 0.605] p=0.108 |
| A - F (minus hybrid) | -0.002 [-0.037, 0.039] p=0.873 | -0.102 [-0.318, 0.118] p=0.272 |

> **Superseded 2026-09-09 by the RX-050 entry above.** A - B committed is
> **+0.417 [0.233, 0.567]**, and both committed contrasts rest on three errors.
> The sentence below is wrong in the committed family and is kept as it shipped.

A - C survives Holm in both families; nothing else does. The asymmetry is the
finding: removing the natural channel does not survive correction, removing the
program channel does. Not "two channels beat one" — specifically the executed
program carries the detection signal.

**Limits, permanent.** Validation only: the test split was spent on 2026-09-05
and these arms ran after it, so they can never be held out. Not H1, which is
defined against arm B5 on the retired binding — H1, H3 and H4 remain NOT TESTABLE
here. n=45, 21 for the headline contrast. Part of the effect is structural, since
arm C has one reasoning channel by construction.

### Added - 2026-09-08 - Pooled analysis, with a guard against the pool that confounds

`analyse_campaign.py --run` is now repeatable, and **refuses** to pool runs whose
split or channel bindings differ — printing both bindings and pointing at
RX-047. A refusal rather than a warning because the confounded table looks
entirely reasonable: that is how arms B-F came to be analysed against an arm A on
a different model in the first place.

### Changed - 2026-09-07 - What AUROC 0.885 is made of (RX-048)

**RESEARCH-RELEVANT, and it changes what the project may claim.** Two properties
of the risk score, recoverable from committed artifacts at zero cost, neither
reported until looked for.

**The score is a three-level flag.** Arm A takes 7 distinct values over 61
held-out questions, 3 covering 93%. `EVALUATION.md` §5.1 forbids exactly this:
*"AUROC is undefined over three ordinal buckets in any useful sense."* The module
emits a float, so the constraint passed on inspection and failed in fact — and
RX-007 had caught the identical defect in B5 without the check reaching arm A.

**62% of the errors it ranks are the system's own abstentions.** The always-wrong
bucket (21/21 validation, 24/24 test) is the abstention bucket. Abstention is
graded as an error *and* feeds the score via `coverage`/`usable_channels`.

**Held-out AUROC over committed answers only — where a hallucination can occur —
is 0.679, 95% CI [0.500, 0.840].** The interval reaches chance. Arms G (0.692)
and H (0.717) agree, so it is a property of the approach.

**Nothing is retracted.** Every figure was computed as the frozen methodology
specifies, and 0.885 remains a valid *deployment triage* number. The
committed-only figure has its own weakness — it conditions on a post-treatment
variable — and both now appear labelled in README, PROJECT_STATUS and
CASE_STUDY_REPORT §11.6. Also explains D45's "plateau": three mass points leave
about two distinguishable cut points, so objectives agreeing is arithmetic.

### Added - 2026-09-07 - The five missing ablation arms (RX-047)

`campaign_20260906T221521Z` — 225 rows, arms B C D E F x 45 validation
questions, 0 failed. Run by the owner. The gap the module audit named is closed.

**C, D and E return the byte-identical 18 correct questions** — and so do arms A,
B5, G, H and B2 on the *other* Channel A model. Every arm that keeps the natural
channel returns the same answers regardless of what else is removed or which
model runs it. Reproduces RX-038 on the new binding: 13/13 correct where
retrieval delivered every gold group, 2/25 where it did not, 0 complete-evidence
failures.

**Arm F degrades safely**: still 8 evidence blocks, so it retrieves worse rather
than less; accuracy 0.400 -> 0.178 and the fall is abstention (0.489 -> 0.711),
not fabrication. When it commits it is right 0.615 of the time.

**But the arms cannot be compared to arm A.** Each is defined as "A minus one
field", and A/G/H ran on gpt-oss-120b while B-F ran on gpt-oss-20b (D46 retired
the former). No same-binding comparison exists. On accuracy the confound is
empirically null; on the risk score it is unproven. `RUN_ARM_A_REBASE.bat` closes
it for 135 requests. An adversarial verification panel confirmed the confound
independently by enumerating all 38 run directories.

The arbiter fired once in 225 rows — B and C each lack a channel, D and E disable
it — so RX-045's fix is still unmeasured. All 225 rows do carry an `explanation`:
Module 16's missing acceptance evidence now exists.

### Fixed - 2026-09-07 - The arbiter audit trail never reached the artifact

`PipelineResult.as_record()` dropped `VerificationResult.metadata`, so RX-045's
`candidate_1` field — recorded so a position effect stays measurable after the
fact — existed in memory and in no run. The 225-row ablation campaign was written
that way. A claim that something "is recorded" is about the file someone will
read. Two tests.

### Fixed - 2026-09-06 - The arbiter always saw the natural channel first (RX-045)

**RESEARCH-RELEVANT.** `run_verification_agent` passed `channel_a` to
`CANDIDATE 1` and `channel_b` to `CANDIDATE 2`, and the orchestrator always
passes natural as A — so the natural channel led on **every arbitration this
project has ever run**. The prompt takes deliberate care not to name which
channel produced which answer; fixing the order gives it away by position, and
first-position preference is well documented in pairwise LLM judging. It sits in
the one component D1 permits to see both answers, and it lands on exactly the
questions the contribution is about.

Found by measuring Module 13's last unmeasured acceptance item — resolution
accuracy, which `EVALUATION.md` §5.4 had asked for since the methodology was
written, which cost nothing, and which nobody had run. Across all three campaigns
the arbiter fired **17 times**, resolved 16, declined 1, was right **3**. The
useful denominator: on 9 of the 16 neither channel held a correct answer, and on
the **7** where one did it returned it **3** times. Per distinct question it chose
correctly 2 of 2 when the natural channel was the right one and 1 of 4 when the
program channel was — the shape a first-position preference produces.

Order is now decided per question by SHA-256 of the question text: deterministic
rather than random, because runs are pinned at temperature 0 (D7a) and a
reordering arbiter would make campaigns irreproducible. 0.463 program-first over
the 188 validated questions. The verdict is translated back into channel terms
before anything downstream reads it, and `metadata.candidate_1` records the order
shown so the effect stays measurable. 8 tests.

**No published number changes** — the arbiter resolved 16 times in 787 rows and
its answer is already inside the graded outcome. **The fix has not been run**, so
3-of-7 is the old arbiter's figure; re-measuring needs a campaign.

`build_report` now emits an `arbitration` block per arm, carrying
`correct_was_available` alongside the raw count so the ratio is never read over
the wrong denominator.

### Changed - 2026-09-06 - The module audit, and the two gaps it found

Re-audited all 35 module rows against the spec §43 checklist. **26 COMPLETE, 8
PARTIAL, 1 BLOCKED**, replacing a blanket *"Nothing is `COMPLETE` … there has not
been [a run]"* that had been false since 2026-09-01. Six rows still read
**"Not yet run"** about arms that had run twice.

**RESEARCH-RELEVANT — the ablation is three arms of eight.** Every campaign ran
A, G and H plus the five baselines. Arms **B, C, D, E and F have never been
executed**, so the full A–H ablation the spec orders does not exist and H3/H4
rest on two ablation arms out of seven. Nothing had said so, because the row's
stale *"Not yet run"* label hid a live gap. `RUN_ABLATION.bat` closes it for 495
requests on validation — measured with `--budget-only`, not estimated.

**RESEARCH-RELEVANT — the test split was evaluated against a voided freeze**
(D48). D46 set four preconditions; steps 1–3 (re-run validation on the new
binding, re-select the threshold, cut `methodology-freeze-v2`) were never done
and step 4 went ahead on 2026-09-05. All seven access-log entries name the voided
tag. AUROC 0.885 and every between-arm comparison are unaffected — threshold-free,
and all seven arms shared one Channel A. Precision, recall, F1 and FPR at 0.51125
are **indicative**. The threshold is deliberately **not** re-selected: chosen
before any test row existed it carries no leakage, and re-choosing it now with
the results read would add some.

### Fixed - 2026-09-06 - 53 orphaned facts, and the estimate that said 21

`prune_facts` accumulates live cell keys across every chunk file and prunes once
at the end. `ingest_dataset` already did this for questions after D42's id change
left 268 stale rows beside 192 current ones; the same defect survived one level
down because facts arrive one chunk file at a time, so nothing ever saw the whole
live set and per-file pruning would have deleted every fact from a file not yet
reached.

**It was 53, not the ~21 estimated.** That estimate compared 1,846 stored rows
against the 1,825 facts extraction *emits*, but 32 of those collide on the same
cell key and collapse on upsert. The live set is 1,793; 1,846 − 1,793 = 53. Table
re-ingested and verified at 1,793. Module 18 → COMPLETE.

Two tests. The second pins that the prune keys exactly as `ingest_facts` writes —
a prune keyed differently is a delete-everything bug that only appears on the
second ingest and looks like a successful re-ingest.

### Added - 2026-09-06 - A methodology-freeze gate on the test split (D48)

`run_campaign.py` refuses `--split test` unless a `methodology-freeze-*` tag
exists whose annotation is not marked VOID, and **fails closed** when git cannot
answer — an unavailable check must never read as permission for a resource that
can be spent once. Refusal happens before `build_split`, so a blocked attempt
leaves no line in the access log. 10 tests.

D46 had written the same precondition as prose in a decision entry, and prose
blocks nothing. One of the ten tests failed when first written and was the
valuable one: a tab-less continuation line from a multi-line tag annotation
parsed as a tag name with an empty subject, which contains no "VOID" and so
counted as a **live** freeze — the gate could have been opened by a sentence
inside the void tag's own body. Fixed in the parser.

### Added - 2026-09-06 - Every recorded row now carries its explanation

`PipelineResult.as_record()` embeds `explain(self).as_dict()`. Module 16's
acceptance evidence did not exist: `explain()` was reachable only from the API's
live-QA path, which D30 turns off by default, so all **787 rows** of campaign
data carried no explanation at all. It is derived from the same object the
verdict came from (D29), so recording it costs no API call and cannot drift from
the verdict beside it. Module 16 stays **PARTIAL** until a campaign actually runs
with it — the code is not the evidence.

### Fixed - 2026-09-06 - A published number had gone stale unnoticed (RX-044)

The anchor repair landed a day after the test split was evaluated, and nothing
re-runs the analysis when the dataset changes. Re-analysed
`campaign_20260905T112212Z` on the repaired dataset: **exactly one number moved.**
Retrieval-caused detection is **0.837** (n=32, 27 errors), not the 0.851 RX-039
reported (n=40, 35 errors); 8 questions became undecidable and sit in `unknown`
rather than being folded into the stratum that would make H2 look supported.
Pooled AUROC, accuracy, precision, recall, F1 and every hypothesis are identical,
which is the expected signature of a repair that touched only evidence anchors.
The H2 conclusion is unchanged in sign and substance. Corrected in
`PROJECT_STATUS.md` and `CASE_STUDY_REPORT.md`; the RX-039 report is preserved
untouched beside a new `_post_anchor_repair.json`.

### Added - 2026-09-06 - The oracle re-run on repaired anchors (RX-043)

`campaign_20260906T092553Z`, arm O, validation, 135 requests. The first oracle
run whose evidence is correct: RX-040's resolved through anchors citing figures
the validator had rejected, handing the channels the wrong chunk on up to 10 of
43 questions.

Restricted to the 37 questions the oracle built for, where every error is a
reasoning error by construction:

| | arm A, real retrieval | arm O, oracle |
|---|---:|---:|
| accuracy | 0.400 | **0.946** (35 of 37) |
| errors | 27 of 45 | **2** |
| blind spot | 1 of 14 | **1 of 33** |

**Retrieval is the entire story.** Same models, prompts and temperature; the
only change is what reaches the reasoner.

**RX-040 is superseded and its mechanism claim is void.** Four of its six
"reasoning errors" were the oracle's own wrong chunk, and its AUROC of 0.541 -
"indistinguishable from chance" - was measuring a broken oracle. The blind-spot
count falls from 5 to 1 for the same reason.

**H2 is unreachable, not merely untestable.** At P(error | correct evidence) =
2/37 = 0.054, twenty reasoning errors need ~370 questions with retrieval
working and nearer 1,000 end to end, against this benchmark's 45. That is the
system being accurate, not the experiment being weak.

The pooled all-45 AUROC of 0.943 is not quoted: it spans the 8 questions the
repair left without anchors, which the oracle handed nothing - 0 of 8 correct
and all high-risk.

CASE_STUDY_REPORT abstract, section 11 and section 15 updated.

### Fixed - 2026-09-06 - RX-040's headline withdrawn; a gold defect found instead

**Research-relevant: a claim was published in the morning and withdrawn the same
day, and the gold data has an internal contradiction affecting ~1 lookup in 10.**

The oracle arm's first reading was that detection collapses to chance (AUROC
0.655) and therefore that the detector had been finding retrieval failure all
along. It rested on 8 errors. **Three are not errors.**

A gold lookup carries an `answer` and `evidence` anchors naming where it is
found - the same claim twice, and they can contradict. FI82a95e99 anchors
["Other Financial Liabilities", "5667"] with answer 61269; the system returned
5667, the figure its own gold evidence cites, and was graded wrong.

Excluding gold-inconsistent questions the oracle AUROC is **0.817**, not 0.655 -
a modest drop from arm A's 0.907 on five errors, not chance. **The mechanism
question returns to "not demonstrated".**

Prevalence, lookups only (derived questions excluded, since a ratio legitimately
matches none of its anchors): **5 of 42 validation, 6 of 58 test**. Eleven qids
are listed in TODO 0b for re-validation. NO GOLD HAS BEEN ALTERED.

Invisible to every metric, because every metric takes `answer` as ground truth
by definition. It surfaced only because the oracle handed the system the cited
chunk and nothing else.

**What survives:** retrieval is the bottleneck. Accuracy 0.400 -> 0.822,
abstention 0.444 -> 0.067, with no change to the reasoner. Gold defects affect
both arms equally, so the doubling stands.

**Also found, and unchanged by the correction:** the deterministic verifier
fires on 1 question of 45 - it abstains on lookups by design and 44 of 45 are
lookups, so arm G removes a component that never runs and H3 tests nothing. And
`scale` is None on all 44 oracle evidence blocks, so the `unit` facet reports
VERIFIED on every error.

### Added - 2026-09-06 - Oracle retrieval answers the research question: no (RX-040)

`campaign_20260906T005652Z`, arm O, validation, 45 questions, 135 requests.
Every question handed the chunks containing its gold evidence, so every
remaining error is a reasoning error by construction. Oracle built for 43 of 45.

| | arm A, real retrieval | arm O, oracle |
|---|---:|---:|
| accuracy | 0.400 | **0.822** |
| abstention | 0.444 | **0.067** |
| AUROC | 0.907 | **0.655** [0.435, 0.876] |
| AUROC, reasoning stratum | - (empty) | **0.541** |
| errors caught at 0.51125 | 26 of 27 | **3 of 8** |

**Retrieval is the bottleneck, measured directly**: accuracy doubles and
abstention collapses when the evidence arrives, with no change to the reasoner.

**Dual-channel disagreement does not detect reasoning hallucination.** With the
evidence supplied the detector falls to an interval containing 0.5, and to 0.541
on the reasoning stratum. It was detecting retrieval failure all along.

The mechanism: the score reads channel agreement and evidence coverage, and both
invert between failure modes. Retrieval fails -> channels get nothing, disagree,
score rises. Retrieval succeeds and the model misreads the table -> both channels
read the SAME evidence and make the SAME mistake, agree, score falls, system is
confidently wrong. Blind spot 5 of 33 agreed answers wrong; 5 of 8 errors are
magnitude errors with both channels inheriting one context_scale from one chunk.

This explains RX-038 and RX-039 rather than contradicting them. Arm A's 0.885
held-out AUROC stands as the end-to-end number.

Caveats: 6 reasoning errors so the interval is enormous; the oracle removes
distractors as well as search (D47); validation only, by design.

CASE_STUDY_REPORT abstract, section 11 and section 15 rewritten from this.

### Added - 2026-09-05 - The held-out test evaluation (RX-039)

`campaign_20260905T112212Z`: 7 arms x 61 test questions = 427 rows, evaluated
once, zero provider failures on either channel. The test split is spent.

**Detection generalises.** Arm A AUROC 0.885 [0.801, 0.954] held out against
0.907 on validation, nothing tuned between them. At the pre-frozen threshold it
catches 34 of 39 wrong answers (precision 0.810, recall 0.872).

**Self-consistency is beaten on both axes, replicated.** 6,445 tokens/question
for AUROC 0.885 against B5's 14,816 for 0.606 - better detection for 43% of the
cost. Validation measured 2.4x, test 2.3x. The only claim in the project that
holds its size and sign out of sample.

**No hypothesis is supported.** H1 p=0.100, H3 p=0.580, H4 p=0.641; every
interval crosses zero, none survives Holm. All three now favour the full system
where H3 and H4 favoured the ablations on validation - consistent with a mild
effect and with noise alike.

**H2 became testable and FAILED.** Validation had no reasoning-caused errors at
all; test has 4. Detection is worse on them (0.787) than on retrieval-caused
errors (0.851), where H2 predicted far better. Whatever makes the risk score
work, this study gives no evidence it is dual-channel disagreement over
reasoning.

**A validation finding did not replicate.** Arm H's blind-spot rate was twice
arm A's on validation - the direction D1 predicts - and the three arms are
indistinguishable on test. Reporting the validation figure alone would have been
reporting noise.

Threshold caveat: 0.51125 was frozen before any test data existed, but on
validation running the since-retired gpt-oss-120b while test ran gpt-oss-20b.
AUROC is threshold-free and unaffected; precision/recall/F1 are indicative until
validation is re-run on the current binding.

CASE_STUDY_REPORT.md sections 11, 13 and 15 rewritten from these numbers.

### Changed - 2026-09-03 - Channel A's model reached end of life mid-project (D46)

**Research-relevant: the validation campaign and its frozen threshold were both
produced on a model that no longer exists.**

`nvidia/openai/gpt-oss-120b`, bound by D44 on 2026-09-01, reached end of life at
2026-09-03T08:00:00Z and returns HTTP 410. It lasted two days. A test-split run
had already been started against it and was caught during startup, before its
first API call, so it wrote **zero rows**.

Rebound to `nvidia/openai/gpt-oss-20b` - verified by live call, and by a 1-row
pipeline smoke test that exercises retrieval, both channels, the consistency
engine and the arbiter. Independence is unaffected: still two models from two
labs on one provider.

**A listing is not evidence a model is servable.** `/models` still lists
gpt-oss-120b hours after it began returning 410, and lists `qwen/qwen3.6-27b`,
which 404s. CLAUDE.md's "never hard-code a model id, discover with `--list`" is
only half the rule; only a real completion establishes availability.

**`methodology-freeze-v1` is VOID for evaluation** and retained as a record.
Validation must be re-run on the new binding, the threshold re-selected, and
`methodology-freeze-v2` cut, before the test split is touched. RX-038 is not
withdrawn - it is a correct measurement of a real configuration - but it can no
longer be the validation baseline for a held-out result.

### Added - 2026-09-03 - Campaigns refuse to start unless every binding answers

`run_campaign.py` makes one real completion per distinct channel binding before
loading the embedding model, and exits 2 naming the dead binding if any fails.
Two or three calls against 765 for a campaign, and it runs before the minutes of
startup rather than after.

The index has had a preflight for this shape of problem since D-1 - refuse to
spend a day of quota on a configuration that cannot answer. The models had none,
and that is how a test run reached startup against a model that had been dead
for eight hours.

`max_tokens=256`, not a token or two: these are reasoning models that spend
completion budget on hidden reasoning, and a tight budget returns empty with
`finish_reason=length`, which would refuse a HEALTHY binding. Caught by testing
the check against a good configuration as well as a bad one.

### Fixed - 2026-09-03 - A gold anchor of 12232 could never match a filing printing 12,232

**Research-relevant: this is the H2 stratifier and the retrieval metrics. Every
retrieval figure this project has published is understated by it.**

Gold evidence anchors store the bare numeral, because that is what the answer
carries. Filings print the grouped one. `normalise()` preserved commas by
design - the docstring argued that matching either form would accept a chunk
whose digit grouping was mangled - and `EvidenceSpan.satisfied_by` requires ALL
anchors. So a group whose anchors were ("cost of technical sub-contractors",
"12232") was unsatisfiable however well retrieval had done: the label matched,
the numeral could not.

The project's own documented domain trap - thousands separators - reaching its
evaluator instead of its extractor.

On RX-038's campaign it put `all_evidence_retrieved` at **1 of 45** where the
true figure is **13**, attributed every error to retrieval by construction, and
left the reasoning stratum empty because nothing could enter it. The tell was
visible in the data beforehand: **17 questions answered correctly while the
predicate said none of their evidence had been retrieved.**

- `degroup()` removes commas only from canonical grouping, Western
  (1,234,567) and Indian (12,34,567) - both present in this corpus.
- `satisfied_by` compares each anchor literally and degrouped. A mangled
  "1,2232" is still not read as 12232, so the caution the original docstring
  protected is intact.
- Eight tests pin it, including the mangling case and that loosening the
  numeral must not loosen the conjunction or the page check.

Corrected result: **13 of 13 correct where evidence was retrieved, 5 of 32
where it was not.** Retrieval as the bottleneck stops being an inference from
error labels and becomes a direct measurement.

### Fixed - 2026-09-03 - H2 printed "NOT TESTABLE" with an empty reason

A stratum can clear the size gate and still yield no AUROC, because AUROC is
undefined without both an error and a non-error. That branch set no `reason`, so
the console printed a bare dash - hiding the campaign's most interesting number.
It now says: *AUROC is undefined for reasoning_caused (13 questions, 0 errors)*.
The untestability is itself the result.

### Fixed - 2026-09-02 - Every efficiency figure this project produced was zero

**Research-relevant: H5 was null and D11's "at matched API cost" comparison
could not run, on runs whose artifacts carried complete token usage.**

`build_efficiency` in `evaluation/report.py` read `record[channel]["tokens"]`.
The recorder has always written `record[channel]["usage"]["total_tokens"]`, and
nothing ever wrote `tokens`. So `rows_with_token_usage` was 0 on every arm of
every run and `tokens_per_question` was 0.0 - on 312 of 315 rows that carried
full usage blocks.

It survived because it failed politely: the note said the zero was "0 by
absence rather than by measurement", which reads as scrupulous and was false.

- Reads the `usage` block; the flat `tokens` key is still honoured so older
  artifacts keep working.
- Reads `samples` too. B5 makes five calls per question, and counting it as one
  understated the arm fivefold - exactly the quantity H1's matched-cost
  condition exists to test.
- Reports `unattributed_tokens` per arm: tokens in a row's own `tokens_used`
  that appear in no channel block. The verification agent is the known case, and
  it is invoked without its usage being recorded anywhere, so an arm whose
  arbiter fires is undercounted. Now visible instead of silent.

What this recovered, on RX-038's data: self-consistency (B5) costs 13,709
tokens per question against the full system's 5,783 - 2.4x - for AUROC 0.600
against 0.907. The clearest supported claim in the campaign, previously
invisible.

### Changed - 2026-09-01 - Both reasoning channels moved to one provider (D44)

**Research-relevant: this narrows the dual-channel independence claim, and every
result produced after this point is under the narrower claim.**

`NATURAL_CHANNEL_MODEL` and `VERIFICATION_AGENT_MODEL` moved from
`groq/qwen/qwen3.6-27b` to `nvidia/openai/gpt-oss-120b`. `PROGRAM_CHANNEL_MODEL`
is unchanged on `nvidia/nvidia/nemotron-3-ultra-550b-a55b`. Both channels are now
served by NVIDIA, bound to models from two different labs.

Groq's 200,000 tokens/day was the campaign's binding constraint: 3,701,880
estimated tokens is eight days for four arms, and B1/B2/B4 could not run at all,
because a baseline must share arm A's natural model and that model sat on the
exhausted provider. NVIDIA has no observed daily token cap; the six-arm campaign
costs 720 requests and fits in one day.

What is lost, per D44: correlated availability (one endpoint now fails both
channels at once) and any shared serving-layer transformation, the latter
unmeasured and the real limitation. `channels_are_independent()` has always
treated "same provider, different models" as independent and returns that exact
string, which `config.json` records per run — so no artifact inherits a stronger
claim than the binding that produced it.

Arm H is unaffected and still bounds how much of the disagreement signal comes
from model diversity rather than prompt and modality difference.

### Fixed - 2026-09-01 - The H1 planner reported everything as affordable

`scripts/run_h1.py` planned the campaign against a hard-coded `GROQ`. D44 moved
Channel A to NVIDIA, so every Groq figure went to zero — and
`groq_tokens_per_question()` ended in `max(1, tokens // probe)`, a guard against
dividing by zero that turned "this provider serves none of the scope" into "1
token per question". The planner then reported an affordable scope of two
hundred thousand questions: a tool that cannot fail loudly, announcing that
everything fits at the exact moment it stopped modelling the pipeline.

- `natural_provider()` resolves the binding rather than assuming a name.
- `provider_tokens_per_day()` returns `None` where a cap has never been
  observed. None is a real answer: NVIDIA publishes no daily token cap, so
  planning it as 0 says nothing fits, and guessing a value repeats the Gemini
  error (registry said 1,500/day, provider enforced 20).
- `tokens_per_question()` returns 0 honestly; the `max(1, ...)` floor is gone.
- `affordable_questions()` returns `None` when the provider does not bind,
  which is not the same claim as "unlimited questions", and `main()` prints
  "not bounded by tokens" plus a pointer to the request count.
- `groq_tokens_per_question()` and `groq_tokens_available()` remain as thin
  shims for callers that specifically mean Groq.

**The four tests that caught this were themselves reading the ambient `.env`,**
so they asserted "Channel A is on Groq today" as much as any arithmetic, and all
four broke when a decision was taken with no estimator logic changed. They now
pin their own bindings, and three tests cover the D44 shape directly: that the
planner follows the binding, that an unused provider costs 0, and that it
returns `None` rather than a number.

### Added - 2026-09-01 - B1 and B4 baselines scheduled

Both share arm A's natural channel and were unrunnable while that channel was on
the exhausted provider. `campaign_20260901T105355Z` runs A, B5, G, H, B1 and B4
over all 45 validation questions. B2 still fits in the day's remaining requests
and is left to the owner, per D43.

### Fixed - 2026-09-01 - A refusal counted as a parser bug (RX-036)

**Research-relevant: `parse_failure_rate` and `abstention_rate` were
misattributed on any arm without a natural channel.**

`PipelineResult.abstained` consulted only the natural channel. The program
channel makes the same decision — it prints `{"value": null}` when the evidence
does not support an answer, and its own comment calls that "an abstention...
recorded as such" — but nothing downstream read it.

Invisible for fourteen runs, because every arm before B3 had a natural channel
that abstains on the same evidence and sets the flag anyway. B3 is Channel B
alone, and there the branch was simply never reached: 2 of the first 9 rows were
programs that ran cleanly in the sandbox and correctly declined, and all of them
were reported as parse failures. Accuracy is unaffected (EVALUATION.md §3 counts
both as incorrect); the attribution is the whole reason the rates are separate.

`abstained` now discriminates on `ProgramChannelResult.executed` — `execution.ok`
— so a timeout, a crash, or a non-JSON final line all stay failures and only a
clean run that chose to return nothing counts as a decision.

`_channel_record` now writes `executed` into the run artifact. It was absent, so
existing rows could only be re-analysed by matching on `failure_reason` prose —
the habit RX-033 was written to break. Future runs can be re-analysed rather
than re-run when an abstention rule changes.

`campaign_20260901T064310Z` is VOID at 10 rows and B3 restarted under one
consistent rule.

### Added - 2026-09-01 - B3 baseline, on the provider that is not the bottleneck

The four-arm campaign is blocked on Groq (196,887 of 200,000 tokens). B3 is "RAG
+ programmatic reasoning, single channel" — Channel B only, which runs on NVIDIA
— and question understanding is rule-based, so a B3 row makes exactly one API
call and it is not to Groq. `--budget-only` confirms it: 45 calls, all nvidia.
It runs while the campaign sleeps, and the Groq counter does not move.

B2 is deliberately NOT run. Plain RAG on the natural channel has to use arm A's
natural model or the comparison confounds architecture with model choice, which
puts it on the exhausted provider.

### Measured - 2026-09-01 - Cross-encoder reranking does not work here (RX-035)

**Research-relevant: a negative result, and the reason the 0.720 ceiling stays
unclaimed.**

RX-034 measured the headroom and named reranking as the lever. Tested:
`ms-marco-MiniLM-L-6-v2` over a 100-candidate fused pool scores **0.240 against
the 0.280 baseline** across the same 100 evidence groups. It lifted 7 groups
into the top 10 and pushed 11 out; of the 75 groups inside the pool, 30 moved up
and 43 moved down, so it is churning the ordering rather than doing nothing.

Per company it runs from **+0.160 (Reliance) to −0.160 (HDFC)** at n=25 each —
the signature of arbitrary behaviour, not of a technique that suits some
documents. Quoting the Reliance column alone would have shown a +57% relative
gain built entirely from choosing which quarter of the data to show.

The query-form hypothesis was flagged in the code before the run and then
eliminated: these questions are ~80% boilerplate, but stripping it changes
nothing (Tata scores 0.000 either way).

`HybridRetriever.reranker` defaults to `None` and `RERANK_MODEL` stays unset, so
no prior measurement changes. The module and the paired harness
(`scripts/measure_reranking.py`) are kept — the baseline it computes reproduces
RX-034's 0.280 exactly from separately written code, which is the best available
evidence that it measures the reranker and not the instrument.

### Added - 2026-09-01 - `GET /stats`, and a dashboard that counts rows

Corpus and progress figures the UI previously had no way to show: 5 filings,
1,664 pages, 2,780 tables, 1,846 extracted figures, 192 questions split 115
validated / 29 rejected / 48 pending, 231 evidence spans. Counted per request
rather than cached, and one test writes to the database between two calls to pin
that — a cached corpus figure that survives a re-ingest is the stale-number
failure mode this project keeps finding in its own documentation.

The schema has **no field for a metric nobody has computed**. Accuracy,
detection AUROC and hallucination rate are absent, and the dashboard says "Not
yet measured" in words with the reason, rather than rendering a zero that reads
like a result.

### Fixed - 2026-09-01 - Two stale claims in the GENERATED case study

`CASE_STUDY.md` asserted "Every question is PENDING human validation" long after
115 of 192 had passed it. A hardcoded claim in a generated document is worse
than one in a hand-written file, because regeneration re-asserts it as fresh;
the sentence is now derived from the counts.

The headline reported "Numerical accuracy 100.0%" over one graded question with
no qualifier, above a table that correctly marked the same cell too thin to
read. `MIN_CELL` now guards the headline too, and the warning names the step
size — a rate over 1 row moves in 100-point steps.

### Fixed - 2026-09-01 - An upsert-only ingest cannot express a deletion

**Research-relevant: the questions table held two generations of gold at once.**

Re-ingesting the corrected dataset left the database reading **460 questions** —
the 192 current ones plus all 268 pre-D42 rows. D42 replaced sequential qids
(`FI0250`) with content-derived ones (`FI222c2fc4`), so no new row collided with
an old one and nothing was overwritten. Half the table carried the
wrong-statement provenance RX-026 and RX-027 were written to remove, and a
`SELECT` returning both generations looks entirely plausible.

`ingest_dataset` now prunes questions the artifact no longer contains, taking
their evidence and answers with them — the rule the evidence spans already
followed one level down. Safe because the direction is one-way (D4): the files
are canonical and this table is a projection of them.

The database now matches the dataset exactly: 192 questions (115 validated / 29
rejected / 48 pending), 231 evidence spans, 1,846 facts, 1,664 pages, 537
sections, 2,780 tables. ~21 orphaned facts remain — `ingest_facts` is called
once per chunk file, so it never sees the whole live set.

### Added - 2026-09-01 - `diagnose_retrieval_ranks.py`, and the retrieval ceiling (RX-034)

**Research-relevant: bounds every end-to-end accuracy claim.**

`evaluate_retrieval.py --diagnose` files every miss whose evidence exists in the
corpus as `ranked_too_low`, whether it came 11th or 3,000th — and those need
opposite fixes. The new script reports the actual rank.

Across 100 evidence groups on the four generated gold sets, document-filtered
hybrid: the right chunk is in the top 10 for **0.280** of them, within rank 70
for **0.720**, and a candidate at all for **0.870**. Tata Motors, the worst
document, goes 0.040 → 0.680 by rank 70. **Coverage is not the binding problem;
ordering is** — the profile a cross-encoder reranker exists to fix.

Not RX-028's numbers and not a replacement for them: that measured corpus-wide
planned retrieval, which is what the campaign runs. The rank distribution
transfers, the level does not. Nothing was tuned — RX-028's ordering holds.

Also added `datasets/retrieval_eval/README.md`. Four of the nine gold sets are
pre-D42 and **not one of their `source_qid`s resolves** against the current
dataset; their evidence pages are the defective provenance RX-026/RX-027
corrected. They are kept because RX-017 and RX-020's control was measured on
them, and are now labelled rather than left as a trap.

### Added - 2026-08-31 - Human validation complete; the H1 campaign is running

**Research-relevant: the gold set is final for the validation and test splits.**

The owner judged the remaining 59 test-split rows (54 validated, 5 rejected),
completing all 192 candidates: **115 validated, 29 rejected**, 48 train-split
rows left pending deliberately. Usable as gold: **45** on validation, **61** on
test. The test split was validated before any result existed, so no judgment on
it can have been influenced by seeing the system perform; it stays sealed until
`methodology-freeze-v1`.

Validation rate over the whole exercise: **44% on the first 45 rows, 83% after**
the D42/RX-027/RX-029 fixes landed — the clearest evidence those fixes worked.

The first campaign run against that gold is **void** — see below.

### Fixed - 2026-08-31 - A campaign reported 180 completed rows with Channel A dead for 166 (RX-033)

**Research-relevant: an entire run artefact is void, and the defect could void
any future one silently.**

`campaign_20260831T184152Z` reported `completed 180, skipped 0, failed 0` — 45
questions, 4 arms, nothing flagged. **Channel A was rate-limited on 166 of those
rows.** Read at face value the run says the dual-channel system is uncertain on
97% of questions; the figure is a rate limit, not a finding. Unlike a crash, the
artefact is shaped exactly like a real one.

**Cause: two quota exception classes, one handler.** `campaign.py` stops on
`QuotaExhaustedError` — the *provider* refusing mid-call. The refusal that fires
first is `DailyQuotaExhausted` — the *local limiter* declining to make the call
at all. They were unrelated classes, so the campaign's stop never saw the one
that actually happens. `orchestrator._raise_if_quota_exhausted`, which exists
solely to prevent this and whose docstring describes the outcome in advance,
compared against a single class name. **Every test passed throughout, because
they inject the class that never fires in production.**

Fixed by making `DailyQuotaExhausted` a subclass of `QuotaExhaustedError` — one
event seen from two sides — and by deriving the guard's names from the class
hierarchy, computed per call so it cannot depend on import order. **Verified end
to end against a genuinely exhausted allowance:** the identical condition now
writes 0 rows and stops cleanly.

Two further defects fixed alongside:

- **A recorded channel could not be told from an abstention** without parsing
  provider prose. `_channel_record` now stores `error_type`.
- **`run_h1.py` analysed the wrong run.** It selected
  `sorted(glob("*/metrics.json"))[-1]` — alphabetical, not chronological — so
  `qu-llm_20260829T181529Z` won and a stale run's empty hypothesis table was
  printed as the campaign's. The run is now identified by being new relative to
  a snapshot taken before launch.

**The void run is preserved, not deleted** (`experiments/runs/` is append-only,
spec §46.7) and carries a `VOID.md`. **It must not be resumed:**
`recorder.completed()` treats its 166 dead rows as done. The re-run starts fresh
— `campaign_20260831T211058Z` is the `--limit 2` verification run that proved
the stop works, and its config pins two questions, so it is evidence rather than
a campaign to continue.

### Added - 2026-09-01 - `run_campaign_unattended.py`

The campaign needs ~8 days of free-tier allowance and stops each time the day's
is spent, so it needs resuming several times a day for a week — and every hour
nobody resumes it is allowance that refilled and went unused, since Groq's
window is rolling (RX-024) and accrues whether or not anything spends it.

**Not a retry loop**, and CLAUDE.md's rule against those still holds: it makes no
API calls of its own, never re-attempts a failed call, and the limiter remains
the only thing deciding whether a request may be made. It waits for the window
to refill and re-invokes the campaign runner. It stops for good on the deadline,
on completion, or on any non-quota failure — a run failing for a real reason
should not be restarted for a week.

It caught two of its own bugs on its first two runs, which is the argument for
having written it:

- Reading the target from `config.json` once up front would have declared a
  45-question campaign complete after 8 pairs, because the run id it was pointed
  at was created by a `--limit 2` smoke test. The target is re-read every cycle.
- It assumed a quota stop exits 0. `run_campaign.py` exits **3** — a deliberate
  third code meaning "incomplete, resumable", distinct from both success and
  failure. The supervisor read it as a real error and stopped **four rows into a
  180-row campaign** with eight days of allowance left. Both scripts now share a
  `STOPPED_EARLY` constant, imported rather than written out, with tests pinning
  the contract.

### Fixed - 2026-08-31 - The correctness predicate rejected the most complete answer (RX-031)

**Research-relevant: `correctness.judge` is the predicate under QA accuracy, the
detection label, every ablation comparison and every error class.**

Two defects of the RX-030 shape — right value, metadata that destroys it — found
by re-reading the finished gold before importing it.

**A scale word on a quotient (1 row).** Reliance's debt-to-equity ratio was
validated as `0.41 crore`. `crore` multiplies the gold by 10⁷, so a model
answering `0.41` graded **wrong** and one answering `0.41 crore` graded
**correct** — the grader was inverted on that question. Resolved by
transcription: the validator's own note said "0.41 times", so the unit is
dimensionless and the figure is untouched. `review_gold.py` now refuses a scale
word on a ratio, margin, return-on, yield or coverage question, and a sweep of
all 192 rows finds no other instance.

**A gold that states no unit kind rejected a stated one (44 of 115 rows).**
`8,415.03 crore` parses as scale CRORE with unit kind UNKNOWN — `crore` names a
scale, not a currency — and `judge()` compared dimensions symmetrically, so an
unstated gold kind was treated as a dimension of its own. It **rejected
`8,415.03 INR crore`**, the most complete answer a model can give, while
accepting the barer `8,415.03 crore`. The predicate was grading models on how
little they said, on 38% of the gold set, and would have reported it as a
units-failure rate.

Fixed in the predicate, not by editing 44 gold labels: an UNKNOWN gold kind no
longer rejects a stated prediction kind, the assumption is written into the
judgment trace, and the magnitude test still runs on canonical values so a
percentage cannot slip past a crore-scaled gold. **No gold label was altered.**

### Changed - 2026-08-31 - `LLM_MAX_TOKENS` is 4096, and scope is no longer pre-committed (RX-032)

**Research-relevant: amends D43.**

D43 planned the campaign at `max_tokens=1024` and recorded the scope as
conditional on a measurement not yet taken. The measurement was taken: six
questions asked at both budgets, paired — **3 parse failures at 1024 that 4096
answered, 0 the other way**. The bound model emits `<think>` reasoning before
its answer, so 1024 truncates the reply mid-structure. Per call 2,804 → 5,876.

`run_h1.py` no longer truncates the run to what today's forecast affords. The
campaign is question-major and resumable — an interruption leaves every finished
question with every arm, and `--resume` re-spends nothing — so it attempts all
45 and prints `affordable` as a forecast. Truncating could only lose questions;
calls landing under their reserved budget simply get further.

### Fixed - 2026-08-30 - A validator's eight rejections, chased to two extraction defects

**Research-relevant: gold data, retrieval gold, and every retrieval figure
reported so far.**

The owner judged 9 worksheet rows and rejected 8. Every rejection said the same
thing: the figure is real, printed where the generator claimed, and answers a
different question. Chasing that produced three findings, and the last was the
most serious.

**RX-026 - answers read from the wrong set of financial statements.** An Indian
filing states most metrics twice: standalone (the parent alone) and consolidated
(the group). Tata Motors' total borrowings are 13,771.04 crore standalone and
98,500.09 crore consolidated. The questions name the basis; nothing checked that
the cited page was on it. 35 of 268 candidates were wrong this way, and 27 of the
91 retrieval-gold questions graded retrieval against evidence entirely in the
wrong section - so a retriever returning the CORRECT page scored a miss.

**RX-027, defect 1 - a split table loses its column years.** The chunker repeats
a table's preamble in every part but not the header row that names the years. The
second part of HDFC Bank's consolidated balance sheet - the part holding Advances
and Investments - named no year, so every figure in it fell back to the document
fiscal year with `year_is_stated` False. The generator requires a stated year, so
it skipped the balance sheet and answered from a front-of-report summary table.
**Requiring a stated year was biasing selection toward summary and note tables**,
which have the cleanest headers. Fixed by carrying an earlier part's years to
later parts of the same table.

**RX-027, defect 2 - two-panel balance sheets leaked figures across the panels.**
Indian balance sheets are often printed as two panels side by side and extraction
flattens both into one row. Reading `cells[0]` as the label and then taking every
numeric cell to the end of the row gave Reliance's `goodwill` four facts, all
four `year_is_stated`: 14,989 and 15,270, which are goodwill, and 9,25,788 and
8,28,881, which are **total equity**. A **62x error** carrying a correct page,
row label, column index and year - indistinguishable downstream from a right
answer, in a project whose subject is detecting numerically wrong answers. Fixed
by segmenting rows on their label cells, which also unlocks the right-hand panel
where equity, borrowings and payables live.

**D42 - a question now names the statements it was answered from.** Consolidated
preferred; standalone used where it is all there is, with the question reworded
to say so; a figure whose page belongs to neither section is not asked about at
all. A definition may no longer claim a total the source row does not support -
under Ind AS the consolidated balance sheet splits borrowings and prints no sum.

**Question ids are derived from content instead of a counter**, so a regeneration
no longer renumbers every question and silently invalidates recorded human
verdicts.

| | before | after |
|---|---:|---:|
| facts mined | 1,273 | **1,825** |
| gold candidates | 268 | **193** |
| candidates on a basis they do not name | 35 | **0** |
| cited pages unplaceable in a section | 164 | **0** |
| retrieval gold on the unasked basis | 27/91 | **0/100** |
| largest company share | 47.4% | **23.8%** |

Seven of the eight rejections are resolved by these fixes; the eighth carried a
note pasted from another row and needs re-judging. **The 9 verdicts were not
carried forward** - seven were rejections of a question that no longer exists,
and recording them against a changed question would assert a human judgment
nobody made. The old worksheet is preserved at commit `045f971`.

**Every retrieval figure reported before today predates the corrected gold** and
must be re-measured before it is quoted.

Also fixed: the validator's on-screen excerpt no longer contains control
characters (Sun Pharma's filing carries U+0007 where a bullet should be, and it
was reaching the worksheet and beeping the terminal).

### Decided - 2026-08-30 - FinVerify-IND is 268 questions, and the count was never the real deviation

**Research-relevant: gold data scope.** The owner approved freezing the dataset
at **268** against spec §34's ~500 target, as the quality-over-size trade §34
itself permits (D41). §1 requires a scope deviation be documented and approved
rather than absorbed; both are now on record. Validation proceeds against these
268 and nothing regenerates - regenerating after validation begins would discard
human work.

The reason is D34's own result read forward. That extension grew the lexicon
from 33 corpus-confirmed terms to 61 and the set from 160 to 268, but the gains
were concentrated in banking vocabulary, so HDFC Bank now holds **47.4%** of the
questions and Tata Motors 7.1%. Another 232 from the same generator against the
same five filings deepens that skew rather than correcting it.

**Settling it surfaced a sharper limitation than the one being settled.** §34
names twelve question categories; the set is **256 lookup and 12 multi-hop**, so
every derived category rests on 12 questions and several are absent entirely.
That is the deviation the write-up must state, because three consequences follow
from it and none follows from the count: H1 is tested mostly on single-figure
retrieval, the deterministic channel engages on 12 of 268 (RX-025), and the
program channel is exercised on the same 12. Recorded in `DATASET.md` and
carried in `TODO.md` as a write-up blocker.

### Fixed - 2026-08-30 - A systematic sweep: config that did nothing, paths that pointed nowhere, and a budget wrong by 90x

Started from one incident - a validation session that recorded zero verdicts -
and swept the class rather than the instance.

**The campaign could not have run (RX-022).** `RateLimit` modelled requests per
minute, requests per day and tokens per *minute*. It did not model tokens per
*day*, which on Groq's free tier is the limit that ends the day: at the moment
the provider refused, the client-side counter reported 14,293 of 14,400 requests
still free. `estimate_requests` counted requests only, so `--budget-only`
reported the full 13-arm campaign as 0.30 days. It is 98. Even the minimal H1
pair is 27 days, not 0.08. Now modelled, enforced *before* the call so a
campaign sees the wall coming, and reported per provider with the binding unit
named. Groq's 200,000 is OBSERVED from a live 429, not from documentation - the
registry already carried one published figure that was wrong by 75x.

**RX-019's 35% Channel A parse-failure rate is withdrawn.** It was measured
while the measurement was consuming the day's token allowance; three runs on
identical seeds and temperature 0 gave 35%, 45% and 95%, which is a trend in
quota consumption rather than variance. Compounding it, the script's own
classifier matched on message text and filed `groq: quota exhausted: ...` under
`parse_failure` - the exact misattribution it exists to prevent. Both channels
already record the exception *class* in metadata for this reason; the classifier
now keys on it.

**Nine of ten declared settings were read by nothing (RX-021).** `LLM_MAX_TOKENS`
had been in `.env` since Module 8 annotated `# reasoning models need headroom`,
and every call used whatever default its own signature carried. Now resolved at
call time through `backend/services/llm/settings.py`, which raises on a
malformed value rather than reverting to a default. `SANDBOX_*` likewise, via
`SandboxConfig.from_env()` - those defaults happened to match `.env` exactly so
nothing ran unlimited, but an operator tightening a limit got no tightening and
no error. `ABLATION_SAME_MODEL` was deleted rather than wired: arms are
ArmConfig objects (D26), and a second source of truth could disagree with the
arm actually running.

**Thirty-odd paths resolved against the working directory (RX-021).** Anchored
to `backend/core/paths.PROJECT_ROOT`. Three mattered beyond inconvenience:
TEST_ACCESS_LOG is the audit trail for the sealed test split and its whole value
is leaving evidence IN the repository; RUNS_ROOT decides where a campaign
resumes, and a resume that finds nothing does not fail, it starts again; and
seven bare `load_dotenv()` calls meant every API key silently went missing when
run from elsewhere.

**`/health` could not say which build was answering (D40).** It reports on the
database and the vector index, both external, and was returning `status: ok`
from a container whose code predated this sweep. `BUILD_REF` is now stamped into
the image and echoed, defaulting to the literal "unknown" rather than to
anything plausible.

**The guard that matters more than the individual fixes.**
`tests/test_settings_are_wired.py` fails the build if a variable declared in
`.env.example` is consumed nowhere, or if any module declares a CWD-relative
path constant, or if any script calls a bare `load_dotenv()`. Every defect above
was invisible to a green suite for weeks.

Also fixed: a bodiless HTTP 404 is now transient rather than terminal. A 404
that names a model is a retired id and must not be retried; a 404 with an empty
body makes no claim, and treating silence as a claim cost two questions to a
provider blip that no retry was even attempted for.

1102 tests, lint clean. Recorded as D38, D39, D40, RX-021, RX-022.


### Added - 2026-08-29 - Two measurements: one hypothesis refuted, one latent defect found

**RX-018 - Module 7's LLM refinement path, live for the first time.** Written and
stub-tested since Module 7, never once run against a real model.
`scripts/measure_question_understanding.py` put 30 company-balanced questions
through it. Mechanically it is sound: 30/30 succeeded, none raised, and it
resolves metrics *better* than the rule parser - `other equity` where the rule
parser returns the whole question as one blob.

It also returns the wrong Indian fiscal year on **7 of 30**, and always the same
way: "the year ended March 31, 2023" is FY 2022-23 and the model answers 2023-24,
seven times out of seven, on every prior-year question and never on a
current-year one.

That is disqualifying rather than a tuning problem. 72% of FinVerify-IND asks
about a prior year (D36); the fiscal year is the field whose failure returns an
empty evidence set rather than a bad ranking; and one QuestionSpec feeds both
channels, so the error would be common-mode (D19) on the majority of the
dataset. Recorded as **D37**: question understanding stays deterministic. It was
already off - `orchestrator.py` calls `parse_question` - but by accident of
wiring rather than by decision. Now by decision, with the reason beside the code.

**RX-017 - the transfer gap is not a question-phrasing artifact.** RX-018 exposed
that the Infosys gold set is hand-written short form while the four generated
sets carry FinVerify-IND's full template, so RX-015's transfer gap confounded
*document* with *phrasing*. `scripts/measure_query_formulation.py` separated
them: four arms over the same 63 questions, varying only the query text with the
company filter held constant.

No arm beats the baseline. Removing the issuer's name from the query is
indistinguishable from doing nothing (-0.042, 95% CI [-0.167, +0.083], p=0.798)
and swings +0.231 on HDFC Bank against -0.222 on Sun Pharma. **Hypothesis
refuted**; RX-015's three original suspects are the only ones standing.

The run did find something else. Removing the parenthesised definition costs
**-0.250** evidence accuracy (p=0.004), collapsing Reliance from 0.438 to 0.062.
The gloss D22 added to pin a metric's *definition* turns out to carry most of the
retrieval signal too. That is a limitation the write-up must state: these
retrieval figures are an upper bound **conditioned on the question format**, and
a user asking a bare "what were advances?" would do materially worse.

15 tests, guarding the thing a null result depends on - that each arm's transform
actually fires. A null result from a broken transform is indistinguishable from a
real one.


### Added - 2026-08-29 - Validation you can actually sit down and do

Validation stood at 0/268 because a 268-row CSV checked against five filings
totalling 1,665 pages is a bad interface, not because of effort. Two scripts,
neither of which validates anything.

**`scripts/precheck_gold.py`** takes the *searching* off the validator and
leaves the *judging*. For each candidate it opens the cited PDF page through
PyMuPDF's own text layer and looks for the figure in every grouping this corpus
uses - western, Indian, bare, fractional, parenthesised for negatives - then
writes the raw line back into the worksheet. **250/268 figures are printed where
the generator said they are; 6 are too short to search and had their row found
by label; 12 are the deliberately blank derived metrics; zero are missing**
(RX-016).

That is a check on the generator. It is not validation and the code cannot
become validation: `ValidationStatus` is not importable from it, and a test
asserts so through the AST rather than the text, so the rule can still be
explained in a docstring.

**`scripts/review_gold.py`** shows one question per screen with that line from
the filing beside the candidate answer, and writes after every response so
stopping is always safe. It has **no default answer** - Enter re-prompts. A
keypress meaning "yes" when you meant "next" turns a validation pass into a
rubber stamp, and a rubber-stamped gold set is indistinguishable from an
unvalidated one in the data but not in what it claims. The worksheet remains the
round-trip format (D23); this is a front end onto the same file.

Two defects in the pre-check itself, both found and fixed before use: the
minimum-digit gate measured the raw figure rather than its searchable variants,
flagging five HDFC dividend rows as absent from a page they are printed on; and
the excerpt took the first line carrying the row label, which on Tata Motors
p449 is a footnote defining the row rather than the row. 30 tests.


### Added - 2026-08-29 - PROJECT_MAP.md, and an upload that overwrote its own corpus

`PROJECT_MAP.md` records where every file, service, volume and model cache
actually is - generated from `git ls-files`, `wc -l` and the live containers
rather than written from memory, and it says so, so a stale count is
re-derivable instead of quietly wrong.

Building it turned up two defects that no test was watching for.

**The API stored uploads under their own filename.** `annual-report.pdf` is
what several of these companies call their filing, so a second upload of a
different document under a shared name overwrote the first while the registry
still recorded the first document's sha256 - a hash describing a file that no
longer existed. Not a crash; a severed provenance chain in a system whose
entire claim is provenance. Uploads are now stored content-addressed as
`{document_id}_{name}`, with a test that writes two different files under one
name and asserts both survive.

**The API's own tests wrote into the real corpus directory.** `UPLOAD_DIR` was
a module constant, so `test.pdf` (13 bytes) and `a.pdf` (18 bytes) had been
sitting in `documents/raw/` beside five genuine annual reports since the
suite last ran. Now read per call from `FINVERIFY_UPLOAD_DIR`, redirected to
`tmp_path` by the fixture. Read per call and not at import, because a
module-level `os.environ.get` is evaluated before any fixture can redirect it -
the same evaluation-time trap as the argparse defaults fixed in D33.

Neither reached a result: nothing globs `documents/raw/`, and `configs/corpus.json`
names its five files explicitly. Both were on a path to.

### Changed - 2026-08-29 - TODO.md caught up with D36

Section 0 still carried `[!] Retrieval on the other four filings is unmeasured`
as an open blocker after RX-015 had measured it. Replaced with the result,
the per-company table, and the one question RX-015 genuinely leaves open - why
retrieval does not transfer off Infosys. The critical path is now
**validate gold answers -> run the campaign -> analyse -> write up**; retrieval
re-measurement has happened.


### Added - 2026-08-26 - Every remaining module: the engine is built

Modules 3 (OCR), 4, 16, 17, 18, 20, 21, 22, 23, 24, 25, 27, 28 and 31 went from
NOT STARTED or a PARTIAL stub to implemented, tested and documented. 637 tests
to **977 passing, 1 skipped**; ruff clean.

**Module 22 - evaluation framework.** `correctness.py` (the match predicate;
sign and unit are categorical, never tolerable), `qa.py` (abstention is
incorrect AND separately counted), `detection.py` (AUROC by Mann-Whitney with
mid-ranks, AP with no interpolation, risk-coverage with excess-AURC, the
agreement x correctness table), `statistics.py` (paired percentile bootstrap,
Holm-Bonferroni over H1-H5), `efficiency.py` (three cost figures kept apart).

**Modules 17, 23, 24, 28 - one pipeline, thirteen arms, a survivable campaign.**
LangGraph over eight nodes branching to the arbiter only on a real DISAGREE.
Every baseline and ablation arm is an `ArmConfig` over that one pipeline, and a
test asserts each ablation differs from the full system in exactly one field.
The campaign runner is append-only, keyed on (arm, question), and
question-major - so an interruption leaves a BALANCED prefix rather than one
finished arm and nothing to compare it with. Recorded as D26 and D27.

**Module 26 - FinVerify-IND reaches 160 candidates** across all five filings,
splits assigned, audit clean, worksheet exported. Every question is PENDING;
`evaluation.dataset` refuses to serve one to an evaluation.

**Module 4 - financial facts** with every field spec 12 names, columns labelled
from the table's own header, merged cells split.

**Module 16 - explanations derived, never generated** (D29). A test asserts the
module contains no provider call.

**Module 25 - error analysis** with the H2 stratifier, which fails to `unknown`
and never to a stratum.

**Modules 18, 20, 21, 31 - the product tier.** PostgreSQL as a projection of the
artifacts rather than their replacement (D28), all 16 spec entities, Alembic
migration applied to a live database. FastAPI with all nine spec endpoints and
live QA off by default (D30). React frontend, six screens, strict typecheck.
Four-container stack built and verified end to end.

**Module 27 - the case study computes itself** and refuses to render a table
from no data.

### Research-relevant

Six defects were found by RUNNING the system rather than by testing it. Each
would have shipped silently.

1. **B5's risk score would have been quantised to five values.** With n = 5,
   `1 - modal_fraction` makes the self-consistency baseline almost entirely
   ties, and AUROC counts a tie as half a concordance - so the full system would
   have beaten it partly because the baseline was rounded off. H1 would have
   read as supported for the wrong reason. Fixed by ordering continuously within
   each coarse level (D27).

2. **Quota exhaustion was silently becoming an abstention.** Both channels catch
   provider failures and return an unavailable channel, which is right. But a
   quota error caught that way lets a campaign run for hours writing rows where
   every channel is unavailable - indistinguishable in the artifact from genuine
   abstentions, corrupting the abstention rate, the parse-failure rate and the
   detection labels of a run that looks complete. Channels now record the
   exception CLASS and the orchestrator re-raises quota exhaustion.

3. **Camelot merges two year-columns into one cell.** "76  161" parses to 76
   without complaint - the current year by convention, the prior year the moment
   a table leads with its comparative. A figure that is real, printed, cited and
   answering a different question. Merged cells are now split, flagged, and left
   year-less unless the merged header names the years.

4. **A missing dependency was reported as an unreachable service.** `/health`
   said `vector_index: false` because importing QdrantIndex pulled in
   pymupdf and camelot, absent from the API image. The check now carries the
   exception text.

5. **Two PostgreSQL servers were listening on 5432** - the container and a
   native Windows service - and every host connection authenticated against the
   wrong one while `docker compose ps` reported healthy (D31).

6. **Tesseract was installed but not on PATH**, so `shutil.which` reported it
   missing. Every scanned page would have been recorded as unreadable: a
   property of the environment silently attributed to the document (D32).

### Changed

- `extraction.py` and `indexing.py` no longer import PDF-parsing libraries at
  module scope; counting vectors required a table parser transitively.
- The generator delegates table mining to Module 4 instead of duplicating it,
  and generates one question per (metric, STATED year).
- Year-header search widened from 3 rows to 6, with a data-row guard. Measured
  over 13,559 table chunks: coverage 18.1% -> 32.1%.
- The dataset audit no longer reports the 11 deliberately-blank derived answers
  as problems. An audit that flags intended states is one people learn to
  ignore.
- The project's PostgreSQL moved to port 5433; `.env.example` updated.
- `uvicorn` and `python-multipart` pinned - the lock file can now start the
  service it documents.

### Decisions

D26 (one pipeline, arms as configs), D27 (continuous self-consistency scoring),
D28 (the database is a projection), D29 (derived explanations), D30 (live QA off
by default), D31 (PostgreSQL on 5433), D32 (OCR labelling).

---


### Added - 2026-08-26 - Module 14, the error taxonomy (two axes, not one)

`backend/verification/taxonomy.py`. The vocabulary the research question is
stated in, so its shape constrains what the project can conclude.

Spec 22's ten minimum categories answer two different questions in one enum:
"wrong unit" says what an error LOOKS LIKE, "retrieval error" says where it
ENTERED. They are not alternatives - a wrong unit has a provenance and a
retrieval error has a form - and forced into one flat label they compete for a
single slot. Tagging an answer RETRIEVAL_ERROR discards that it was a scale
confusion; tagging it WRONG_SCALE discards the stratum it belongs to.

That is fatal for D12, which stratifies detection metrics by provenance while
Module 25 analyses by kind. So every label carries both axes. Recorded as D25,
since spec 22 requires taxonomy changes be documented.

Additions beyond the minimum: `WRONG_SCALE` (crore/lakh/million - the headline
error class, unreportable if folded into a general unit error), `SIGN_ERROR`
(parenthesised negatives), and provenances `QUESTION_UNDERSTANDING` (D19),
`EXTRACTION`, and `DEFINITIONAL_AMBIGUITY` (D22).

Two constraints the implementation enforces:

- **Classification requires gold.** Channel disagreement is the PREDICTOR, not
  the error. Treating it as the label would let the system grade its own
  homework - detection becomes trivially perfect and measures nothing. A
  disagreement may only ever produce a SUGGESTED label, never MECHANICAL.
- **Unknown never becomes false.** `question_parsed_correctly` and
  `extraction_faulty` are tri-state; unchecked yields UNDETERMINED /
  NEEDS_HUMAN, never REASONING. Otherwise REASONING becomes the default bucket
  for every error nobody investigated, inflating exactly the stratum H2 turns on.

637 tests passing (28 new), ruff clean.

### Verified - 2026-08-26 - api-ninjas key is not an LLM provider

A key was supplied for api-ninjas.com. Tested: valid (`/v1/facts` and
`/v1/quotes` return 200), but `/v1/chat`, `/v1/chat/completions`,
`/v1/completions` and `/v1/llm` all return 404. API Ninjas is a utility-API
collection with no text-generation endpoint, so it cannot serve either reasoning
channel. Not added to `.env`. What D21 needs is an OpenAI-compatible
`POST /v1/chat/completions` endpoint serving a reasoning-capable model.

### Added - 2026-08-25 - Module 15, confidence and risk scoring

`backend/verification/confidence.py`. Emits the continuous risk score the whole
detection evaluation ranks on, plus the four user-facing facets of spec 23
(evidence / arithmetic / agreement / unit).

Three properties are load-bearing:

- **The score is RISK, not confidence.** EVALUATION.md 5.1 defines s in [0,1]
  with higher meaning more likely WRONG, which is the inverse of the consistency
  engine's score. Emitting one where the other is expected crashes nothing and
  silently lands every AUROC in the paper at 1-AUROC - a working detector reads
  as broken and a broken one reads as working. The inversion now happens exactly
  once, and `RiskAssessment` only ever exposes risk. A test class guards it.
- **Bands are derived, never stored.** HIGH/MEDIUM/LOW is a view of the
  continuous value, because AUROC over three ordinal buckets is not a meaningful
  quantity.
- **Uncalibrated is declared, not hidden.** `calibrated=False` until weights are
  fitted on FinVerify-IND (D23). AUROC is rank-based and so is valid uncalibrated;
  Brier and ECE are not, and quoting them off provisional weights would be
  fabricated evidence.

Signals combine by damped noisy-OR rather than sum or max: sum lets three mild
signals outrank one certainty, and max is so coarse that many answers collapse
onto identical scores - which AUROC punishes, since ties count as half-discordant.

### Changed - 2026-08-25 - `ConsistencyReport` exposes both factors of its score

`score` was `base_score * coverage`, and Module 15 could not recover the factors
from the product. They mean different things: contradiction is *evidence of
error*, coverage shortfall is *absence of corroboration*. RX-007 showed what
pre-multiplying them costs - every lookup ranked below every computed answer, so
the score partly encoded "is this a lookup?" instead of "is this wrong?".
`base_score`, `coverage`, `applicable_channels` and `usable_shortfall` are now
exposed separately so calibration can learn their relative weight from data.

609 tests passing (26 new), ruff clean.

### Decided - 2026-08-25 - Four owner decisions (D21-D24)

- **D21** - cross-vendor independence to be restored via a second free vendor
  (Cerebras), superseding D20's cross-lab compromise. **DECIDED, NOT VERIFIED**:
  `CEREBRAS_API_KEY` is empty, no call has been made, and the free tier and its
  real limits are unconfirmed. D21 carries a four-point acceptance checklist and
  stays inactive until all four hold.
- **D22** - metric definitions pinned in the question text for the main set, plus
  a deliberately unpinned ambiguity subset measured separately. Turns RX-007's
  false-positive mode into a reportable finding rather than a caveat.
- **D23** - FinVerify-IND is 150 questions, not 500. Binding constraint is
  validation capacity, not generation; a gold set validated tiredly is worse than
  a smaller one validated well.
- **D24** - conference deadline. Modules 18, 20, 21, 27, 31 deferred outright;
  the research result (14, 15, 22-26, 28) ships. Deferrals are recorded as
  deferrals - the write-up must not present unbuilt components as built.

### Added - 2026-08-25 - Module 13, the verification agent

`backend/agents/verification_agent.py`. Adjudicates when the channels disagree,
and is the one component allowed to see both answers - Channel B is forbidden
from seeing Channel A because they are independent ESTIMATES; the arbiter is the
judge OF that disagreement, strictly downstream, and nothing it produces
re-enters either channel.

Triggered on DISAGREE only: an arbiter cannot adjudicate between an answer and an
absence, so UNCERTAIN is left standing and the two populations stay separable in
the metrics. Its prompt deliberately does not reveal which candidate came from
executed code - naming it invites deference on authority rather than on evidence.
Abstention is a first-class outcome, because a manufactured resolution is worse
than an open disagreement: the open one at least leaves the answer flagged.

### Fixed - 2026-08-25 - Four defects from an adversarial audit (RX-007)

All four were invisible to a green test suite, and three push the same direction
- they inflate measured disagreement and depress AUROC, making the method look
worse than it is for reasons unrelated to the models.

- **PERCENT vs RATIO rejected on the label**, before `canonical()` ran. All three
  channels agreeing on return on equity (0.2977 / 0.2977 / 0.2967) scored
  DISAGREE at 0.000. Unit kinds are now grouped by DIMENSION; genuine
  rate-vs-multiple confusion still surfaces, as SCALE_MISMATCH.
- **An abandoned mid-thought figure became an answer.** An unclosed `<think>`
  means the reply was cut off while candidates were still being weighed; the
  channel returned one the model had explicitly rejected, with available=True.
- **Channel B kept a second, smaller copy of the unit vocabulary.** Six unit
  strings resolved 10^3 to 10^12 apart between the channels, manufacturing
  SCALE_MISMATCH - the project's headline error class - out of a parser gap.
  `detect_units()` now lives once, in `financial_value.py`.
- **Coverage penalty counted absolute channels.** Every lookup was capped at 0.90
  because the deterministic channel abstains by design. Now achieved-over-
  achievable; `ChannelAnswer.applicable` separates "could not apply" from "tried
  and failed".

### Research-relevant - definitional ambiguity is not hallucination

The three channels split on return on equity because they used three standard
definitions (closing equity, average equity, owners' share). The detector flags
that as risk, correctly by its own lights and wrongly for the research question.
Module 26 gold answers must pin the definition or accept a documented range, and
error analysis needs definitional ambiguity as its own category.

600 tests passing, ruff clean.

### Fixed - 2026-08-24 - Two defects found by auditing the project rather than the tests

Both were invisible to a green test suite, and one of them is a research-
integrity defect rather than a code bug.

**The headline retrieval number was not reproducible.** `PROJECT_STATUS.md`
reports 0.909 evidence-retrieval accuracy — the Module 7 planned-retrieval path,
which is what `scripts/run_slice.py` actually uses. That figure was measured once
from a scratch script that no longer exists, and `evaluate_retrieval.py` had no
way to produce it: the committed harness only ever scored the plain hybrid path
(0.818). A number that only a deleted script can regenerate is functionally a
claim from memory, which is precisely what `EXPERIMENTS.md` exists to prevent.

`--planned` now scores that arm. Re-measured on 2026-08-24 against the same
collection: R@10 **0.932**, MRR **0.616**, evidence accuracy **0.909** — identical
to RX-005 to three decimals.

**A contentless question manufactured evidence.** `parse_question("")` produced a
sub-question with an empty search string, and the retriever answered it. Measured
against the live index, the empty question returned three arbitrary tables —
foreign-currency analysis, segment reporting — which would then have been handed
to BOTH reasoning channels as evidence. Since the channels are instructed to use
only the evidence given, that is the same failure as answering from memory, one
layer down: the system supplies a plausible context for a question that asked
nothing.

`content_terms()` now separates the two callers' needs — retrieval still falls
back to the original text (searching it beats searching an empty string), while
question understanding detects the empty case and emits **zero** sub-questions.
Nothing is retrieved, `format_evidence([])` tells the channels to refuse, and the
reason is recorded in `ambiguities` rather than being silent.

521 tests passing, ruff clean.

### Added - 2026-08-24 - Modules 8 and 9: the two reasoning channels

The research contribution, running end to end for the first time.

- `backend/agents/evidence.py` - one formatter, used by BOTH channels. If they
  saw the same chunks rendered differently, a disagreement could come from the
  rendering rather than the reasoning. Empty evidence renders as an explicit
  refusal instruction, because a model handed an empty string answers from
  memory - which is the hallucination being studied.
- `backend/agents/natural_channel.py` (Channel A) - NL reasoning. Output is
  parsed, never trusted: JSON in prose, JSON in a fence, and an empty reply (D17)
  are all handled, and an unparseable channel becomes UNAVAILABLE rather than a
  fabricated number. The value goes through `parse_financial_value`, not
  `float()` - "1,37,814 crore" read as 137814 is the scale error this project
  exists to catch.
- `backend/agents/program_channel.py` (Channel B) - generate, AST-validate,
  execute in a container. **Independence is enforced by the signature** (D1):
  there is no parameter through which Channel A's output could be passed, so the
  leak cannot be reintroduced by a caller who forgot.
- `scripts/run_slice.py` - the vertical slice, writing run artifacts per D3, and
  **refusing to run** when the two channels resolve to the same model.

First result: "How much were trade payables?" - Channel A answered 3,956 crore by
reading; Channel B wrote a program that passed the allowlist, ran in a container
in 2.2s, and printed 3,956 crore. AGREE, score 0.900. On a question where
retrieval failed, both channels ABSTAINED rather than inventing a figure, and the
engine reported UNCERTAIN (RX-006).

`tests/test_channels.py` includes `TestChannelIndependence` - research-validity
gates, not ordinary unit tests. A failure there is a bug in the code, never a
test to update.

### Fixed - 2026-08-24 - Three bugs the first live run exposed

- **Non-ASCII in generated code crashed the pipeline.** `subprocess.run(text=True)`
  encodes stdin with the host codepage (cp1252 on Windows), which cannot
  represent the rupee sign or the non-breaking hyphen that appeared in a
  generated comment. UnicodeEncodeError propagated out of the containment layer,
  whose contract is to REPORT failure, never raise it. Explicit UTF-8 plus a
  last-resort guard; regression-tested over five characters.
- **A spent quota was retried for 621 seconds.** A pace limit and an exhausted
  allowance share HTTP 429 and had one retry policy. `QuotaExhaustedError` now
  separates them and is never retried.
- **`ConsistencyReport.band` is a method, not a property** - the slice printed a
  bound method into its own artifact.

### Changed - 2026-08-24 - Gemini's recorded daily limit: 1,500 -> 20 (OBSERVED)

`gemini-3.7-flash` returned 429 after ~17 requests in a day, naming
`generate_content_free_tier_requests, limit: 20`. The previous figure came from
published Flash-class limits and is wrong for this model id by ~75x.

**Research-relevant.** At 20 requests/day, Channel A answers 20 questions/day, so
a 500-question evaluation across 6 arms is months of wall-clock on this binding.
This is a planning decision to take before Module 26, not during it - see the
risk in PROJECT_STATUS.md.

511 tests passing, ruff clean.

### Added - 2026-08-24 - Module 7: question understanding

`backend/agents/question_understanding.py`, `backend/agents/metrics_lexicon.py`,
`backend/retrieval/planned.py`. Turns a question into the figures it needs, where
to look for them, the arithmetic that relates them, and the units the answer
should carry.

It was built now because RX-004 showed the remaining retrieval failures were
question problems, not retriever problems - and it fixes them:

    hybrid, stripped query   R@10 0.864  MRR 0.551  evidence acc 0.818
    + Module 7 planning      R@10 0.932  MRR 0.616  evidence acc 0.909

- **Derived metrics decompose.** "Return on equity" is a line item in no
  document; it is profit divided by equity, from two different statements. One
  query cannot rank both, which is why that question failed at every K in four
  consecutive measurements. Sub-questions are retrieved separately and merged
  **round-robin, not by score** - taking the global top-K lets the stronger
  sub-question fill every slot, which produces a confident answer from half the
  evidence.
- **Statement hints, but only on an exact line-item match.** Attaching the
  statement to any lexicon match rescued "total assets" and lost "total other
  financial liabilities" (reported in a note, not on the balance sheet).
  Issuing both hinted and unhinted queries split the budget and lost both. Two
  rejected mechanisms are recorded in RX-005 with their reasons.
- **Deterministic first, LLM second.** The rule parser always runs; the LLM
  refines it when available. Costs no free-tier quota for the common case (D14),
  keeps a retrieval measurement reproducible, and degrades instead of stopping
  when a provider is down. Any provider failure - outage, quota, malformed JSON,
  empty metric list - returns the deterministic spec.

**Research-relevant (D19):** both channels consume the same `QuestionSpec`. This
does not violate dual-channel independence, which is a claim about reasoning over
given evidence, but it IS a common-mode failure path like shared evidence (H2):
a mis-parsed question makes both channels wrong the same way and their agreement
proves nothing. `ambiguities` and `PlannedRetrieval.coverage` exist so those
cases are visible; the Module 14 taxonomy needs a question-understanding error
category alongside retrieval-caused and reasoning-caused.

### Fixed - 2026-08-24 - Two silent bugs the Module 7 measurement exposed

- **A metadata filter that matched nothing returned nothing.**
  `extract_fiscal_year` took the first year in the question, so "between March
  31, 2023 and March 31, 2024" produced a filter for a year this corpus does not
  contain, and retrieval returned an empty list without complaint. A question
  naming more than one year now yields no filter - the correct answer, not a
  parse failure.
- **"pat" matched inside "patents".** The metric lexicon used plain substring
  matching, so "How many patents were filed?" resolved confidently to "profit
  for the year". Also latent: "eps" in "steps", "roa" in "broad". Same bug class
  as "in(cr)ease of 500" read as 500 crore in financial_value.py; aliases are now
  word-boundary anchored.

465 tests passing, ruff clean.

### Added - 2026-08-24 - Query preparation, and a correction to what RX-002 concluded

`backend/retrieval/query.py`. A financial question is mostly boilerplate and the
boilerplate matches everything: for "What were total assets as at March 31,
2024?", `total` appears in 16% of the corpus's chunks, `assets` in 25%, and the
date scaffolding in 27-57%. Stripping it moved evidence-retrieval accuracy from
0.636 to **0.818** and MRR from 0.341 to **0.551** - the largest single retrieval
gain in the project, with no re-indexing, no new model and no new component.

- Stopword list is deliberately NOT a general English one: `other`, `total`,
  `net`, `current`, `basic`, `diluted` are kept, because they are what separates
  "other equity" from "equity" and basic EPS from diluted.
- The date is dropped from the search text but **not discarded** - which fiscal
  year is being asked about belongs in the `fiscal_year` metadata filter.
  `extract_fiscal_year` does the deterministic part; Module 7 will do the rest.
- A `kind="table"` filter was tested in the same pass and **rejected**: alone it
  gained nothing, and combined with stripping it lost ground.

**Research-relevant - this supersedes the conclusion of RX-002.** That entry
found fusion beaten by its own keyword leg and a weight sweep monotonically bad,
and deferred the fusion default as untunable on 22 questions. On clean queries
every non-zero weight beats BM25 alone and the sweep is **flat** across all of
them, so plain RRF is correct and no hyperparameter needs tuning or reporting.
The earlier finding was an artifact of noisy queries. The general lesson is
recorded because it will recur: **a measured deficit is not evidence about the
component you happened to be looking at** - two rounds of work were pointed at
fusion and reranking by a number whose real cause was the query text.

Reranking is now DEFERRED rather than justified: six of the eight misses it was
meant to rescue sat at ranks 12-74 and are retrieved without it.

D2a: `intfloat/e5-base-v2` replaces bge-base as the embedding model, chosen by
measurement on identical chunks with an identical BM25 leg (RX-003).

430 tests passing.

### Added — 2026-08-23 — Retrieval evaluation harness, and what it found

Module 6 left retrieval *running* but not *established*. This adds the
measurement that settles it, and the measurement immediately falsified three
things the code believed about itself.

- `evaluation/metrics/retrieval.py` — Recall@K, Precision@K, MRR and evidence
  retrieval accuracy over gold evidence **groups** (all-of across groups, any-of
  within one). 24 unit tests, no Qdrant required.
- `datasets/retrieval_eval/infosys_fy24_v1.json` — 22 validation questions with
  33 evidence spans over the Infosys FY2023-24 consolidated statements. Every
  anchor was copied from the source page and is re-verified on demand.
- `scripts/evaluate_retrieval.py` — `--validate-gold` checks every anchor against
  the PDF; `--arms` scores the semantic and keyword legs separately; `--diagnose`
  separates "the evidence was ranked too low" from "the evidence never reached
  the index", which need opposite fixes and are indistinguishable from a score.
- `scripts/index_corpus.py` — one reproducible command from
  `documents/registry.json` to a populated Qdrant collection, with a chunk cache
  so changing the embedding model does not re-run Camelot.
- `HybridRetriever.retrieve_arms()` — all three arms from one pass over one
  candidate set, so a difference between them is fusion and nothing else.

**Research-relevant — D18 amends `EVALUATION.md` §4.** Gold spans are located by
`(page, literal anchors)` rather than by character offsets. Table chunks are
re-rendered pipe tables, not substrings of the page text, so an offset rule could
only be computed against the chunker's own output — making the gold labels a
function of the system under test.

### Fixed — 2026-08-23 — Three defects the harness exposed, all silent

None of these raised an error; all three were found by measuring, and two are
content-integrity bugs rather than ranking ones.

- **Multi-row table headers were being thrown away.** `header` took row 0 only,
  which on the profit and loss statement was the unit caption — so
  `Year ended March 31, / 2024 2023` was discarded, and part 2 of the split table
  read `Profit for the year | 26,248  24,108` with **nothing in the chunk saying
  which column is which year**. Headers are now merged across the leading
  non-data rows and repeated into every part.
- **Section detection was labelling statements with table row labels.** The
  profit and loss statement was tagged `"Expenses"`, the trade receivables note
  `"3 years"`. Two causes: a single Title-Case word was enough to qualify, and
  `_NOTE_HEADING` carried `re.I`, which made `[A-Z]` match `"3 years"`. Since the
  section is written into every chunk title *and* every citation, a wrong one
  misdescribes the evidence a reader is asked to check.
- **Table figures were still being indexed twice.** `chunk_document` has always
  claimed page text duplicating a table is excluded; it was not. Page 13 alone
  slipped eight fragments past `is_prose` — `"Current tax 2.17 8,390 9,287 ..."`
  clears the alpha ratio and the eight-word minimum exactly. `is_table_spillover`
  now drops them: 65 paragraphs on the Infosys filing, every one inspected and
  confirmed to be flattened table content rather than narrative.

### Changed — 2026-08-23 — Runtime moved to free-tier LLM providers (D8a)

No Anthropic API credits are available, so the runtime now uses **Groq + Google
Gemini** free tiers (no credit card). 33 new tests; 305 passing overall.

- `backend/services/llm/` — provider-agnostic layer (spec §5): typed errors,
  usage accounting, sliding-window rate limiter with **persisted daily quotas**,
  retry with backoff, and a channel→model registry.
- One OpenAI-compatible adapter covers Groq, Gemini, OpenRouter, Cerebras and a
  local Ollama server, so provider choice is configuration rather than code.
- `scripts/verify_llm_providers.py` — lists what each provider *currently*
  serves and health-checks every binding with **real API calls**.

**Research-relevant — this strengthens the design rather than weakening it:**

- **D1 independence is now cross-vendor.** Channel A on Gemini, Channel B on a
  Groq-served open model means different training corpora, architectures and
  failure modes — not two checkpoints from one lab. The cross-provider ablation
  D8 deferred as a "later" nice-to-have is now the *default*. H4 correspondingly
  tests a stronger claim.
- **D7 superseded by D7a: temperature is back.** These models accept
  `temperature`, so runs are pinned at 0 and the reproducibility record regains a
  decoding parameter that frontier Claude models reject outright. Still not
  bit-level determinism, so variance remains measured, and the **resolved** model
  id is recorded per call to catch silent checkpoint substitution.
- **D14: rate limits replace money as the binding constraint.** `cost_usd` is
  0.00 on a free tier, which would make spec §33's cost analysis vacuous.
  `equivalent_cost_usd` at published paid rates is recorded alongside, and the
  free-tier zero is never presented as evidence the method is cheap. Throughput
  is bounded by ~15,900 requests/day against an estimated 6,000–8,000 per full
  run, so the evaluation must be resumable across days.
- **Stated limitation:** free models are weaker at program synthesis than
  frontier models. Absolute accuracy will be lower; the claim concerns *relative*
  detection across arms on identical inputs, and absolute figures must never be
  set beside published frontier results as though the setups matched.

- **D15:** httpx directly rather than the `openai` SDK — the hard part
  (free-tier quota management) is custom either way.

Updated throughout: `RESEARCH.md`, `HYPOTHESES.md`, `EVALUATION.md`,
`ENVIRONMENT.md`, `CLAUDE.md`, `PROJECT_STATUS.md`, `TODO.md`, `README.md`,
`.env.example`, `scripts/verify_environment.py`.

### Added — 2026-08-23 — Milestone 2 (part 1): verification core

220 tests passing. Modules 5, 9 (execution half), 10, 11, 12, 29.

- `backend/core/financial_value.py` — scale/sign/currency/unit parsing on
  `Decimal`, with every transformation traced and every guess flagged.
- `backend/verification/deterministic.py` — 10 operations combining operands in
  canonical form, so crore and million combine correctly rather than by written
  magnitude. Failures returned as structured results, not raised.
- `backend/verification/consistency.py` — verdict **plus a continuous score**.
- `backend/services/code_validator.py` — static AST allowlist; 54 tests, each a
  real escape technique.
- `backend/services/sandbox.py` — disposable container per execution.

**Three real bugs found and fixed, two by probing rather than by tests passing:**

1. Substring token matching read `"in(cr)ease of 500"` as 500 **crore** — a
   10,000,000× error produced from ordinary English prose, precisely the failure
   class the normaliser exists to prevent. Now word-boundary anchored.
2. The same defect found a rupee sign in `"fi(rs)t half"`.
3. The UNKNOWN-unit tolerance allowed `25% + 100 crore → 1000000000.25` —
   arithmetically clean, semantically meaningless. Percent is dimensionless and
   never combines additively with a magnitude.

**Research-relevant:**

- The deterministic verifier can **overrule unanimous channels**. Both channels
  share one evidence set, so a retrieval error makes both compute faithfully from
  the wrong number and agree; this is the only path that catches the
  "agree and both wrong" cell (`EVALUATION.md` §5.4).
- Scale disagreement is a **named** disagreement type. A clean power-of-ten gap
  in financial QA is essentially never coincidence — it is crore/lakh/million
  confusion — so naming it converts an opaque mismatch into a diagnosable class.
- Consistency scoring weights are grouped and labelled as **calibration
  parameters awaiting the validation sweep**, not validated constants. Spec §23
  requires the scoring methodology be experimentally justified.

**Security:** no host-execution fallback. Docker down ⇒ `BLOCKED` and Channel B
recorded missing, which the consistency engine already handles as UNCERTAIN. A
"temporary" fallback is how untrusted generated code reaches the host.

### Added — 2026-08-23 — Milestone 1: Research Foundation

- `RESEARCH.md` — problem, gap, contribution, scope, threats-to-validity table.
- `LITERATURE_REVIEW.md` — 12 sources, each verified against a primary source
  (arXiv / ACL Anthology / PMLR). Identifies the gap: cross-modality disagreement
  has not been evaluated as a *detector* of numerical hallucination in financial
  QA.
- `RESEARCH_QUESTIONS.md` — RQ1–RQ5, each with an explicit negative-result
  condition.
- `HYPOTHESES.md` — H1–H5 with directional predictions, measurements,
  falsification conditions, and pre-registered thresholds.
- `EVALUATION.md` — metric definitions fixed before experiments: correctness
  predicate (TAU = 0.005 relative, with published sensitivity sweep), QA,
  retrieval, detection (AUROC primary), efficiency, 6 comparison arms, 8 ablation
  arms, statistical protocol.
- `CLAUDE.md`, `PROJECT_STATUS.md`, `TODO.md`, `README.md`.

**Research-relevant:**

- Added baseline **B5 (self-consistency, single model/modality)** beyond the
  spec's four. Without it H1 has no comparator and the central claim is not
  falsifiable. Recorded as D11.
- Added ablation arm **H (same model on both channels)** to test decision D1
  directly — turning the main threat to validity into a measurable result (H4).
- Established that detection metrics must be **stratified by error provenance**
  (retrieval-caused vs reasoning-caused). Both channels share retrieved evidence,
  so agreement is expected to be near-blind to retrieval error; a pooled AUROC
  would overstate the contribution.
- Recorded that **Module 15 must emit a continuous confidence score**, not just
  HIGH/MEDIUM/LOW — AUROC is the primary metric and is not meaningful over three
  ordinal buckets.

### Added — 2026-08-23 — Milestone 0: Environment Readiness Gate

- Python 3.12.10 venv (host had only 3.14.7, shipped without pip); 117 packages
  pinned in `requirements.lock.txt`; torch is the explicit CPU build.
- `scripts/verify_environment.py` — verifies each component by import/invocation
  and exits non-zero on required failures, doubling as the M0 acceptance test.
- `docker-compose.yml` — PostgreSQL 16 + Qdrant, both confirmed responding.
- `ENVIRONMENT.md`, `.gitignore`, `.env.example`, `pyproject.toml`, repo skeleton
  per spec §41.
- Tesseract OCR 5.4.0.

**Research-relevant:**

- Recorded that `temperature` / `top_p` / `budget_tokens` are **removed** on
  Claude Opus 5 and Sonnet 5. There is no temperature=0 determinism knob on
  current models, so reproducibility is pinned via model id + effort + thinking
  mode + prompt version, and run-to-run variance is *measured* rather than
  assumed to be zero (D7).

### Known blockers

- `ANTHROPIC_API_KEY` unset — blocks Modules 7, 8, 9, 13 and all experiments.
- Human validator not yet named for FinVerify-IND gold labels (spec §16).
- Ghostscript absent; Camelot lattice mode unavailable. Deferred to Module 3
  extraction-quality evidence (D10).

### Fixed - 2026-08-27 - The collection nobody was querying

A retrieval misconfiguration that survived a green suite, a healthy
`docker compose ps`, a passing `/health` and 978 tests. **Research-relevant:**
every question about a company other than Infosys would have been answered from
Infosys's pages, by an embedding model that did not build the index being
queried.

**Root cause.** D2a (2026-08-23) switched the embedding model from
`BAAI/bge-base-en-v1.5` to `intfloat/e5-base-v2` on measured evidence and
re-indexed into `finverify_e5`. It updated `EMBEDDING_MODEL`. It did not update
`QDRANT_COLLECTION`, which kept naming `finverify_chunks` — the superseded BGE
index, holding 906 Infosys chunks against the corpus's 22,930 across five
filings. A decision applied to one of two variables that must move together.

Both models emit 768 dimensions, so Qdrant accepted the cross-model queries and
returned ranked, scored, wrong results. **RX-011** settled which model built
which collection by probe rather than inference.

**Added.** `backend/rag/manifest.py` — `documents/index_manifest.json` records
per collection the embedding model that built it and the companies and documents
**verified present by querying the index**, never copied from the registry.
`preflight()` raises before a campaign, slice, retrieval evaluation or live API
question. Recorded as **D33**. 14 tests.

**Also fixed, same root cause.** Five scripts disagreed on the fallback for the
same two settings; `index_corpus.py` and `evaluate_retrieval.py` never loaded
`.env`; `run_campaign.py` and `run_slice.py` built argparse defaults *before*
`load_dotenv`, so `.env` reached them too late — the one accident by which the
campaign escaped the wrong collection.

**Found while fixing it:**

- The live-QA endpoint omitted `url=` and could never have reached Qdrant from
  inside a container — masked because live QA is off by default (D30).
- `sys.stdout` on a Windows console cannot encode `₹`, so any script crashed on
  the first line of real evidence. `scripts/_console.py`.
- `run_slice.py` filtered retrieval to the Infosys document unconditionally,
  written when the corpus was one filing.
- `/health` reported `vector_index: true` throughout. It now runs the preflight,
  and was **proved able to report `false`** by restoring the broken config
  deliberately.

**Corrected claims.** RX-005's 0.909 evidence accuracy / 0.932 R@10 were
measured on the 906-chunk Infosys slice and **do not describe the current
22,930-chunk index**. Flagged in `PROJECT_STATUS.md` and `TODO.md`; must be
re-measured before being quoted beside campaign results. An earlier report of
"5 chunks indexed short" was withdrawn — it counted cache-header lines as
chunks; 22,930 on disk, 22,930 indexed, zero UUID5 collisions.

### Added - 2026-08-27 - pages, sections and tables actually hold rows

Spec §26 names them; they had a schema and no ingest path, so the database held
zero of all three. `ingest_structure` fills them from the chunk cache: 1,664
pages, 537 sections, 2,780 tables. `tables.parsing_accuracy` finally has
somewhere to live — HDFC Bank 49 tables below 80% accuracy against Reliance's 0,
which is the shape D10a would predict and the case for revisiting it *with a
measurement*. `pages.text` stays NULL rather than reassembled from overlapping
chunks. 7 tests.

Database at this point: 5 documents, 680 facts across **all five** companies
(Tata Motors was 0), 160 questions, 225 evidence rows. D34's lexicon extension
later took facts to 1,245 and questions to 268. `users` is the only table empty by
design, and says so.

**Tests: 978 → 999 passing, 0 skipped.**

### Fixed - 2026-08-27 - The campaign searched five filings without knowing which

**Research-relevant, and the most consequential defect found on this project.**

Re-measuring retrieval after D33 (as RX-011 required) exposed that *every*
retrieval measurement had been filtered to a single document, while the campaign
filters by nothing. `evaluate_retrieval.py` passed `document_id` on every call;
`run_campaign.py` built one `PipelineDeps` for the whole campaign with
`company=None` — necessarily, since one object serves every question — and
`parse_question` does not extract the company from the question text.

So every campaign question would have searched all 22,930 chunks across five
filings, unscoped. Measured on the 22 gold questions (RX-012):

| Condition | R@10 | MRR | Evidence acc. | Misses |
|---|---:|---:|---:|---:|
| company known | 0.932 | 0.616 | **0.909** | 2/22 |
| company unknown | 0.750 | 0.357 | **0.727** | 6/22 |

MRR falls hardest — the right evidence, when found, ranks far lower, and at
`top_k=8` the channels would frequently never see it. The whole result would
have been computed on evidence the system largely failed to retrieve, and the
failure would have read as a limitation of the method.

**Fixed.** The company travels with the question from the dataset, which knows
it authoritatively, rather than being parsed back out of the question text.
`run_question` takes `company` and `document_id`; `PipelineState` carries them;
the per-question value overrides the campaign-wide default. Three gates in
`test_orchestrator.py`, including one asserting that an unknown company yields
**no filter rather than a guessed one**.

**Also established.** Module 7's planned retrieval is worth **+0.409 R@10**
corpus-wide (0.523 → 0.932), not the +0.068 the single-document slice suggested
— a sixfold understatement that would have gone into the write-up. Corpus-wide
and unscoped, hybrid (0.523) barely beats keyword alone (0.500). Knowing which
company is worth more than any retrieval tuning measured here.

**Added.** `--corpus-wide` and `--no-company` to `evaluate_retrieval.py`, and
both `scope` and `company_known` recorded in every retrieval report — the two
runs were previously indistinguishable in the artifact.

**New known limitation.** Gold evidence spans exist for **Infosys only**. RX-012
describes 22 Infosys questions among five filings; retrieval quality on the
other 127 candidate questions is unmeasured. Recorded in `TODO.md` as a blocker
on any per-sector retrieval claim.

**Tests: 999 → 1002 passing, 0 skipped.**

### Added - 2026-08-27 - Sector vocabulary, confirmed against the filings

**Research-relevant: changes the dataset.** The metric lexicon was built on the
Infosys vertical slice and never left it — it contained `cost of technical
sub-contractors` and **no banking vocabulary at all**, while the corpus includes
a 585-page bank filing. RX-012 made the cost concrete: retrieval is the binding
constraint, planned retrieval carries it, and planned retrieval depends on the
lexicon.

33 entries → **61**; 5 derived metrics → 6. Every term was counted as a table
**row label** across all five filings before being added.

**Nine textbook-obvious terms were rejected because the corpus does not contain
them** — seven of them banking vocabulary, the exact gap being closed. HDFC Bank
writes `deposits` (17 row labels); `total deposits` appears **zero** times. No
`gross NPA`, `net NPA`, `CASA`, `demand deposits`, `savings bank deposits`,
`term deposits` or `segment revenue` row label exists anywhere in the corpus.
Recorded as **D34**: the fix for "I assumed" is not "assume harder".

Statement hints now follow the filer's convention — Indian banks file a *Profit
and Loss Account* and *Schedules to the Balance Sheet*, never a *Statement of
Profit and Loss*.

**Candidates 160 → 268.** Every company now clears the case study's 15-question
reporting floor with margin (Tata Motors 15 → 19, Reliance 17 → 29), so
per-sector cells are reportable for the first time. **But HDFC Bank went 56 →
127 and is now 47% of the set** — any pooled detection metric over this dataset
is substantially a statement about one bank. Stated in `DATASET.md` and
`PROJECT_STATUS.md`; not capped, because discarding real gold-able questions to
balance a table trades labels for cosmetics and every analysis path stratifies.

Splits re-assigned and stratified: 81 train / 81 validation / 106 test.

### Added - 2026-08-27 - The validation worksheet is interleaved by company

Validation is partial by design — ~40 questions gives a first result — so the
order a validator works down the sheet decides which companies that result
covers. Grouped by company, the first 40 rows were **40 HDFC Bank questions**.
Interleaved round-robin smallest-first, 40 rows is 8 from each of the five
filings. Verified on the exported sheet at prefixes of 20, 40 and 80.

D27's principle applied to human work: make the prefix of an interrupted job
representative, because the job will be interrupted. Recorded as **D35**.

### Fixed - 2026-08-27 - Six backspace bytes in a regex

A shell heredoc interpreted `\b` and wrote 0x08 into `metrics_lexicon.py`,
silently disabling every alias of the debt-to-equity ratio. The entry was
present in the tuple, the module imported, `ruff` was clean and the tests
passed — the pattern simply could never match, and it renders as nothing in an
editor, in a diff and in review.

Added `tests/test_source_hygiene.py`: no control characters in any source file;
every derived metric reachable by a question naming it; every lexicon alias
resolving to its entry. Covers the class, not the instance — the third defect on
this project of the shape *present but unreachable*.

**Tests: 1002 → 1009 passing, 0 skipped.**

### Fixed - 2026-08-27 - A scale token read correctly by accident

**Found by running the pipeline live on HDFC Bank (RX-013), not by a test.**

Both channels independently returned 23,79,786 for total deposits and agreed at
risk 0.000. The figure is right — HDFC's own narrative on p.217 reads *"Total
Deposits rose by 26.4 per cent to ₹ 23,79,786 crore"*.

But the evidence came from the highlights page, which heads its column
**`Deposits (K Cr)`**. The parser matched `cr`, **silently discarded the `K`**,
and got the right answer without reasoning about the pair. A filer using "K Cr"
to mean thousand-crore would have been misread by three orders of magnitude with
no warning — the exact failure class this project exists to detect.

Probing exposed a second case: **`1 thousand crore` parses as 10³, not 10¹⁰**.
The scale loop takes the first token it matches and stops.

Measured frequency rather than guessed: compound scale expressions occur **29
times** in the corpus (`k cr` ×14, `k crore` ×11, `k\xa0crore` ×4), **all in
HDFC Bank's filing**. `thousand crore` occurs zero times.

**Added `ParseWarning.AMBIGUOUS_COMPOUND_SCALE`**, fired when a second
scale-shaped token survives beside the one used; the trace names both. **The
arithmetic is deliberately unchanged** — promoting bare `k` to a scale would turn
the only case that occurs into a 1000× error against documentary evidence, and
changing compound handling for a phrase no document uses is how D10 reached two
conclusions measurement overturned. The value is kept, the doubt recorded.

Five tests, including one that asserts the reading the *source document*
supports, so a later "obvious" fix fails there rather than in a published number.

**Tests: 1009 → 1014 passing, 0 skipped.**

### Changed - 2026-08-29 - Cross-vendor channel independence (D21 ACTIVE)

**Research-relevant.** Channel independence moved from **cross-lab** to
**cross-vendor**. `PROGRAM_CHANNEL_MODEL` is now
`nvidia/nvidia/nemotron-3-ultra-550b-a55b` on NVIDIA NIM; Channel A stays on
Groq. `channels_are_independent()` reports `cross-provider`, and
`verify_llm_providers.py` exits 0 on all four bindings with real calls.

D21 had been BLOCKED since 2026-08-25: Cerebras passed step 1 and failed step 2
with HTTP 402 on every completion (RX-008). NVIDIA passes all four steps
(RX-014). Verified end to end on RX-010's question — both channels independently
returned INR 88,461 crore, AGREE, band HIGH — giving that figure a fourth
independent derivation, the first on another vendor's hardware.

**Every document claiming "cross-lab, not cross-vendor" was corrected:**
`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `SRS.md`, `PROJECT_STATUS.md`,
`COMPLETION_GUIDE.md`. A superseded limitation left in the text is a stale claim,
and this project has had to correct those before.

**Two caveats recorded rather than smoothed over:**

- NVIDIA's **daily limit is UNOBSERVED** — it sends no rate-limit headers and no
  daily refusal has fired. The registry value is a pacing placeholder labelled
  as such, never a measurement.
- **Latency on this provider must not be reported.** Identical prompts returned
  in 0.42s and 12.5s; the free 550B endpoint sheds load with HTTP 503 under
  contention rather than 429 under quota. 503 was already in
  `_RETRYABLE_STATUS`, so no code changed. Request and token counts are
  unaffected.

Also recorded: Nemotron is a reasoning model returning `reasoning_content`
separately. At a small `max_tokens` the budget is consumed by reasoning and
`content` returns reasoning prose with `finish_reason: length` — which the
JSON-parsing channels would read as a malformed answer rather than a truncation.
At the channels' actual 2,048 budget it returns clean JSON.

### Removed - 2026-08-29 - Cerebras, as a provider

Owner decision: the project runs on Groq (Channel A) and NVIDIA NIM (Channel B),
so the dead third provider and its key are gone.

Removed entirely: `CEREBRAS_API_KEY` from `.env` and `.env.example`, the
`cerebras` `ProviderSpec` from `registry.py` (29 lines), its environment-gate
check, its persisted counter in `experiments/.rate_limit_state.json`, and every
mention in the current-state documents.

**Kept, deliberately, in three append-only records** — `EXPERIMENTS.md`
(RX-008), `DECISIONS.md` (D20/D21 history) and this file. RX-008 is a *recorded
failed experiment*: `CLAUDE.md` says failed experiments stay, and spec §46.7
requires them to be recorded. Deleting it would also delete the reason D21's
checklist demands a real completion rather than a reachable endpoint — the
distinction that made NVIDIA's acceptance meaningful. Where those records are
now superseded they carry forward pointers rather than edits.

Current-state prose no longer names the vendor; it refers to "an earlier
candidate" and cites RX-008, so the lesson survives without the dead provider
appearing to be a live option.

Also corrected while in these files: `AGENTS.md` still described the lab axis as
"Qwen vs GPT-OSS" (Channel B is Nemotron now) and pointed at superseded D20;
`README.md`'s quick start still asked for a Gemini key rather than the NVIDIA
key Channel B actually needs, and said "six defects" where the same file says
thirteen.

Environment gate 32/34, all four bindings VERIFIED by real calls, 1014 passed,
0 skipped, lint clean.

### Fixed - 2026-08-29 - The fiscal-year filter returned zero chunks for 72% of the dataset

**Research-relevant, and the worst defect found on this project.**

A chunk's `fiscal_year` is the year of the DOCUMENT. `retrieve_for_spec` matched
it against the year the QUESTION asked about. Every chunk in this corpus carries
`2023-24`, so a question about the year ended March 2023 filtered to **zero
chunks** and retrieval returned an empty list - not a poor ranking, nothing.

**193 of FinVerify-IND's 268 questions (72%) ask about a prior year**, because an
annual report states its own year beside the previous one. All of them would have
run both channels over an empty evidence set, and the campaign would have
recorded that as the method failing.

Nothing errored: the filter was valid, Qdrant answered normally, and an empty
result is indistinguishable from "this report does not contain that". The
docstring beside the filter already warned that guessing a year "would silently
exclude the right evidence" - and the comment did not stop it happening.

Fixed in D36: a question about year Y admits documents for Y and Y+1, via
`MatchAny`. Ten tests pin it, including one asserting `MatchAny` rather than a
conjunction - no chunk carries two fiscal years, so an AND would be worse than
the original bug. One existing test asserted the old exact match and was
deliberately widened, with the reason recorded in the test body.

### Added - 2026-08-29 - Retrieval gold for the whole corpus, and what it exposed

`scripts/build_retrieval_gold.py` builds retrieval gold for the four filings that
never had any. Spans come from FinVerify-IND's recorded page and row label and
are then **verified against the PDF's own text layer**; the whole document is
scanned so every page carrying the same label-and-figure pair is listed as an
alternative. 6 of 97 candidates could not be confirmed and were **dropped, not
guessed at**. `--validate-gold` independently re-verified **184/184 spans**.

This produces gold EVIDENCE LOCATIONS, never gold ANSWERS. A gold answer asserts
what a metric means and needs a human (spec §16); a gold span asserts that two
strings appear on a page, which is decidable by reading the file.

**The result (RX-015): 0.909 is a development-set number.** Like-for-like on
recent-year questions, evidence accuracy@10 is Infosys 0.909, Sun Pharma 0.611,
HDFC Bank 0.538, Reliance 0.438, Tata Motors 0.375. Every retrieval decision on
this project - chunking, lexicon, statement hints, RRF weights, header window -
was measured on Infosys and only Infosys.

`ARCHITECTURE.md`, `AGENTS.md` and `PROJECT_STATUS.md` no longer quote 0.909 as a
property of the system. H2's retrieval-caused stratum is now expected to be
closer to half the questions than the ~9% Infosys implied.
