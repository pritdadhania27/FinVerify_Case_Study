"""Extract, chunk, embed and index the registered corpus (spec Modules 3, 6, 19).

One command rebuilds the retrieval index from the raw PDFs, so a retrieval
measurement can always be reproduced from documents/registry.json rather than
from whatever happened to be sitting in Qdrant.

Extraction is cached to `documents/processed/<document_id>.chunks.jsonl`. Camelot
takes minutes per report, and the whole point of the cache is that swapping the
embedding model - which is how decision D2 gets settled - does not re-run it.
The cache records the chunker's parameters, so a chunking change invalidates it
instead of silently serving stale chunks.

Usage:
    python scripts/index_corpus.py                      # all registered documents
    python scripts/index_corpus.py --company "Infosys Limited"
    python scripts/index_corpus.py --model intfloat/e5-base-v2 --collection finverify_e5
    python scripts/index_corpus.py --rechunk             # ignore the chunk cache
    python scripts/index_corpus.py --status              # report without indexing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from dotenv import load_dotenv  # noqa: E402

from backend.documents.acquisition import DocumentRegistry  # noqa: E402
from backend.documents.extraction import extract_document  # noqa: E402
from backend.rag.chunking import (  # noqa: E402
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    TABLE_MAX_CHARS,
    Chunk,
    chunk_document,
    deserialise_chunk,
    serialise_chunk,
)
from backend.rag.embedding import Embedder  # noqa: E402
from backend.rag.indexing import QdrantIndex  # noqa: E402
from backend.rag.manifest import IndexManifest, write_manifest  # noqa: E402

REGISTRY = PROJECT_ROOT / "documents/registry.json"
CACHE_DIR = PROJECT_ROOT / "documents/processed"

# Bumped whenever chunking changes shape in a way that invalidates cached output.
CHUNKER_VERSION = 3


def _cache_key() -> dict:
    return {
        "chunker_version": CHUNKER_VERSION,
        "max_chars": DEFAULT_MAX_CHARS,
        "overlap": DEFAULT_OVERLAP,
        "table_max_chars": TABLE_MAX_CHARS,
    }


def load_or_build_chunks(record, *, rechunk: bool = False) -> tuple[list[Chunk], str]:
    """Chunks for one document, from cache when the cache is still valid."""
    cache = CACHE_DIR / f"{record.document_id}.chunks.jsonl"

    if cache.exists() and not rechunk:
        with cache.open(encoding="utf-8") as fh:
            header = json.loads(fh.readline())
            if header.get("cache_key") == _cache_key() and header.get("sha256") == record.sha256:
                cached = [deserialise_chunk(json.loads(ln)) for ln in fh if ln.strip()]
                return cached, "cached"

    document = extract_document(record.path, record.document_id)
    if document.quality.problems:
        print(f"          extraction problems: {'; '.join(document.quality.problems)}")
    chunks = chunk_document(
        document, company=record.company, fiscal_year=record.fiscal_year
    )

    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w", encoding="utf-8") as fh:
        # A header line carries the invalidation key, so a stale cache is
        # detected rather than trusted.
        fh.write(
            json.dumps(
                {
                    "document_id": record.document_id,
                    "sha256": record.sha256,
                    "cache_key": _cache_key(),
                    "pages": document.quality.pages_total,
                    "tables": document.quality.tables_found,
                    "scale_context_coverage": round(document.quality.scale_context_coverage, 4),
                }
            )
            + "\n"
        )
        for chunk in chunks:
            fh.write(json.dumps(serialise_chunk(chunk), ensure_ascii=False) + "\n")
    return chunks, "extracted"


def main() -> int:
    # BEFORE the parser, not after. argparse evaluates `default=` at
    # add_argument() time, so a load_dotenv(PROJECT_ROOT / ".env") further down would be read too
    # late and .env would be silently ignored in favour of the literals below.
    # That ordering bug is half of what let D-1 survive: the campaign runner
    # happened to escape the wrong collection only because its .env never
    # reached its defaults.
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", help="index only this company")
    parser.add_argument("--document-id", help="index only this document id")
    parser.add_argument(
        "--model",
        # The default names the model D2a chose by measurement (RX-003). It
        # previously named BGE, which D2a superseded - so a bare-shell run
        # embedded with one model into a collection built by the other.
        default=os.environ.get("EMBEDDING_MODEL", "intfloat/e5-base-v2"),
        help="sentence-transformers model",
    )
    parser.add_argument(
        "--collection", default=os.environ.get("QDRANT_COLLECTION", "finverify_e5")
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--device", default=os.environ.get("EMBEDDING_DEVICE", "cpu"))
    parser.add_argument("--rechunk", action="store_true", help="ignore the chunk cache")
    parser.add_argument("--recreate", action="store_true", help="drop and rebuild the collection")
    parser.add_argument("--status", action="store_true", help="report without indexing")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="record what is already in the collection and exit, without re-indexing",
    )
    args = parser.parse_args()

    registry = DocumentRegistry(REGISTRY)
    records = [
        r
        for r in registry.all()
        if (args.company is None or r.company == args.company)
        and (args.document_id is None or r.document_id == args.document_id)
    ]
    if not records:
        print("no registered documents match the filter", file=sys.stderr)
        return 1

    if args.status:
        for record in records:
            cache = CACHE_DIR / f"{record.document_id}.chunks.jsonl"
            state = "cached" if cache.exists() else "not chunked"
            print(f"{state:12} {record.company} ({record.page_count} pages)")
        return 0

    all_companies = sorted({r.company for r in registry.all()})
    all_document_ids = sorted(r.document_id for r in registry.all())

    if args.write_manifest:
        # Records an existing collection without spending hours re-embedding it.
        # The model cannot be read back out of Qdrant - vectors carry no
        # provenance - so it is taken from --model/.env and the caller is
        # responsible for naming the model that actually built it. Marked
        # `derived` in the manifest so a reader knows it was reconstructed.
        index = QdrantIndex(url=args.qdrant_url, collection=args.collection)
        present = index.indexed_companies(all_companies)
        write_manifest(
            IndexManifest(
                collection=args.collection,
                embedding_model=args.model,
                dimension=index.dimension,
                point_count=index.count(),
                companies=present,
                document_ids=index.indexed_documents(all_document_ids),
                source="derived",
                notes=(
                    (
                        "reconstructed from the live collection; the embedding "
                        "model was supplied by the caller, not read from Qdrant"
                    ),
                ),
            )
        )
        print(f"manifest: {args.collection} <- {args.model}")
        print(f"  {index.count()} points, {len(present)}/{len(all_companies)} companies")
        for name in all_companies:
            print(f"    {'yes' if name in present else 'NO ':4} {name}")
        return 0

    print(f"embedding model: {args.model} on {args.device}")
    embedder = Embedder(args.model, device=args.device)
    index = QdrantIndex(
        url=args.qdrant_url, collection=args.collection, dimension=embedder.dimension
    )
    created = index.ensure_collection(recreate=args.recreate)
    print(f"collection: {args.collection} (dim {embedder.dimension}, "
          f"{'created' if created else 'existing'})\n")

    total_chunks = 0
    total_truncated = 0
    started = time.monotonic()

    for record in records:
        print(f"{record.company}")
        chunks, source = load_or_build_chunks(record, rechunk=args.rechunk)
        tables = sum(1 for c in chunks if c.kind.value == "table")
        print(f"          {len(chunks)} chunks ({tables} table, {len(chunks) - tables} text)"
              f" [{source}]")

        result = embedder.embed_passages([c.text for c in chunks])
        if result.truncated_count:
            # Silent truncation drops the trailing rows of a table from its
            # vector with no error raised, so it is reported every time.
            print(f"          WARNING {result.truncated_count} chunks exceed "
                  f"{embedder.max_seq_length} tokens and were truncated")
        total_truncated += result.truncated_count

        upserted = index.upsert(chunks, result.vectors)
        print(f"          indexed {upserted} in {result.elapsed_seconds:.1f}s embedding\n")
        total_chunks += upserted

    elapsed = time.monotonic() - started

    # Written every run, so the record of which model built this collection can
    # never drift from the collection itself. D2a changed the model and left the
    # collection variable pointing at the old index; nothing noticed for days
    # because there was nowhere for that pairing to be checked.
    present = index.indexed_companies(all_companies)
    write_manifest(
        IndexManifest(
            collection=args.collection,
            embedding_model=args.model,
            dimension=embedder.dimension,
            point_count=index.count(),
            companies=present,
            document_ids=index.indexed_documents(all_document_ids),
            source="indexer",
        )
    )

    print(f"{total_chunks} chunks indexed in {elapsed:.0f}s; collection now holds {index.count()}")
    print(f"manifest updated: {args.collection} <- {args.model} "
          f"({len(present)}/{len(all_companies)} companies)")
    if total_truncated:
        print(f"{total_truncated} chunks were truncated by the embedder - retrieval on those "
              f"chunks cannot see their tail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
