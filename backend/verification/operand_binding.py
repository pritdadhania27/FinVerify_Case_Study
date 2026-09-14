"""Bind question operands to figures in the evidence (spec Modules 4, 10; D6).

The deterministic verifier is exact **given** `(operation, operands)`. D6 recorded
the honest limitation that operand binding might have to come from an LLM, which
would make the "deterministic" third opinion only as deterministic as the model
that fed it.

This module removes that limitation for the cases it covers. Binding is done by
matching a metric's label against the row labels of the retrieved table chunks and
reading the figure from that row - regex and lexicon, no model. For a question it
can bind, the third channel is **fully deterministic and cannot hallucinate**,
which is a materially stronger position than D6 assumed and worth stating in the
write-up.

**It refuses far more often than it succeeds, and that is correct.** A binder that
guessed which row was meant would manufacture a confident third opinion out of a
mis-read label, and the consistency engine weights the deterministic channel
highly enough to overrule two agreeing channels. Wrong-and-confident is the worst
possible behaviour here, so every failure to bind is reported as an unbound
metric rather than resolved by proximity or best-guess.

**Scale comes from the chunk, not the cell.** A financial table states its units
once in a caption; the cells are bare numerals. `parse_financial_value` is given
the chunk's `scale`/`currency` as context, so "3,956" in a table captioned
"(In crore)" binds as 3,956 crore rather than as 3,956 rupees - the 10-million-fold
error this project exists to catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.agents.metrics_lexicon import LEXICON
from backend.core.financial_value import FinancialValue, Scale, parse_financial_value

__all__ = ["BoundOperand", "BindingResult", "bind_operands", "metric_in_text"]

_SCALE_BY_LABEL = {scale.label: scale for scale in Scale}
_ALIASES: dict[str, tuple[str, ...]] = {
    entry.canonical: (entry.canonical, *entry.aliases) for entry in LEXICON
}

# A pipe-table row: "| Trade payables | 2.14 | 3,956  3,865 |"
_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
# A cell that is a usable figure. Note references like "2.14" and "2.4.1 and 2.1"
# are excluded by requiring either a separator, a decimal with <= 2 places, or
# parentheses - a note number is not a quantity and binding one would be silent
# nonsense.
_FIGURE = re.compile(r"^\(?\s*-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\s*\)?$|^\(?\s*-?\d+\.\d{1,2}\s*\)?$")
_BARE_INT = re.compile(r"^\(?\s*-?\d{1,6}\s*\)?$")
_NOTE_REF = re.compile(r"^\d+(?:\.\d+)+(?:\s+and\s+[\d.]+)*$")


@dataclass(frozen=True)
class BoundOperand:
    """One figure, with enough provenance to audit where it came from."""

    metric: str
    value: FinancialValue
    row_label: str
    citation: str
    column_index: int

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "amount": str(self.value.amount),
            "scale": self.value.scale.label,
            "currency": self.value.currency,
            "canonical": str(self.value.canonical()),
            "row_label": self.row_label,
            "citation": self.citation,
            "column_index": self.column_index,
        }


@dataclass(frozen=True)
class BindingResult:
    operands: tuple[BoundOperand, ...]
    unbound: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Every metric the question needs was bound to a figure."""
        return not self.unbound and bool(self.operands)


def _labels_for(metric: str) -> tuple[str, ...]:
    lowered = metric.lower().strip()
    for canonical, aliases in _ALIASES.items():
        if lowered == canonical or lowered in {a.lower() for a in aliases}:
            return aliases
    return (lowered,)


def metric_in_text(text: str) -> str | None:
    """The lexicon metric a question is asking about, or None.

    `parse_question` hands the deterministic channel a sub-question metric that
    is the question's own words with the boilerplate still attached -
    "HDFC Bank Limited, reported provisions provisions reported consolidated
    financial statements". `_labels_for` cannot match that against a table row,
    so binding on a lookup would fail every time for a reason that has nothing
    to do with the evidence.

    This resolves the metric the way the rest of the module works: against the
    lexicon, by regex, with no model. **Longest alias wins**, because "other
    financial liabilities" and "financial liabilities" are both in the lexicon
    and the shorter one would silently bind the wrong row.

    Returns None when no lexicon metric appears, which the caller must treat as
    "cannot check this question" rather than as "the evidence is wrong".
    """
    if not text:
        return None
    lowered = re.sub(r"\s+", " ", text.lower())
    best: str | None = None
    for aliases in _ALIASES.values():
        for alias in aliases:
            if len(alias) <= len(best or ""):
                continue
            if re.search(rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])", lowered):
                best = alias
    if best is None:
        return None
    # Return the CANONICAL name, so `_labels_for` expands it to every alias
    # rather than matching only the spelling that happened to appear.
    for canonical, aliases in _ALIASES.items():
        if best in aliases:
            return canonical
    return best


def _cells(line: str) -> list[str] | None:
    match = _ROW.match(line)
    if not match:
        return None
    return [c.strip() for c in match.group(1).split("|")]


def _is_figure(cell: str) -> bool:
    text = cell.strip()
    if not text or _NOTE_REF.match(text):
        return False
    return bool(_FIGURE.match(text) or _BARE_INT.match(text))


def _row_matches(label_cell: str, labels: tuple[str, ...]) -> bool:
    """Does this row's label denote the metric?

    Anchored at the start rather than substring-matched anywhere. "Total assets"
    must not match the row "Total assets held for sale", and matching in the
    middle of a longer label is how "profit for the year" would bind to
    "Profit for the year attributable to non-controlling interests".
    """
    normalised = re.sub(r"\s+", " ", label_cell.strip().lower()).strip(" :*#")
    for label in labels:
        candidate = label.lower()
        if normalised == candidate:
            return True
        # Allow a trailing footnote marker or note reference only.
        if normalised.startswith(candidate) and len(normalised) - len(candidate) <= 4:
            return True
    return False


def bind_operands(
    metrics: list[str],
    evidence_blocks: list,
    *,
    column: int = 0,
) -> BindingResult:
    """Find each metric's figure in the retrieved evidence.

    `column` selects which figure on the row to take when a row carries several -
    financial statements put the current year first, so 0 is the reporting year
    and 1 the comparative. Taking the wrong one is a plausible-looking wrong
    answer, which is why it is an explicit parameter rather than a guess.

    A metric matched in more than one place must agree, or it is left unbound: a
    figure that differs between two statements is a restatement or a
    misidentification, and picking one silently is exactly the provenance failure
    the corpus notes warn about for HDFC Bank.
    """
    operands: list[BoundOperand] = []
    unbound: list[str] = []
    notes: list[str] = []

    for metric in metrics:
        labels = _labels_for(metric)
        found: list[BoundOperand] = []

        for block in evidence_blocks:
            scale = _SCALE_BY_LABEL.get((getattr(block, "scale", None) or "").lower())
            currency = getattr(block, "currency", None)
            citation = getattr(block, "citation", "")

            for line in (getattr(block, "text", "") or "").splitlines():
                cells = _cells(line)
                if not cells or not cells[0]:
                    continue
                if not _row_matches(cells[0], labels):
                    continue

                # Camelot routinely packs a whole year-pair into one cell:
                # "| Profit for the year |  | 26,248  24,108 |". Splitting on
                # whitespace has to happen BEFORE the figure test, not after -
                # testing the packed cell first rejects it outright and the row
                # is skipped, which reads as "the metric is not in the evidence".
                figures: list[str] = []
                for cell in cells[1:]:
                    parts = cell.split()
                    if len(parts) > 1 and all(_is_figure(p) for p in parts):
                        figures.extend(parts)
                    elif _is_figure(cell):
                        figures.append(cell)
                if len(figures) <= column:
                    continue

                value = parse_financial_value(
                    figures[column], context_scale=scale, context_currency=currency
                )
                if value is None:
                    continue
                found.append(
                    BoundOperand(
                        metric=metric,
                        value=value,
                        row_label=cells[0],
                        citation=citation,
                        column_index=column,
                    )
                )

        if not found:
            unbound.append(metric)
            notes.append(f"{metric!r}: no evidence row matched this label")
            continue

        canonicals = {op.value.canonical() for op in found}
        if len(canonicals) > 1:
            unbound.append(metric)
            notes.append(
                f"{metric!r}: matched {len(found)} rows with disagreeing values "
                f"({sorted(str(c) for c in canonicals)}); refusing to choose"
            )
            continue

        operands.append(found[0])

    return BindingResult(
        operands=tuple(operands), unbound=tuple(unbound), notes=tuple(notes)
    )
