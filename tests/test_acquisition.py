"""Tests for document acquisition and registration (spec Module 2)."""

import json
from pathlib import Path

import pymupdf
import pytest

from backend.documents.acquisition import (
    DocumentRegistry,
    DocumentStatus,
    register_document,
    validate_pdf,
)

REAL_PDF = Path("documents/raw/infosys_ar_2023-24_consolidated.pdf")


def make_pdf(path: Path, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def error_page_pdf(tmp_path: Path) -> Path:
    """Reproduces the real incident: a CDN error page served as a valid PDF.

    The genuine article arrived with HTTP 200, content-type application/pdf, and
    parsed cleanly. Only its *content* revealed it was not the annual report.
    """
    return make_pdf(
        tmp_path / "denied.pdf",
        [
            (
                "Access Denied\n"
                "You don't have permission to access "
                "\"http://www.example.com/annual-report.pdf\" on this server.\n"
                "Reference #18.6c392017"
            )
        ],
    )


class TestErrorPageDetection:
    """The lesson from acquiring the first real document."""

    def test_cdn_error_page_is_rejected(self, error_page_pdf):
        result = validate_pdf(error_page_pdf)
        assert result.status is DocumentStatus.REJECTED
        assert any("error page" in p for p in result.problems)

    def test_error_page_would_otherwise_pass_every_naive_check(self, error_page_pdf):
        """It really is a well-formed PDF with a text layer - that is the trap."""
        assert error_page_pdf.stat().st_size > 0
        assert error_page_pdf.read_bytes()[:5] == b"%PDF-"
        doc = pymupdf.open(error_page_pdf)
        assert doc.page_count == 1
        assert doc[0].get_text().strip()
        doc.close()

    @pytest.mark.parametrize(
        "text",
        ["Access Denied", "403 Forbidden", "404 Not Found", "Please enable JavaScript to continue"],
    )
    def test_common_error_page_phrasings(self, tmp_path, text):
        pdf = make_pdf(tmp_path / "e.pdf", [text])
        assert validate_pdf(pdf).status is DocumentStatus.REJECTED

    def test_long_document_mentioning_access_is_not_rejected(self, tmp_path):
        """A real report may legitimately discuss access control.

        The marker check applies only to very short documents, so vocabulary
        alone never disqualifies a genuine filing.
        """
        pages = ["Access to the premises is restricted. " + "Financial detail. " * 40] * 8
        assert validate_pdf(make_pdf(tmp_path / "long.pdf", pages)).ok


class TestValidation:
    def test_missing_file(self, tmp_path):
        result = validate_pdf(tmp_path / "nope.pdf")
        assert result.status is DocumentStatus.REJECTED
        assert any("exist" in p for p in result.problems)

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.pdf"
        p.write_bytes(b"")
        assert validate_pdf(p).status is DocumentStatus.REJECTED

    def test_not_a_pdf(self, tmp_path):
        """Spec section 38 failure case: malformed PDF."""
        p = tmp_path / "fake.pdf"
        p.write_bytes(b"This is plain text pretending to be a PDF")
        result = validate_pdf(p)
        assert result.status is DocumentStatus.REJECTED
        assert any("magic bytes" in x for x in result.problems)

    def test_truncated_pdf(self, tmp_path):
        p = tmp_path / "trunc.pdf"
        p.write_bytes(b"%PDF-1.7\n" + b"garbage" * 10)
        assert validate_pdf(p).status is DocumentStatus.REJECTED

    def test_min_pages_enforced(self, tmp_path):
        pdf = make_pdf(tmp_path / "short.pdf", ["Financial detail. " * 40] * 2)
        assert validate_pdf(pdf, min_pages=10).status is DocumentStatus.REJECTED
        assert validate_pdf(pdf, min_pages=1).ok

    def test_scanned_pdf_needs_ocr_rather_than_being_rejected(self, tmp_path):
        """Spec section 38 failure case: scanned PDF.

        Scanned filings are common. Rejecting them would silently shrink the
        corpus; accepting them as valid would let the extractor report emptiness
        as fact. NEEDS_OCR is the honest third outcome.
        """
        doc = pymupdf.open()
        for _ in range(4):
            doc.new_page()  # image-less, text-less page stands in for a scan
        p = tmp_path / "scan.pdf"
        doc.save(p)
        doc.close()
        result = validate_pdf(p)
        assert result.status is DocumentStatus.NEEDS_OCR
        assert result.ok, "scanned documents are usable after OCR, not rejected"

    def test_partial_text_coverage_is_noted(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Financial detail. " * 40, fontsize=11)
        doc.new_page()  # blank / scanned insert
        p = tmp_path / "mixed.pdf"
        doc.save(p)
        doc.close()
        result = validate_pdf(p)
        assert result.status is DocumentStatus.VALID
        assert result.text_coverage == 0.5
        assert any("OCR" in n for n in result.notes)


class TestRegistration:
    def test_document_id_is_content_derived(self, tmp_path):
        a = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        b = tmp_path / "b.pdf"
        b.write_bytes(a.read_bytes())
        assert register_document(a).document_id == register_document(b).document_id

    def test_different_content_gets_different_id(self, tmp_path):
        a = make_pdf(tmp_path / "a.pdf", ["Revenue detail. " * 40])
        b = make_pdf(tmp_path / "b.pdf", ["Expense detail. " * 40])
        assert register_document(a).document_id != register_document(b).document_id

    def test_provenance_is_recorded(self, tmp_path):
        pdf = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        rec = register_document(
            pdf, source_url="https://example.com/ar.pdf", retrieved_at="2026-08-23"
        )
        assert rec.source_url == "https://example.com/ar.pdf"
        assert rec.retrieved_at == "2026-08-23"
        assert rec.registered_at

    def test_explicit_metadata_overrides_heuristics(self, tmp_path):
        pdf = make_pdf(tmp_path / "a.pdf", ["Acme Limited year ended March 31, 2024 " * 10])
        rec = register_document(pdf, company="Override Corp", fiscal_year="1999-00")
        assert rec.company == "Override Corp"
        assert rec.fiscal_year == "1999-00"

    def test_fiscal_year_heuristic(self, tmp_path):
        pdf = make_pdf(tmp_path / "a.pdf", ["for the year ended March 31, 2024. " * 20])
        assert register_document(pdf).fiscal_year == "2023-24"

    def test_rejected_document_still_produces_a_record(self, tmp_path, error_page_pdf):
        """A rejection is data. It must be recordable, not thrown away."""
        rec = register_document(error_page_pdf)
        assert rec.status == "rejected"
        assert rec.problems
        assert rec.sha256


class TestRegistry:
    def test_duplicate_detected_by_hash_not_filename(self, tmp_path):
        a = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        b = tmp_path / "differently_named.pdf"
        b.write_bytes(a.read_bytes())

        reg = DocumentRegistry(tmp_path / "registry.json")
        _, new_a = reg.add(register_document(a))
        _, new_b = reg.add(register_document(b))
        assert new_a is True
        assert new_b is False, "same bytes under a different name is one document"
        assert len(reg) == 1

    def test_re_registration_preserves_original_provenance(self, tmp_path):
        """A later re-download that knows less must not erase what was recorded."""
        pdf = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        reg = DocumentRegistry(tmp_path / "registry.json")
        reg.add(register_document(pdf, source_url="https://original.example/ar.pdf"))
        stored, is_new = reg.add(register_document(pdf))
        assert is_new is False
        assert stored.source_url == "https://original.example/ar.pdf"

    def test_round_trip_through_disk(self, tmp_path):
        pdf = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        path = tmp_path / "registry.json"
        reg = DocumentRegistry(path)
        rec, _ = reg.add(register_document(pdf, source_url="https://example.com/x.pdf"))
        reg.save()

        reloaded = DocumentRegistry(path)
        assert len(reloaded) == 1
        assert reloaded.get(rec.document_id).source_url == "https://example.com/x.pdf"

    def test_registry_file_is_valid_json(self, tmp_path):
        pdf = make_pdf(tmp_path / "a.pdf", ["Financial detail. " * 40])
        path = tmp_path / "registry.json"
        reg = DocumentRegistry(path)
        reg.add(register_document(pdf))
        reg.save()
        assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


@pytest.mark.skipif(not REAL_PDF.exists(), reason="real report not downloaded")
class TestRealAnnualReport:
    """Against the actual Infosys FY2023-24 consolidated financial statements."""

    def test_validates(self):
        result = validate_pdf(REAL_PDF, min_pages=10)
        assert result.status is DocumentStatus.VALID
        assert result.page_count == 79

    def test_has_near_complete_text_layer(self):
        assert validate_pdf(REAL_PDF).text_coverage > 0.9

    def test_registers_with_provenance(self):
        rec = register_document(
            REAL_PDF,
            source_url="https://www.infosys.com/investors/reports-filings/annual-report/annual/documents/2023-24/consolidated.pdf",
            retrieved_at="2026-08-23",
            company="Infosys Limited",
            fiscal_year="2023-24",
        )
        assert rec.status == "valid"
        assert rec.page_count == 79
        assert len(rec.sha256) == 64
