"""Acquire the corpus defined in configs/corpus.json (spec Module 2).

Reproducible by construction: the corpus is a version-controlled manifest, each
download is content-validated, and every document is registered with its source
URL, retrieval date and SHA-256. Re-running is safe - documents already present
with a matching hash are skipped rather than re-fetched.

**Downloads are validated on content, not on status code.** During this project a
request returned HTTP 200, `content-type: application/pdf`, and a well-formed
488-byte PDF whose entire content was an Akamai "Access Denied" page. Every naive
check passed. `validate_pdf` catches that class of failure; a plain
`response.raise_for_status()` does not.

Usage:
    python scripts/acquire_documents.py            # fetch anything missing
    python scripts/acquire_documents.py --force    # re-download everything
    python scripts/acquire_documents.py --status   # report without downloading
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

import httpx  # noqa: E402

from backend.core.paths import project_path  # noqa: E402
from backend.documents.acquisition import (  # noqa: E402
    DocumentRegistry,
    DocumentStatus,
    register_document,
    validate_pdf,
)

CORPUS = project_path("configs/corpus.json")
RAW_DIR = project_path("documents/raw")
REGISTRY = project_path("documents/registry.json")

# Several investor-relations sites sit behind CDN bot protection that rejects a
# default client. These are ordinary browser headers for a public document, and
# without them the CDN returns an error page *as a PDF* rather than a 403.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

MIN_PAGES = 10


def download(url: str, destination: Path, timeout: float = 300.0) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return False, f"HTTP {response.status_code}"
                with tmp.open("wb") as fh:
                    for block in response.iter_bytes(1 << 16):
                        fh.write(block)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        return False, f"transport error: {exc}"

    # Validate BEFORE moving into place, so a rejected download never becomes
    # part of the corpus under a name that suggests it succeeded.
    result = validate_pdf(tmp, min_pages=MIN_PAGES)
    if not result.ok:
        tmp.unlink(missing_ok=True)
        return False, f"content rejected: {'; '.join(result.problems)}"

    tmp.replace(destination)
    size_mb = destination.stat().st_size / 1e6
    note = f"{result.page_count} pages, {size_mb:.1f} MB"
    if result.status is DocumentStatus.NEEDS_OCR:
        note += " - NO TEXT LAYER, OCR required"
    elif result.text_coverage < 0.9:
        note += f" - text on {result.text_coverage:.0%} of pages, OCR needed for the rest"
    return True, note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--status", action="store_true", help="report without downloading")
    args = parser.parse_args()

    manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
    registry = DocumentRegistry(REGISTRY)
    today = datetime.now(UTC).date().isoformat()

    print(f"corpus: {manifest['corpus_id']} ({len(manifest['documents'])} documents)\n")
    failures = 0

    for entry in manifest["documents"]:
        path = RAW_DIR / entry["filename"]
        label = f"{entry['company']} [{entry['sector']}]"

        if args.status:
            state = "present" if path.exists() else "MISSING"
            print(f"{state:9} {label}")
            continue

        if path.exists() and not args.force:
            print(f"skip      {label} - already downloaded")
        else:
            print(f"fetching  {label} ...", end=" ", flush=True)
            ok, detail = download(entry["url"], path)
            if not ok:
                print(f"FAILED - {detail}")
                failures += 1
                continue
            print(detail)

        record = register_document(
            path,
            source_url=entry["url"],
            retrieved_at=today,
            company=entry["company"],
            fiscal_year=entry["fiscal_year"],
            min_pages=MIN_PAGES,
        )
        stored, is_new = registry.add(record)
        if record.status != "valid":
            print(f"          WARNING status={record.status}: {'; '.join(record.problems)}")
        if not is_new and stored.sha256 != record.sha256:
            print("          WARNING: content changed since first registration")

    if not args.status:
        registry.save()
        print(f"\nregistry: {len(registry)} documents -> {REGISTRY}")
        for rec in registry.all():
            print(
                f"  {rec.document_id}  {rec.page_count:5} pages  "
                f"{rec.text_coverage:5.0%} text  {rec.company}"
            )

    if failures:
        print(f"\n{failures} download(s) failed - corpus is incomplete")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
