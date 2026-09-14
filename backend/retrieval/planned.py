"""Retrieval driven by a QuestionSpec (spec Modules 6 + 7).

`HybridRetriever` answers one query. A question needing two figures from two
statements needs two, and merging them is not the same as issuing one longer
query: *"return on equity"* ranks nothing useful, while *"profit for the year"*
and *"total equity"* each rank their own statement at the top.

Merging is **round-robin by sub-question, not by score.** Interleaving is the
whole point: taking the global top-K by fused score lets the sub-question with
the stronger lexical signal fill every slot, which is precisely the failure this
exists to prevent - retrieving one of two needed figures produces a confident,
wrong answer rather than a visible gap. Round-robin guarantees each figure gets
its share of the budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.agents.question_understanding import QuestionSpec
from backend.retrieval.hybrid import HybridRetriever, RetrievalResult

__all__ = ["PlannedRetrieval", "retrieve_for_spec"]


@dataclass(frozen=True)
class PlannedRetrieval:
    """Merged evidence, plus which sub-question surfaced each chunk."""

    results: list[RetrievalResult]
    per_sub_question: dict[str, list[RetrievalResult]]
    spec: QuestionSpec

    @property
    def coverage(self) -> dict[str, int]:
        """How many chunks each figure contributed. A zero is a visible gap."""
        return {metric: len(hits) for metric, hits in self.per_sub_question.items()}


# How many years after its own can a filing still report? Indian listed
# companies publish a ten-year financial-highlights summary under SEBI's listing
# obligations, so a FY2023-24 report legitimately states FY2014-15 figures.
# Eleven gives that convention one year of margin. It is a horizon on a
# ONE-DIRECTIONAL constraint, not a guess at relevance: the direction is what
# carries the meaning, and the number only bounds it.
_RESTATEMENT_HORIZON_YEARS = 11


def acceptable_document_years(fiscal_year: str) -> tuple[str, ...]:
    """Which documents could state figures for `fiscal_year`.

    A chunk's `fiscal_year` is the year of the DOCUMENT it came from, not the
    year of the column the figure sits in. The constraint is one-directional: a
    report can state any year up to and including its own, and no year after it.
    So the documents that could carry FY Y are those published in FY Y **or
    later** - never earlier.

    Matching the two exactly was a silent, total failure (D36). Every chunk in
    this corpus carries `2023-24`, so a question about the year ended March 2023
    filtered to **zero chunks** and retrieval returned an empty list. Both
    channels then answered from no evidence.

    D36 fixed that by admitting Y and Y+1, and its docstring claimed "widening
    further would start admitting documents that cannot contain the figure at
    all." **That was backwards.** A later report can always restate an earlier
    year; it is an *earlier* report that cannot contain a *later* figure. The
    Y+1 window silently returned zero chunks for every question reaching further
    back than one year - 115 of FinVerify-IND's 268 (43%), because HDFC Bank's
    ten-year summary and several multi-year comparatives put questions in the
    set going back to FY2013-14 (RX-020).

    The window is bounded rather than open because a filter that admits
    everything is not a filter; `_RESTATEMENT_HORIZON_YEARS` records why the
    bound is where it is.
    """
    match = re.fullmatch(r"(\d{4})-(\d{2})", fiscal_year)
    if match is None:
        return (fiscal_year,)
    start = int(match.group(1))
    return tuple(
        f"{year}-{(year + 1) % 100:02d}"
        for year in range(start, start + _RESTATEMENT_HORIZON_YEARS + 1)
    )


def retrieve_for_spec(
    retriever: HybridRetriever,
    spec: QuestionSpec,
    *,
    top_k: int = 10,
    document_id: str | None = None,
    **filters,
) -> PlannedRetrieval:
    """Retrieve for every sub-question and interleave the results.

    The fiscal year from the spec is applied as a metadata filter only when the
    caller has not already fixed one, and only when the spec actually found one -
    guessing a year would silently exclude the right evidence, which is worse
    than not filtering (see `extract_fiscal_year`).
    """
    if document_id is not None:
        filters["document_id"] = document_id
    if spec.company and "company" not in filters:
        filters["company"] = spec.company
    if spec.fiscal_year and "fiscal_year" not in filters:
        filters["fiscal_year"] = acceptable_document_years(spec.fiscal_year)

    subs = spec.sub_questions or ()
    if not subs:
        return PlannedRetrieval([], {}, spec)

    # Each sub-question is given the full budget; interleaving then trims to
    # top_k. Asking for top_k // n each would leave a figure with two slots on a
    # five-figure question, which is not enough to be found at all.
    per_sub: dict[str, list[RetrievalResult]] = {}
    for sub in subs:
        per_sub[sub.metric] = retriever.retrieve(sub.search_text, top_k=top_k, **filters)

    merged: list[RetrievalResult] = []
    seen: set[str] = set()
    for rank in range(top_k):
        for sub in subs:
            hits = per_sub[sub.metric]
            if rank >= len(hits):
                continue
            hit = hits[rank]
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            merged.append(hit)
            if len(merged) >= top_k:
                return PlannedRetrieval(merged, per_sub, spec)
    return PlannedRetrieval(merged, per_sub, spec)
