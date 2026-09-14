"""Question understanding (spec Module 7).

Turns a natural-language financial question into something retrieval and the two
reasoning channels can act on: which figures are needed, where to look for them,
what arithmetic relates them, and what the answer's units should be.

**This module is on the critical path for retrieval quality, not just parsing.**
RX-004 measured the retrieval deficit and found two residual classes, both of
which are question problems rather than retriever problems:

* *"What was the return on equity?"* needs profit **and** total equity, from two
  different statements. One query cannot rank both, so R14 failed at every K in
  four consecutive measurements. It needs **decomposition**.
* *"What were total assets?"* has no selective term at all - `total` appears in
  16% of the corpus's chunks and `assets` in 25%. It needs a **statement hint**
  that a reader supplies instinctively and a retriever cannot.

Both are supplied here, from `metrics_lexicon`.

**Deterministic first, LLM second, and the order matters.** The rule-based parser
runs always; the LLM refines it when available. That ordering is deliberate:

* it costs no free-tier quota for the common case, and quota is the binding
  constraint on the whole evaluation (D14);
* it is reproducible, so a retrieval measurement is not silently a measurement of
  a model's mood on that day;
* a provider outage degrades question understanding instead of stopping the
  pipeline.

**Research-validity note (D19).** Both reasoning channels consume the same
`QuestionSpec`. That is a shared upstream component and therefore a common-mode
failure path, like shared evidence (H2): a mis-parsed question makes both
channels wrong in the same way, and their agreement would be uninformative. It
does not violate dual-channel independence, which is a claim about *reasoning*
over given evidence, but it does bound what channel agreement can prove, and the
`ambiguities` field exists so a question the parser was unsure about can be
excluded or reported separately rather than silently averaged in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from backend.services.llm.settings import llm_settings
from backend.agents.metrics_lexicon import (
    decompose_derived,
    lookup_metric,
    statement_hint,
)
from backend.retrieval.query import (
    content_terms,
    extract_fiscal_year,
    extract_fiscal_years,
    strip_question_boilerplate,
)

__all__ = [
    "SubQuestion",
    "QuestionSpec",
    "parse_question",
    "understand_question",
    "QUESTION_UNDERSTANDING_PROMPT",
]

# Operations the deterministic verifier can check (backend/verification).
_OPERATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcagr\b|\bcompound annual growth\b", re.I), "cagr"),
    (re.compile(r"\bgrow(?:th|n|)\b|\bincrease[d]?\b|\bdecrease[d]?\b|\bchange[d]?\b", re.I),
     "percentage_change"),
    (re.compile(r"\bmargin\b", re.I), "margin"),
    (re.compile(r"\bratio\b|\breturn on\b", re.I), "ratio"),
    (re.compile(r"\b(?:share|proportion|percentage|fraction)\s+of\b", re.I), "percentage_of"),
    (re.compile(r"\bdifference\b|\bhow much (?:more|less)\b", re.I), "difference"),
    (re.compile(r"\btotal\s+of\b|\bsum\s+of\b|\bcombined\b", re.I), "sum"),
    (re.compile(r"\baverage\b|\bmean\b", re.I), "average"),
]

@dataclass(frozen=True)
class SubQuestion:
    """One figure the answer needs, and the query that should find it."""

    metric: str
    search_text: str
    statement: str | None = None
    fiscal_year: str | None = None

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "search_text": self.search_text,
            "statement": self.statement,
            "fiscal_year": self.fiscal_year,
        }


@dataclass(frozen=True)
class QuestionSpec:
    """What the pipeline needs to know about a question before answering it."""

    original: str
    sub_questions: tuple[SubQuestion, ...]
    operation: str = "lookup"
    fiscal_year: str | None = None
    company: str | None = None
    expected_unit: str = "currency"
    derived_metric: str | None = None
    ambiguities: tuple[str, ...] = ()
    source: str = "deterministic"
    metadata: dict = field(default_factory=dict)

    @property
    def is_answerable(self) -> bool:
        """False when the question carried no content terms to search for.

        An unanswerable spec has NO sub-questions, so `retrieve_for_spec`
        retrieves nothing and the channels are handed the explicit
        "NO EVIDENCE" instruction rather than arbitrary chunks.
        """
        return bool(self.sub_questions)

    @property
    def is_multi_hop(self) -> bool:
        return len(self.sub_questions) > 1

    @property
    def search_texts(self) -> tuple[str, ...]:
        return tuple(sq.search_text for sq in self.sub_questions)

    def as_dict(self) -> dict:
        return {
            "original": self.original,
            "operation": self.operation,
            "fiscal_year": self.fiscal_year,
            "company": self.company,
            "expected_unit": self.expected_unit,
            "derived_metric": self.derived_metric,
            "sub_questions": [sq.as_dict() for sq in self.sub_questions],
            "ambiguities": list(self.ambiguities),
            "source": self.source,
        }


def _search_text_for(metric: str, *, hint: str | None) -> str:
    """Query text for one figure, with its statement appended as a soft hint.

    Appended to the *text* rather than applied as a `section` filter on purpose:
    a filter that is wrong excludes the answer outright, while a hint that is
    wrong only dilutes. RX-004 rejected a `kind="table"` filter for exactly this
    reason - it excluded evidence that legitimately lives in narrative.
    """
    return f"{metric} {hint}".strip() if hint else metric


def parse_question(question: str, *, company: str | None = None) -> QuestionSpec:
    """Rule-based question understanding. No model call, fully reproducible."""
    # A question with no content terms is unanswerable, and must not reach a
    # retriever. Measured on the live index, the empty question returned three
    # arbitrary tables which would then have been presented to both reasoning
    # channels as evidence - retrieval manufacturing evidence for a question that
    # asked nothing. Emitting zero sub-questions makes the gap explicit: nothing
    # is retrieved, and `format_evidence([])` tells the channels to refuse.
    if not content_terms(question):
        return QuestionSpec(
            original=question,
            sub_questions=(),
            operation="lookup",
            company=company,
            expected_unit="unknown",
            ambiguities=(
                (
                    "the question contains no searchable content terms, so there "
                    "is nothing to retrieve; answering it would mean inventing "
                    "both the question and the answer"
                ),
            ),
        )

    ambiguities: list[str] = []
    fiscal_year = extract_fiscal_year(question)
    if fiscal_year is None:
        ambiguities.append(
            "no fiscal year stated; retrieval cannot be year-filtered and a "
            "multi-year corpus may return the wrong period"
        )

    operation = next(
        (name for pattern, name in _OPERATION_PATTERNS if pattern.search(question)), "lookup"
    )

    # -- derived metrics decompose into their components ----------------
    derived = decompose_derived(question)
    if derived is not None:
        subs = tuple(
            SubQuestion(
                metric=component,
                search_text=_search_text_for(component, hint=statement_hint(component)),
                statement=statement_hint(component),
                fiscal_year=fiscal_year,
            )
            for component in derived.components
        )
        return QuestionSpec(
            original=question,
            sub_questions=subs,
            operation=derived.operation,
            fiscal_year=fiscal_year,
            company=company,
            expected_unit=derived.unit_kind,
            derived_metric=derived.canonical,
            ambiguities=tuple(ambiguities),
        )

    # -- a single reported figure ---------------------------------------
    stripped = strip_question_boilerplate(question)
    entry = lookup_metric(question)
    hint = entry.statement if entry else None
    if entry is None:
        ambiguities.append(
            f"metric {stripped!r} is not in the lexicon; retrieval falls back to "
            f"the stripped question text with no statement hint"
        )

    expected_unit = entry.unit_kind if entry else "currency"
    if operation in {"percentage_change", "margin", "percentage_of", "cagr"}:
        expected_unit = "percent"
    elif operation == "ratio":
        expected_unit = "ratio"

    # **No statement hint on this path**, and that is a measured decision rather
    # than an omission. Appending the statement to a *reported* metric's query
    # was tried and rejected: it rescued "total assets" (no selective term of its
    # own) and lost "total other financial liabilities", which is reported in
    # note 2.13 rather than on the balance sheet the lexicon points at. Issuing
    # both the hinted and unhinted query instead only split the budget and lost
    # both. Net effect across the validation set was zero, so the mechanism is
    # not carried (RX-005).
    #
    # The hint IS used for decomposed derived metrics above, and the difference
    # is real: there the component names come out of the lexicon by construction,
    # so their statement is known rather than inferred from a substring match on
    # whatever the user happened to type.
    # The one case where the hint IS safe on this path: the question reduces
    # *exactly* to a known line item, so which statement reports it is known
    # rather than inferred. "total assets" qualifies and needs the help; "total
    # other financial liabilities" does not qualify - it merely contains a
    # lexicon entry - and is left alone, which is what stopped it regressing.
    exact = entry is not None and stripped.lower() in {entry.canonical, *entry.aliases}
    search_text = _search_text_for(stripped, hint=hint) if (exact and hint) else stripped

    sub_questions = [
        SubQuestion(metric=stripped, search_text=search_text, statement=hint,
                    fiscal_year=fiscal_year)
    ]

    # A comparison across two named years needs both columns, which the primary
    # statements put in one row - so it stays a single retrieval, and the years
    # are carried for the verifier rather than split into sub-questions.
    years = extract_fiscal_years(question)
    metadata: dict = {"fiscal_years_mentioned": years} if years else {}
    if entry is not None:
        metadata["canonical_metric"] = entry.canonical

    return QuestionSpec(
        original=question,
        sub_questions=tuple(sub_questions),
        operation=operation,
        fiscal_year=fiscal_year,
        company=company,
        expected_unit=expected_unit,
        ambiguities=tuple(ambiguities),
        metadata=metadata,
    )


QUESTION_UNDERSTANDING_PROMPT = """\
You analyse questions about financial statements. You do NOT answer them and you \
do NOT compute anything.

Return ONLY a JSON object with these keys:
  "operation":      one of lookup, sum, difference, ratio, margin, average,
                    percentage_change, percentage_of, cagr
  "metrics":        list of the exact reported line items needed to answer, using
                    the wording a financial statement would use (e.g. "profit for
                    the year", not "net income"). A ratio needs BOTH components.
  "expected_unit":  currency, percent, ratio, or count
  "fiscal_year":    the Indian fiscal year label like "2023-24", or null
  "ambiguities":    list of strings; anything genuinely unclear about what is
                    being asked. Empty list if the question is unambiguous.

Rules:
- A derived figure is never a single metric. "Return on equity" is
  ["profit for the year", "total equity"].
- Do not invent a metric the question does not ask for.
- If the question cannot be answered from a financial statement, return an empty
  metrics list and say why in ambiguities.

Question: {question}
JSON:"""


def _spec_from_llm_payload(
    question: str, payload: dict, *, company: str | None, fallback: QuestionSpec
) -> QuestionSpec:
    metrics = [m for m in payload.get("metrics", []) if isinstance(m, str) and m.strip()]
    if not metrics:
        # An empty metric list is not a usable spec. Keeping the deterministic
        # parse is strictly better than retrieving on nothing.
        return fallback

    fiscal_year = payload.get("fiscal_year") or fallback.fiscal_year
    subs = tuple(
        SubQuestion(
            metric=m,
            search_text=_search_text_for(m, hint=statement_hint(m)),
            statement=statement_hint(m),
            fiscal_year=fiscal_year,
        )
        for m in metrics
    )
    ambiguities = tuple(
        a for a in payload.get("ambiguities", []) if isinstance(a, str) and a.strip()
    )
    return QuestionSpec(
        original=question,
        sub_questions=subs,
        operation=str(payload.get("operation", fallback.operation)),
        fiscal_year=fiscal_year,
        company=company,
        expected_unit=str(payload.get("expected_unit", fallback.expected_unit)),
        derived_metric=fallback.derived_metric,
        ambiguities=ambiguities or fallback.ambiguities,
        source="llm",
        metadata=fallback.metadata,
    )


def understand_question(
    question: str,
    *,
    company: str | None = None,
    provider=None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> QuestionSpec:
    """Deterministic parse, refined by the LLM when one is supplied.

    **NOT ON THE CAMPAIGN PATH, and that is a decision (D37), not an oversight.**
    `orchestrator.py` calls `parse_question` directly. RX-018 ran this refinement
    against a live model and found it returns the wrong Indian fiscal year on
    every prior-year question tested - "the year ended March 31, 2023" is FY
    2022-23, and the model answers 2023-24, seven times out of seven. 72% of
    FinVerify-IND asks about a prior year (D36), the fiscal year is the field
    whose failure returns an EMPTY evidence set rather than a bad ranking, and
    one QuestionSpec feeds both channels - so this error would be common-mode
    (D19) on the majority of the dataset. It resolves metrics better than the
    rule parser; that does not buy back the year.

    Before wiring this in, read RX-018. The sensible shape is narrower than
    "use the LLM": take its metric, keep the deterministic year.

    Any provider failure - outage, quota, malformed JSON, empty metric list -
    returns the deterministic spec rather than raising. Question understanding is
    upstream of everything, so failing closed here would stop the pipeline for a
    component that has a working fallback.

    `max_tokens` is generous by default: these models spend budget on hidden
    reasoning before emitting anything, and a small budget returns an empty
    string with HTTP 200 (D17).
    """
    settings = llm_settings()
    temperature = settings.temperature if temperature is None else temperature
    max_tokens = settings.max_tokens if max_tokens is None else max_tokens

    baseline = parse_question(question, company=company)
    if provider is None or model is None:
        return baseline

    from backend.services.llm.base import Message, Role

    try:
        response = provider.complete(
            [Message(Role.USER, QUESTION_UNDERSTANDING_PROMPT.format(question=question))],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=True,
            prompt_version="question_understanding_v1",
        )
        payload = json.loads(response.text)
    except Exception as exc:  # noqa: BLE001 - see docstring: degrade, never stop
        return QuestionSpec(
            **{
                **{k: v for k, v in vars(baseline).items() if k != "ambiguities"},
                "ambiguities": (
                    *baseline.ambiguities,
                    (
                        f"LLM question understanding unavailable "
                        f"({type(exc).__name__}); used the deterministic parse"
                    ),
                ),
            }
        )

    if not isinstance(payload, dict):
        return baseline
    return _spec_from_llm_payload(question, payload, company=company, fallback=baseline)
