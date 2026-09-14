"""Which set of financial statements is a page part of? (RX-026, D42)

An Indian annual report states most metrics twice: once in the STANDALONE
statements (the parent company alone) and once in the CONSOLIDATED statements
(the group, subsidiaries included). They are different numbers answering
different questions. Tata Motors' total borrowings are 13,771.04 crore
standalone against 98,500.09 crore consolidated - seven times apart - because
the consolidated figures carry Jaguar Land Rover.

Extraction records the page a figure came from. Nothing recorded which set of
statements that page belongs to, so a question naming the consolidated basis
could be answered from the standalone section without anything raising: the
figure is real, it is printed exactly where the provenance says, and it is the
wrong number. That is the same shape as every other defect this project has
found - a valid-looking answer with a silent selection error underneath.

The classifier is deliberately narrow, because a permissive one is worse than
none: it produces a confident wrong label instead of an honest "unknown", and
an unknown page can be sent to a human while a mislabelled one cannot.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# A running header is a title, not a sentence. The length guard is what stops
# body prose being read as a section marker even when it names one: a
# consolidated note that says "refer to the standalone financial statements" is
# a cross-reference, and reading it as a boundary put 23 of Reliance's 25
# retrieval questions into the wrong section on the first attempt.
MAX_HEADER_CHARS = 90

# How many lines from the top of a page can carry the running header.
HEADER_LINES = 3

_CONSOLIDATED = re.compile(r"consolidated", re.I)
_STANDALONE = re.compile(r"standalone", re.I)
_STATEMENT = re.compile(r"financial statement|balance sheet|statement of|schedules", re.I)

CONSOLIDATED = "consolidated"
STANDALONE = "standalone"
UNKNOWN = "-"


def classify_header(line: str) -> str | None:
    """The basis a single running-header line declares, or None.

    None is not a failure. It means this line is not a section boundary, which
    is true of almost every line in a filing.
    """
    line = line.strip()
    if not line or len(line) > MAX_HEADER_CHARS:
        return None
    if not _STATEMENT.search(line):
        return None
    consolidated = bool(_CONSOLIDATED.search(line))
    standalone = bool(_STANDALONE.search(line))
    if consolidated and not standalone:
        return CONSOLIDATED
    if standalone and not consolidated:
        return STANDALONE
    # A line naming both resolves nothing. "Notes to the Standalone and
    # Consolidated Financial Statements" is a contents entry, not a boundary.
    return None


def page_basis(pdf_path: str | Path) -> dict[int, str]:
    """1-indexed page number -> CONSOLIDATED | STANDALONE | UNKNOWN.

    Forward-filled, because a section names itself on its opening pages and
    then stops: Tata Motors' standalone notes run 145 pages under the header
    "Notes forming part of Financial Statements", which names neither basis.

    Pages before any marker stay UNKNOWN and are never assigned to a basis.
    That distinction carries the weight here - "we could not tell" is a
    different fact from "it is standalone", and collapsing the two is how a
    front-of-report summary table came to answer a question about the
    consolidated balance sheet.
    """
    import pymupdf

    basis: dict[int, str] = {}
    current = UNKNOWN
    document = pymupdf.open(str(pdf_path))
    try:
        for index in range(document.page_count):
            lines = [
                line for line in document[index].get_text().splitlines() if line.strip()
            ]
            for line in lines[:HEADER_LINES]:
                declared = classify_header(line)
                if declared:
                    current = declared
                    break
            basis[index + 1] = current
    finally:
        document.close()
    return basis


@lru_cache(maxsize=8)
def _cached(pdf_path: str) -> tuple[tuple[int, str], ...]:
    return tuple(sorted(page_basis(pdf_path).items()))


def basis_for(pdf_path: str | Path) -> dict[int, str]:
    """`page_basis` with a cache - the generator asks per fact, not per page."""
    return dict(_cached(str(pdf_path)))


def sections(basis: dict[int, str]) -> list[tuple[str, int, int]]:
    """Contiguous runs as (basis, first_page, last_page). For reporting."""
    runs: list[tuple[str, int, int]] = []
    previous, start = None, None
    for page in sorted(basis):
        if basis[page] != previous:
            if previous is not None:
                runs.append((previous, start, page - 1))
            previous, start = basis[page], page
    if previous is not None:
        runs.append((previous, start, max(basis)))
    return runs
