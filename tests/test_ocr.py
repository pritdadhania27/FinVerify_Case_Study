"""Tests for OCR (spec Module 3, §11).

OCR does not fail loudly. It fails by returning *something*: a 5 read as an S, a
decimal point lost to a speck, and the result is a plausible figure on the right
page with no marker of doubt. That is the failure this project exists to detect,
arriving through the front door - so most of these tests are about the labelling
rather than about the reading.

The tests that need a real binary are SKIPPED, not passed, when Tesseract is
absent, so a green run on a machine without it never reads as "OCR was verified".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.documents.ocr import (
    LOW_CONFIDENCE,
    MIN_TEXT_CHARS,
    OcrPage,
    OcrResult,
    needs_ocr,
    ocr_document,
    ocr_page,
    tesseract_available,
    tesseract_path,
    tesseract_version,
)

REAL_PDF = Path("documents/raw/infosys_ar_2023-24_consolidated.pdf")

requires_tesseract = pytest.mark.skipif(
    not tesseract_available(),
    reason="Tesseract is not installed; skipped rather than passed so a green "
    "run on a machine without it never reads as 'OCR was verified'",
)
requires_document = pytest.mark.skipif(
    not REAL_PDF.exists(),
    reason="documents/raw/ is gitignored (third-party filings are not "
    "redistributed); run scripts/acquire_documents.py",
)


class TestBinaryResolution:
    """Tesseract does not add itself to PATH on Windows.

    `shutil.which` therefore reports "not installed" on a machine that has it,
    which would silently downgrade every scanned page to unreadable and record
    that as a property of the DOCUMENT rather than of the environment.
    """

    def test_an_env_override_takes_precedence(self, monkeypatch, tmp_path):
        fake = tmp_path / "tesseract"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setenv("TESSERACT_CMD", str(fake))
        assert tesseract_path() == str(fake)

    def test_a_nonexistent_override_is_ignored_rather_than_returned(self, monkeypatch):
        """Returning a path that is not there would fail later and further away."""
        monkeypatch.setenv("TESSERACT_CMD", "/definitely/not/here/tesseract")
        assert tesseract_path() != "/definitely/not/here/tesseract"

    @requires_tesseract
    def test_the_version_is_recorded_not_assumed(self):
        version = tesseract_version()
        assert version and "tesseract" in version.lower()


class TestNeedsOcr:
    def test_an_empty_page_needs_ocr(self):
        assert needs_ocr("") is True

    def test_a_page_with_a_text_layer_does_not(self):
        assert needs_ocr("x" * (MIN_TEXT_CHARS + 1)) is False

    def test_a_stray_page_number_does_not_count_as_a_text_layer(self):
        """A scanned page often carries a stamped footer. Requiring exactly zero
        characters would miss those pages entirely."""
        assert needs_ocr("  298  ") is True

    def test_none_is_treated_as_empty(self):
        assert needs_ocr(None) is True


class TestLabelling:
    """The output must never be mistakable for a text layer."""

    def test_every_ocr_page_is_marked_as_ocr(self):
        page = OcrPage(page=1, text="x", mean_confidence=90, digit_confidence=90, words=5)
        assert page.source == "ocr"

    def test_low_digit_confidence_flags_the_page_despite_a_healthy_mean(self):
        """The dangerous case: prose read perfectly, figures read badly."""
        page = OcrPage(
            page=1, text="x", mean_confidence=96.0,
            digit_confidence=LOW_CONFIDENCE - 20, words=100,
        )
        assert page.low_confidence is True

    def test_a_confident_page_is_not_flagged(self):
        page = OcrPage(page=1, text="x", mean_confidence=95.0, digit_confidence=95.0, words=100)
        assert page.low_confidence is False

    def test_a_page_with_no_digits_is_judged_on_its_mean_alone(self):
        page = OcrPage(page=1, text="x", mean_confidence=92.0, digit_confidence=None, words=40)
        assert page.low_confidence is False

    def test_the_result_lists_its_low_confidence_pages(self):
        result = OcrResult(
            document_id="d1",
            pages=(
                OcrPage(1, "a", 95.0, 95.0, 10),
                OcrPage(2, "b", 95.0, 40.0, 10),
            ),
        )
        assert result.low_confidence_pages == (2,)


class TestMissingTesseractIsReportedNotRaised:
    """A corpus with one scanned document should still process the other four."""

    def test_ocr_document_returns_a_result_that_says_so(self, monkeypatch):
        monkeypatch.setattr("backend.documents.ocr.tesseract_version", lambda: None)
        result = ocr_document("whatever.pdf", "d1", pages=[1])
        assert result.available is False
        assert result.succeeded == 0
        assert any("not installed" in p for p in result.problems)

    def test_no_text_is_claimed_for_a_page_that_was_not_read(self, monkeypatch):
        monkeypatch.setattr("backend.documents.ocr.tesseract_path", lambda: None)
        page = ocr_page("whatever.pdf", 1)
        assert page.text == ""
        assert page.words == 0
        assert any("NOT read" in p for p in page.problems)

    def test_choosing_pages_requires_something_to_choose_from(self):
        with pytest.raises(ValueError):
            ocr_document("whatever.pdf", "d1")


class TestFailureCases:
    @requires_tesseract
    def test_an_unopenable_file_is_data_not_a_crash(self, tmp_path):
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        page = ocr_page(str(broken), 1)
        assert page.words == 0
        assert page.problems

    @requires_tesseract
    def test_a_page_beyond_the_end_is_reported(self, tmp_path):
        import pymupdf

        path = tmp_path / "one_page.pdf"
        document = pymupdf.open()
        document.new_page()
        document.save(str(path))
        document.close()
        page = ocr_page(str(path), 99)
        assert page.words == 0
        assert any("could not render" in p for p in page.problems)

    @requires_tesseract
    def test_a_blank_page_yields_no_text_rather_than_noise(self, tmp_path):
        import pymupdf

        path = tmp_path / "blank.pdf"
        document = pymupdf.open()
        document.new_page()
        document.save(str(path))
        document.close()
        page = ocr_page(str(path), 1, dpi=150)
        assert page.text.strip() == ""


@requires_tesseract
@requires_document
class TestAgainstTheRealFiling:
    """The end-to-end check: read a real balance sheet off the rendered page."""

    def test_figures_are_recovered_from_the_balance_sheet(self):
        page = ocr_page(str(REAL_PDF), 12)
        assert page.words > 100
        # The same figures Module 4 reads from the text layer. If OCR and the
        # text layer disagreed about these, one of them would be wrong and the
        # pipeline could not tell which.
        collapsed = page.text.replace(" ", "")
        assert "2,071" in page.text
        assert "88,461" in page.text
        assert "Totalequity" in collapsed

    def test_digit_confidence_is_reported_separately(self):
        page = ocr_page(str(REAL_PDF), 12)
        assert page.digit_confidence is not None
        assert 0 <= page.digit_confidence <= 100

    def test_a_missing_scale_banner_is_reported(self):
        """Losing "Rs. in crore" makes every figure on the page wrong by 10^7,
        so its absence is stated rather than discovered downstream."""
        page = ocr_page(str(REAL_PDF), 12)
        if not page.scale_readable:
            assert any("scale banner" in p for p in page.problems)

    def test_only_pages_without_a_text_layer_are_read(self):
        """Re-OCRing a page that already has selectable text swaps an exact
        source for a lossy one."""
        result = ocr_document(
            str(REAL_PDF), "f356fc75d6d63fa3", page_texts=["x" * 500] * 5, max_pages=3
        )
        assert result.attempted == 0
        assert result.available is True
