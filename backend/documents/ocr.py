"""OCR for scanned pages (spec Module 3, §11).

A scanned annual report yields no text layer at all: `page.get_text()` returns
an empty string, extraction reports zero figures, and the pipeline answers "the
evidence needed is unavailable". That is honest but useless, and spec §11 lists
OCR as a required component.

**The reason this is a separate module, and the reason its output is labelled.**
OCR does not fail loudly. It fails by returning *something* - a `5` read as an
`S`, a `1` as a `7`, a decimal point lost to a speck of dust - and the result is
a plausible number on the right page with no marker of doubt. That is precisely
the failure this project exists to detect, arriving through the front door.

So:

- OCR is **opt-in per page**, and only for pages with no usable text layer.
  Re-OCRing a page that already has selectable text swaps a perfect source for a
  lossy one.
- Every OCR'd page is flagged `source="ocr"` with its mean confidence, and the
  flag travels into the extraction quality report. A downstream consumer can
  refuse an OCR'd figure, or weight it lower, but it can never fail to know.
- **Digits are the risky part**, so the confidence of the numeric characters is
  reported separately from the page mean. A page that reads its prose perfectly
  and its figures badly has a high mean and is exactly the page to distrust.
- The scale banner ("Rs. in crore") is usually printed small; when it is lost,
  every figure on the page is off by a factor of ten million. `scale_readable`
  says whether one was recovered at all.

**Tesseract is checked at call time, never assumed.** If it is missing, this
returns a result that says so rather than raising - a corpus where one document
is scanned should still process the other four - and the quality report carries
the reason.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "OcrPage",
    "OcrResult",
    "tesseract_path",
    "tesseract_available",
    "tesseract_version",
    "needs_ocr",
    "ocr_page",
    "ocr_document",
    "MIN_TEXT_CHARS",
    "LOW_CONFIDENCE",
]

# A page with fewer than this many characters of extracted text is treated as
# having no usable text layer. Chosen above zero because a scanned page often
# carries a stray digit or a page number from a stamped footer, and requiring
# exactly zero would miss those pages entirely.
MIN_TEXT_CHARS = 60

# Below this mean confidence the page is flagged. Not a threshold for rejecting
# it - that is the caller's decision - but the point at which a figure read from
# this page should not be trusted without a human looking at the scan.
LOW_CONFIDENCE = 70.0


# Where Tesseract installs itself on Windows. Checked because it does NOT add
# itself to PATH by default, and `shutil.which` therefore reports "not
# installed" for a machine that has it - which would silently downgrade every
# scanned page to "unreadable" and record that as a property of the document
# rather than of the environment.
_KNOWN_LOCATIONS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)


def tesseract_path() -> str | None:
    """The tesseract binary, or None.

    Resolution order: TESSERACT_CMD, then PATH, then the standard install
    locations. The env var comes first so an operator can point at a specific
    build without altering PATH for the whole machine.
    """
    override = os.environ.get("TESSERACT_CMD")
    if override and Path(override).exists():
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in _KNOWN_LOCATIONS if Path(p).exists()), None)


def tesseract_available() -> bool:
    return tesseract_path() is not None


def tesseract_version() -> str | None:
    """The installed version, or None. Recorded rather than assumed.

    ENVIRONMENT.md records what was verified on this machine; a run on another
    machine records what was actually there, so a difference in extraction
    quality between two runs is traceable to a difference in tooling.
    """
    binary = tesseract_path()
    if binary is None:
        return None
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15, check=False
        ).stdout
        return out.splitlines()[0].strip() if out else None
    except Exception:  # noqa: BLE001 - a version probe must never break extraction
        return None


def needs_ocr(text: str, *, min_chars: int = MIN_TEXT_CHARS) -> bool:
    """Does this page lack a usable text layer?

    Deliberately conservative. A false positive re-OCRs a page that already had
    selectable text, replacing an exact source with a lossy one - which is worse
    than leaving a sparse page alone, because the sparse page is visibly sparse
    and the OCR'd one is confidently wrong.
    """
    return len((text or "").strip()) < min_chars


@dataclass(frozen=True)
class OcrPage:
    """One OCR'd page. `source` is what stops this being mistaken for a text layer."""

    page: int
    text: str
    mean_confidence: float
    # Confidence over numeric tokens only. A page that reads its prose well and
    # its figures badly has a healthy mean and is the page most worth doubting.
    digit_confidence: float | None
    words: int
    source: str = "ocr"
    scale_readable: bool = False
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def low_confidence(self) -> bool:
        worst = min(
            [c for c in (self.mean_confidence, self.digit_confidence) if c is not None]
            or [0.0]
        )
        return worst < LOW_CONFIDENCE

    def as_dict(self) -> dict:
        return {
            "page": self.page,
            "source": self.source,
            "mean_confidence": self.mean_confidence,
            "digit_confidence": self.digit_confidence,
            "words": self.words,
            "low_confidence": self.low_confidence,
            "scale_readable": self.scale_readable,
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class OcrResult:
    document_id: str
    pages: tuple[OcrPage, ...] = ()
    attempted: int = 0
    succeeded: int = 0
    available: bool = True
    tesseract: str | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def low_confidence_pages(self) -> tuple[int, ...]:
        return tuple(p.page for p in self.pages if p.low_confidence)

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "available": self.available,
            "tesseract": self.tesseract,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "low_confidence_pages": list(self.low_confidence_pages),
            "pages": [p.as_dict() for p in self.pages],
            "problems": list(self.problems),
        }


def _confidences(data: dict) -> tuple[float, float | None, int]:
    """Mean confidence, digit-only confidence, and the word count.

    pytesseract reports -1 for boxes it did not score; including those would
    drag the mean towards a number that means nothing.
    """
    texts = data.get("text", [])
    raw = data.get("conf", [])
    scored: list[tuple[str, float]] = []
    for token, conf in zip(texts, raw, strict=False):
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        if value < 0 or not str(token).strip():
            continue
        scored.append((str(token), value))

    if not scored:
        return 0.0, None, 0
    mean = sum(c for _, c in scored) / len(scored)
    digits = [c for token, c in scored if any(ch.isdigit() for ch in token)]
    return mean, (sum(digits) / len(digits) if digits else None), len(scored)


def ocr_page(
    path: str,
    page_number: int,
    *,
    dpi: int = 300,
    language: str = "eng",
) -> OcrPage:
    """Render one page and read it.

    300 dpi rather than the 72 dpi a PDF page renders at natively: at 72 dpi the
    digits in a financial table are a few pixels tall and tesseract confuses 3
    with 8 routinely. Higher costs render time and memory, and 300 is the point
    where the accuracy curve flattens for printed tables.
    """
    try:
        import pymupdf
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        return OcrPage(
            page=page_number, text="", mean_confidence=0.0, digit_confidence=None,
            words=0, problems=(f"OCR dependency missing: {exc}",),
        )

    binary = tesseract_path()
    if binary is None:
        return OcrPage(
            page=page_number, text="", mean_confidence=0.0, digit_confidence=None,
            words=0,
            problems=(
                (
                    "tesseract is not on PATH; the page was NOT read and no "
                    "text is claimed for it"
                ),
            ),
        )

    try:
        document = pymupdf.open(path)
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=dpi)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        document.close()
    except Exception as exc:  # noqa: BLE001 - a bad page is data, not a crash
        return OcrPage(
            page=page_number, text="", mean_confidence=0.0, digit_confidence=None,
            words=0, problems=(f"could not render page: {exc}",),
        )

    # pytesseract shells out to whatever `tesseract_cmd` names, and its default
    # is the bare name - which fails on a machine where the binary is installed
    # but not on PATH. Point it at what was actually resolved.
    pytesseract.pytesseract.tesseract_cmd = binary

    try:
        data = pytesseract.image_to_data(
            image, lang=language, output_type=pytesseract.Output.DICT
        )
        text = pytesseract.image_to_string(image, lang=language)
    except Exception as exc:  # noqa: BLE001
        return OcrPage(
            page=page_number, text="", mean_confidence=0.0, digit_confidence=None,
            words=0, problems=(f"tesseract failed: {exc}",),
        )

    mean, digit, words = _confidences(data)

    # The scale banner is small print and is the first thing a marginal scan
    # loses. Losing it makes every figure on the page wrong by a factor of ten
    # million, so its absence is reported rather than inferred later.
    from backend.documents.extraction import detect_scale_context

    scale, _currency = detect_scale_context(text)
    problems: list[str] = []
    if scale is None:
        problems.append(
            "no scale banner recovered from this page; figures read here have no "
            "magnitude context and must not be used without one"
        )
    if digit is not None and digit < LOW_CONFIDENCE:
        problems.append(
            f"numeric characters scored {digit:.1f} mean confidence: figures from "
            "this page need a human to confirm them against the scan"
        )

    return OcrPage(
        page=page_number,
        text=text,
        mean_confidence=mean,
        digit_confidence=digit,
        words=words,
        scale_readable=scale is not None,
        problems=tuple(problems),
    )


def ocr_document(
    path: str,
    document_id: str,
    *,
    page_texts: list[str] | None = None,
    pages: list[int] | None = None,
    dpi: int = 300,
    language: str = "eng",
    max_pages: int | None = None,
) -> OcrResult:
    """OCR the pages of `path` that have no usable text layer.

    `page_texts` is the already-extracted text, so this can decide which pages
    need reading rather than re-reading all of them. Passing None means every
    requested page is OCR'd, which is the caller explicitly asking for it.

    A missing tesseract returns `available=False` with a stated reason. It does
    not raise: a corpus with one scanned document should still process the other
    four, and losing the whole batch to a missing binary would be a worse outcome
    than losing one document's figures.
    """
    version = tesseract_version()
    if version is None:
        return OcrResult(
            document_id=document_id,
            available=False,
            problems=(
                (
                    "tesseract is not installed or not on PATH. Scanned pages "
                    "were NOT read; no text is claimed for them. Install "
                    "Tesseract OCR and re-run to recover them."
                ),
            ),
        )

    if pages is None:
        if page_texts is None:
            raise ValueError("pass either `pages` or `page_texts` to choose what to OCR")
        pages = [
            index + 1 for index, text in enumerate(page_texts) if needs_ocr(text)
        ]
    if max_pages is not None:
        pages = pages[:max_pages]

    read = [ocr_page(path, number, dpi=dpi, language=language) for number in pages]
    succeeded = [p for p in read if p.words > 0]
    problems: list[str] = []
    low = [p.page for p in succeeded if p.low_confidence]
    if low:
        problems.append(
            f"{len(low)} page(s) read at low confidence: {low}. Figures from these "
            "pages are not reliable without a human check against the scan."
        )
    failed = [p.page for p in read if p.words == 0]
    if failed:
        problems.append(f"{len(failed)} page(s) produced no text at all: {failed}")

    return OcrResult(
        document_id=document_id,
        pages=tuple(read),
        attempted=len(pages),
        succeeded=len(succeeded),
        available=True,
        tesseract=version,
        problems=tuple(problems),
    )
