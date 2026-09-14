"""Query preparation for retrieval (spec Module 6/7 boundary).

A natural-language financial question is mostly boilerplate. Measured against the
906 chunks of the Infosys FY2023-24 statements:

    "What were total assets as at March 31, 2024?"

    term      appears in
    what        0.0% of chunks
    were        5.0%
    total      16.4%
    assets     25.2%
    as         56.5%
    at         35.2%
    march      33.7%
    31,        32.7%
    2024       26.9%

Every term a reader would consider *the question* is among the most common terms
in the corpus, and the scaffolding around it - "what were", "as at March 31,
2024" - matches a third of the document. BM25 sums term contributions, so a chunk
stuffed with common terms outscores the one chunk that actually answers, and the
dense leg is embedding a sentence whose content words are swamped by date
scaffolding shared with everything else.

Stripping that scaffolding took evidence-retrieval accuracy from 0.636 to 0.864
on the validation set, with no change to the index, the model, or the fusion
(EXPERIMENTS.md RX-004). It is the single largest retrieval gain measured so far
and it cost nothing.

**The date is dropped, not ignored.** "as at March 31, 2024" carries real
information - which fiscal year is being asked about - and on a single-year
corpus it is pure noise, but on a multi-year one it is essential. It belongs in a
**metadata filter** (`fiscal_year`, already an indexed payload field), not in the
query text where it can only dilute. Extracting it is Module 7's job; until
Module 7 exists, `extract_fiscal_year` does the deterministic part so the
information is not silently lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "STOPWORDS",
    "content_terms",
    "prepare_query",
    "strip_question_boilerplate",
    "extract_fiscal_year",
    "extract_fiscal_years",
]

# Interrogative scaffolding and function words. Deliberately NOT a general
# English stopword list: domain terms that a general list would drop ("other",
# "total", "net", "current") are exactly the words that distinguish
# "other equity" from "equity" and "total current assets" from "total assets".
STOPWORDS = frozenset(
    {
        # interrogatives and framing
        "what", "which", "how", "much", "many", "was", "were", "is", "are", "did",
        "does", "do", "had", "has", "have", "the", "a", "an",
        # prepositions and conjunctions
        "of", "for", "to", "in", "on", "at", "as", "by", "with", "from", "and",
        "or", "that", "this", "it", "its", "their",
        # entity words that match every chunk of a single company's filing
        "company", "group", "companys", "groups",
    }
)

# Reporting-period scaffolding. Matched as phrases so that a bare "year" inside
# "year-on-year growth" survives.
_PERIOD_PHRASE = re.compile(
    r"\b(?:as\s+at|as\s+on|for\s+the\s+year\s+ended|year\s+ended|during\s+the\s+year"
    r"|at\s+the\s+end\s+of\s+the\s+(?:financial\s+|fiscal\s+)?year)\b",
    re.I,
)
_DATE = re.compile(r"\b(?:31st|31)?\s*(?:january|february|march|april|may|june|july|august"
                   r"|september|october|november|december)\s*,?\s*\d{1,2}?,?\s*(?:20\d\d)?\b", re.I)
_FISCAL_YEAR = re.compile(r"\b(?:FY\s*)?(20\d\d)\s*[-/]\s*(\d{2}|20\d\d)\b|\b(20\d\d)\b", re.I)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9,.'-]*")


@dataclass(frozen=True)
class QuerySpec:
    """What retrieval should search for, and what it should filter on."""

    search_text: str
    fiscal_year: str | None = None
    original: str = ""


def extract_fiscal_years(question: str) -> list[str]:
    """Every Indian fiscal year label the question names, oldest first.

    March 31 of year Y ends fiscal year (Y-1)-Y, which is the convention the
    corpus registry uses. A bare calendar year is deliberately NOT read this way:
    "revenue in 2024" is genuinely ambiguous between the calendar year and either
    adjacent fiscal year, and filtering on a guess silently excludes the right
    evidence while not filtering only costs precision.
    """
    found: list[str] = []
    for match in re.finditer(r"\b(?:FY\s*)?(20\d\d)\s*[-/]\s*(\d{2})\b", question, re.I):
        label = f"{match.group(1)}-{match.group(2)}"
        if label not in found:
            found.append(label)
    for match in re.finditer(r"\bmarch\s+31,?\s*(20\d\d)\b", question, re.I):
        year = int(match.group(1))
        label = f"{year - 1}-{str(year)[2:]}"
        if label not in found:
            found.append(label)
    return sorted(found)


def extract_fiscal_year(question: str) -> str | None:
    """The single fiscal year to filter on, or None when that would be wrong.

    Returns None when a question names **more than one** year. That is not a
    failure to parse - it is the correct answer. "By how much did revenue grow
    between March 31, 2023 and March 31, 2024?" names two, and filtering to
    either one excludes half the evidence. Taking the first match here filtered a
    single-year corpus to a year it does not contain and returned nothing at all,
    silently, which is how question R13 regressed the moment metadata filtering
    was switched on.
    """
    years = extract_fiscal_years(question)
    return years[0] if len(years) == 1 else None


def content_terms(question: str) -> list[str]:
    """The question's content words, with scaffolding removed and NO fallback.

    Separate from `strip_question_boilerplate` because the two callers need
    opposite things from the empty case. Retrieval wants a non-empty query and
    is right to fall back to the original text; question *understanding* needs to
    know that there was nothing there, because a question with no content terms
    is unanswerable and must not be sent to a retriever at all.

    Measured on the live index, the question "" returned three arbitrary tables
    - foreign-currency analysis and segment reporting - which would then have
    been handed to both reasoning channels as *evidence*. Retrieval manufacturing
    evidence for a question that asked nothing is the same failure mode as
    answering from memory, one layer down.
    """
    text = _PERIOD_PHRASE.sub(" ", question)
    text = _DATE.sub(" ", text)
    return [w for w in _WORD.findall(text) if w.lower().strip(".,'") not in STOPWORDS]


def strip_question_boilerplate(question: str) -> str:
    """Reduce a question to its content terms.

    Falls back to the original question if stripping would leave nothing - for a
    retriever, searching the original text is strictly better than searching an
    empty string, which returns an arbitrary ranking rather than an error. Use
    `content_terms` when you need to detect that case instead of paper over it.
    """
    stripped = " ".join(content_terms(question)).strip(" ?.,")
    return stripped or question


def prepare_query(question: str) -> QuerySpec:
    return QuerySpec(
        search_text=strip_question_boilerplate(question),
        fiscal_year=extract_fiscal_year(question),
        original=question,
    )
