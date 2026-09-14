"""Financial document acquisition and registration (spec Module 2).

Registers a PDF with a content hash, provenance, and - importantly - a
**validation verdict about whether the bytes are actually the document they
claim to be**.

That last part is not defensive over-engineering. While acquiring the first real
report for this project, a download returned HTTP 200, `content-type:
application/pdf`, and a well-formed 488-byte PDF whose entire content was an
Akamai "Access Denied" page. Every naive check passed: the request succeeded, the
file existed, it parsed as a PDF, it had a text layer. An extraction pipeline
built on it would have produced confident nonsense from a document that was never
the annual report. So `validate_pdf` checks what the document *contains*, not
merely that bytes arrived.

The document id is derived from the content hash, so the same bytes always
register as the same document and re-downloading cannot silently fork a corpus.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path

import pymupdf

__all__ = [
    "DocumentStatus",
    "DocumentRecord",
    "ValidationResult",
    "validate_pdf",
    "register_document",
    "DocumentRegistry",
]

# Phrases that appear in the error pages CDNs return with a 200 status and a
# PDF content-type. Matched only against very short documents, so a real report
# that happens to discuss access control is not rejected.
_ERROR_PAGE_MARKERS = (
    "access denied",
    "you don't have permission",
    "forbidden",
    "404 not found",
    "page not found",
    "request blocked",
    "are you a robot",
    "enable javascript",
)
_ERROR_PAGE_MAX_PAGES = 3

_MIN_CHARS_FOR_TEXT_LAYER = 100


class DocumentStatus(Enum):
    VALID = "valid"
    NEEDS_OCR = "needs_ocr"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ValidationResult:
    status: DocumentStatus
    page_count: int = 0
    pages_with_text: int = 0
    total_characters: int = 0
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is not DocumentStatus.REJECTED

    @property
    def text_coverage(self) -> float:
        return self.pages_with_text / self.page_count if self.page_count else 0.0


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    filename: str
    path: str
    sha256: str
    byte_size: int
    page_count: int
    status: str
    text_coverage: float
    company: str | None = None
    fiscal_year: str | None = None
    title: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    registered_at: str = ""
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pdf(path: Path, *, min_pages: int = 1) -> ValidationResult:
    """Decide whether this file is a usable financial document.

    Distinguishes three outcomes rather than two: usable, usable-but-scanned
    (needs OCR), and rejected. Collapsing the middle case into "rejected" would
    discard scanned filings, which are common; collapsing it into "valid" would
    hand the extractor a document with no text and let it report emptiness as
    fact.
    """
    problems: list[str] = []
    notes: list[str] = []

    if not path.exists():
        return ValidationResult(DocumentStatus.REJECTED, problems=("file does not exist",))
    if path.stat().st_size == 0:
        return ValidationResult(DocumentStatus.REJECTED, problems=("file is empty",))

    with path.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            return ValidationResult(
                DocumentStatus.REJECTED, problems=("missing %PDF- magic bytes",)
            )

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a rejection
        return ValidationResult(
            DocumentStatus.REJECTED, problems=(f"unparseable PDF: {exc}",)
        )

    try:
        page_count = doc.page_count
        page_texts = [doc[i].get_text() for i in range(page_count)]
    except Exception as exc:  # noqa: BLE001
        doc.close()
        return ValidationResult(
            DocumentStatus.REJECTED, page_count=0, problems=(f"failed to read pages: {exc}",)
        )
    finally:
        if not doc.is_closed:
            doc.close()

    total_chars = sum(len(t) for t in page_texts)
    pages_with_text = sum(1 for t in page_texts if len(t.strip()) >= _MIN_CHARS_FOR_TEXT_LAYER)

    # The CDN-error-page check. Restricted to very short documents so that a real
    # report discussing access rights is never rejected for its vocabulary.
    if page_count <= _ERROR_PAGE_MAX_PAGES:
        haystack = " ".join(page_texts).lower()
        hits = [m for m in _ERROR_PAGE_MARKERS if m in haystack]
        if hits:
            return ValidationResult(
                DocumentStatus.REJECTED,
                page_count=page_count,
                pages_with_text=pages_with_text,
                total_characters=total_chars,
                problems=(
                    (
                        f"content looks like a server error page, not a document "
                        f"(matched {hits!r} in a {page_count}-page file)"
                    ),
                ),
            )

    if page_count < min_pages:
        problems.append(f"only {page_count} pages, expected at least {min_pages}")

    if problems:
        return ValidationResult(
            DocumentStatus.REJECTED, page_count, pages_with_text, total_chars, tuple(problems)
        )

    if pages_with_text == 0:
        notes.append("no extractable text on any page - scanned document, OCR required")
        status = DocumentStatus.NEEDS_OCR
    elif pages_with_text < page_count:
        missing = page_count - pages_with_text
        notes.append(
            f"{missing} of {page_count} pages have little or no text - "
            "likely scanned inserts; OCR needed for those pages"
        )
        status = DocumentStatus.VALID
    else:
        status = DocumentStatus.VALID

    return ValidationResult(status, page_count, pages_with_text, total_chars, (), tuple(notes))


def _guess_fiscal_year(text: str) -> str | None:
    """Indian filings state a fiscal year that spans two calendar years."""
    for pattern in (
        r"year ended March 31,\s*(\d{4})",
        r"FY\s?(\d{4})-(\d{2,4})",
        r"(\d{4})-(\d{2})\b",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 1:
                year = int(groups[0])
                return f"{year - 1}-{str(year)[2:]}"
            start, end = groups
            return f"{start}-{end[-2:]}"
    return None


def _guess_company(text: str) -> str | None:
    m = re.search(r"\b([A-Z][A-Za-z&.\- ]{2,60}?)\s+(?:Limited|Ltd\.?)\b", text)
    return f"{m.group(1).strip()} Limited" if m else None


def register_document(
    path: str | Path,
    *,
    source_url: str | None = None,
    retrieved_at: str | None = None,
    company: str | None = None,
    fiscal_year: str | None = None,
    min_pages: int = 1,
) -> DocumentRecord:
    """Validate, hash, and describe a document.

    Explicit `company` / `fiscal_year` always win over the heuristic guesses -
    the heuristics exist to save typing, not to be trusted as ground truth.
    """
    path = Path(path)
    validation = validate_pdf(path, min_pages=min_pages)
    digest = _sha256(path)

    title = None
    metadata: dict = {}
    first_pages_text = ""
    if validation.ok:
        try:
            doc = pymupdf.open(path)
            metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
            title = metadata.get("title") or None
            first_pages_text = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
            doc.close()
        except Exception:  # noqa: BLE001 - metadata is a convenience, not a gate
            pass

    return DocumentRecord(
        document_id=digest[:16],
        filename=path.name,
        path=str(path).replace("\\", "/"),
        sha256=digest,
        byte_size=path.stat().st_size if path.exists() else 0,
        page_count=validation.page_count,
        status=validation.status.value,
        text_coverage=round(validation.text_coverage, 4),
        company=company or _guess_company(first_pages_text),
        fiscal_year=fiscal_year or _guess_fiscal_year(first_pages_text),
        title=title,
        source_url=source_url,
        retrieved_at=retrieved_at,
        registered_at=datetime.now(UTC).isoformat(timespec="seconds"),
        problems=validation.problems,
        notes=validation.notes,
        metadata=metadata,
    )


class DocumentRegistry:
    """A JSON-backed registry keyed by content hash.

    Duplicate detection is by hash, not filename: the same report saved under two
    names is one document, and two different reports saved under one name are not.
    """

    def __init__(self, path: str | Path = "documents/registry.json") -> None:
        self.path = Path(path)
        self._records: dict[str, DocumentRecord] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in raw.get("documents", []):
            entry["problems"] = tuple(entry.get("problems", ()))
            entry["notes"] = tuple(entry.get("notes", ()))
            self._records[entry["document_id"]] = DocumentRecord(**entry)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "documents": [asdict(r) for r in self._records.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
        """Register `record`. Returns (stored_record, is_new).

        An existing hash returns the record already held rather than overwriting
        it, so provenance recorded on first acquisition is never lost to a later
        re-download that knows less about where the file came from.
        """
        existing = self._records.get(record.document_id)
        if existing is not None:
            return existing, False
        self._records[record.document_id] = record
        return record, True

    def get(self, document_id: str) -> DocumentRecord | None:
        return self._records.get(document_id)

    def all(self) -> list[DocumentRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)
