"""Hybrid retrieval: semantic + keyword + fusion (spec Module 6).

Semantic search alone is weak on this corpus. A dense vector of a financial table
is dominated by the general shape of "a financial table" rather than by which
line items it holds, so the scores bunch tightly and separate poorly. BM25 does
not have that problem: "trade payables" is a rare literal term and lexical
matching finds it at once. The converse also holds in principle - BM25 cannot
serve "what did the company owe suppliers", where no query term appears in the
document at all.

Fusion is **Reciprocal Rank Fusion**: it combines *ranks* rather than scores,
because cosine similarity (~0.5-0.6, tightly bunched) and BM25 (unbounded,
corpus-dependent) are not on comparable scales, and picking a normalisation would
silently become a tuned parameter nobody reported.

**Measured twice, with opposite results, and the second one is why
`strip_boilerplate` exists.** On raw questions, fusion lost to BM25 alone at every
K and a weight sweep was monotonically bad (RX-001, RX-002) - which read as a
verdict on fusion. It was not. The questions were carrying reporting-period
scaffolding that matches a third of the corpus, and the dense leg suffers from
that far more than the lexical one does. With the scaffolding stripped, every
non-zero weight beats BM25 alone and the sweep is *flat* across all of them
(RX-004): plain RRF is correct and there is no weight to tune.

`semantic_weight` / `keyword_weight` therefore stay at 1.0 and are kept only as
an ablation handle. Anything else would be a hyperparameter tuned on the
validation split and would have to be reported as one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from backend.rag.embedding import Embedder
from backend.rag.indexing import QdrantIndex, SearchHit
from backend.retrieval.query import strip_question_boilerplate

__all__ = ["RetrievalResult", "HybridRetriever", "RRF_K", "tokenize"]

# Standard RRF constant (Cormack et al.). Damps the influence of the very top
# ranks so one leg cannot dominate on a single confident hit.
RRF_K = 60

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.,'-]*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping numerals intact.

    Numbers are deliberately preserved: "2,071" is a legitimate and highly
    selective query term when checking whether a specific figure appears in the
    corpus, which is exactly what verification needs to do.
    """
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    text: str
    payload: dict
    fused_score: float
    semantic_rank: int | None = None
    keyword_rank: int | None = None
    semantic_score: float | None = None
    keyword_score: float | None = None
    # Set only when a reranker ran. `fused_rank` is the position this chunk held
    # BEFORE reranking, kept so a reordering can be audited after the fact -
    # without it there is no way to tell a reranker that promoted the right
    # chunk from one that left the order alone. Both stay None on the plain
    # hybrid path, which is how a reader tells the two configurations apart.
    fused_rank: int | None = None
    rerank_score: float | None = None

    @property
    def citation(self) -> str:
        return self.payload.get("citation", "")

    @property
    def found_by(self) -> str:
        """Which leg surfaced this - useful for retrieval error analysis."""
        if self.semantic_rank is not None and self.keyword_rank is not None:
            return "both"
        return "semantic" if self.semantic_rank is not None else "keyword"


@dataclass
class HybridRetriever:
    """Semantic + BM25 with reciprocal-rank fusion and metadata filtering.

    The BM25 index is built over the same filtered candidate set the vector
    search draws from, so the two legs never disagree about what the corpus is.
    It is cached per filter, since rebuilding it on every query would dominate
    latency on a corpus of this size.
    """

    index: QdrantIndex
    embedder: Embedder
    candidate_multiplier: int = 5
    # Equal weights are plain RRF. They are NOT the obvious default here:
    # measured on the Infosys statements, plain RRF scored below BM25 alone at
    # every K, because averaging a strong ranker with a weak one lands between
    # them. Any value other than 1.0 is a parameter tuned on validation and must
    # be reported as one (see EXPERIMENTS.md RX-001/RX-002).
    semantic_weight: float = 1.0
    keyword_weight: float = 1.0
    # Strip interrogative and reporting-period scaffolding before searching. On
    # by default because it is the largest measured retrieval gain in this module
    # (RX-004); the flag exists so it can be ablated rather than assumed.
    strip_boilerplate: bool = True
    # Off by default. RX-034 measured the headroom a reranker could reach (0.720
    # against 0.280 at rank 10) but a headroom is not a result, and turning this
    # on by default would change what evidence every channel sees mid-campaign.
    reranker: object | None = None
    # How deep to rerank. RX-034's bands are the whole argument for this number:
    # the gold chunk is within rank 20 for 0.440 of groups and within 70 for
    # 0.720, so a pool of 40 - what candidate_multiplier=5 gives at top_k=8 -
    # leaves the largest available band unreachable no matter how good the
    # reranker is. 100 covers it with margin; going deeper buys 0.150 more at
    # roughly triple the latency, which is a trade to make against measurement,
    # not in advance.
    rerank_candidates: int = 100
    _bm25_cache: dict = field(default_factory=dict, repr=False)

    def _bm25_for(self, filter_key: str, query_filter) -> tuple[BM25Okapi, list[dict]]:
        cached = self._bm25_cache.get(filter_key)
        if cached is not None:
            return cached
        payloads = self.index.scroll_all(query_filter=query_filter)
        corpus = [tokenize(p.get("text", "")) for p in payloads]
        # BM25Okapi divides by average document length, so an empty corpus is a
        # ZeroDivisionError rather than an empty result.
        bm25 = BM25Okapi(corpus) if corpus else None
        self._bm25_cache[filter_key] = (bm25, payloads)
        return bm25, payloads

    def _legs(
        self, question: str, query_filter, filter_key: str, candidates: int
    ) -> tuple[list[SearchHit], list[tuple[dict, float]]]:
        if self.strip_boilerplate:
            question = strip_question_boilerplate(question)
        query_vector = self.embedder.embed_queries([question]).vectors[0]
        semantic: list[SearchHit] = self.index.search(
            query_vector, limit=candidates, query_filter=query_filter
        )

        bm25, payloads = self._bm25_for(filter_key, query_filter)
        keyword: list[tuple[dict, float]] = []
        if bm25 is not None:
            scores = bm25.get_scores(tokenize(question))
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            keyword = [
                (payloads[i], float(scores[i])) for i in ranked[:candidates] if scores[i] > 0
            ]
        return semantic, keyword

    @staticmethod
    def _fuse(
        semantic: list[SearchHit],
        keyword: list[tuple[dict, float]],
        *,
        semantic_weight: float = 1.0,
        keyword_weight: float = 1.0,
    ) -> list[RetrievalResult]:
        """Reciprocal rank fusion over the two legs. Returns the FULL ordering.

        Untruncated on purpose: the evaluation harness needs to compare the fused
        ordering against each leg's own ordering over the same candidate set, and
        truncating here would silently drop items a single-leg arm still ranks.
        """
        fused: dict[str, dict] = {}

        def entry_for(chunk_id: str, payload: dict, text: str) -> dict:
            return fused.setdefault(
                chunk_id,
                {"payload": payload, "text": text, "score": 0.0,
                 "s_rank": None, "k_rank": None, "s_score": None, "k_score": None},
            )

        for rank, hit in enumerate(semantic, start=1):
            entry = entry_for(hit.chunk_id, hit.payload, hit.text)
            entry["score"] += semantic_weight / (RRF_K + rank)
            entry["s_rank"] = rank
            entry["s_score"] = hit.score

        for rank, (payload, score) in enumerate(keyword, start=1):
            entry = entry_for(
                payload.get("chunk_id", ""), payload, payload.get("text", "")
            )
            entry["score"] += keyword_weight / (RRF_K + rank)
            entry["k_rank"] = rank
            entry["k_score"] = score

        ordered = sorted(fused.items(), key=lambda kv: kv[1]["score"], reverse=True)
        return [
            RetrievalResult(
                chunk_id=chunk_id,
                text=entry["text"],
                payload=entry["payload"],
                fused_score=entry["score"],
                semantic_rank=entry["s_rank"],
                keyword_rank=entry["k_rank"],
                semantic_score=entry["s_score"],
                keyword_score=entry["k_score"],
            )
            for chunk_id, entry in ordered
        ]

    def _fused(
        self, semantic: list[SearchHit], keyword: list[tuple[dict, float]]
    ) -> list[RetrievalResult]:
        return self._fuse(
            semantic,
            keyword,
            semantic_weight=self.semantic_weight,
            keyword_weight=self.keyword_weight,
        )

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 10,
        company: str | None = None,
        fiscal_year: str | None = None,
        document_id: str | None = None,
        # Spec section 27 requires filtering by section, and the index has
        # supported it all along - `build_filter` takes it and the payload field
        # is indexed. It was simply never threaded through here, so the only
        # entry point the pipeline uses could not reach it. An index-layer
        # capability nothing can call is not a capability.
        section: str | None = None,
        kind: str | None = None,
        pages: tuple[int, int] | None = None,
    ) -> list[RetrievalResult]:
        query_filter = self.index.build_filter(
            company=company,
            fiscal_year=fiscal_year,
            document_id=document_id,
            section=section,
            kind=kind,
            pages=pages,
        )
        # The cache key must name every filter, or two different filters share a
        # cached candidate set and the second silently gets the first's results.
        filter_key = repr((company, fiscal_year, document_id, section, kind, pages))
        candidates = max(top_k * self.candidate_multiplier, top_k)
        if self.reranker is not None:
            # The reranker can only reorder what fusion hands it, so the pool has
            # to be drawn deep enough to contain the chunk it is supposed to
            # promote. Widening it here rather than raising candidate_multiplier
            # keeps the un-reranked path measuring exactly what it measured
            # before - otherwise the comparison would confound a deeper pool with
            # the reranker itself.
            candidates = max(candidates, self.rerank_candidates)
        semantic, keyword = self._legs(question, query_filter, filter_key, candidates)
        fused = self._fused(semantic, keyword)
        if self.reranker is None:
            return fused[:top_k]
        # The FULL question, not the boilerplate-stripped form the legs use.
        # Stripping helps a bag-of-words scorer and a bi-encoder because
        # "what was the" adds only noise to term overlap. A cross-encoder is
        # trained on MS MARCO, whose queries are natural questions, and it reads
        # the pair jointly - the interrogative is signal about what is being
        # asked for. This is an assumption, not a measurement, and it is the
        # first thing to ablate if the reranker underperforms.
        return self.reranker.rerank(question, fused[: self.rerank_candidates], top_k=top_k)

    def retrieve_arms(
        self,
        question: str,
        *,
        top_k: int = 10,
        **filters,
    ) -> dict[str, list[RetrievalResult]]:
        """The same query as three arms: semantic only, keyword only, and fused.

        Exists so that "hybrid beats either leg alone" is a measurement rather
        than a claim in a docstring. All three arms are computed from one pass
        over the same candidate set, so any difference between them is fusion and
        nothing else - not a different filter, a different corpus snapshot, or a
        re-embedded query.
        """
        query_filter = self.index.build_filter(**filters)
        filter_key = repr(tuple(sorted(filters.items())))
        candidates = max(top_k * self.candidate_multiplier, top_k)
        semantic, keyword = self._legs(question, query_filter, filter_key, candidates)

        return {
            "semantic": [
                RetrievalResult(
                    chunk_id=hit.chunk_id, text=hit.text, payload=hit.payload,
                    fused_score=hit.score, semantic_rank=rank, semantic_score=hit.score,
                )
                for rank, hit in enumerate(semantic[:top_k], start=1)
            ],
            "keyword": [
                RetrievalResult(
                    chunk_id=payload.get("chunk_id", ""), text=payload.get("text", ""),
                    payload=payload, fused_score=score, keyword_rank=rank,
                    keyword_score=score,
                )
                for rank, (payload, score) in enumerate(keyword[:top_k], start=1)
            ],
            "hybrid": self._fused(semantic, keyword)[:top_k],
        }

    def clear_cache(self) -> None:
        self._bm25_cache.clear()
