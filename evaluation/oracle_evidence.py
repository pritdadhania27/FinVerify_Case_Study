"""The chunks that actually contain a question's gold evidence.

**What this is for.** H2 asks whether dual-channel disagreement detects
*reasoning* errors. Answering it needs errors made with the evidence in hand,
and the end-to-end system rarely produces any: retrieval delivered every gold
group on 34% of test questions, and the model erred on 19% of those, so a
reasoning error arrives once per 15 questions. RX-039 had four, which is not a
stratum.

Oracle retrieval removes the first term. Hand each question the chunks that
contain its gold evidence and `P(evidence delivered)` is 1 by construction, so
**every remaining error is a reasoning error by definition**. That is the exact
population H2 is about and the one the pipeline cannot supply on its own.

**What it is not.** An arm using this is not an end-to-end system measurement
and must never be reported as one - it has been handed the answer's location.
It is a diagnostic that isolates the reasoning step, in the way an oracle-context
condition does throughout RAG work. RX-039 remains the end-to-end result.

**It also removes the distractors.** Real retrieval supplies 8 blocks; this
supplies one per gold group. The condition is therefore "evidence present AND
almost nothing else", not "evidence present" alone, and a low error rate under
it shows the model reasons correctly when handed exactly the right table rather
than when the right table arrives among seven near-misses. Padding with the real
retrieval results up to `top_k` would fix that and is deliberately not done - it
needs a retriever in a path that otherwise needs none. D47 records the trade.

**Why it does not leak the answer.** The blocks are whole retrieved chunks, the
same text the channels would have seen had retrieval found them. The gold
*value* is not injected, no span is highlighted, and the surrounding table rows
and distractor figures come along with it. The model still has to read the right
row of the right table and apply the right scale. What it no longer has to do is
find the page.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.agents.evidence import DEFAULT_MAX_BLOCKS, EvidenceBlock
from evaluation.error_analysis import PROCESSED
from evaluation.metrics.retrieval import EvidenceGroup

__all__ = ["OracleEvidence", "oracle_blocks_for"]


class OracleEvidence:
    """Gold spans -> the chunks containing them, cached per document.

    Reads the same processed chunk cache the run artifacts are resolved
    against, so the text handed to a channel here is byte-identical to what
    retrieval would have handed it.
    """

    def __init__(self, root: Path = PROCESSED) -> None:
        self._root = Path(root)
        self._chunks: dict[str, list[dict]] = {}

    def _document(self, document_id: str) -> list[dict]:
        if document_id in self._chunks:
            return self._chunks[document_id]
        records: list[dict] = []
        path = self._root / f"{document_id}.chunks.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # The first line is a manifest, not a chunk.
                if record.get("chunk_id"):
                    records.append(record)
        self._chunks[document_id] = records
        return records

    def blocks_for(self, question, *, max_blocks: int = DEFAULT_MAX_BLOCKS) -> list[EvidenceBlock]:
        """One block per gold group, in the question's own group order.

        `question` may be a `DatasetQuestion` or the raw dataset dict the
        campaign runner passes around - the two carry the same two fields under
        the same names, and requiring one of them here would mean converting a
        payload back into a model purely to read `.evidence`.

        Returns `[]` when the question has no gold evidence, or when a group's
        spans match nothing in the cache. **Empty is not silently equivalent to
        "no evidence needed"** - the caller must treat it as a failure to build
        the oracle condition rather than as an oracle with nothing in it, which
        would quietly turn this arm into a closed-book one.
        """
        if isinstance(question, dict):
            evidence = question.get("evidence")
            document_id = question.get("document_id")
        else:
            evidence = question.evidence
            document_id = question.document_id
        if not evidence or not document_id:
            return []
        records = self._document(document_id)
        if not records:
            return []

        chosen: list[dict] = []
        seen: set[str] = set()
        for group_record in evidence:
            group = EvidenceGroup.from_dict(group_record)
            for record in records:
                page = record.get("page")
                if page is None:
                    continue
                if not group.satisfied_by(int(page), record.get("text") or ""):
                    continue
                if record["chunk_id"] not in seen:
                    seen.add(record["chunk_id"])
                    chosen.append(record)
                break          # ANY span satisfies a group; one chunk is enough
            else:
                # A group nothing satisfies means the gold points somewhere the
                # chunker never produced. Refuse rather than build a partial
                # oracle, which would put an unretrievable question into the
                # reasoning stratum and count its failure as a reasoning error.
                return []

        return [
            EvidenceBlock(
                ref=f"E{index}",
                text=record.get("text") or "",
                citation=(
                    f"{record.get('company') or ''} "
                    f"{record.get('fiscal_year') or ''} p.{record.get('page')}"
                ).strip()
                or "citation unavailable",
                page=record.get("page"),
                scale=record.get("context_scale"),
                currency=record.get("context_currency"),
                section=record.get("section"),
                chunk_id=record.get("chunk_id"),
            )
            for index, record in enumerate(chosen[:max_blocks], start=1)
        ]


def oracle_blocks_for(question, *, index: OracleEvidence | None = None) -> list[EvidenceBlock]:
    """Convenience wrapper; build one `OracleEvidence` per campaign to reuse its cache."""
    return (index or OracleEvidence()).blocks_for(question)
