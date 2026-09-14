"""What is actually in a vector collection, and which model put it there.

This module exists because of a defect that survived a green test suite, a
healthy `docker compose ps`, a passing `/health`, and 978 tests.

D2a switched the embedding model from `BAAI/bge-base-en-v1.5` to
`intfloat/e5-base-v2` on measured evidence, and re-indexed into a new collection
named `finverify_e5`. It updated `EMBEDDING_MODEL` in `.env`. It did **not**
update `QDRANT_COLLECTION`, which kept pointing at `finverify_chunks` - the
superseded BGE index.

Both models emit 768 dimensions. So Qdrant accepted E5 query vectors against a
BGE index and returned results, ranked, scored, and wrong. Measured on the
phrase "total equity attributable to owners of the company":

    query model   collection          top score   top hit
    e5            finverify_chunks       0.5403   Infosys p.67   (wrong page)
    bge           finverify_chunks       0.7761   Infosys p.12   (the balance sheet)
    e5            finverify_e5           0.8865   the right page
    bge           finverify_e5           0.4246   wrong

Nothing errors. Nothing warns. Retrieval just gets quietly worse, and a
downstream reader attributes it to the method.

A dimension check cannot catch this - the dimensions match. The only thing that
can is recording which model built the collection and refusing to query it with
a different one. That is what this file is for.

The manifest lives in a file rather than in Qdrant because files are this
project's source of truth (D4, extended by D28), and because a manifest stored
inside the thing it describes cannot report that the thing is missing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backend.core.paths import project_path

__all__ = [
    "IndexManifest",
    "IndexConfigurationError",
    "MANIFEST_PATH",
    "load_manifest",
    "write_manifest",
    "verify",
    "preflight",
]

MANIFEST_PATH = project_path("documents/index_manifest.json")


class IndexConfigurationError(RuntimeError):
    """The configured index cannot answer the questions about to be asked.

    Raised rather than warned. A warning about a retrieval misconfiguration
    scrolls past and the campaign runs anyway; that is precisely the failure
    this module was written to prevent.
    """


@dataclass(frozen=True)
class IndexManifest:
    collection: str
    embedding_model: str
    dimension: int
    point_count: int
    companies: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    built_at: str = ""
    # How this record came to exist. "indexer" means index_corpus.py wrote it as
    # it indexed and is authoritative. "derived" means it was reconstructed from
    # a live collection plus a recorded experiment artifact - trustworthy, but
    # worth saying so rather than presenting reconstruction as observation.
    source: str = "indexer"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, data: dict) -> IndexManifest:
        return cls(
            collection=data["collection"],
            embedding_model=data["embedding_model"],
            dimension=int(data["dimension"]),
            point_count=int(data.get("point_count", 0)),
            companies=tuple(data.get("companies", ())),
            document_ids=tuple(data.get("document_ids", ())),
            built_at=data.get("built_at", ""),
            source=data.get("source", "indexer"),
            notes=tuple(data.get("notes", ())),
        )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all(path: Path | str = MANIFEST_PATH) -> dict[str, IndexManifest]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: IndexManifest.from_json(entry) for name, entry in raw.items()}


def load_manifest(collection: str, path: Path | str = MANIFEST_PATH) -> IndexManifest | None:
    return load_all(path).get(collection)


def write_manifest(manifest: IndexManifest, path: Path | str = MANIFEST_PATH) -> None:
    """Record one collection. Other collections in the file are left alone.

    Keyed by collection name so a machine can hold several - the superseded BGE
    index is still on this host, and a manifest that could only describe one
    collection would have to pretend the other did not exist.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_all(path)
    stamped = manifest if manifest.built_at else _replace_built_at(manifest, _now())
    existing[manifest.collection] = stamped
    path.write_text(
        json.dumps(
            {name: m.to_json() for name, m in sorted(existing.items())},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _replace_built_at(manifest: IndexManifest, when: str) -> IndexManifest:
    data = manifest.to_json()
    data["built_at"] = when
    return IndexManifest.from_json(data)


def verify(
    *,
    collection: str,
    embedding_model: str,
    live_count: int | None = None,
    required_companies: tuple[str, ...] = (),
    indexed_companies: tuple[str, ...] | None = None,
    path: Path | str = MANIFEST_PATH,
) -> IndexManifest:
    """Refuse to proceed unless the index can answer the questions being asked.

    Returns the manifest on success; raises `IndexConfigurationError` naming the
    exact mismatch and the exact command that fixes it. Every message is written
    to be actionable at 2am by someone who did not write this code.
    """
    manifest = load_manifest(collection, path)
    if manifest is None:
        known = ", ".join(sorted(load_all(path))) or "none recorded"
        raise IndexConfigurationError(
            f"no index manifest for collection {collection!r} (known: {known}).\n"
            f"  Either the collection was built before manifests existed, or "
            f"QDRANT_COLLECTION names a collection nothing indexed.\n"
            f"  Fix:  python scripts/index_corpus.py --collection {collection} "
            f"--model {embedding_model} --write-manifest"
        )

    if manifest.embedding_model != embedding_model:
        raise IndexConfigurationError(
            f"embedding model mismatch on collection {collection!r}.\n"
            f"  indexed with : {manifest.embedding_model}\n"
            f"  querying with: {embedding_model}\n"
            f"  Both may emit the same number of dimensions, so Qdrant will NOT "
            f"error - it will return confidently ranked, wrong results.\n"
            f"  Fix: set EMBEDDING_MODEL={manifest.embedding_model}, or point "
            f"QDRANT_COLLECTION at a collection built with {embedding_model}."
        )

    if live_count is not None and live_count == 0:
        raise IndexConfigurationError(
            f"collection {collection!r} is empty. Nothing has been indexed into it.\n"
            f"  Fix:  python scripts/index_corpus.py --collection {collection}"
        )

    missing = tuple(c for c in required_companies if c not in (indexed_companies or ()))
    if required_companies and indexed_companies is not None and missing:
        raise IndexConfigurationError(
            f"collection {collection!r} does not cover the registered corpus.\n"
            f"  registered but NOT indexed: {', '.join(missing)}\n"
            f"  Questions about these companies would retrieve some other "
            f"company's evidence, and both channels would reason over it.\n"
            f"  Fix:  python scripts/index_corpus.py --collection {collection}"
        )

    return manifest


def preflight(
    index,
    *,
    embedding_model: str,
    required_companies: tuple[str, ...] = (),
    path: Path | str = MANIFEST_PATH,
) -> IndexManifest:
    """`verify`, with the live figures read off the index for you.

    Takes the index by duck-type rather than by import so this module stays
    free of the Qdrant client and remains testable against a stub. Call this
    once at the top of anything that is about to retrieve - a campaign, a
    slice, a retrieval evaluation, an API startup.
    """
    return verify(
        collection=index.collection,
        embedding_model=embedding_model,
        live_count=index.count(),
        required_companies=required_companies,
        indexed_companies=index.indexed_companies(list(required_companies)),
        path=path,
    )
