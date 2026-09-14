# FinVerify-AI — Complete Project & Research Execution Specification

**Document purpose:** This Markdown file is the authoritative execution blueprint for humans and AI coding/research agents working on the FinVerify-AI project.

**Project:** FinVerify-AI  
**Working title:** A Dual-Channel Agentic Framework for Numerical Hallucination Detection in Financial Document Question Answering

---

## 1. How This Document Must Be Used

This document defines:

- the project scope;
- all required modules;
- development order;
- technology requirements;
- research requirements;
- documentation requirements;
- testing requirements;
- evaluation requirements;
- case-study requirements;
- rules for AI coding/research agents.

### Mandatory rule

**No AI agent may skip, merge, remove, or substantially redesign a module without documenting the reason and receiving user approval when the change affects the research contribution, evaluation methodology, architecture, security, or project scope.**

The agent must work module-by-module and maintain traceability between:

`Requirement → Design → Implementation → Test → Experiment → Result → Documentation`

---

# 2. Project Vision

FinVerify-AI is a financial-document question-answering and verification system designed to reduce and detect numerical hallucinations.

The central architecture uses:

1. Hybrid financial-document retrieval;
2. Natural-language reasoning channel;
3. Programmatic reasoning channel;
4. Deterministic numerical verification where possible;
5. Consistency/disagreement detection;
6. Verification agent;
7. Hallucination/error classification;
8. Confidence and risk scoring;
9. Evidence-based explainability;
10. Research-grade evaluation and ablation studies.

The project must be implemented as both:

- a usable software system; and
- a reproducible research/evaluation framework.

---

# 3. STRICT PRE-START REQUIREMENT

## 3.1 Environment Readiness Gate

**Before starting the actual project implementation or case study, the complete required software stack must be installed, configured, tested, and documented.**

The AI agent must first inspect the current development environment.

The agent must NOT assume that a required tool, SDK, runtime, database, API, package, CLI, Docker service, or external account is available.

If anything is missing, the agent must:

1. identify the missing dependency;
2. explain why it is required;
3. ask the user for permission/access/credentials where necessary;
4. provide the exact installation/configuration requirement;
5. verify successful installation/configuration;
6. record the result in project documentation.

### Possible access/permission requirements

The AI agent may need user authorization for:

- Claude/Anthropic API access if API usage is required;
- other LLM API providers if selected for comparison;
- Git/GitHub;
- Hugging Face;
- financial-data/document sources;
- model downloads;
- Docker;
- PostgreSQL;
- Qdrant;
- cloud deployment;
- storage;
- external research databases;
- any paid service.

**Never fabricate credentials, API keys, access, installed software, dataset availability, or successful configuration.**

---

# 4. Recommended Development Platform / IDE

## Primary recommendation

### VS Code + Claude Code

Recommended setup:

- Visual Studio Code as the primary IDE;
- Claude Code as the primary AI coding agent;
- Git/GitHub for version control;
- WSL2/Ubuntu on Windows if the project is developed on Windows;
- Docker Desktop;
- Python;
- Node.js;
- PostgreSQL;
- Qdrant.

### Why this setup

The project is a multi-component system involving:

- Python backend;
- React/TypeScript frontend;
- databases;
- vector retrieval;
- LLM agents;
- document processing;
- sandboxed execution;
- automated testing;
- experiment tracking;
- Docker;
- research scripts.

VS Code provides a practical unified environment for these components, while Claude Code can inspect the repository, modify multiple files, run commands/tests, and maintain the project context.

---

# 5. Recommended LLM Strategy for a Claude Pro User

## Primary development model

Use the strongest current Claude model available through the user's Claude Pro/Claude Code access for:

- architecture assistance;
- implementation;
- debugging;
- refactoring;
- test generation;
- documentation;
- code review;
- research workflow assistance.

### Important distinction

**Claude Pro/Claude Code access and Anthropic API access are not automatically the same thing.**

The project must not assume that a Claude Pro subscription provides API credits or API access.

If the application itself needs Claude as a runtime LLM, the agent must verify the required API access separately.

---

## Recommended runtime LLM architecture

Do NOT hard-code the entire system to one provider.

Create an LLM abstraction layer:

```text
Application
    |
    v
LLM Provider Interface
    |
    +-- Anthropic
    +-- OpenAI-compatible provider
    +-- Local model
    +-- Future provider
```

This allows controlled research comparisons and prevents vendor lock-in.

### Research recommendation

For final experiments, compare at least:

- LLM-only baseline;
- RAG baseline;
- RAG + programmatic reasoning;
- proposed dual-channel verification system.

The exact model/version used in every experiment must be recorded.

---

# 6. Recommended Technology Stack

## Backend

- Python 3.12 or project-compatible current stable version
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic

## Frontend

- React
- TypeScript
- Vite
- modern component/UI library as appropriate

## Database

- PostgreSQL

## Vector Database

- Qdrant

## Agent Orchestration

- LangGraph or an equivalent explicitly justified orchestration framework

## Document Processing

- PyMuPDF
- pdfplumber
- Camelot where appropriate
- OCR for scanned documents
- Docling or equivalent where beneficial

## Embeddings

Use a strong current embedding model selected through evaluation rather than assuming one model is universally best.

Candidate families may include:

- BGE
- E5

The final choice must be documented and evaluated.

## Numerical execution

- Python
- isolated sandbox/container
- strict resource/time limits
- restricted filesystem
- no unrestricted network access

## Experiment Tracking

- MLflow or an equivalent reproducible experiment-tracking system

## Testing

- pytest
- frontend test framework
- integration tests
- end-to-end tests where appropriate

## DevOps

- Git
- GitHub
- Docker
- Docker Compose
- CI/CD where appropriate

---

# 7. Global AI-Agent Execution Rules

## Rule 1 — Plan before coding

Before implementing a module, the agent must:

1. inspect existing project state;
2. read relevant documentation;
3. identify dependencies;
4. define the implementation plan;
5. define acceptance criteria;
6. implement;
7. test;
8. document;
9. report completion.

---

## Rule 2 — Never pretend something works

The agent must distinguish:

- implemented;
- tested;
- partially tested;
- not tested;
- blocked;
- unavailable.

Do not report "complete" without evidence.

---

## Rule 3 — Use tokens effectively

**The AI agent must use every available context/token budget efficiently.**

Do not waste context on:

- repetitive explanations;
- unnecessary restatement;
- verbose commentary;
- repeatedly reading unchanged files;
- generating duplicate code;
- speculative implementation;
- unnecessary documentation duplication.

Prefer:

- inspecting only relevant files;
- concise planning;
- batched related operations;
- reusable utilities;
- structured logs;
- focused tests;
- incremental commits.

**Token efficiency must never mean skipping important reasoning, testing, security review, or documentation.**

---

## Rule 4 — Documentation is mandatory

The AI agent must maintain proper documentation throughout development.

Documentation must be updated when:

- architecture changes;
- dependencies change;
- APIs change;
- database schema changes;
- prompts change;
- agent behavior changes;
- evaluation methodology changes;
- experiments are added;
- bugs are discovered;
- decisions are made.

---

## Rule 5 — Preserve reproducibility

Every research experiment must record:

- dataset version;
- document version;
- model/provider;
- model version;
- prompt version;
- retrieval configuration;
- embedding model;
- top-k;
- temperature/decoding configuration where applicable;
- agent configuration;
- verification configuration;
- evaluation metrics;
- execution time;
- token usage;
- estimated cost where available;
- raw results.

---

# 8. Module 0 — Research Foundation

## Objective

Define the scientific foundation before production implementation.

### Components

- literature review;
- problem definition;
- research gap;
- research questions;
- hypotheses;
- objectives;
- scope;
- limitations;
- evaluation methodology.

### Deliverables

- `RESEARCH.md`
- `LITERATURE_REVIEW.md`
- `RESEARCH_QUESTIONS.md`
- `HYPOTHESES.md`

---

# 9. Module 1 — Dataset & Benchmark

## Objective

Create a reliable benchmark foundation.

### Initial datasets

- FinQA
- TAT-QA
- ConvFinQA

### Custom dataset

Create **FinVerify-IND** using publicly available Indian financial reports.

### Dataset record

Each validated question should support:

```text
question
document_id
company
year
page
section
evidence
table
gold_answer
gold_calculation
gold_program
operation_type
difficulty
unit
```

### Rules

- Keep train/validation/test separation.
- Protect the final test set.
- Do not silently modify gold answers.
- Record dataset provenance.
- Human verification is required for custom gold data.

---

# 10. Module 2 — Financial Document Acquisition

## Objective

Collect and manage financial documents.

### Functions

- PDF upload;
- document registration;
- company identification;
- report-year identification;
- document metadata;
- hashing;
- duplicate detection;
- version management;
- storage.

---

# 11. Module 3 — Document Intelligence

## Objective

Convert financial PDFs into structured information.

### Pipeline

```text
PDF
 ↓
Parser
 ↓
OCR if required
 ↓
Text extraction
 ↓
Table extraction
 ↓
Section detection
 ↓
Page mapping
 ↓
Financial information
```

### Components

- PDF parser;
- OCR;
- text extraction;
- table extraction;
- header/footer handling;
- section detection;
- page mapping;
- extraction quality checks.

Every extracted item must retain source provenance.

---

# 12. Module 4 — Financial Knowledge Structuring

## Objective

Convert extracted information into structured financial facts.

### Financial fact fields

```text
metric
value
unit
currency
year
company
document
page
section
table
row
column
```

---

# 13. Module 5 — Data Cleaning & Normalization

## Objective

Normalize financial values.

Examples:

```text
2.4 billion
→ 2400 million
```

```text
25%
→ 0.25
```

Handle:

- currency;
- units;
- percentages;
- negative values;
- decimals;
- missing values;
- financial notation.

All transformations must be traceable.

---

# 14. Module 6 — Hybrid RAG

## Objective

Retrieve correct financial evidence.

### Pipeline

```text
Question
 ↓
Query analysis
 ↓
Semantic retrieval
+
Keyword retrieval
+
Table retrieval
+
Metadata filtering
 ↓
Reranking
 ↓
Evidence set
```

### Components

- chunking;
- embeddings;
- vector database;
- keyword retrieval;
- semantic retrieval;
- table retrieval;
- reranking;
- evidence selection.

### Evaluation

Measure:

- Recall@K;
- Precision@K;
- MRR;
- evidence retrieval accuracy.

---

# 15. Module 7 — Question Understanding

## Objective

Classify and decompose financial questions.

### Question types

- direct lookup;
- arithmetic;
- percentage;
- ratio;
- growth;
- comparison;
- margin;
- trend;
- multi-step;
- cross-year;
- cross-table.

Extract:

- entities;
- metrics;
- years;
- operations;
- required evidence.

---

# 16. Module 8 — Natural-Language Reasoning Channel

## Objective

Generate an answer using evidence-grounded natural reasoning.

### Pipeline

```text
Question + Evidence
 ↓
Reasoning Agent
 ↓
Reasoning steps
 ↓
Answer A
```

Structured output must include:

```text
answer
numeric_value
unit
reasoning_steps
evidence
confidence
```

---

# 17. Module 9 — Programmatic Reasoning Channel

## Objective

Independently calculate the answer through executable reasoning.

### Pipeline

```text
Question + Evidence
 ↓
Program generation
 ↓
Code validation
 ↓
Sandbox
 ↓
Execution
 ↓
Answer B
```

Requirements:

- isolated execution;
- resource limits;
- timeout;
- restricted filesystem;
- no unrestricted network;
- execution logs;
- error handling.

---

# 18. Module 10 — Deterministic Numerical Verification

## Objective

Use deterministic calculations as an additional verification authority.

Whenever the operation can be represented deterministically:

```text
Extracted values
+
Expected operation
 ↓
Deterministic calculator
 ↓
Verified result
```

This module should not depend entirely on an LLM.

---

# 19. Module 11 — Consistency Engine

## Objective

Compare independent reasoning outputs.

Compare:

- numerical answer;
- units;
- signs;
- evidence;
- reasoning;
- deterministic result.

### Output

```text
AGREE
DISAGREE
UNCERTAIN
```

Generate a consistency score.

---

# 20. Module 12 — Disagreement Detection

## Objective

Detect whether independent channels disagree.

Store:

```text
question_id
answer_a
answer_b
difference
deterministic_result
evidence_difference
disagreement_type
```

Research objective:

> Determine whether disagreement is a useful signal for numerical hallucination.

---

# 21. Module 13 — Verification Agent

## Trigger

Primarily activate when:

```text
Channel A != Channel B
```

### Pipeline

```text
Conflict
 ↓
Re-retrieve evidence
 ↓
Check numbers
 ↓
Check units
 ↓
Check formula
 ↓
Recalculate
 ↓
Resolve
```

Record every verification action.

---

# 22. Module 14 — Hallucination Detection

## Error taxonomy

At minimum:

- wrong evidence;
- wrong number;
- wrong year;
- wrong unit;
- arithmetic error;
- wrong formula;
- wrong metric;
- unsupported claim;
- retrieval error;
- reasoning error.

The taxonomy may be expanded during research, but changes must be documented.

---

# 23. Module 15 — Confidence & Risk Scoring

Generate a final reliability assessment.

Example:

```text
Answer: 25%

Evidence: VERIFIED
Arithmetic: VERIFIED
Channel agreement: YES
Unit: VERIFIED

Confidence: HIGH
Risk: LOW
```

For conflicts:

```text
Channel agreement: NO
Evidence: PARTIAL
Risk: HIGH
```

The scoring methodology must be experimentally justified.

---

# 24. Module 16 — Evidence & Explainability

Final user response should expose:

- answer;
- calculation;
- evidence;
- source document;
- page;
- verification status;
- confidence/risk.

Do not expose hidden chain-of-thought. Provide concise, user-facing reasoning summaries and verifiable evidence instead.

---

# 25. Module 17 — Agent Orchestration

Implement the complete workflow:

```text
START
 ↓
Question Understanding
 ↓
Hybrid Retrieval
 ↓
Evidence Validation
 ↓
 ┌───────────────┴───────────────┐
 ↓                               ↓
Natural Channel             Program Channel
 ↓                               ↓
 └───────────────┬───────────────┘
                 ↓
       Deterministic Verification
                 ↓
          Consistency Engine
                 ↓
          ┌──────┴──────┐
          ↓             ↓
        AGREE       DISAGREE
          ↓             ↓
          │       Verification Agent
          │             ↓
          └──────┬──────┘
                 ↓
          Confidence/Risk
                 ↓
             Final Answer
```

---

# 26. Module 18 — Database

Use PostgreSQL.

Major entities:

- users;
- companies;
- documents;
- reports;
- pages;
- sections;
- tables;
- financial_facts;
- questions;
- evidence;
- reasoning_runs;
- program_runs;
- verification_runs;
- answers;
- hallucination_events;
- experiments;
- evaluation_results.

Database migrations must be version controlled.

---

# 27. Module 19 — Vector Database

Use Qdrant or an explicitly justified alternative.

Store:

- chunk ID;
- document ID;
- page;
- text;
- embedding;
- metadata.

Support filtering by:

- company;
- year;
- document;
- page;
- section;
- table;
- financial metric.

---

# 28. Module 20 — Backend API

Use FastAPI.

Core endpoints:

```text
POST /documents/upload
GET  /documents
GET  /documents/{id}
POST /questions/ask
GET  /answers/{id}
GET  /verification/{id}
GET  /evidence/{id}
GET  /experiments
GET  /evaluation/results
```

API contracts must be documented.

---

# 29. Module 21 — Frontend

Recommended:

- React;
- TypeScript;
- Vite.

Required screens:

1. Dashboard
2. Document upload
3. Document viewer
4. Financial QA
5. Verification view
6. Research/evaluation dashboard

The UI is secondary to the research engine.

---

# 30. Module 22 — Research Evaluation

Compare:

```text
Baseline 1 — LLM only
Baseline 2 — RAG
Baseline 3 — RAG + Programmatic reasoning
Baseline 4 — Agentic RAG
Proposed — FinVerify-AI
```

### Metrics

QA:

- exact match;
- numerical accuracy;
- execution accuracy.

Retrieval:

- Recall@K;
- Precision@K;
- MRR.

Hallucination detection:

- precision;
- recall;
- F1;
- AUROC;
- false-positive rate;
- false-negative rate.

Efficiency:

- latency;
- token usage;
- API cost;
- number of agent calls.

---

# 31. Module 23 — Ablation Study

Run controlled experiments by removing components.

Examples:

- full system;
- without natural reasoning;
- without program reasoning;
- without consistency engine;
- without verification agent;
- without hybrid retrieval;
- without deterministic verifier.

The goal is to identify which components cause measurable improvement.

---

# 32. Module 24 — Error Analysis

Automatically aggregate failure categories.

Analyze:

- retrieval failures;
- arithmetic failures;
- unit failures;
- evidence failures;
- reasoning failures;
- year-selection failures;
- metric-selection failures.

Generate tables and plots.

---

# 33. Module 25 — Cost & Performance

Track:

- model calls;
- tokens;
- latency;
- execution time;
- cost;
- retrieval time;
- verification frequency.

Research question:

> How much additional reliability does dual-channel verification provide relative to its computational cost?

---

# 34. Module 26 — FinVerify-IND

Create a custom Indian financial QA benchmark.

### Initial target

Approximately **500 high-quality, human-validated questions**.

Question categories:

- revenue;
- profit;
- growth;
- margin;
- ratio;
- percentage;
- comparison;
- trend;
- CAGR;
- multi-step;
- cross-table;
- cross-year.

Do not optimize for dataset size at the expense of gold-label quality.

---

# 35. Module 27 — Real-World Case Study

Select a representative set of Indian companies and public financial reports.

### Pipeline

```text
Company
 ↓
Financial Reports
 ↓
Questions
 ↓
FinVerify-AI
 ↓
Answers
 ↓
Verification
 ↓
Error Analysis
```

Analyze:

- accuracy;
- disagreement rate;
- hallucination detection;
- error types;
- difficult question categories;
- cost;
- latency;
- verification effectiveness.

---

# 36. Module 28 — Experiment Management

Use MLflow or an equivalent system.

Record:

```text
experiment_id
dataset
dataset_version
model
model_version
prompt_version
embedding_model
retrieval_configuration
top_k
agent_configuration
verification_configuration
metrics
latency
token_usage
cost
raw_results
```

Never overwrite raw experiment results.

---

# 37. Module 29 — Security

Especially important because generated code is executed.

Implement:

- sandbox;
- CPU limits;
- memory limits;
- timeout;
- restricted filesystem;
- no unrestricted network;
- allowed-library policy;
- secret management;
- input validation;
- API authentication where required.

Never commit secrets.

---

# 38. Module 30 — Testing & QA

## Unit tests

Test:

- parser;
- extractor;
- normalizer;
- retriever;
- calculator;
- consistency engine;
- database;
- API.

## Integration tests

Test:

```text
PDF
 ↓
Extraction
 ↓
RAG
 ↓
Channel A
 ↓
Channel B
 ↓
Verification
 ↓
Final Answer
```

## Failure tests

Include:

- malformed PDFs;
- scanned PDFs;
- missing tables;
- ambiguous questions;
- wrong units;
- negative values;
- zero denominator;
- conflicting evidence;
- missing evidence;
- model timeout;
- code execution failure.

---

# 39. Module 31 — Deployment

Recommended architecture:

```text
Frontend
   |
FastAPI
   |
   +-- PostgreSQL
   +-- Qdrant
   +-- Agent Engine
           |
           +-- LLM
           +-- Sandbox
           +-- Verifier
```

Use Docker/Docker Compose for reproducibility.

---

# 40. Module 32 — Documentation

Maintain at minimum:

```text
README.md
SRS.md
ARCHITECTURE.md
RESEARCH.md
LITERATURE_REVIEW.md
DATASET.md
RAG.md
AGENTS.md
VERIFICATION.md
EVALUATION.md
EXPERIMENTS.md
API.md
DATABASE.md
DEPLOYMENT.md
TESTING.md
CASE_STUDY.md
TODO.md
DECISIONS.md
CHANGELOG.md
```

Documentation is a continuous activity, not a final-week task.

---

# 41. Recommended Repository Structure

```text
finverify-ai/
│
├── backend/
│   ├── api/
│   ├── agents/
│   ├── rag/
│   ├── retrieval/
│   ├── verification/
│   ├── documents/
│   ├── database/
│   └── services/
│
├── frontend/
│
├── datasets/
│   ├── raw/
│   ├── processed/
│   ├── benchmark/
│   └── finverify_ind/
│
├── documents/
│   ├── raw/
│   ├── processed/
│   └── extracted/
│
├── evaluation/
│   ├── baselines/
│   ├── metrics/
│   ├── ablation/
│   ├── error_analysis/
│   └── reports/
│
├── experiments/
│
├── scripts/
│
├── tests/
│
├── configs/
│
├── docs/
│
├── docker/
│
├── .env.example
├── CLAUDE.md
├── README.md
└── pyproject.toml
```

---

# 42. AI-Agent Development Sequence

The agent must implement in this order:

```text
1. Environment readiness
2. Research specification
3. Repository foundation
4. Dataset pipeline
5. Document acquisition
6. Document intelligence
7. Financial knowledge structuring
8. Normalization
9. Hybrid RAG
10. Question understanding
11. Natural reasoning
12. Program reasoning
13. Deterministic verification
14. Consistency engine
15. Disagreement detection
16. Verification agent
17. Hallucination detection
18. Confidence/risk
19. Explainability
20. Orchestration
21. Database
22. Backend API
23. Evaluation framework
24. Baselines
25. Ablation
26. Error analysis
27. FinVerify-IND
28. Frontend
29. Case study
30. Security
31. Testing
32. Deployment
33. Final documentation
```

The agent must not jump directly to the final UI while the research engine remains unvalidated.

---

# 43. Milestone Acceptance Policy

A module is considered **complete** only when all applicable conditions are satisfied:

```text
[ ] Requirements defined
[ ] Dependencies verified
[ ] Implementation complete
[ ] Unit tests written
[ ] Integration tests where applicable
[ ] Failure cases considered
[ ] Documentation updated
[ ] Configuration documented
[ ] Logs/errors handled
[ ] Git commit created
[ ] Acceptance criteria passed
```

If an item is not completed, the module must be marked:

`PARTIAL` or `BLOCKED`

rather than `COMPLETE`.

---

# 44. AI-Agent Token and Context Management Policy

The AI agent must use its context window efficiently.

### Required behavior

- Read project instructions first.
- Inspect only relevant files when possible.
- Reuse existing utilities.
- Avoid duplicate implementations.
- Batch independent inspections.
- Prefer concise internal task plans.
- Avoid unnecessary repeated explanations.
- Summarize completed work in structured form.
- Keep long-term project state in documentation files rather than relying solely on conversation context.

### But:

**Efficiency must not result in skipped validation.**

Testing, security, reproducibility, research methodology, and required documentation always take priority over saving tokens.

---

# 45. AI-Agent Permission Policy

The AI agent may ask the user for:

- software installation permission;
- API access;
- credentials;
- dataset access;
- GitHub access;
- cloud access;
- model download permission;
- deployment credentials;
- paid API authorization.

The agent must ask before performing actions that require unavailable authorization.

Never guess or fabricate access.

---

# 46. Research Integrity Rules

The agent must:

1. Never fabricate experimental results.
2. Never fabricate citations.
3. Never fabricate datasets.
4. Never alter gold labels to improve scores.
5. Never selectively report only favorable experiments.
6. Preserve raw results.
7. Record failed experiments.
8. Clearly distinguish baseline and proposed-system results.
9. Freeze evaluation methodology before final experiments.
10. Document changes to methodology.

---

# 47. Final Research Deliverables

The finished project must contain:

## Software

- working FinVerify-AI application;
- backend;
- frontend;
- document pipeline;
- hybrid RAG;
- dual reasoning;
- deterministic verification;
- consistency engine;
- verification agent;
- explainability;
- research dashboard.

## Dataset

- benchmark datasets;
- custom FinVerify-IND dataset;
- dataset documentation;
- validation protocol.

## Research

- literature review;
- research gap;
- research questions;
- hypotheses;
- baseline experiments;
- ablation studies;
- error analysis;
- statistical analysis;
- cost/latency analysis.

## Case Study

- selected financial companies;
- financial reports;
- QA experiments;
- verification results;
- failure analysis;
- conclusions.

## Academic Documentation

- SRS;
- architecture;
- methodology;
- experiments;
- results;
- case study;
- limitations;
- future work;
- thesis/paper material.

---

# 48. Final Definition of Success

FinVerify-AI is considered research-ready only when:

1. The complete software stack is installed and verified.
2. Financial documents can be processed reliably.
3. Evidence can be retrieved with measurable retrieval performance.
4. Natural reasoning and programmatic reasoning work independently.
5. Deterministic verification works for supported calculations.
6. The consistency engine can detect disagreements.
7. The verification agent can investigate disagreements.
8. Hallucination/error categories are measurable.
9. Baselines have been implemented.
10. Ablation studies have been completed.
11. FinVerify-IND has been validated.
12. A real-world case study has been completed.
13. Results are reproducible.
14. Security tests pass.
15. Documentation is complete.
16. Raw experiment results are preserved.

---

# 49. Golden Rule

> **Build for research validity first, production quality second, and visual polish third.**

The system must be scientifically measurable, reproducible, testable, and explainable—not merely impressive in a demonstration.

---

## End of FinVerify-AI Execution Specification
