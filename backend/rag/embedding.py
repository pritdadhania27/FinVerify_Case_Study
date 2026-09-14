"""Local embedding (spec Module 6, decision D2).

Runs `sentence-transformers` locally rather than calling an embedding API. Two
reasons: no free LLM provider here offers embeddings, and a locally pinned model
produces the same vectors in a year while a hosted endpoint may not - which
matters for a reproducibility artifact.

Two details that are easy to get wrong and quietly cost retrieval quality:

* **BGE and E5 expect an instruction prefix on queries but not on passages.**
  Embedding both sides identically is a silent quality regression - no error, no
  warning, just worse recall. The prefix is applied per model family here.
* **Silent truncation.** These models cap at 512 tokens and simply drop the
  remainder. A table chunk cut short loses its last rows, and the retrieved
  evidence is then wrong in a way nothing reports. Truncation is counted and
  surfaced rather than ignored.

Model choice (BGE vs E5) is deliberately *not* decided here. Spec section 6
requires it be settled by retrieval evaluation, which is Module 6's job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = ["EmbeddingResult", "Embedder", "QUERY_PREFIXES"]

# Applied to queries only. Passages are embedded bare.
QUERY_PREFIXES: dict[str, str] = {
    "bge": "Represent this sentence for searching relevant passages: ",
    "e5": "query: ",
}
_E5_PASSAGE_PREFIX = "passage: "


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimension: int
    truncated_count: int = 0
    elapsed_seconds: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)


class Embedder:
    """Wraps a sentence-transformers model with correct prefixing and truncation
    reporting."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-base-en-v1.5",
        *,
        device: str = "cpu",
        batch_size: int = 16,
        normalize: bool = True,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        # Cosine similarity via dot product requires normalised vectors; Qdrant
        # is configured for COSINE, so this must stay on.
        self.normalize = normalize
        self._model = SentenceTransformer(model_name, device=device)
        self.max_seq_length = int(getattr(self._model, "max_seq_length", 512))

    @property
    def family(self) -> str:
        lowered = self.model_name.lower()
        if "bge" in lowered:
            return "bge"
        if "e5" in lowered:
            return "e5"
        return "unknown"

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def _count_truncated(self, texts: list[str]) -> int:
        """How many inputs the model will silently cut short."""
        tokenizer = getattr(self._model, "tokenizer", None)
        if tokenizer is None:
            return 0
        truncated = 0
        for text in texts:
            try:
                length = len(tokenizer.encode(text, add_special_tokens=True))
            except Exception:  # noqa: BLE001 - a counting failure must not break embedding
                continue
            if length > self.max_seq_length:
                truncated += 1
        return truncated

    def embed_passages(self, texts: list[str]) -> EmbeddingResult:
        """Embed documents for indexing. No query prefix."""
        if self.family == "e5":
            prepared = [_E5_PASSAGE_PREFIX + t for t in texts]
        else:
            prepared = list(texts)
        return self._embed(prepared, texts, kind="passage")

    def embed_queries(self, texts: list[str]) -> EmbeddingResult:
        """Embed queries. Applies the model family's instruction prefix.

        Omitting this on BGE/E5 degrades recall with no error raised.
        """
        prefix = QUERY_PREFIXES.get(self.family, "")
        prepared = [prefix + t for t in texts]
        return self._embed(prepared, prepared, kind="query")

    def _embed(self, prepared: list[str], for_counting: list[str], *, kind: str) -> EmbeddingResult:
        if not prepared:
            return EmbeddingResult([], self.model_name, self.dimension)

        truncated = self._count_truncated(prepared)
        started = time.monotonic()
        vectors = self._model.encode(
            prepared,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        elapsed = time.monotonic() - started

        notes: list[str] = []
        if truncated:
            notes.append(
                f"{truncated}/{len(prepared)} {kind}s exceeded {self.max_seq_length} tokens "
                "and were truncated - trailing content is not represented in the vector"
            )
        if kind == "query" and self.family in QUERY_PREFIXES:
            notes.append(f"applied {self.family} query prefix")

        return EmbeddingResult(
            vectors=[v.tolist() for v in vectors],
            model=self.model_name,
            dimension=int(vectors.shape[1]),
            truncated_count=truncated,
            elapsed_seconds=elapsed,
            notes=tuple(notes),
        )
