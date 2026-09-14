# docs/

**The project's documentation lives at the repository root, not here.**

Spec §40 names the required documents by filename, and every one of them exists
at the top level where a reader — or a reviewer opening the repository for the
first time — will actually find it:

| Document | Covers |
|---|---|
| [`README.md`](../README.md) | what this is, in one page |
| [`SRS.md`](../SRS.md) | requirements, including the ones constraining what may be *claimed* |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | the pipeline, and where the design could still be wrong |
| [`AGENTS.md`](../AGENTS.md) | the four agents, and what each is forbidden to see |
| [`RAG.md`](../RAG.md) | retrieval, measured |
| [`VERIFICATION.md`](../VERIFICATION.md) | the verification chain and what each part may claim |
| [`DATASET.md`](../DATASET.md) | FinVerify-IND, its splits, and its limitations |
| [`DATABASE.md`](../DATABASE.md) | schema, and why it is a projection |
| [`API.md`](../API.md) | endpoints, and the three things a schema cannot tell you |
| [`DEPLOYMENT.md`](../DEPLOYMENT.md) | the stack, and what is *not* deployed |
| [`TESTING.md`](../TESTING.md) | the research-validity gates, and what is untested |
| [`RESEARCH.md`](../RESEARCH.md) · [`RESEARCH_QUESTIONS.md`](../RESEARCH_QUESTIONS.md) · [`HYPOTHESES.md`](../HYPOTHESES.md) | the question and its falsifiable predictions |
| [`LITERATURE_REVIEW.md`](../LITERATURE_REVIEW.md) | prior work, every citation verified against its source |
| [`EVALUATION.md`](../EVALUATION.md) | every metric's computation, fixed before any experiment ran |
| [`EXPERIMENTS.md`](../EXPERIMENTS.md) | what was measured, including the failures |
| [`DECISIONS.md`](../DECISIONS.md) | D1–D52, with the reasoning, not just the choice |
| [`CASE_STUDY.md`](../CASE_STUDY.md) | the corpus, and the results section once there is one |
| [`PROJECT_STATUS.md`](../PROJECT_STATUS.md) · [`TODO.md`](../TODO.md) · [`CHANGELOG.md`](../CHANGELOG.md) | state, next actions, history |
| [`ENVIRONMENT.md`](../ENVIRONMENT.md) | what was verified on this machine, and what failed |
| [`ENGINEERING_RULES.md`](../ENGINEERING_RULES.md) | the agent operating manual |

## Why this directory is empty

Spec §41's layout lists a `docs/` directory, so it exists. But splitting the
documentation between two locations would mean a reader has to know which half
holds what, and the §40 filenames are the contract — moving them here to fill a
folder would break it.

The deeper reason is that most of this project's explanation belongs in the code
rather than in prose beside it. A docstring explaining why the coverage penalty
counts *achievable* corroboration sits next to the line that computes it, and
stays true when that line changes. The same paragraph in a `docs/` file is a
paragraph that goes stale silently — and this project has already had to correct
three stale claims in its own README.

If a future module needs a long-form document that is not one of the §40 set,
it belongs here.

## The case-study submission is deliberately not in this repository

`FinVerify_AI_Final_Case_Study.docx` - the M.Tech case study written from this project,
structured on the course template - is built outside the repository and kept locally.
Its title page carries the author's name and registration number, which do not belong in
a public repository, so `docs/*.docx` and `docs/*.pdf` are gitignored.

What the document contains is all here in reproducible form: every figure in it was
re-derived from the committed run artifacts, reports, dataset and live API on
2026-09-13, and every screenshot is of the running application. Where the case study and
a root document disagreed, the root documents were corrected to match the artifacts in
the same pass (see `CHANGELOG.md`, 2026-09-13).
