"""Shared retrieval vocabulary that must not load the index engine.

The candidate and result limits, the page identity, and the mode string are
used by configuration, fusion and the tool result alike; a module with no
LanceDB or HTTP imports keeps the RAG-off path light.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_RESULT_LIMIT",
    "Mode",
    "PageKey",
]

Mode = Literal["hybrid", "lexical"]
PageKey = tuple[str, str]

#: How many candidates each retriever hands the fusion.
DEFAULT_CANDIDATE_LIMIT = 50
#: How many pages the search shows when no reranker changes the order.
DEFAULT_RESULT_LIMIT = 20
