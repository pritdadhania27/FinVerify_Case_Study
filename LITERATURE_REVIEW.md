# Literature Review

**Scope:** numerical reasoning over financial documents, program-aided reasoning,
and hallucination detection by consistency or verification.

**Citation policy.** Every entry below was retrieved and checked against a
primary source (arXiv abstract page, ACL Anthology, or PMLR proceedings) during
this review. Nothing here is written from recollection. Where a figure appeared
only in a secondary summary and could not be confirmed against the paper itself,
it is **not** quoted — see the note under FAITH. Spec §46 forbids fabricated
citations, and an unverifiable number is a fabricated citation with extra steps.

---

## 1. Benchmarks for numerical reasoning over financial documents

**FinQA: A Dataset of Numerical Reasoning over Financial Data.**
Chen et al., EMNLP 2021. arXiv:[2109.00122](https://arxiv.org/abs/2109.00122),
DOI 10.18653/v1/2021.emnlp-main.300. Code: [czyssrs/FinQA](https://github.com/czyssrs/FinQA).

Expert-annotated QA over earnings reports, each example pairing a report excerpt
and table with a question requiring multi-step arithmetic. Its decisive design
choice for this project is the **gold reasoning program**: a symbolic operator
sequence with operands grounded in the source text or table. That gives an
executable ground truth, not merely a final number — so a system can be scored on
*how* it arrived at an answer, not only *whether* the answer matched. The paper
reports that large pre-trained models fall well short of expert humans on
multi-step numerical reasoning.

**TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual
Content in Finance.** Zhu, Lei, Huang, Wang, Zhang, Lv, Feng, Chua; ACL/IJCNLP
2021. arXiv:[2105.07624](https://arxiv.org/abs/2105.07624) ·
[ACL Anthology](https://aclanthology.org/2021.acl-long.254/).

16,552 QA pairs over 182 real financial reports, deliberately mixing tabular and
textual evidence. Answering requires addition, subtraction, multiplication,
division, counting, comparison/sorting, and compositions thereof. The hybrid
table-plus-text structure is what makes retrieval non-trivial in this domain: the
number and the fact that names it frequently live in different representations.

**ConvFinQA: Exploring the Chain of Numerical Reasoning in Conversational Finance
Question Answering.** Chen et al., EMNLP 2022.
arXiv:[2210.03849](https://arxiv.org/abs/2210.03849) ·
[ACL Anthology](https://aclanthology.org/2022.emnlp-main.421/).
Splits: 3,037 train / 421 dev / 434 test conversations.

Extends FinQA to multi-turn dialogue, where later questions depend on earlier
questions and answers. Contributes long-range reasoning chains — the regime where
a single arithmetic slip propagates silently through every subsequent turn.

## 2. Program-aided reasoning

**PAL: Program-aided Language Models.** Gao, Madaan, Zhou, Alon, Liu, Yang,
Callan, Neubig. arXiv:[2211.10435](https://arxiv.org/abs/2211.10435); ICML 2023,
[PMLR v202](https://proceedings.mlr.press/v202/gao23f/gao23f.pdf).

The model reads the problem and emits a program; a Python interpreter executes
it. The motivating observation is precisely the failure mode this project
targets: models decompose problems correctly and *then* make arithmetic mistakes
in the solution step. Moving execution to an interpreter removes that class of
error.

**Program of Thoughts Prompting: Disentangling Computation from Reasoning for
Numerical Reasoning Tasks.** Chen, Ma, Wang, Cohen; TMLR 2023.
arXiv:[2211.12588](https://arxiv.org/abs/2211.12588).

Same core idea, and directly relevant here because PoT is evaluated on
**FinQA, ConvFinQA, and TAT-QA** among others, reporting an average gain of
roughly 12% over chain-of-thought across its evaluation suite.

**Why this matters for the present work.** PAL and PoT establish that a generated
program plus an interpreter is a *stronger* numerical reasoner than
natural-language chain-of-thought. Both treat that as a route to **higher
accuracy**. Neither treats the program channel as an **independent verification
authority** whose *disagreement* with a natural-language channel carries
diagnostic information. That reframing — from accuracy mechanism to error
detector — is where this project sits.

## 3. Hallucination detection by consistency

**SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative
Large Language Models.** Manakul, Liusie, Gales; EMNLP 2023.
arXiv:[2303.08896](https://arxiv.org/abs/2303.08896) ·
[OpenReview](https://openreview.net/forum?id=RwzFNbJ3Ez).

The premise: if a model knows something, independently sampled responses agree;
if it is hallucinating, they diverge and contradict. Detection needs no external
database. Five variants (BERTScore, MQAG, Unigram, NLI, GPT-Prompt).

**The limitation this project starts from:** SelfCheckGPT's samples are
*homogeneous* — same model, same prompt, same modality, differing only by
sampling stochasticity. Such samples share the model's systematic biases. A
confidently-held wrong belief produces consistent wrong samples, and consistency
then reads as confidence. This is where a *cross-modality* signal should behave
differently, and whether it actually does is an empirical question rather than an
assumption.

## 4. Self-verification and its limits

**Chain-of-Verification Reduces Hallucination in Large Language Models.**
Dhuliawala et al. arXiv:[2309.11495](https://arxiv.org/abs/2309.11495);
Findings of ACL 2024,
[anthology](https://aclanthology.org/2024.findings-acl.212.pdf).

Four stages: draft, plan verification questions, answer them *independently* so
the answers are not biased by the draft, then revise. The independence of the
verification step is the load-bearing idea, and it is the same intuition this
project pushes further — from independently-answered sub-questions to an
independently-*executed* program. The authors note CoVe reduces but does not
eliminate hallucination, and that they do not study error types beyond factual
inaccuracy — numerical reasoning errors specifically are left open.

**Large Language Models Cannot Self-Correct Reasoning Yet.** Huang, Chen, Mishra,
Zheng, Yu, Song, Zhou; ICLR 2024.
arXiv:[2310.01798](https://arxiv.org/abs/2310.01798).

Intrinsic self-correction — a model revising itself with no external feedback —
fails on reasoning tasks, and performance sometimes *degrades* after
self-correction.

**This is the theoretical hinge of the present work.** If self-correction fails
without external feedback, then a verification signal must come from something
that is not the model. A Python interpreter executing a generated program is
external: the interpreter has no beliefs about revenue and cannot be persuaded by
a plausible-sounding derivation. A deterministic calculator is more external
still. This is the argument for grounding verification in execution rather than
in a second opinion from the same model.

## 5. Hallucination in financial contexts specifically

**FAITH: A Framework for Assessing Intrinsic Tabular Hallucinations in Finance.**
Zhang, Fu, Warrier, Wang, Tan, Huang. arXiv:[2508.05201](https://arxiv.org/abs/2508.05201)
(v1 Aug 2025, v2 Oct 2025).

Frames hallucination assessment as context-aware masked span prediction over real
financial documents, with an evaluation set built from S&P 500 annual reports.

> **Integrity note.** Secondary summaries of FAITH quote a striking collapse from
> ~95.6% accuracy on simple lookups to near-zero on multivariate calculations. I
> could not confirm those figures against the arXiv abstract page, so they are
> **not** cited as established here. If that contrast is used in the final
> write-up, the numbers must first be read out of the paper's own results
> section. Flagged in `TODO.md`.

**PHANTOM** — hallucination detection in financial long-context QA, NeurIPS 2025
([OpenReview](https://openreview.net/forum?id=5YQAo0S3Hm)) — and **FinGround**,
detecting and grounding financial hallucinations via atomic claim verification
(arXiv:[2604.23588](https://arxiv.org/pdf/2604.23588)) — both indicate active,
recent attention to hallucination *in this domain*. Both approach it primarily as
claim-grounding against source text.

**FinanceBench** (Islam et al.,
[paper](https://uploads-ssl.webflow.com/64e655d42d3be60f582d0472/65558c28757acd0fa312c5ec_FinanceBench__ACL_%20(3).pdf))
contributes open-book financial QA over real filings.

> **Currency sweep DONE 2026-09-06. The gap claim survives, and it is narrower
> than it was.** This section was re-swept specifically for work pairing program
> execution with natural-language reasoning *as a detector*. Two items are close
> enough to name, and neither takes the gap — both were read at source rather
> than from a search summary.
>
> **Nearest work: Chen et al., *Fighting Numerical Hallucinations via
> Data-centric Compilation for Online Financial QA*** (arXiv
> [2605.31064](https://arxiv.org/abs/2605.31064), 29 May 2026; accepted KDD 2026,
> ADS track). Same domain and same failure mode — numerical hallucination in
> financial QA — and it *does* execute programs, transforming queries and
> retrieved documents into "verifiable, executable reasoning programs". But
> execution is used to **produce a correct answer**, not to produce a second
> opinion whose disagreement with a first is the signal. It is a strengthened
> version of strand 2, and its existence sharpens the motivation here rather than
> pre-empting the contribution. **It should be cited in the write-up**; it is the
> closest published neighbour this project has.
>
> **Also checked and further away:** Kovács et al., *Beyond Document Grounding:
> Span-Level Hallucination Detection over Code, Tool Output, and Documents*
> (arXiv [2607.00895](https://arxiv.org/abs/2607.00895), 1 Jul 2026) detects
> hallucinated spans across structured evidence types including code, by training
> a detector on injected hallucinations — evidence *types*, not reasoning
> *channels*. Multi-agent debate work (e.g. CSMAD) manufactures disagreement, but
> between same-modality agents, which is the homogeneity strand 3 already
> identifies as the limitation.
>
> **So the gap below stands as written**, with one qualification worth stating
> plainly in the write-up: executable-program approaches to financial numerical
> hallucination are now an active 2026 line, so the contribution is *disagreement
> as a detector*, not *program execution in finance*, and the second must not be
> claimed.

---

## 6. Synthesis: the gap this project addresses

Reading the strands together:

1. Financial numerical QA has mature benchmarks with **executable gold programs**
   (FinQA), hybrid table/text evidence (TAT-QA), and long reasoning chains
   (ConvFinQA).
2. Program-aided reasoning reliably beats natural-language arithmetic (PAL, PoT)
   — and is validated on exactly these financial benchmarks. It is framed as an
   **accuracy** technique.
3. Consistency-based hallucination detection works, but over **homogeneous
   samples** that share a model's systematic biases (SelfCheckGPT).
4. Self-verification without external feedback is unreliable, and can make things
   worse (Huang et al.); CoVe's gains come from making verification
   *independent*.
5. Financial hallucination is a live, actively-benchmarked problem (FAITH,
   PHANTOM, FinGround), addressed mainly as claim-grounding rather than as
   numerical cross-checking.

**The gap.** No work located here systematically measures whether **disagreement
between reasoning channels of different modality** — natural-language reasoning
versus an *executed* program versus a *deterministic* recomputation — is a
reliable **detector** of numerical hallucination in financial document QA. The
ingredients are established individually; their combination as a detection signal
is not evaluated.

**Two things must be measured honestly for that gap to be worth filling:**

- **Shared-evidence contamination.** Both channels read the same retrieved
  evidence. If retrieval surfaced the wrong number, both channels compute
  faithfully from it and *agree on a wrong answer*. Agreement therefore cannot
  detect retrieval failure, only reasoning failure. Any claim about detection
  power must be reported separately for retrieval-caused and reasoning-caused
  errors, or it overstates what the method can do.
- **Disagreement is not free.** Two channels plus a verification agent cost more
  than one answer. The honest framing is a detection-rate-per-unit-cost curve,
  not detection rate alone (spec §33).

These two constraints shape `RESEARCH_QUESTIONS.md` and `HYPOTHESES.md`.

---

## Sources

- [FinQA (arXiv:2109.00122)](https://arxiv.org/abs/2109.00122)
- [TAT-QA (arXiv:2105.07624)](https://arxiv.org/abs/2105.07624) · [ACL Anthology](https://aclanthology.org/2021.acl-long.254/)
- [ConvFinQA (arXiv:2210.03849)](https://arxiv.org/abs/2210.03849) · [ACL Anthology](https://aclanthology.org/2022.emnlp-main.421/)
- [PAL (arXiv:2211.10435)](https://arxiv.org/abs/2211.10435) · [PMLR v202](https://proceedings.mlr.press/v202/gao23f/gao23f.pdf)
- [Program of Thoughts (arXiv:2211.12588)](https://arxiv.org/abs/2211.12588)
- [SelfCheckGPT (arXiv:2303.08896)](https://arxiv.org/abs/2303.08896) · [OpenReview](https://openreview.net/forum?id=RwzFNbJ3Ez)
- [Chain-of-Verification (arXiv:2309.11495)](https://arxiv.org/abs/2309.11495) · [ACL Findings 2024](https://aclanthology.org/2024.findings-acl.212.pdf)
- [LLMs Cannot Self-Correct Reasoning Yet (arXiv:2310.01798)](https://arxiv.org/abs/2310.01798)
- [FAITH (arXiv:2508.05201)](https://arxiv.org/abs/2508.05201)
- [PHANTOM (OpenReview)](https://openreview.net/forum?id=5YQAo0S3Hm)
- [FinGround (arXiv:2604.23588)](https://arxiv.org/pdf/2604.23588)
- [FinanceBench](https://uploads-ssl.webflow.com/64e655d42d3be60f582d0472/65558c28757acd0fa312c5ec_FinanceBench__ACL_%20(3).pdf)
