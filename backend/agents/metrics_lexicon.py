"""Financial metric vocabulary (spec Module 4/7).

Two pieces of domain knowledge that a general-purpose parser cannot supply, both
of which the retrieval measurements showed are load-bearing:

**Derived metrics decompose into components.** "Return on equity" is not a line
item anywhere in an annual report; it is profit divided by equity, and those two
figures live in two different statements. A single retrieval cannot rank both,
which is exactly why question R14 failed at every K in RX-001 through RX-004. The
decomposition is knowledge a financial analyst has and a retriever does not.

**A metric implies a statement.** "Total assets" has no selective term - `total`
appears in 16% of the corpus's chunks and `assets` in 25% (RX-004) - so lexical
and dense retrieval both flounder. But a reader knows immediately that total
assets is a balance-sheet line, and the balance sheet is a small, nameable part
of the document. Adding that hint to the query text is a soft signal both
retrieval legs can use, and unlike a hard `section` filter it cannot exclude
evidence that legitimately lives elsewhere - the `kind="table"` filter was
rejected in RX-004 for exactly that reason.

The lexicon is deliberately small and explicit. It is not an attempt at a
financial ontology; it covers the metrics the corpus's primary statements
actually contain, and unknown metrics pass through untouched rather than being
forced into a category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Statement",
    "MetricEntry",
    "LEXICON",
    "DERIVED",
    "lookup_metric",
    "statement_hint",
    "decompose_derived",
]


class Statement:
    """Where a metric is reported. Used as a query hint, never as a hard filter."""

    BALANCE_SHEET = "consolidated balance sheet"
    PROFIT_AND_LOSS = "consolidated statement of profit and loss"
    CASH_FLOW = "consolidated statement of cash flows"
    EQUITY = "consolidated statement of changes in equity"
    # Indian banks file under a different convention: HDFC Bank's sections read
    # "Consolidated Profit and Loss Account" and "SCHEDULES TO THE CONSOLIDATED
    # BALANCE SHEET", never "Statement of Profit and Loss". Since the statement
    # is a soft query hint rather than a filter, the wrong wording costs ranking
    # rather than correctness - but it costs it on every banking question.
    PROFIT_AND_LOSS_ACCOUNT = "consolidated profit and loss account"
    SCHEDULES = "schedules to the consolidated balance sheet"


@dataclass(frozen=True)
class MetricEntry:
    canonical: str
    statement: str | None = None
    aliases: tuple[str, ...] = ()
    # What the answer should be, so a unit mismatch downstream is detectable.
    unit_kind: str = "currency"


LEXICON: tuple[MetricEntry, ...] = (
    # -- balance sheet -------------------------------------------------
    MetricEntry("total assets", Statement.BALANCE_SHEET, ("total asset",)),
    MetricEntry("total equity", Statement.BALANCE_SHEET, ("shareholders equity", "net worth")),
    MetricEntry("equity share capital", Statement.BALANCE_SHEET),
    MetricEntry("other equity", Statement.BALANCE_SHEET, ("reserves and surplus",)),
    MetricEntry("trade payables", Statement.BALANCE_SHEET,
                ("owed to suppliers", "supplier dues", "accounts payable")),
    MetricEntry("trade receivables", Statement.BALANCE_SHEET,
                ("owed by customers", "accounts receivable", "debtors")),
    MetricEntry("cash and cash equivalents", Statement.BALANCE_SHEET, ("cash balance",)),
    MetricEntry("goodwill", Statement.BALANCE_SHEET),
    MetricEntry("property, plant and equipment", Statement.BALANCE_SHEET, ("fixed assets",)),
    MetricEntry("total current liabilities", Statement.BALANCE_SHEET),
    MetricEntry("total current assets", Statement.BALANCE_SHEET),
    MetricEntry("lease liabilities", Statement.BALANCE_SHEET),
    MetricEntry("other financial liabilities", Statement.BALANCE_SHEET),
    # -- profit and loss -----------------------------------------------
    MetricEntry("revenue from operations", Statement.PROFIT_AND_LOSS,
                ("revenue", "turnover", "sales", "top line")),
    MetricEntry("total income", Statement.PROFIT_AND_LOSS),
    MetricEntry("profit for the year", Statement.PROFIT_AND_LOSS,
                ("net profit", "net income", "profit after tax", "pat", "bottom line")),
    MetricEntry("profit before tax", Statement.PROFIT_AND_LOSS, ("pbt", "pre-tax profit")),
    MetricEntry("employee benefit expenses", Statement.PROFIT_AND_LOSS,
                ("staff costs", "personnel expenses", "employee costs")),
    MetricEntry("cost of technical sub-contractors", Statement.PROFIT_AND_LOSS,
                ("subcontractor costs", "technical sub-contractors")),
    MetricEntry("finance cost", Statement.PROFIT_AND_LOSS, ("interest expense",)),
    MetricEntry("depreciation and amortization expenses", Statement.PROFIT_AND_LOSS,
                ("depreciation", "amortisation")),
    MetricEntry("total expenses", Statement.PROFIT_AND_LOSS),
    MetricEntry("other income", Statement.PROFIT_AND_LOSS),
    MetricEntry("basic earnings per share", Statement.PROFIT_AND_LOSS,
                ("basic eps", "eps"), unit_kind="ratio"),
    MetricEntry("diluted earnings per share", Statement.PROFIT_AND_LOSS,
                ("diluted eps",), unit_kind="ratio"),
    MetricEntry("total comprehensive income", Statement.PROFIT_AND_LOSS),
    MetricEntry("current tax", Statement.PROFIT_AND_LOSS),
    # -- cash flow ------------------------------------------------------
    MetricEntry("net cash used in financing activities", Statement.CASH_FLOW,
                ("financing cash flow",)),
    MetricEntry("net cash used in investing activities", Statement.CASH_FLOW,
                ("investing cash flow",)),
    MetricEntry("net cash generated from operating activities", Statement.CASH_FLOW,
                ("operating cash flow",)),
    MetricEntry("payment of dividends", Statement.CASH_FLOW,
                ("dividends paid", "dividend payout")),

    # ------------------------------------------------------------------
    # Sector coverage, added 2026-08-27 after RX-012.
    #
    # The lexicon above was built on the Infosys vertical slice and is
    # IT-centric to the point of containing "cost of technical
    # sub-contractors". That is why Reliance and Tata Motors have the thinnest
    # candidate cells, and it is a plausible reason retrieval may not
    # generalise off Infosys.
    #
    # Every entry below was confirmed to appear as a table ROW LABEL in at
    # least one filing before being added. The count in each comment is how
    # many of the five filings use it. Terms that a textbook would predict but
    # the corpus does not contain were REJECTED rather than added on faith:
    #   total deposits, gross NPA, net NPA, CASA, demand deposits, savings bank
    #   deposits, term deposits, segment revenue, total non-current liabilities.
    # Seven of those nine are banking vocabulary, which is exactly the gap this
    # block was meant to close - a reminder that the fix for "I assumed" is not
    # "assume harder".
    # ------------------------------------------------------------------

    # -- present in 4-5 filings: general ---------------------------------
    MetricEntry("investments", Statement.BALANCE_SHEET),                    # 5
    MetricEntry("deferred tax", Statement.BALANCE_SHEET,
                ("deferred tax assets", "deferred tax liabilities")),       # 5
    MetricEntry("provisions", Statement.BALANCE_SHEET),                     # 5
    MetricEntry("interest income", Statement.PROFIT_AND_LOSS),              # 5
    MetricEntry("borrowings", Statement.BALANCE_SHEET,
                ("debt", "total borrowings")),                              # 4
    MetricEntry("total liabilities", Statement.BALANCE_SHEET),              # 4
    MetricEntry("financial assets", Statement.BALANCE_SHEET),               # 4
    MetricEntry("financial liabilities", Statement.BALANCE_SHEET),          # 4
    MetricEntry("intangible assets", Statement.BALANCE_SHEET),              # 4
    MetricEntry("other expenses", Statement.PROFIT_AND_LOSS),               # 4

    # -- banking (HDFC Bank) ---------------------------------------------
    MetricEntry("deposits", Statement.SCHEDULES,
                ("customer deposits", "deposit base")),                     # 4
    MetricEntry("advances", Statement.SCHEDULES,
                ("loans and advances", "loan book")),                       # 4
    MetricEntry("interest earned", Statement.PROFIT_AND_LOSS_ACCOUNT),      # 2
    MetricEntry("interest expended", Statement.PROFIT_AND_LOSS_ACCOUNT),    # 1
    MetricEntry("net interest income", Statement.PROFIT_AND_LOSS_ACCOUNT,
                ("nii",)),                                                  # 1
    MetricEntry("provisions and contingencies",
                Statement.PROFIT_AND_LOSS_ACCOUNT),                         # 1
    MetricEntry("capital adequacy", None, ("capital adequacy ratio", "car"),
                unit_kind="percent"),                                       # 1
    MetricEntry("book value", None, ("book value per share",),
                unit_kind="ratio"),                                         # 1
    MetricEntry("dividend per share", None, ("dps",), unit_kind="ratio"),   # 1

    # -- manufacturing, auto, pharma --------------------------------------
    MetricEntry("inventories", Statement.BALANCE_SHEET, ("stock", "stock in hand")),   # 3
    MetricEntry("contingent liabilities", None),                            # 3
    MetricEntry("cost of materials consumed", Statement.PROFIT_AND_LOSS,
                ("material costs", "raw material consumed")),               # 2
    MetricEntry("changes in inventories", Statement.PROFIT_AND_LOSS),       # 2
    MetricEntry("capital work-in-progress", Statement.BALANCE_SHEET,
                ("cwip",)),                                                 # 2
    MetricEntry("revenue from contracts with customers",
                Statement.PROFIT_AND_LOSS),                                 # 2
    MetricEntry("raw materials", Statement.PROFIT_AND_LOSS),                # 2
    MetricEntry("non-controlling interests", Statement.BALANCE_SHEET,
                ("minority interest",)),                                    # 2
    MetricEntry("retained earnings", Statement.BALANCE_SHEET),              # 2
    MetricEntry("total non-current assets", Statement.BALANCE_SHEET),       # 2
    MetricEntry("exceptional items", Statement.PROFIT_AND_LOSS),            # 1
)


@dataclass(frozen=True)
class DerivedMetric:
    """A ratio that is computed, not reported - so it must be decomposed."""

    canonical: str
    components: tuple[str, ...]
    operation: str
    patterns: tuple[str, ...] = ()
    unit_kind: str = "percent"
    _compiled: tuple = field(default=(), repr=False, compare=False)


DERIVED: tuple[DerivedMetric, ...] = (
    DerivedMetric(
        "return on equity",
        ("profit for the year", "total equity"),
        "ratio",
        (r"\breturn\s+on\s+equity\b", r"\broe\b"),
    ),
    DerivedMetric(
        "return on assets",
        ("profit for the year", "total assets"),
        "ratio",
        (r"\breturn\s+on\s+assets\b", r"\broa\b"),
    ),
    DerivedMetric(
        "net profit margin",
        ("profit for the year", "revenue from operations"),
        "margin",
        (r"\bnet\s+profit\s+margin\b", r"\bprofit\s+margin\b"),
    ),
    DerivedMetric(
        "current ratio",
        ("total current assets", "total current liabilities"),
        "ratio",
        (r"\bcurrent\s+ratio\b",),
        unit_kind="ratio",
    ),
    DerivedMetric(
        # Both components confirmed present as row labels in 4 filings each, and
        # the definition is unambiguous. Other textbook ratios were left out
        # because their components are not reported as line items in this
        # corpus - a derived metric whose operands cannot be retrieved is a
        # question with no answer, not a harder question.
        "debt to equity ratio",
        ("borrowings", "total equity"),
        "ratio",
        (r"\bdebt[\s-]*(?:to|/)[\s-]*equity\b", r"\bd/e\s+ratio\b",
         r"\bgearing\b"),
        unit_kind="ratio",
    ),
    DerivedMetric(
        "goodwill as a share of total assets",
        ("goodwill", "total assets"),
        "percentage_of",
        (r"\bgoodwill\b.{0,40}\b(?:share|proportion|percentage|fraction)\b",
         r"\b(?:share|proportion|percentage|fraction)\b.{0,40}\bgoodwill\b"),
    ),
)

_ALIAS_INDEX: dict[str, MetricEntry] = {}
for _entry in LEXICON:
    _ALIAS_INDEX[_entry.canonical] = _entry
    for _alias in _entry.aliases:
        _ALIAS_INDEX.setdefault(_alias, _entry)

# Word-boundary anchored, NOT plain substring. The abbreviations in this lexicon
# are short and hide inside ordinary words: "pat" (profit after tax) matches
# inside "patents", "eps" inside "steps", "roa" inside "broad". A substring match
# on "How many patents were filed?" confidently returned "profit for the year".
# The identical bug - "in(cr)ease of 500" read as 500 crore - was caught in
# financial_value.py by probing rather than by the first test pass; this one was
# caught by a test written to assert the parser does NOT guess.
_ALIAS_PATTERNS: list[tuple[re.Pattern[str], MetricEntry, int]] = sorted(
    (
        (re.compile(rf"(?<!\w){re.escape(key)}(?!\w)", re.I), entry, len(key))
        for key, entry in _ALIAS_INDEX.items()
    ),
    key=lambda item: -item[2],
)

_DERIVED_PATTERNS = [
    (re.compile(p, re.I), d) for d in DERIVED for p in (d.patterns or (re.escape(d.canonical),))
]


def lookup_metric(text: str) -> MetricEntry | None:
    """Longest matching lexicon entry named in `text`, if any.

    Longest-first so "total current assets" is not read as "total assets", and
    "profit before tax" is not read as "profit for the year" - each pair differs
    by a large number in the same document.
    """
    for pattern, entry, _ in _ALIAS_PATTERNS:
        if pattern.search(text):
            return entry
    return None


def statement_hint(text: str) -> str | None:
    """The statement a metric mention implies, as query-text hint."""
    entry = lookup_metric(text)
    return entry.statement if entry else None


def decompose_derived(question: str) -> DerivedMetric | None:
    """The derived metric this question asks for, if it asks for one."""
    for pattern, derived in _DERIVED_PATTERNS:
        if pattern.search(question):
            return derived
    return None
