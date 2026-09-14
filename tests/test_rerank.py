"""The reranker must reorder without losing what fusion already established.

None of these load the cross-encoder. The model's *quality* is an empirical
question answered by scripts/measure_reranking.py against gold, and it answered
it badly (RX-035); what is pinned here is the mechanics around it, which stay
correct regardless of which model is plugged in - that the pre-rerank position
survives, that fusion's own scores are not overwritten, and above all that a
retriever with no reranker configured behaves exactly as it did before this
module existed. That last one is the whole reason the default is None.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.retrieval.hybrid import RetrievalResult  # noqa: E402
from backend.retrieval.rerank import Reranker, reranker_from_env  # noqa: E402


class StubScorer:
    """Returns a score per passage from a lookup, so ordering is decided by the
    test rather than by 22 million downloaded parameters."""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.seen: list[tuple[str, str]] = []

    def predict(self, pairs, **_kwargs):
        self.seen.extend(pairs)
        return [self.scores.get(passage, 0.0) for _question, passage in pairs]


def result(chunk_id: str, text: str, fused_score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id, text=text, payload={"page": 1}, fused_score=fused_score,
        semantic_rank=1, keyword_rank=2, semantic_score=0.9, keyword_score=3.0,
    )


def reranker_with(scores: dict[str, float]) -> tuple[Reranker, StubScorer]:
    stub = StubScorer(scores)
    reranker = Reranker()
    reranker._model = stub
    return reranker, stub


class TestItActuallyReorders:
    def test_the_highest_scoring_passage_comes_first(self):
        reranker, _ = reranker_with({"weak": 0.1, "strong": 9.0})
        pool = [result("a", "weak", 0.5), result("b", "strong", 0.4)]
        assert [r.chunk_id for r in reranker.rerank("q", pool)] == ["b", "a"]

    def test_top_k_truncates_after_reordering_not_before(self):
        """Truncating first would discard the chunk the reranker exists to
        promote - the whole point is that it is currently ranked too low."""
        reranker, _ = reranker_with({"first": 0.0, "second": 0.0, "buried": 9.0})
        pool = [result("a", "first", 0.9), result("b", "second", 0.8),
                result("c", "buried", 0.1)]
        assert [r.chunk_id for r in reranker.rerank("q", pool, top_k=1)] == ["c"]

    def test_negative_scores_order_correctly(self):
        """Cross-encoder logits are routinely negative - a naive `or 0.0`
        fallback in the sort key would sort every negative below an absent one."""
        reranker, _ = reranker_with({"bad": -11.4, "good": -0.5})
        pool = [result("a", "bad", 0.9), result("b", "good", 0.1)]
        assert [r.chunk_id for r in reranker.rerank("q", pool)] == ["b", "a"]

    def test_ties_fall_back_to_the_fused_order(self):
        reranker, _ = reranker_with({"x": 5.0, "y": 5.0})
        pool = [result("a", "x", 0.9), result("b", "y", 0.8)]
        assert [r.chunk_id for r in reranker.rerank("q", pool)] == ["a", "b"]


class TestProvenanceSurvives:
    def test_the_pre_rerank_position_is_recorded(self):
        reranker, _ = reranker_with({"weak": 0.1, "strong": 9.0})
        pool = [result("a", "weak", 0.5), result("b", "strong", 0.4)]
        promoted = reranker.rerank("q", pool)[0]
        assert promoted.fused_rank == 2, "cannot audit a promotion without its origin"
        assert promoted.rerank_score == 9.0

    def test_fusion_scores_are_not_overwritten(self):
        """A reranked result must still answer 'what did fusion think?' - the
        two orderings are compared in the error analysis."""
        reranker, _ = reranker_with({"weak": 0.1, "strong": 9.0})
        pool = [result("a", "weak", 0.5), result("b", "strong", 0.4)]
        promoted = reranker.rerank("q", pool)[0]
        assert promoted.fused_score == 0.4
        assert promoted.semantic_rank == 1 and promoted.keyword_rank == 2

    def test_the_question_reaches_the_model_paired_with_each_passage(self):
        reranker, stub = reranker_with({"x": 1.0, "y": 2.0})
        reranker.rerank("what were borrowings", [result("a", "x", 0.5),
                                                 result("b", "y", 0.4)])
        assert stub.seen == [("what were borrowings", "x"),
                             ("what were borrowings", "y")]


class TestFailureCases:
    def test_an_empty_pool_is_not_an_error(self):
        reranker, stub = reranker_with({})
        assert reranker.rerank("q", []) == []
        assert stub.seen == [], "an empty pool must not reach the model at all"

    def test_scoring_no_passages_returns_no_scores(self):
        reranker, _ = reranker_with({})
        assert reranker.score("q", []) == []


class TestItIsOffUnlessAskedFor:
    """A reranker changes what evidence every channel sees. Enabling it by
    accident would mean two runs sharing a config id measured different
    systems - so absence of configuration must mean absence of reranking."""

    def test_no_env_variable_means_no_reranker(self, monkeypatch):
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        assert reranker_from_env() is None

    def test_an_empty_or_whitespace_value_is_not_a_model_id(self, monkeypatch):
        monkeypatch.setenv("RERANK_MODEL", "   ")
        assert reranker_from_env() is None

    def test_a_named_model_is_honoured(self, monkeypatch):
        monkeypatch.setenv("RERANK_MODEL", "cross-encoder/whatever")
        built = reranker_from_env()
        assert built is not None and built.model_name == "cross-encoder/whatever"

    def test_a_retriever_without_one_returns_plain_fusion(self):
        """The regression that matters most: every measurement taken before this
        module existed must still be reproducible by the same code path."""
        from backend.retrieval.hybrid import HybridRetriever

        assert HybridRetriever(index=None, embedder=None).reranker is None

    def test_an_unreranked_result_carries_no_rerank_provenance(self):
        plain = result("a", "x", 0.5)
        assert plain.fused_rank is None and plain.rerank_score is None
