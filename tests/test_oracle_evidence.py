"""The oracle arm must isolate reasoning, and must not become something else.

It exists because H2 needs errors made WITH the evidence in hand, and the
end-to-end system produces roughly one per fifteen questions (RX-039 had four).
Handing each question the chunks containing its gold evidence makes every
remaining error a reasoning error by construction.

Two ways that goes wrong, and both are silent:

  * the oracle degrades into a CLOSED-BOOK arm, and its failures are then
    counted as reasoning errors when the model was given nothing at all;
  * the oracle leaks the gold ANSWER rather than its location, and the arm
    measures transcription.

Everything here is aimed at one or the other.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.orchestrator import ArmConfig, _node_retrieve  # noqa: E402
from evaluation.oracle_evidence import OracleEvidence  # noqa: E402

CHUNKS = [
    {"chunk_id": "doc:p13:t0:0", "page": 13, "section": "Notes",
     "company": "Acme Ltd", "fiscal_year": "2023-24",
     "context_scale": "crore", "context_currency": "INR",
     "text": "Cost of technical sub-contractors | | 12,232 14,062 |"},
    {"chunk_id": "doc:p58:t0:1", "page": 58, "section": "Other",
     "text": "Travel expenses | | 1,204 1,110 |"},
]


@pytest.fixture()
def cache(tmp_path):
    path = tmp_path / "doc.chunks.jsonl"
    path.write_text(
        "\n".join([json.dumps({"document_id": "doc", "pages": 79})]
                  + [json.dumps(c) for c in CHUNKS]),
        encoding="utf-8",
    )
    return OracleEvidence(root=tmp_path)


def _question(**over):
    base = {
        "document_id": "doc",
        "evidence": [{
            "group_id": "g1",
            "any_of": [{"page": 13,
                        "anchors": ["Cost of technical sub-contractors", "12232"]}],
        }],
    }
    base.update(over)
    return base


class TestItFindsTheChunkAndNotTheAnswer:
    def test_it_resolves_a_gold_group_to_its_chunk(self, cache):
        blocks = cache.blocks_for(_question())
        assert [b.chunk_id for b in blocks] == ["doc:p13:t0:0"]

    def test_the_block_is_the_whole_chunk_the_channels_would_have_seen(self, cache):
        """Not the span, not the value - the chunk. The model still has to read
        the right row of the right table and apply the right scale."""
        block = cache.blocks_for(_question())[0]
        assert block.text == CHUNKS[0]["text"]
        assert "14,062" in block.text, "the distractor column comes along with it"

    def test_it_carries_the_scale_and_currency_context(self, cache):
        block = cache.blocks_for(_question())[0]
        assert (block.scale, block.currency) == ("crore", "INR")

    def test_the_first_line_of_the_cache_is_a_manifest_not_a_chunk(self, cache):
        """It has no chunk_id, and reading it as one would put a page-less
        record into the evidence."""
        assert all(b.chunk_id for b in cache.blocks_for(_question()))

    def test_a_dataset_object_works_as_well_as_a_dict(self, cache):
        class Q:
            document_id = "doc"
            evidence = _question()["evidence"]

        assert cache.blocks_for(Q())[0].chunk_id == "doc:p13:t0:0"


class TestItRefusesToBuildAPartialOracle:
    """A partial oracle is worse than none: the question enters the reasoning
    stratum and its failure is counted as a reasoning error when the evidence
    was never actually supplied."""

    def test_a_group_nothing_satisfies_yields_no_blocks_at_all(self, cache):
        q = _question(evidence=[
            {"group_id": "g1", "any_of": [{"page": 13, "anchors": ["12232"]}]},
            {"group_id": "g2", "any_of": [{"page": 99, "anchors": ["nowhere"]}]},
        ])
        assert cache.blocks_for(q) == [], "one unsatisfiable group voids the oracle"

    def test_a_question_with_no_gold_evidence_yields_nothing(self, cache):
        assert cache.blocks_for(_question(evidence=[])) == []

    def test_an_unknown_document_yields_nothing(self, cache):
        assert cache.blocks_for(_question(document_id="missing")) == []

    def test_the_grouped_numeral_still_has_to_match(self, cache):
        """The anchor is "12232" and the filing prints "12,232". If `degroup`
        regressed, the oracle would silently resolve nothing and every oracle
        question would look unbuildable."""
        assert cache.blocks_for(_question()), "degroup-aware matching is required here"


class TestTheOracleArmIsNotAClosedBookArm:
    """`_node_retrieve` tests `deps.retriever is None` for the closed-book path.
    An oracle arm needs no retriever, so if that test ran first the arm would
    quietly become B1 - handed nothing, and every failure recorded as a
    reasoning error."""

    class _Deps:
        retriever = None
        document_id = None

    def _state(self, blocks):
        return {
            "config": ArmConfig(name="O", retrieval="oracle"),
            "deps": self._Deps(),
            "spec": None,
            "oracle_blocks": blocks,
        }

    def test_oracle_wins_over_the_closed_book_branch_with_no_retriever(self):
        out = _node_retrieve(self._state(["block"]))
        assert out["blocks"] == ["block"]
        assert out["node_log"][0]["mode"] == "oracle"

    def test_an_unbuildable_oracle_is_recorded_as_such_not_as_closed_book(self):
        out = _node_retrieve(self._state([]))
        assert out["node_log"][0]["mode"] == "oracle"
        assert out["node_log"][0]["oracle_available"] is False

    def test_the_mode_is_named_in_the_log_so_the_artifact_says_which_arm_this_was(self):
        out = _node_retrieve(self._state(["a", "b"]))
        assert out["node_log"][0] == {
            "node": "retrieve", "mode": "oracle", "blocks": 2, "oracle_available": True,
        }


class TestTheArmIsConfigured:
    def test_oracle_is_an_accepted_retrieval_mode(self):
        assert not ArmConfig(name="O", retrieval="oracle").validate()

    def test_a_typo_is_still_rejected(self):
        assert ArmConfig(name="O", retrieval="orcale").validate()

    def test_arm_O_exists_and_is_a_detector(self):
        from evaluation.arms import ALL_ARMS, detection_arms

        arm = ALL_ARMS["O"]
        assert arm.retrieval == "oracle"
        assert arm.use_natural and arm.use_program, "it is arm A with the retrieval swapped"
        assert "O" in {a.name for a in detection_arms()}, "it must reach the detection table"
