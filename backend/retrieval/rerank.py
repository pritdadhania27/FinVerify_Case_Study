"""Cross-encoder reranking over the fused candidate pool.

RX-034 decomposed the retrieval miss and found the problem is ordering, not
coverage. On 100 evidence groups over five filings, the gold chunk was in the
top 10 for 0.280 of them, within rank 70 for 0.720, and within 300 for 0.870.
The right chunk is usually a candidate already and is being out-scored by
near-duplicates - the same table from a different year, the standalone twin of a
consolidated statement, a summary that repeats the caption without the figure.

That is precisely what a cross-encoder is for. The bi-encoder behind the vector
leg must embed the query and the passage separately, so it can never condition
one on the other; BM25 sees only term overlap, which near-duplicates share by
construction. A cross-encoder reads the pair jointly and can tell "borrowings as
at March 31, 2024" from "borrowings as at March 31, 2023" - a distinction worth
almost nothing in cosine space and everything to a reader.

**The ceiling this can reach is 0.720, not 1.000.** Reranking reorders a pool;
it cannot retrieve what the pool does not contain. The 0.150 sitting between
rank 70 and rank 300 is a recall problem that a larger candidate pool has to
solve first, and the 13 groups that never rank at all are beyond both. Quoting
any improvement here as though it addressed the whole 0.720 gap would be a
category error.

Local and free: this is a 22M-parameter model on CPU. It costs latency, not
quota, which is why it can be measured while the campaign is quota-blocked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cost is the reason for the guard
    from backend.retrieval.hybrid import RetrievalResult

# MS MARCO cross-encoders are trained on exactly this task - reorder a candidate
# list for a short query - and the L-6 variant is the smallest one that is not
# noticeably worse than its larger siblings on passage ranking. Overridable
# because a model id is a measurement parameter, not a constant: report which
# one produced a number rather than assuming this one did.
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# 512 tokens is the model's own limit. A financial table chunk can exceed it, in
# which case the tail is truncated - recorded here because a silently truncated
# passage scores on its head alone, and the figure being asked about may live in
# the part that was dropped.
MAX_LENGTH = 512


@dataclass
class Reranker:
    """Scores (question, passage) pairs jointly and reorders by that score.

    The model is loaded on first use rather than in __init__ so that
    constructing a retriever stays cheap - the API process builds one per
    request path, and most requests never rerank.
    """

    model_name: str = DEFAULT_RERANK_MODEL
    device: str = "cpu"
    batch_size: int = 32
    _model: object | None = field(default=None, repr=False, compare=False)

    def _loaded(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name, device=self.device, max_length=MAX_LENGTH
            )
        return self._model

    def score(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._loaded()
        raw = model.predict(
            [(question, passage) for passage in passages],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [float(value) for value in raw]

    def rerank(
        self, question: str, results: list[RetrievalResult], *, top_k: int | None = None
    ) -> list[RetrievalResult]:
        """Reorder `results` by cross-encoder score, keeping fusion's verdict.

        `fused_score` and both leg ranks survive untouched, and the pre-rerank
        position is written to `fused_rank`. Provenance travels with the data
        (ENGINEERING_RULES.md), and without the original position there is no way to answer
        the only question that matters about a reranker after the fact: did it
        move this chunk, and from where?
        """
        if not results:
            return []
        scores = self.score(question, [result.text for result in results])
        stamped = [
            replace(result, fused_rank=position, rerank_score=score)
            for position, (result, score) in enumerate(
                zip(results, scores, strict=True), start=1
            )
        ]
        # Sort by rerank score, breaking ties on the fused position so the order
        # is total and reproducible. Python's sort is stable, but relying on that
        # alone would make the result depend on the pool's incoming order.
        stamped.sort(key=lambda r: (-(r.rerank_score or 0.0), r.fused_rank or 0))
        return stamped[:top_k] if top_k else stamped


def reranker_from_env() -> Reranker | None:
    """Build a reranker if RERANK_MODEL is set, else None.

    Off unless asked for. A reranker changes what evidence every channel sees,
    so turning it on silently would mean two runs with the same config id
    measured different systems.
    """
    model = os.environ.get("RERANK_MODEL", "").strip()
    if not model:
        return None
    return Reranker(
        model_name=model,
        device=os.environ.get("EMBEDDING_DEVICE", "cpu"),
    )
