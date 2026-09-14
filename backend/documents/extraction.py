"""Document intelligence: PDF to structured, provenance-bearing content (Module 3).

Every item this module emits carries where it came from - document, page, and for
table cells the table index, row and column. Spec section 11 requires provenance
on every extracted item, and it is also what makes an answer auditable: "revenue
was 1,53,670 crore" is a claim, while "revenue was 1,53,670 crore, page 14, table
2, row 3" is a checkable one.

The most valuable thing here is **table scale context detection**. Financial
tables state their scale once, in a caption or header - "(Rs. in crore)" - and
then omit it from all 200 cells beneath. A cell parsed in isolation therefore has
no recoverable magnitude, and assuming units yields answers wrong by a factor of
ten million. This module finds that caption and threads it into every cell of the
table it governs.

**Camelot in `stream` mode is the primary table extractor** (decision D10,
reversed on measurement). The original plan was pdfplumber, on the reasoning that
Camelot needs Ghostscript. Measuring it on the real report showed two things: the
Ghostscript dependency applies only to `lattice` mode, not `stream`; and
pdfplumber cannot read these tables at all. On the consolidated balance sheet,
pdfplumber recovered the row labels but **every numeric column came back empty**,
while Camelot returned `['Equity share capital', '2.12', '2,071', '2,069']` at
100% parsing accuracy. Indian filings set financial statements as whitespace-
aligned text with no ruling lines, which is precisely the case `stream` targets
and line-detection cannot see.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from backend.core.financial_value import Scale

__all__ = [
    "Provenance",
    "PageText",
    "ExtractedTable",
    "ExtractedDocument",
    "ExtractionQuality",
    "detect_scale_context",
    "extract_document",
]

# Explicit unit *declarations* - "(In ₹ crore)", "Rs. in lakhs". Strong enough to
# apply to a whole page, because this form is a statement about units rather than
# prose that happens to contain the word.
_SCALE_DECLARATION_PATTERNS: list[tuple[re.Pattern[str], Scale]] = [
    (re.compile(r"\bin\s+(?:rs\.?|inr|₹|usd|\$)?\s*crores?\b", re.I), Scale.CRORE),
    (re.compile(r"\bin\s+(?:rs\.?|inr|₹|usd|\$)?\s*lakhs?\b", re.I), Scale.LAKH),
    (re.compile(r"\bin\s+(?:rs\.?|inr|₹|usd|\$)?\s*millions?\b", re.I), Scale.MILLION),
    (re.compile(r"\bin\s+(?:rs\.?|inr|₹|usd|\$)?\s*billions?\b", re.I), Scale.BILLION),
    (re.compile(r"\bin\s+(?:rs\.?|inr|₹|usd|\$)?\s*thousands?\b", re.I), Scale.THOUSAND),
    (re.compile(r"\((?:rs\.?|inr|₹|usd|\$)\s*(?:in\s*)?crores?\)", re.I), Scale.CRORE),
    (re.compile(r"\((?:rs\.?|inr|₹|usd|\$)\s*(?:in\s*)?lakhs?\)", re.I), Scale.LAKH),
    (re.compile(r"\((?:rs\.?|inr|₹|usd|\$)\s*(?:in\s*)?millions?\)", re.I), Scale.MILLION),
]

# Looser forms, used only against the short caption window immediately around a
# table where a bare "crore" is very likely to be the unit label.
_SCALE_CONTEXT_PATTERNS: list[tuple[re.Pattern[str], Scale]] = [
    *_SCALE_DECLARATION_PATTERNS,
    (re.compile(r"\bcrores?\b", re.I), Scale.CRORE),
    (re.compile(r"\blakhs?\b", re.I), Scale.LAKH),
    (re.compile(r"\bmillions?\b", re.I), Scale.MILLION),
    (re.compile(r"\bbillions?\b", re.I), Scale.BILLION),
]

_CURRENCY_CONTEXT = [
    (re.compile(r"₹|\brs\.?\b|\binr\b", re.I), "INR"),
    (re.compile(r"\$|\busd\b", re.I), "USD"),
    (re.compile(r"€|\beur\b", re.I), "EUR"),
]

# Heading heuristics for section detection: short lines, title case or all caps,
# no terminal full stop.
_HEADING_MAX_WORDS = 12
# NOT re.I: the trailing [A-Z] is the whole discriminator. Case-insensitively,
# "3 years" is a note heading, which is how the trade receivables note came to be
# labelled "3 years" - a fragment of the ageing schedule's column header.
_NOTE_HEADING = re.compile(r"^\s*(?:[Nn]ote\s+)?\d+(?:\.\d+)*\s+[A-Z]")

# Lowercase in a title case heading by convention, so they must not count
# against the capitalisation ratio: "Consolidated Statement of Profit and Loss"
# is a heading, and "of"/"and" were dropping it below the threshold.
_TITLE_FUNCTION_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
     "the", "to", "with"}
)

# No heading ends on one of these; a line that does is a wrapped sentence whose
# remainder is on the next line.
_TRAILING_FRAGMENT_WORDS = _TITLE_FUNCTION_WORDS | {"is", "are", "was", "were", "under",
                                                    "over", "into", "that", "which"}

# A cell that is only a figure: "2.18", "153,670", "(1,234)", "12.4%", "-", "24".
_NUMERIC_CELL = re.compile(r"^[\s(₹$€]*[-–—]?[\d][\d,.\s]*\)?%?$")


@dataclass(frozen=True)
class Provenance:
    """Where a piece of content came from. Travels with the content."""

    document_id: str
    page: int  # 1-indexed, matching what a reader sees
    section: str | None = None
    table_index: int | None = None
    row: int | None = None
    column: int | None = None

    def cite(self) -> str:
        parts = [f"p.{self.page}"]
        if self.section:
            parts.append(self.section)
        if self.table_index is not None:
            cell = f"table {self.table_index + 1}"
            if self.row is not None and self.column is not None:
                cell += f" r{self.row + 1}c{self.column + 1}"
            parts.append(cell)
        return ", ".join(parts)


@dataclass(frozen=True)
class PageText:
    page: int
    text: str
    section: str | None
    provenance: Provenance
    replacement_chars: int = 0


@dataclass(frozen=True)
class ExtractedTable:
    """A table plus the scale/currency context that governs its cells."""

    page: int
    table_index: int
    rows: tuple[tuple[str, ...], ...]
    header: tuple[str, ...] | None
    caption: str | None
    context_scale: Scale | None
    context_currency: str | None
    section: str | None
    provenance: Provenance
    # Camelot's own confidence in this parse. Surfaced rather than discarded so a
    # badly-parsed table can be down-weighted instead of silently trusted.
    parsing_accuracy: float = 0.0
    # Page that actually declared the scale. When it differs from `page`, the
    # scale was inherited from an earlier page of a multi-page statement, which a
    # reader may want to verify rather than trust.
    scale_source_page: int | None = None
    # How many leading rows `header` was merged from, so the chunker can drop
    # them from the body instead of repeating them.
    header_rows: int = 0

    @property
    def scale_was_inherited(self) -> bool:
        return self.scale_source_page is not None and self.scale_source_page != self.page

    def cell_provenance(self, row: int, column: int) -> Provenance:
        return Provenance(
            self.provenance.document_id, self.page, self.section,
            self.table_index, row, column,
        )

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), max((len(r) for r in self.rows), default=0))


@dataclass(frozen=True)
class ExtractionQuality:
    """Spec section 11 requires extraction quality checks. These are them."""

    pages_total: int
    pages_with_text: int
    tables_found: int
    tables_with_scale_context: int
    replacement_chars: int
    repeated_lines_removed: int
    mean_table_accuracy: float = 0.0
    problems: tuple[str, ...] = ()

    @property
    def scale_context_coverage(self) -> float:
        return self.tables_with_scale_context / self.tables_found if self.tables_found else 0.0


@dataclass(frozen=True)
class ExtractedDocument:
    document_id: str
    pages: tuple[PageText, ...]
    tables: tuple[ExtractedTable, ...]
    sections: tuple[str, ...]
    quality: ExtractionQuality
    headers_footers: tuple[str, ...] = field(default_factory=tuple)

    def page(self, number: int) -> PageText | None:
        return next((p for p in self.pages if p.page == number), None)

    def tables_on_page(self, number: int) -> list[ExtractedTable]:
        return [t for t in self.tables if t.page == number]


def detect_scale_context(text: str, *, strict: bool = False) -> tuple[Scale | None, str | None]:
    """Find the scale and currency declared in `text`.

    This is what turns an unlabelled "1,53,670" into "1,53,670 crore". Without
    it, a cell has no recoverable magnitude and every downstream calculation is
    a guess dressed as a number.

    `strict=True` requires an explicit declaration form ("in ₹ crore"), which is
    what a whole page of prose needs - a bare mention of "crore" in a narrative
    sentence is not a statement about the units of a nearby table.
    """
    patterns = _SCALE_DECLARATION_PATTERNS if strict else _SCALE_CONTEXT_PATTERNS
    scale = next((s for pattern, s in patterns if pattern.search(text)), None)
    currency = next((c for pattern, c in _CURRENCY_CONTEXT if pattern.search(text)), None)
    return scale, currency


def _page_scale_contexts(
    page_texts: list[str],
) -> list[tuple[Scale | None, str | None, int | None]]:
    """Resolve each page's effective scale, carrying declarations forward.

    Financial statements span several pages and declare their units **once**, on
    the first. In the Infosys FY2023-24 filing the consolidated balance sheet
    runs across two pages: page 11 says "(In ₹ crore)", and page 12 -
    "Consolidated Balance Sheet (contd.)" - says nothing at all. Reading page 12
    in isolation leaves 88,461 as a bare number, and treating a crore figure as
    units is a 10,000,000x error.

    Carry-forward is therefore necessary, but it is also a guess, so the page
    that actually made the declaration is recorded alongside it. A downstream
    reader can then check whether the inheritance was reasonable instead of
    taking it on trust.
    """
    resolved: list[tuple[Scale | None, str | None, int | None]] = []
    current: tuple[Scale | None, str | None, int | None] = (None, None, None)
    for index, text in enumerate(page_texts):
        scale, currency = detect_scale_context(text, strict=True)
        if scale is not None:
            current = (scale, currency, index + 1)
        resolved.append(current)
    return resolved


def _find_repeated_lines(page_texts: list[str], threshold: float = 0.5) -> set[str]:
    """Lines appearing on most pages are running headers/footers, not content."""
    if len(page_texts) < 4:
        return set()
    counts: Counter[str] = Counter()
    for text in page_texts:
        lines = {ln.strip() for ln in text.splitlines() if 3 < len(ln.strip()) < 120}
        counts.update(lines)
    cutoff = max(3, int(len(page_texts) * threshold))
    # Pure page numbers vary per page and so never reach the threshold anyway;
    # this guards the case where a header embeds a constant.
    return {line for line, n in counts.items() if n >= cutoff and not line.isdigit()}


def _looks_like_heading(line: str) -> bool:
    """Is this line a section heading, or a table row label that looks like one?

    Measured against the Infosys filing, the original rule tagged the profit and
    loss statement's section as "Expenses" and the trade receivables note's as
    "3 years" - single Title-Case words lifted out of table rows. Since the
    section is written into every chunk's title and into its citation, a wrong
    one is worse than none: it misdescribes the evidence a reader is asked to
    check.

    Two changes fixed it. A single Title-Case word is no longer enough (real
    statement titles are multi-word), and lowercase function words no longer
    count against the capitalisation ratio - "Consolidated Statement of Profit
    and Loss" is title case, but "of" and "and" dragged it to 0.67 and it was
    being rejected while "Expenses" was accepted.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 90 or stripped.endswith("."):
        return False
    if len(stripped.split()) > _HEADING_MAX_WORDS:
        return False

    # A heading is a complete phrase. These are the marks of a fragment: a
    # wrapped sentence ("... Stater digital platform and"), a column header
    # carried over from a table ("As at March 31,"), or a mid-phrase cut that
    # closes a bracket it never opened.
    if stripped[-1] in ",;:" or stripped.split()[-1].lower() in _TRAILING_FRAGMENT_WORDS:
        return False
    if stripped.count(")") > stripped.count("("):
        return False

    if _NOTE_HEADING.match(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    if all(c.isupper() for c in letters) and len(letters) > 3:
        return True

    words = [w for w in stripped.split() if w[:1].isalpha()]
    significant = [w for w in words if w.lower() not in _TITLE_FUNCTION_WORDS]
    # Two content words minimum. One is how "Expenses" became the section name of
    # the profit and loss statement, and how "March" (in "As at March 31,")
    # became the section name of half the notes.
    if len(significant) < 2:
        return False
    return sum(w[0].isupper() for w in significant) / len(significant) >= 0.7


def _is_data_row(row: tuple[str, ...]) -> bool:
    """A labelled row carrying at least one figure - i.e. table body, not header."""
    cells = [c.strip() for c in row]
    if not cells or not cells[0]:
        return False
    return any(_NUMERIC_CELL.match(c) for c in cells[1:] if c)


def _merge_header_rows(rows: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...] | None, int]:
    """The table's header, flattened into one row, plus how many rows it spans.

    Financial statements carry a *multi-row* header: a unit caption, then column
    names, then the fiscal years. Camelot returns them as separate rows, and
    taking only the first one - which is what this used to do - picked up
    "(In crore, except equity share and per equity share data)" and threw away
    "Year ended March 31, / 2024 2023".

    That is not merely a ranking problem. The header is repeated into every part
    of a split table, so with the wrong header chosen, part 2 of the profit and
    loss statement read "Profit for the year | 26,248  24,108" with nothing
    anywhere in the chunk saying which column is which year. A number whose
    period cannot be recovered is worse than a missing number.
    """
    if not rows:
        return None, 0

    first_data = next((i for i, row in enumerate(rows[:4]) if _is_data_row(row)), None)
    if first_data is None:
        # Nothing that looks like a data row near the top - this is probably not
        # a figures table at all. Fall back rather than inventing structure.
        return (rows[0] if any(rows[0]) else None), 0
    if first_data == 0:
        # The table opens straight into data: it has no header. Saying so beats
        # promoting a row of figures to header and repeating it into every part.
        return None, 0

    header_rows = rows[:first_data]
    width = max(len(r) for r in header_rows)
    merged = tuple(
        " ".join(
            part
            for row in header_rows
            for part in [row[column].strip() if column < len(row) else ""]
            if part
        )
        for column in range(width)
    )
    return (merged if any(merged) else None), first_data


def _section_for_page(text: str, current: str | None) -> str | None:
    """Track the most recent heading; carry it forward across pages."""
    for line in text.splitlines():
        if _looks_like_heading(line):
            current = line.strip()
    return current


def _caption_for_table(page_text: str, table_rows: list[list[str]]) -> str:
    """Approximate a table's caption from the page text preceding its first cell.

    pdfplumber gives table bounding boxes, but correlating them with PyMuPDF's
    text stream is fragile across differing coordinate handling. Searching the
    page text for the first cell value and taking the preceding window is less
    precise but degrades gracefully, and a caption is a hint rather than an
    authority - a scale stated in the cell itself always wins (see
    parse_financial_value, where an explicit scale beats context).
    """
    first_cell = next(
        (c.strip() for row in table_rows for c in row if c and c.strip()), None
    )
    if not first_cell:
        return page_text[:400]
    idx = page_text.find(first_cell[:40])
    if idx == -1:
        return page_text[:400]
    return page_text[max(0, idx - 400) : idx + 120]


def extract_document(
    path: str,
    document_id: str,
    *,
    max_pages: int | None = None,
    extract_tables: bool = True,
) -> ExtractedDocument:
    """Extract text, tables, sections and quality metrics from a PDF."""
    problems: list[str] = []

    # Reported, not raised: an unreadable document is an expected pipeline event
    # that belongs in the quality report, not an exception that aborts a batch.
    try:
        doc = pymupdf.open(path)
        limit = min(doc.page_count, max_pages) if max_pages else doc.page_count
        raw_texts = [doc[i].get_text() for i in range(limit)]
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return ExtractedDocument(
            document_id=document_id,
            pages=(),
            tables=(),
            sections=(),
            quality=ExtractionQuality(
                pages_total=0, pages_with_text=0, tables_found=0,
                tables_with_scale_context=0, replacement_chars=0,
                repeated_lines_removed=0,
                problems=(f"could not open document: {exc}",),
            ),
        )

    repeated = _find_repeated_lines(raw_texts)
    removed_count = 0

    pages: list[PageText] = []
    sections: list[str] = []
    current_section: str | None = None

    for index, raw in enumerate(raw_texts):
        kept_lines = []
        for line in raw.splitlines():
            if line.strip() in repeated:
                removed_count += 1
                continue
            kept_lines.append(line)
        cleaned = "\n".join(kept_lines)

        current_section = _section_for_page(cleaned, current_section)
        if current_section and (not sections or sections[-1] != current_section):
            sections.append(current_section)

        # U+FFFD means the PDF's glyph mapping lost information. It cannot be
        # recovered here, but a silent replacement character inside a number
        # would corrupt a value, so the count is reported as a quality signal.
        replacements = cleaned.count("�")

        page_no = index + 1
        pages.append(
            PageText(
                page=page_no,
                text=cleaned,
                section=current_section,
                provenance=Provenance(document_id, page_no, current_section),
                replacement_chars=replacements,
            )
        )

    # Resolved before tables, because a continuation page's units live on the
    # page where the statement began.
    page_contexts = _page_scale_contexts(raw_texts)

    tables: list[ExtractedTable] = []
    accuracies: list[float] = []
    if extract_tables:
        try:
            # Imported here, not at module scope. Camelot is a heavy dependency
            # (Ghostscript-adjacent, pulls in cv2) and nothing else in this
            # module needs it - but a module-level import made `from
            # backend.rag.indexing import QdrantIndex` require it transitively,
            # so a service that only counts vectors could not start without a
            # PDF table parser. Worse, the failure surfaced as "the index is
            # unreachable": a missing dependency reported as a broken service.
            import camelot

            found = camelot.read_pdf(path, pages=f"1-{limit}", flavor="stream")
        except Exception as exc:  # noqa: BLE001
            found = []
            problems.append(f"camelot table extraction failed: {exc}")

        per_page_index: dict[int, int] = {}
        for table in found:
            try:
                page_no = int(table.page)
                frame = table.df
            except Exception as exc:  # noqa: BLE001
                problems.append(f"unreadable table object: {exc}")
                continue

            rows = tuple(
                tuple(str(cell).strip() for cell in record)
                for record in frame.values.tolist()
            )
            if not rows:
                continue

            table_index = per_page_index.get(page_no, 0)
            per_page_index[page_no] = table_index + 1

            page_text = raw_texts[page_no - 1] if 0 < page_no <= len(raw_texts) else ""
            section = next((p.section for p in pages if p.page == page_no), None)
            caption = _caption_for_table(page_text, [list(r) for r in rows])

            # A declaration in the table's own caption is authoritative; the
            # page-level context (possibly inherited from an earlier page of a
            # multi-page statement) is the fallback.
            scale, currency = detect_scale_context(caption)
            scale_source = page_no if scale is not None else None
            if scale is None and 0 < page_no <= len(page_contexts):
                scale, page_currency, scale_source = page_contexts[page_no - 1]
                currency = currency or page_currency

            report = getattr(table, "parsing_report", {}) or {}
            accuracy = float(report.get("accuracy", 0.0))
            accuracies.append(accuracy)
            if accuracy < 80.0:
                problems.append(
                    f"low table parsing accuracy {accuracy:.1f} on page {page_no} "
                    f"table {table_index + 1}"
                )

            header, header_row_count = _merge_header_rows(rows)
            tables.append(
                ExtractedTable(
                    page=page_no,
                    table_index=table_index,
                    rows=rows,
                    header=header,
                    header_rows=header_row_count,
                    caption=caption.strip()[:300] or None,
                    context_scale=scale,
                    context_currency=currency,
                    section=section,
                    parsing_accuracy=accuracy,
                    scale_source_page=scale_source,
                    provenance=Provenance(document_id, page_no, section, table_index),
                )
            )

    quality = ExtractionQuality(
        pages_total=limit,
        pages_with_text=sum(1 for p in pages if len(p.text.strip()) > 100),
        tables_found=len(tables),
        tables_with_scale_context=sum(1 for t in tables if t.context_scale is not None),
        replacement_chars=sum(p.replacement_chars for p in pages),
        repeated_lines_removed=removed_count,
        mean_table_accuracy=(sum(accuracies) / len(accuracies)) if accuracies else 0.0,
        problems=tuple(problems),
    )

    return ExtractedDocument(
        document_id=document_id,
        pages=tuple(pages),
        tables=tuple(tables),
        sections=tuple(sections),
        quality=quality,
        headers_footers=tuple(sorted(repeated)),
    )
