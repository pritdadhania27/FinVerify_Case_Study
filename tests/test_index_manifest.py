"""Tests for the retrieval preflight (defect D-1).

These are research-validity gates, not ordinary unit tests. Each one pins a
property that, when it failed silently, would have published a configuration
error as a finding about the method.

The defect being pinned: D2a switched the embedding model from BGE to E5 and
re-indexed into a new collection, updating `EMBEDDING_MODEL` but not
`QDRANT_COLLECTION`. Both models emit 768 dimensions, so every runtime check in
the system passed - the collection existed, was reachable, was non-empty, and
had the right vector size. Retrieval simply returned the wrong page.

If one of these fails, the finding is that the guard has been weakened. **The
fix is the code, never the test.**
"""

from __future__ import annotations

import json

import pytest

from backend.rag.manifest import (
    IndexConfigurationError,
    IndexManifest,
    load_manifest,
    preflight,
    verify,
    write_manifest,
)

E5 = "intfloat/e5-base-v2"
BGE = "BAAI/bge-base-en-v1.5"

FIVE = (
    "HDFC Bank Limited",
    "Infosys Limited",
    "Reliance Industries Limited",
    "Sun Pharmaceutical Industries Limited",
    "Tata Motors Limited",
)


def manifest(**overrides) -> IndexManifest:
    base = {
        "collection": "finverify_e5",
        "embedding_model": E5,
        "dimension": 768,
        "point_count": 22930,
        "companies": FIVE,
    }
    return IndexManifest(**{**base, **overrides})


class FakeIndex:
    """Stands in for QdrantIndex by duck-type, as `preflight` requires."""

    def __init__(self, collection: str, count: int, companies: tuple[str, ...]):
        self.collection = collection
        self._count = count
        self._companies = companies

    def count(self) -> int:
        return self._count

    def indexed_companies(self, candidates: list[str]) -> tuple[str, ...]:
        return tuple(c for c in candidates if c in self._companies)


# --------------------------------------------------------------------------
# The gate that encodes the defect
# --------------------------------------------------------------------------


def test_same_dimension_different_model_is_still_refused(tmp_path):
    """The whole point. A dimension check cannot catch this; only the model can.

    BGE-base and E5-base are both 768-dimensional. Qdrant accepts either
    against either and returns ranked results with plausible scores. Measured on
    this corpus, querying the BGE collection with E5 returned 0.5403 against
    BGE's own 0.7761 - and, more to the point, a different page.
    """
    path = tmp_path / "m.json"
    stored = manifest(collection="c", embedding_model=BGE, dimension=768)
    write_manifest(stored, path)

    with pytest.raises(IndexConfigurationError) as exc:
        verify(collection="c", embedding_model=E5, path=path)

    message = str(exc.value)
    assert BGE in message and E5 in message, "must name BOTH models, not just complain"
    # The dimensions are equal and the guard still fires. Stated as an
    # assertion so a future refactor cannot quietly reduce this to a size check.
    assert stored.dimension == 768


def test_the_mismatch_message_says_qdrant_will_not_error(tmp_path):
    """An operator who thinks the database would have complained will not look here."""
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="c", embedding_model=BGE), path)

    with pytest.raises(IndexConfigurationError) as exc:
        verify(collection="c", embedding_model=E5, path=path)

    assert "not error" in str(exc.value).lower()


def test_a_matching_pair_is_allowed_through(tmp_path):
    path = tmp_path / "m.json"
    write_manifest(manifest(), path)
    result = verify(collection="finverify_e5", embedding_model=E5, path=path)
    assert result.point_count == 22930


# --------------------------------------------------------------------------
# Corpus coverage
# --------------------------------------------------------------------------


def test_a_collection_missing_companies_is_refused_and_names_them(tmp_path):
    """The other half of D-1: the collection was one company out of five.

    Retrieval would have returned Infosys evidence for an HDFC Bank question,
    and both channels would have reasoned over it - possibly agreeing, since
    they share evidence.
    """
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="small", companies=("Infosys Limited",)), path)

    with pytest.raises(IndexConfigurationError) as exc:
        verify(
            collection="small",
            embedding_model=E5,
            required_companies=FIVE,
            indexed_companies=("Infosys Limited",),
            path=path,
        )

    message = str(exc.value)
    for absent in ("HDFC Bank Limited", "Tata Motors Limited"):
        assert absent in message, f"{absent} is missing and the message must say so"

    # The company that IS present must not appear in the missing list, or the
    # operator re-indexes the wrong thing.
    missing_line = next(ln for ln in message.splitlines() if "NOT indexed" in ln)
    assert "Infosys Limited" not in missing_line


def test_asking_only_for_indexed_companies_passes(tmp_path):
    """A campaign restricted to Infosys must not fail because Tata is absent.

    The requirement is scoped to the questions actually being asked, not to the
    whole registry - otherwise the guard would block legitimate narrow runs and
    get switched off, which is how guards die.
    """
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="small", companies=("Infosys Limited",)), path)
    verify(
        collection="small",
        embedding_model=E5,
        required_companies=("Infosys Limited",),
        indexed_companies=("Infosys Limited",),
        path=path,
    )


def test_an_empty_collection_is_refused(tmp_path):
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="c", point_count=0), path)
    with pytest.raises(IndexConfigurationError, match="empty"):
        verify(collection="c", embedding_model=E5, live_count=0, path=path)


# --------------------------------------------------------------------------
# Missing manifest
# --------------------------------------------------------------------------


def test_an_unknown_collection_is_refused_not_assumed_fine(tmp_path):
    """Absence of a manifest must never be read as absence of a problem."""
    path = tmp_path / "m.json"
    write_manifest(manifest(), path)

    with pytest.raises(IndexConfigurationError) as exc:
        verify(collection="typo_in_env", embedding_model=E5, path=path)

    message = str(exc.value)
    assert "typo_in_env" in message
    assert "finverify_e5" in message, "must list what IS known, to expose a typo"
    assert "index_corpus.py" in message, "must name the command that fixes it"


def test_no_manifest_file_at_all_is_refused(tmp_path):
    with pytest.raises(IndexConfigurationError, match="none recorded"):
        verify(collection="anything", embedding_model=E5, path=tmp_path / "absent.json")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_writing_one_collection_leaves_the_others_alone(tmp_path):
    """Both the current and the superseded index live on this host.

    A manifest that could only describe one collection would have to pretend
    the other did not exist - and the superseded one is exactly the thing a
    misconfiguration points at.
    """
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="finverify_e5", embedding_model=E5), path)
    write_manifest(
        manifest(collection="finverify_chunks", embedding_model=BGE, point_count=906), path
    )

    assert load_manifest("finverify_e5", path).embedding_model == E5
    assert load_manifest("finverify_chunks", path).embedding_model == BGE


def test_a_written_manifest_round_trips(tmp_path):
    path = tmp_path / "m.json"
    original = manifest(document_ids=("a", "b"), notes=("derived by probe",), source="derived")
    write_manifest(original, path)

    restored = load_manifest("finverify_e5", path)
    assert restored.companies == FIVE
    assert restored.document_ids == ("a", "b")
    assert restored.source == "derived"
    assert restored.built_at, "a manifest with no timestamp cannot be aged"


def test_the_file_is_readable_json_keyed_by_collection(tmp_path):
    """A human debugging a retrieval problem at 2am should be able to cat it."""
    path = tmp_path / "m.json"
    write_manifest(manifest(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert list(raw) == ["finverify_e5"]
    assert raw["finverify_e5"]["embedding_model"] == E5


# --------------------------------------------------------------------------
# preflight wiring
# --------------------------------------------------------------------------


def test_preflight_reads_the_live_index_rather_than_trusting_the_manifest(tmp_path):
    """The manifest records what was true at index time; the index is now.

    A manifest claiming 22,930 points against a collection someone has since
    dropped must fail, not pass on the strength of the record.
    """
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="c", point_count=22930), path)

    with pytest.raises(IndexConfigurationError, match="empty"):
        preflight(
            FakeIndex("c", count=0, companies=FIVE), embedding_model=E5, path=path
        )


def test_preflight_passes_on_a_correctly_configured_index(tmp_path):
    path = tmp_path / "m.json"
    write_manifest(manifest(collection="c"), path)
    result = preflight(
        FakeIndex("c", count=22930, companies=FIVE),
        embedding_model=E5,
        required_companies=FIVE,
        path=path,
    )
    assert result.collection == "c"


def test_preflight_catches_the_exact_live_misconfiguration(tmp_path):
    """End to end, the state this machine was actually in on 2026-08-26.

    .env said EMBEDDING_MODEL=e5 and QDRANT_COLLECTION=finverify_chunks. The
    collection was reachable, healthy, non-empty and 768-dimensional, and held
    906 Infosys chunks embedded with BGE.
    """
    path = tmp_path / "m.json"
    write_manifest(
        manifest(
            collection="finverify_chunks",
            embedding_model=BGE,
            point_count=906,
            companies=("Infosys Limited",),
        ),
        path,
    )

    with pytest.raises(IndexConfigurationError) as exc:
        preflight(
            FakeIndex("finverify_chunks", count=906, companies=("Infosys Limited",)),
            embedding_model=E5,
            required_companies=FIVE,
            path=path,
        )

    assert "mismatch" in str(exc.value).lower()
