"""A deterministic embedder for tests and offline evals.

It offers the same ``EmbeddingClient`` seam as the HTTP adapter, so a test can
build an index and search it without a service, a key, or a model. The vector
is a hash-seeded sequence at unit length: the same text always lands in the
same direction, and a query and a document with the same words land in
different ones, the way the real prefix separates them.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

from noteagent.rag.config import EmbeddingConfigError
from noteagent.rag.embedding import Vector, normalize_vector

__all__ = ["FakeEmbedder"]


class FakeEmbedder:
    """A hash-based embedder; it opens no socket and never changes its answer."""

    def __init__(self, dim: int = 1024) -> None:
        if dim <= 0:
            raise EmbeddingConfigError("the fake embedding dimension must be positive")
        self.dim = dim
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def embed(
        self, texts: Sequence[str], *, query: bool = False
    ) -> list[Vector]:
        if isinstance(texts, str):
            raise TypeError(
                "embed expects a sequence of texts, not one string"
            )
        items = tuple(texts)
        self.calls.append((items, query))
        return [self._vector(text, query) for text in items]

    def _vector(self, text: str, query: bool) -> Vector:
        marker = "query:" if query else "document:"
        digest = hashlib.sha256(
            marker.encode("utf-8") + text.encode("utf-8")
        ).digest()
        generator = random.Random(int.from_bytes(digest[:8], "big"))
        return normalize_vector(
            [generator.uniform(-1.0, 1.0) for _ in range(self.dim)]
        )
