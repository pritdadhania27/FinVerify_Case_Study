"""Financial knowledge structuring (spec Module 4).

Turns extracted table rows into `FinancialFact` records carrying every field
spec §12 requires: metric, value, unit, currency, year, company, document, page,
section, table, row, column.

**The column is the point.** An Indian annual report states two years side by
side - "| Trade payables | 3,956 | 3,865 |" - and taking "the first numeric cell"
gets the current year right by convention and wrong whenever the convention is
not followed. A comparative table, a five-year summary, a note that leads with
the prior year: each produces a figure that is entirely real, entirely traceable
to a page, and answers a different question from the one asked. That is the
"restated prior-year figures" trap, and it does not announce itself - the number
is plausible, cited, and wrong.

So columns are labelled from the table's own header where one can be read, and a
fact whose year could not be established says so rather than inheriting the
document's fiscal year by assumption. `year_source` records which happened, so a
downstream consumer can require a stated year rather than trusting a default.

**Facts are extracted, never inferred.** Every value here appears in a table cell
in the source document. Nothing is computed, summed, or reconciled: a derived
figure that looks like an extracted one is indistinguishable from a
hallucination in exactly the way this project exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from backend.agents.metrics_lexicon import LEXICON
from backend.core.financial_value import (
    FinancialValue,
    ParseWarning,
    Scale,
    UnitKind,
    parse_financial_value,
)

__all__ = [
    "FinancialFact",
    "YearSource",
    "parse_row",
    "split_merged_cell",
    "years_in_cell",
    "column_years",
    "facts_from_chunk",
    "facts_from_chunks",
    "index_by_metric",
]

_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")
# A cell that is a figure: digits, separators, an optional sign or accounting
# parentheses. Deliberately strict - a cell like "2.14" that is a note reference
# is indistinguishable from a value by shape alone, which is why the note column
# is excluded by header where one exists.
_NUMERIC = re.compile(r"^[(\-−]?\s*[\d][\d,.\s]*\)?$")
# One figure inside a cell. Used to detect the MERGED-COLUMN case: Camelot
# sometimes collapses two year columns into a single cell, "76  161", and
# `parse_financial_value` then returns 76 without complaint. That is the current
# year by convention and the prior year the moment the convention does not hold -
# a figure that is real, printed, cited, and answering a different question.
_FIGURE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")
# Whole four-digit years. Non-capturing on purpose: `findall` with a capturing
# group returns the group, not the match, which silently yields ["20", "20"]
# instead of ["2024", "2023"] and makes every merged header unreadable.
_YEAR_LITERAL = re.compile(r"\b(?:19|20)\d{2}\b")
_FISCAL = re.compile(r"\b(?:FY|fy)\s?(\d{2,4})\b")
_NOTE_HEADER = re.compile(r"^\s*notes?\b", re.IGNORECASE)

_ALIASES: dict[str, str] = {}
for _entry in LEXICON:
    _ALIASES[_entry.canonical.lower()] = _entry.canonical
    for _alias in _entry.aliases:
        _ALIASES[_alias.lower()] = _entry.canonical
# Longest first, so "total current assets" wins over "total asset".
_ALIAS_ORDER = sorted(_ALIASES, key=len, reverse=True)

_UNIT_KINDS = {entry.canonical: entry.unit_kind for entry in LEXICON}

# How much extra text a row label may carry beyond the metric name before it is
# treated as a different line item. "Trade payables" matches; "Trade payables
# ageing schedule - disputed dues" must not, because the figure beside it
# answers a different question.
_LABEL_SLACK = 12

# How far into a chunk to look for the column header. See `facts_from_chunk`
# for the measurement behind the number.
_HEADER_SEARCH_ROWS = 6


def _carries_data(cells: list[str]) -> bool:
    """Does this row hold figures, as opposed to only labelling columns?

    A header cell may contain a year - that is the point - but a cell holding
    3,956 or 88,461 makes the row data. Without this, a row labelled "As at
    March 31, 2024" with values beside it would be accepted as the header and
    every column mapped to the wrong year.
    """
    for cell in cells[1:]:
        text = (cell or "").strip()
        if not text or not _NUMERIC.match(text):
            continue
        for figure in split_merged_cell(text):
            if not _YEAR_LITERAL.fullmatch(figure.strip()):
                return True
    return False


def split_merged_cell(cell: str) -> list[str]:
    """Figures inside one cell, in order.

    Returns a single-element list for an ordinary cell. Two or more elements
    mean the extractor merged adjacent columns, which must never be resolved by
    taking the first: see `_FIGURE`.
    """
    return _FIGURE.findall(cell.strip())


class YearSource:
    """Where a fact's year came from. Recorded, never assumed away."""

    COLUMN_HEADER = "column_header"
    DOCUMENT = "document_fiscal_year"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FinancialFact:
    """One figure, with everything needed to find it again and to trust it."""

    metric: str
    value: FinancialValue
    document_id: str
    row_label: str
    page: int | None = None
    section: str | None = None
    table_index: int | None = None
    column_index: int | None = None
    column_label: str | None = None
    year: str | None = None
    year_source: str = YearSource.UNKNOWN
    company: str | None = None
    chunk_id: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unit(self) -> str:
        if self.value.unit_kind is UnitKind.PERCENT:
            return "percent"
        parts = [p for p in (self.value.currency, self.value.scale.label) if p]
        return " ".join(parts) or "unit"

    @property
    def currency(self) -> str | None:
        return self.value.currency

    @property
    def canonical(self) -> Decimal:
        return self.value.canonical()

    @property
    def year_is_stated(self) -> bool:
        """True only when the table itself said which year this column is.

        A consumer that needs a year-specific figure should require this rather
        than reading `year`, which may be the document's fiscal year applied as a
        fallback.
        """
        return self.year_source == YearSource.COLUMN_HEADER

    @property
    def citation(self) -> str:
        parts = [f"p.{self.page}" if self.page is not None else "page unknown"]
        if self.section:
            parts.append(self.section)
        if self.table_index is not None:
            parts.append(f"table {self.table_index}")
        if self.column_label:
            parts.append(f"column {self.column_label!r}")
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "value": str(self.value.amount),
            "canonical": str(self.canonical),
            "unit": self.unit,
            "currency": self.currency,
            "year": self.year,
            "year_source": self.year_source,
            "company": self.company,
            "document_id": self.document_id,
            "page": self.page,
            "section": self.section,
            "table": self.table_index,
            "row": self.row_label,
            "column": self.column_label,
            "column_index": self.column_index,
            "chunk_id": self.chunk_id,
            "citation": self.citation,
            "warnings": list(self.warnings),
        }


def parse_row(line: str) -> list[str] | None:
    match = _ROW.match(line.strip())
    if not match:
        return None
    return [cell.strip() for cell in match.group("cells").split("|")]


# An Indian fiscal-year span: "2022-23", "2022/23", "2022-2023". It names the
# year the fiscal year ENDS in, which is the convention every other header in
# this corpus uses ("As at March 31, 2024" -> 2024).
_FISCAL_SPAN = re.compile(r"\b((?:19|20)\d{2})\s*[-/]\s*(\d{2}|\d{4})\b")


def fiscal_span_end(text: str) -> str | None:
    """The calendar year an Indian fiscal-year span ends in, or None.

    "2022-23" ends on 31 March 2023, so it is 2023. Reading it as 2022 - which
    is what taking the first four-digit literal does - labels the column a
    year early, and the question generated from it asks about the wrong year
    while pointing at a real figure. A validator caught six of these on
    Reliance and rejected all six.

    The two-digit tail is completed against the START year's century, not the
    current one, so "1999-00" is 2000 rather than 2100.
    """
    match = _FISCAL_SPAN.search(text)
    if not match:
        return None
    start, tail = match.group(1), match.group(2)
    if len(tail) == 4:
        return tail
    century, start_tail = int(start[:2]), int(start[2:])
    end_tail = int(tail)
    # A tail lower than the start means the century rolled over: 1999-00.
    if end_tail < start_tail:
        century += 1
    return f"{century:02d}{end_tail:02d}"


def _normalise_year(text: str) -> str | None:
    """A four-digit year from a header cell, or None.

    "as at March 31, 2024" -> "2024". "FY24" -> "2024". A cell naming two years
    ("2024 2023") is ambiguous as a single column label and yields None: guessing
    which one it means is the error this function exists to avoid.
    """
    # A fiscal span is checked FIRST: "2022-23" contains exactly one four-digit
    # literal, so the literal branch below would read it as 2022 and be a year
    # early on every Indian fiscal-year column in the corpus.
    span = fiscal_span_end(text)
    if span:
        return span
    full = _YEAR_LITERAL.findall(text)
    if len(full) == 1:
        return full[0]
    if len(full) > 1:
        return None
    fiscal = _FISCAL.search(text)
    if fiscal:
        digits = fiscal.group(1)
        return f"20{digits}" if len(digits) == 2 else digits
    return None


def years_in_cell(text: str) -> list[str]:
    """Every four-digit year named in one header cell, in order.

    The companion to `split_merged_cell`. When the extractor merges two year
    columns it usually merges their header too - "2024 2023" over "76  161" -
    and the two lists then line up positionally. That recovers the year without
    guessing: the pairing is stated by the document, not assumed by convention.
    """
    return _YEAR_LITERAL.findall(text)


def column_years(header: list[str]) -> dict[int, str]:
    """Column index -> year, from a table header row.

    Only columns whose header names exactly one year are mapped. A header that
    could not be read leaves the column unmapped, and a fact from an unmapped
    column carries `year_source = unknown` rather than a plausible guess.
    """
    mapping: dict[int, str] = {}
    for index, cell in enumerate(header):
        year = _normalise_year(cell)
        if year:
            mapping[index] = year
    return mapping


def _match_metric(label: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9,\s\-]", " ", label.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    for alias in _ALIAS_ORDER:
        if (cleaned == alias or cleaned.startswith(alias + " ")) and len(cleaned) <= len(
            alias
        ) + _LABEL_SLACK:
            return _ALIASES[alias]
    return None


def _note_columns(header: list[str]) -> set[int]:
    """Columns that hold note references rather than figures.

    "| Particulars | Note | 2024 | 2023 |" has a note column whose values look
    exactly like small figures. Treating 2.14 as an amount produces a number that
    is real, printed on the page, and entirely wrong.
    """
    return {index for index, cell in enumerate(header) if _NOTE_HEADER.match(cell)}


# A cell carrying three or more consecutive letters is a LABEL, not a value
# and not a note reference. The threshold matters: HDFC Bank writes schedule
# references as "18 (4)", which must not be read as the start of a new
# column block, while "Total Equity" must.
_LABELISH = re.compile(r"[A-Za-z]{3,}")


def row_segments(cells: list[str]) -> list[tuple[int, range]]:
    """Split a row into (label position, the value columns belonging to it).

    Indian balance sheets are frequently printed as TWO PANELS side by side -
    assets on the left, equity and liabilities on the right - and extraction
    flattens both into one row:

        | Goodwill |  | 14,989 | 15,270 | Total Equity |  | 9,25,788 | 8,28,881 |

    Reading `cells[0]` as the label and then scanning every numeric cell to
    the end of the row attributes the RIGHT panel figures to the LEFT panel
    metric. On Reliance p110 that gave goodwill four year-stated facts:
    14,989 and 15,270, which are goodwill, and 9,25,788 and 8,28,881, which
    are total equity - a 62x error carrying a correct-looking page, row and
    year, and indistinguishable from a right answer downstream.

    Segmenting on label cells fixes that and unlocks the right panel at the
    same time: equity, borrowings and payables all live there, and none of
    them was previously extractable from a two-panel balance sheet at all.
    """
    labels = [i for i, cell in enumerate(cells) if cell and _LABELISH.search(cell)]
    segments = []
    for position, start in enumerate(labels):
        stop = labels[position + 1] if position + 1 < len(labels) else len(cells)
        segments.append((start, range(start + 1, stop)))
    return segments


def facts_from_chunk(
    chunk: dict, *, inherited_years: dict[int, str] | None = None
) -> list[FinancialFact]:
    """Structured facts from one table chunk.

    A non-table chunk yields nothing: prose figures lack the row/column
    provenance spec §12 requires, and a fact without provenance is a number.

    `inherited_years` carries the column-year map from an EARLIER PART OF
    THE SAME TABLE, and is used only when this part names no year itself.
    The chunker repeats a table preamble in every part - the caption, the
    unit banner, the rule - but not the year-header row, so the second part
    of a split balance sheet contains no year anywhere. HDFC Bank's
    consolidated balance sheet splits exactly there: part 1 carries the
    header and the liabilities, part 2 carries Advances and Investments and
    names no year. Every figure in part 2 therefore fell back to the
    document fiscal year with `year_is_stated` False, the dataset generator
    requires a stated year, and so it skipped the consolidated balance sheet
    and took its answers from a front-of-report summary table instead. That
    is RX-026 one layer down: the wrong-section answers were a SYMPTOM of
    the right section being unreadable.
    """
    if chunk.get("kind") != "table":
        return []

    lines = [line for line in (chunk.get("text") or "").splitlines() if line.strip()]
    rows = [cells for cells in (parse_row(line) for line in lines) if cells]
    if not rows:
        return []

    # The header is the first row that names a year AND is not itself data.
    #
    # Searched over the first `_HEADER_SEARCH_ROWS` rather than only the first
    # few: the chunker repeats a table's header in every part, but a part often
    # opens with a caption, a unit banner and a spacer before it. Measured over
    # the 13,559 table chunks in this corpus, widening the window from 3 rows to
    # 6 took year-header coverage from 18.1% to 32.1%; 10 rows added only
    # another 5.5 points, so the window stops at 6.
    #
    # The data-row guard is what makes widening safe. A row labelled "As at
    # March 31, 2024" carrying figures names a year and is NOT a header, and
    # accepting it would map every column to the wrong year - which is precisely
    # the silent prior-year error this module exists to prevent.
    header: list[str] = []
    years: dict[int, str] = {}
    for cells in rows[:_HEADER_SEARCH_ROWS]:
        candidate = column_years(cells)
        if candidate and not _carries_data(cells):
            header, years = cells, candidate
            break
    if not header:
        header = rows[0]
        if inherited_years:
            # A part that has its own header always wins, so parts whose
            # columns genuinely differ are never overwritten by a sibling.
            years = dict(inherited_years)
    skip = _note_columns(header)

    context_scale = chunk.get("context_scale")
    context_currency = chunk.get("context_currency")
    scale = next((s for s in Scale if s.label == context_scale), None)

    facts: list[FinancialFact] = []
    for cells in rows:
        if not cells:
            continue
        for label_at, value_columns in row_segments(cells):
            metric = _match_metric(cells[label_at])
            if not metric:
                continue
            row_label = cells[label_at]
            expected = _UNIT_KINDS.get(metric, "currency")
            for index in value_columns:
                cell = cells[index]
                if index in skip or not cell or not _NUMERIC.match(cell):
                    continue
                figures = split_merged_cell(cell)
                merged = len(figures) > 1
                for offset, figure in enumerate(figures):
                    value = parse_financial_value(
                        figure,
                        context_scale=scale if expected == "currency" else None,
                        context_currency=context_currency if expected == "currency" else None,
                        context_unit_kind=(UnitKind.RATIO if expected == "ratio" else None),
                    )
                    if value is None:
                        continue
                    # A merged cell holds several columns' figures. Attributing them
                    # positionally is only safe when the header actually named that
                    # many year columns; otherwise the year is UNKNOWN and the fact
                    # carries the warning rather than a plausible guess.
                    if merged:
                        # A merged header cell names the same number of years as the
                        # merged value cell holds figures: pair them positionally.
                        header_cell = header[index] if index < len(header) else ""
                        named = years_in_cell(header_cell)
                        year = (
                            named[offset]
                            if len(named) == len(figures)
                            else years.get(index + offset)
                        )
                    else:
                        year = years.get(index)
                    warnings = [
                        w.value for w in value.warnings if isinstance(w, ParseWarning)
                    ]
                    if merged:
                        warnings.append("merged_columns")
                        if year is None:
                            warnings.append("year_not_resolvable_in_merged_cell")
                    facts.append(
                        FinancialFact(
                            metric=metric,
                            value=value,
                            document_id=chunk.get("document_id", ""),
                            row_label=row_label,
                            page=chunk.get("page"),
                            section=chunk.get("section"),
                            table_index=chunk.get("table_index"),
                            column_index=index + offset,
                            column_label=(
                                header[index + offset]
                                if index + offset < len(header)
                                else None
                            ),
                            year=year or (None if merged else chunk.get("fiscal_year")),
                            year_source=(
                                YearSource.COLUMN_HEADER if year
                                else YearSource.UNKNOWN if merged
                                else YearSource.DOCUMENT if chunk.get("fiscal_year")
                                else YearSource.UNKNOWN
                            ),
                            company=chunk.get("company"),
                            chunk_id=chunk.get("chunk_id"),
                            warnings=tuple(warnings),
                        )
                    )
    return facts


def _table_key(chunk: dict) -> str | None:
    """The table a chunk belongs to, ignoring which part it is.

    "doc:p412:t0:1" -> "doc:p412:t0". Parts of one table share it. Anything
    not shaped like a table chunk id returns None and inherits nothing.
    """
    chunk_id = chunk.get("chunk_id") or ""
    head, _, tail = chunk_id.rpartition(":")
    return head if head and tail.isdigit() else None


def facts_from_chunks(chunks: list[dict]) -> list[FinancialFact]:
    """Facts from every chunk, with column years carried across the parts
    of a split table.

    Chunks are processed in the order given, which is the order they were
    written, so an earlier part reaches later parts and never the reverse.
    """
    out: list[FinancialFact] = []
    header_years: dict[str, dict[int, str]] = {}
    for chunk in chunks:
        key = _table_key(chunk)
        facts = facts_from_chunk(chunk, inherited_years=header_years.get(key))
        if key and key not in header_years:
            stated = {
                fact.column_index: fact.year
                for fact in facts
                if fact.year_is_stated and fact.column_index is not None and fact.year
            }
            if stated:
                header_years[key] = stated
        out.extend(facts)
    return out


def index_by_metric(
    facts: list[FinancialFact], *, year: str | None = None, require_stated_year: bool = False
) -> dict[str, list[FinancialFact]]:
    """Group facts by metric, optionally filtered to one year.

    `require_stated_year` drops facts whose year came from the document rather
    than the table header. Use it wherever the year matters to the answer: it
    trades coverage for the guarantee that no figure is attributed to a year the
    document did not put beside it.
    """
    grouped: dict[str, list[FinancialFact]] = {}
    for fact in facts:
        if year is not None and fact.year != year:
            continue
        if require_stated_year and not fact.year_is_stated:
            continue
        grouped.setdefault(fact.metric, []).append(fact)
    return grouped
