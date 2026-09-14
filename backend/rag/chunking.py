"""Chunking for financial documents (spec Module 6).

Generic chunkers split on character counts. Applied to a financial statement that
destroys the thing being retrieved: a table cut in half leaves rows whose column
headers are in a different chunk, so "2,071" arrives with no indication that it
is equity share capital, in crore, for FY2024. Retrieval then returns a number
with no meaning and the reasoning channels invent one.

So tables are chunked as **tables**:

* one chunk per table wherever it fits;
* oversized tables split by row, with the header **and the unit declaration
  repeated** in every part;
* the scale context resolved in Module 3 - including the carry-forward from an
  earlier page (decision D13) - is rendered into the chunk text itself.

That last point is what makes a retrieved table chunk self-describing. The units
of a continuation page live on a page that will not be retrieved with it, so if
they are not written into the chunk they are gone.

Provenance travels with every chunk, so any retrieved evidence can cite its
document, page, section and table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from backend.core.financial_value import Scale
from backend.documents.extraction import ExtractedDocument, ExtractedTable, Provenance

__all__ = [
    "ChunkKind",
    "Chunk",
    "chunk_document",
    "serialise_table",
    "serialise_chunk",
    "is_table_spillover",
    "deserialise_chunk",
    "is_prose",
]

# BGE/E5 base models truncate at 512 tokens, silently. The character budgets
# below are set from MEASURED tokens-per-character on this corpus, not from the
# usual ~4 chars/token rule of thumb:
#
#   prose        ~4.0 chars/token
#   pipe tables  ~1.4 chars/token  <- digits, commas and '|' all tokenize densely
#
# At the original 1,800-char table budget, 20 of 234 chunks exceeded 512 tokens
# (the worst at 1,336) and lost their trailing rows from the vector with no
# error raised. 700 characters keeps dense table text inside the window.
DEFAULT_MAX_CHARS = 1_000
DEFAULT_OVERLAP = 120
TABLE_MAX_CHARS = 700

# A "paragraph" of page text that is mostly digits is table spillover: PyMuPDF's
# page text includes table cells as loose runs, and a run of figures separated by
# single newlines survives paragraph splitting. Indexed as text it competes with
# - and beats - the properly structured table chunk covering the same numbers,
# because a bare "86,045" is a tighter match for a numeric query than a full
# table is. Retrieval then returns a number stripped of the label, units and
# year that give it meaning.
MIN_ALPHA_RATIO = 0.45
MIN_WORDS = 8

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class ChunkKind(str, Enum):
    TEXT = "text"
    TABLE = "table"


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit that can always cite where it came from."""

    chunk_id: str
    kind: ChunkKind
    text: str
    provenance: Provenance
    document_id: str
    page: int
    section: str | None = None
    table_index: int | None = None
    part: int = 0
    total_parts: int = 1
    context_scale: Scale | None = None
    context_currency: str | None = None
    company: str | None = None
    fiscal_year: str | None = None
    metadata: dict = field(default_factory=dict)

    def payload(self) -> dict:
        """Flat payload for the vector store.

        Every field here is a filter the spec's Module 19 requires: company,
        year, document, page, section, table.
        """
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind.value,
            "text": self.text,
            "document_id": self.document_id,
            "page": self.page,
            "section": self.section,
            "table_index": self.table_index,
            "part": self.part,
            "total_parts": self.total_parts,
            "scale": self.context_scale.label if self.context_scale else None,
            "currency": self.context_currency,
            "company": self.company,
            "fiscal_year": self.fiscal_year,
            "citation": self.provenance.cite(),
            **self.metadata,
        }


_SCALE_BY_LABEL = {scale.label: scale for scale in Scale}


def serialise_chunk(chunk: Chunk) -> dict:
    """Chunk -> plain JSON-safe dict, for the on-disk chunk cache.

    Distinct from `Chunk.payload()`, which is the flattened form the vector store
    holds: this one round-trips, so an expensive extraction can be reused when
    only the embedding model changes.
    """
    provenance = chunk.provenance
    return {
        "chunk_id": chunk.chunk_id,
        "kind": chunk.kind.value,
        "text": chunk.text,
        "provenance": {
            "document_id": provenance.document_id,
            "page": provenance.page,
            "section": provenance.section,
            "table_index": provenance.table_index,
            "row": provenance.row,
            "column": provenance.column,
        },
        "document_id": chunk.document_id,
        "page": chunk.page,
        "section": chunk.section,
        "table_index": chunk.table_index,
        "part": chunk.part,
        "total_parts": chunk.total_parts,
        "context_scale": chunk.context_scale.label if chunk.context_scale else None,
        "context_currency": chunk.context_currency,
        "company": chunk.company,
        "fiscal_year": chunk.fiscal_year,
        "metadata": chunk.metadata,
    }


def deserialise_chunk(record: dict) -> Chunk:
    """Inverse of `serialise_chunk`.

    An unknown scale label raises rather than defaulting to None: a cache written
    by a different Scale vocabulary would otherwise silently drop units, which is
    the exact failure this pipeline is built to prevent.
    """
    label = record.get("context_scale")
    if label is not None and label not in _SCALE_BY_LABEL:
        raise ValueError(f"unknown scale label in cached chunk: {label!r}")

    return Chunk(
        chunk_id=record["chunk_id"],
        kind=ChunkKind(record["kind"]),
        text=record["text"],
        provenance=Provenance(**record["provenance"]),
        document_id=record["document_id"],
        page=record["page"],
        section=record.get("section"),
        table_index=record.get("table_index"),
        part=record.get("part", 0),
        total_parts=record.get("total_parts", 1),
        context_scale=_SCALE_BY_LABEL[label] if label else None,
        context_currency=record.get("context_currency"),
        company=record.get("company"),
        fiscal_year=record.get("fiscal_year"),
        metadata=record.get("metadata", {}),
    )


def _unit_banner(table: ExtractedTable) -> str:
    """A human- and model-readable statement of the table's units.

    Written into the chunk because a continuation page's units live on a page
    that will not be retrieved alongside it (decision D13).
    """
    if table.context_scale is None and table.context_currency is None:
        return "(units not declared in the source document)"
    currency = table.context_currency or ""
    scale = table.context_scale.label if table.context_scale else ""
    banner = f"(All figures in {currency} {scale})".replace("  ", " ")
    if table.scale_was_inherited:
        banner += f" [units declared on page {table.scale_source_page}, not on this page]"
    return banner


def serialise_table(rows: list[tuple[str, ...]], header: tuple[str, ...] | None) -> str:
    """Render rows as a pipe table so column structure survives embedding.

    Plain whitespace joining loses the column boundaries that make a financial
    table interpretable - "Equity share capital 2.12 2,071 2,069" gives a reader
    no way to tell the note reference from the two fiscal years.
    """
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""

    def line(cells: tuple[str, ...]) -> str:
        padded = list(cells) + [""] * (width - len(cells))
        return "| " + " | ".join(c.replace("\n", " ").strip() for c in padded) + " |"

    lines = []
    if header is not None:
        lines.append(line(header))
        lines.append("|" + "---|" * width)
        body = rows[1:] if rows and rows[0] == header else rows
    else:
        body = rows
    lines.extend(line(r) for r in body)
    return "\n".join(lines)


_NUMERAL = re.compile(r"\d[\d,.]*")
_SENTENCE = re.compile(r"[.!?](?:\s|$)")

# A page-text paragraph is table spillover when most of its figures already sit
# in a table chunk from the same page and it contains no sentence.
SPILLOVER_MIN_NUMERALS = 3
SPILLOVER_COVERAGE = 0.6


def is_table_spillover(paragraph: str, table_numerals: frozenset[str]) -> bool:
    """Is this "paragraph" a flattened copy of a table already indexed?

    `chunk_document` has always claimed table text is excluded from the page-text
    pass. It was not: only `is_prose` stood in the way, and page 13 of the
    Infosys filing slipped eight fragments past it - "Current tax 2.17 8,390
    9,287 Deferred tax 2.17 1,350 (73) Profit for the year" clears the alpha
    ratio and clears the eight-word minimum exactly. Those fragments then compete
    with the structured table chunk covering the same figures, and win, because a
    bare "8,390" is a tighter match for a numeric query than a full table is.
    What comes back is a number stripped of its label, its units and its year.

    The test is deliberately narrow: real narrative that quotes figures - "The
    Group contributed 513 crore ..." - has sentences and survives.
    """
    numerals = _NUMERAL.findall(paragraph)
    if len(numerals) < SPILLOVER_MIN_NUMERALS or _SENTENCE.search(paragraph):
        return False
    covered = sum(1 for n in numerals if n in table_numerals)
    return covered / len(numerals) >= SPILLOVER_COVERAGE


def is_prose(text: str) -> bool:
    """Is this narrative text, or table figures that leaked into the page stream?

    Table numbers are already indexed as part of a structured table chunk that
    carries their labels, units and year. Re-indexing them as bare text creates a
    competitor that wins numeric queries and returns a figure with none of that
    context attached.
    """
    stripped = text.strip()
    if not stripped:
        return False
    letters = sum(c.isalpha() for c in stripped)
    if letters / len(stripped) < MIN_ALPHA_RATIO:
        return False
    return sum(1 for w in stripped.split() if any(c.isalpha() for c in w)) >= MIN_WORDS


def _split_paragraph(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split on sentence boundaries, falling back to hard slicing."""
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_END.split(text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            # A single sentence longer than the budget: hard-slice it rather
            # than emit an oversized chunk the embedder would silently truncate.
            if current:
                parts.append(current)
                current = ""
            for i in range(0, len(sentence), max_chars - overlap):
                parts.append(sentence[i : i + max_chars])
            continue
        if len(current) + len(sentence) + 1 > max_chars:
            parts.append(current)
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = (tail + " " + sentence).strip()
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return [p for p in parts if p.strip()]


def _chunk_table(
    table: ExtractedTable,
    document_id: str,
    company: str | None,
    fiscal_year: str | None,
    max_chars: int,
) -> list[Chunk]:
    banner = _unit_banner(table)
    title = table.section or f"Table on page {table.page}"
    prefix = f"{title}\n{banner}\n"

    header = table.header
    # `header` may be several source rows merged into one (a unit caption, the
    # column names and the fiscal years), so it matches no raw row and cannot be
    # filtered by equality. `header_rows` says how many to skip.
    if table.header_rows:
        body_rows = list(table.rows[table.header_rows:])
    else:
        body_rows = [r for r in table.rows if r != header] if header else list(table.rows)

    parts: list[list[tuple[str, ...]]] = []
    current: list[tuple[str, ...]] = []
    current_len = len(prefix) + len(serialise_table([header] if header else [], header))
    for row in body_rows:
        row_len = sum(len(c) for c in row) + 3 * len(row)
        if current and current_len + row_len > max_chars:
            parts.append(current)
            current = []
            current_len = len(prefix) + (len(" | ".join(header)) if header else 0)
        current.append(row)
        current_len += row_len
    if current:
        parts.append(current)
    if not parts:
        parts = [[]]

    chunks: list[Chunk] = []
    for index, rows in enumerate(parts):
        # The header and unit banner are repeated in EVERY part. A part without
        # them is a grid of digits with no meaning.
        rendered = serialise_table(([header] if header else []) + rows, header)
        text = f"{prefix}{rendered}"
        chunk_id = f"{document_id}:p{table.page}:t{table.table_index}:{index}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                kind=ChunkKind.TABLE,
                text=text,
                provenance=table.provenance,
                document_id=document_id,
                page=table.page,
                section=table.section,
                table_index=table.table_index,
                part=index,
                total_parts=len(parts),
                context_scale=table.context_scale,
                context_currency=table.context_currency,
                company=company,
                fiscal_year=fiscal_year,
                metadata={
                    "parsing_accuracy": table.parsing_accuracy,
                    "scale_inherited": table.scale_was_inherited,
                    "scale_source_page": table.scale_source_page,
                },
            )
        )
    return chunks


def chunk_document(
    document: ExtractedDocument,
    *,
    company: str | None = None,
    fiscal_year: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    table_max_chars: int = TABLE_MAX_CHARS,
) -> list[Chunk]:
    """Chunk an extracted document into retrievable units.

    Tables are chunked first and their text is excluded from the page-text pass,
    so a figure is not indexed twice - once with column structure and once as a
    flat run of digits that would compete with it in retrieval.
    """
    chunks: list[Chunk] = []

    for table in document.tables:
        chunks.extend(
            _chunk_table(table, document.document_id, company, fiscal_year, table_max_chars)
        )

    # Every figure already indexed as part of a structured table, by page. Used
    # to keep the same figure from being indexed a second time as loose text.
    table_numerals: dict[int, frozenset[str]] = {}
    for table in document.tables:
        found = set(table_numerals.get(table.page, ()))
        for row in table.rows:
            for cell in row:
                found.update(_NUMERAL.findall(cell))
        table_numerals[table.page] = frozenset(found)

    for page in document.pages:
        text = page.text.strip()
        if not text:
            continue
        on_page = table_numerals.get(page.page, frozenset())
        for paragraph in _PARAGRAPH_BREAK.split(text):
            paragraph = paragraph.strip()
            if len(paragraph) < 40 or not is_prose(paragraph):
                continue
            if is_table_spillover(paragraph, on_page):
                continue
            for index, part in enumerate(_split_paragraph(paragraph, max_chars, overlap)):
                chunk_id = f"{document.document_id}:p{page.page}:x{len(chunks)}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        kind=ChunkKind.TEXT,
                        text=part,
                        provenance=page.provenance,
                        document_id=document.document_id,
                        page=page.page,
                        section=page.section,
                        part=index,
                        company=company,
                        fiscal_year=fiscal_year,
                    )
                )
    return chunks
