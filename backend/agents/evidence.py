"""Evidence presentation for the reasoning channels (spec Modules 8, 9).

Both channels read the same retrieved evidence. That is a deliberate design
choice, not an oversight: the research question is whether two *reasoning
processes* over identical evidence disagree informatively. Giving them different
evidence would make disagreement a measure of retrieval variance instead.

It is also the project's largest common-mode failure path (H2). When the evidence
is missing or wrong, both channels are wrong in the same way and agree, so
agreement is near-blind to retrieval-caused error. That is why detection metrics
are stratified by error provenance (D12) rather than pooled.

**One formatter, used by both channels, deliberately.** If Channel A and Channel B
saw the same chunks rendered differently, a disagreement could come from the
rendering rather than the reasoning, and the independence claim would be
untestable. The formatter is shared; the *prompts* around it are structurally
disjoint (D1).

Every rendered block carries its citation and its unit banner, because a figure
without them is not evidence - it is a number.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EvidenceBlock", "format_evidence", "evidence_from_results"]

# Enough context to answer, short enough to leave the model room to reason. A
# free-tier model given 20 blocks spends its budget reading rather than
# answering, and these models return an empty string when the budget runs out
# (D17).
DEFAULT_MAX_BLOCKS = 8
DEFAULT_MAX_CHARS_PER_BLOCK = 1_200


@dataclass(frozen=True)
class EvidenceBlock:
    """One retrieved chunk, ready to be shown to a reasoning channel."""

    ref: str
    text: str
    citation: str
    page: int | None = None
    # Carried so a run artifact can name the exact chunk without storing its
    # text. Error analysis re-reads the chunk from the processed cache by id,
    # which keeps the artifact small AND makes the evidence check exact rather
    # than a match against a truncated copy.
    chunk_id: str | None = None
    scale: str | None = None
    currency: str | None = None
    section: str | None = None

    def render(self, *, max_chars: int = DEFAULT_MAX_CHARS_PER_BLOCK) -> str:
        units = " ".join(x for x in (self.currency, self.scale) if x)
        header = f"[{self.ref}] {self.citation}"
        if self.section:
            header += f" | {self.section}"
        if units:
            header += f" | figures in {units}"
        body = self.text.strip()
        if len(body) > max_chars:
            # Truncation is announced. A silently cut table looks complete and
            # invites an answer drawn from rows that are no longer there.
            body = body[:max_chars] + "\n... [evidence truncated]"
        return f"{header}\n{body}"


def evidence_from_results(results, *, max_blocks: int = DEFAULT_MAX_BLOCKS) -> list[EvidenceBlock]:
    """Retrieval results -> evidence blocks, in rank order."""
    blocks: list[EvidenceBlock] = []
    for index, result in enumerate(results[:max_blocks], start=1):
        payload = getattr(result, "payload", {}) or {}
        blocks.append(
            EvidenceBlock(
                ref=f"E{index}",
                text=getattr(result, "text", "") or payload.get("text", ""),
                citation=payload.get("citation", "") or "citation unavailable",
                page=payload.get("page"),
                scale=payload.get("scale"),
                currency=payload.get("currency"),
                section=payload.get("section"),
                chunk_id=getattr(result, "chunk_id", None) or payload.get("chunk_id"),
            )
        )
    return blocks


def format_evidence(
    blocks: list[EvidenceBlock], *, max_chars_per_block: int = DEFAULT_MAX_CHARS_PER_BLOCK
) -> str:
    """Render evidence blocks as the text both channels receive.

    An empty evidence set renders as an explicit statement rather than an empty
    string: a model handed nothing at all will answer from memory, and an answer
    from memory about a specific company's specific fiscal year is exactly the
    hallucination this system exists to detect.
    """
    if not blocks:
        return (
            "NO EVIDENCE WAS RETRIEVED.\n"
            "You must not answer from prior knowledge. Report that the evidence "
            "needed to answer is unavailable."
        )
    return "\n\n".join(block.render(max_chars=max_chars_per_block) for block in blocks)
