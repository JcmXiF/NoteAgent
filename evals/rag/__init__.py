"""Offline retrieval evaluation for the RAG backend.

The harness measures a retrieval variant against an annotated query set. It
never calls a model or the network: the Stage 0 baseline is the lexical search
already in ``noteagent.wiki.local_search``, and later variants use fake
embeddings, so a run is deterministic and costs nothing.
"""

from __future__ import annotations

__all__: list[str] = []
