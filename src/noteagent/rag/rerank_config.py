"""The rerank settings, free of the HTTP and index imports.

The RAG switch has to read these settings even when the index engine is not
loaded; keeping them here lets a run with RAG off stay light.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from noteagent.rag.env import read_boolean, read_integer, read_number
from noteagent.rag.types import DEFAULT_CANDIDATE_LIMIT

__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_TOP_N",
    "ENV_API_KEY",
    "ENV_CANDIDATES",
    "ENV_ENABLED",
    "ENV_ENDPOINT",
    "ENV_MODEL",
    "ENV_TIMEOUT",
    "ENV_TOP_N",
    "RerankConfig",
    "RerankConfigError",
    "load_rerank_config",
]

DEFAULT_TOP_N = 10
DEFAULT_TIMEOUT = 10.0

ENV_ENABLED = "NOTEAGENT_RAG_RERANK_ENABLED"
ENV_ENDPOINT = "NOTEAGENT_RAG_RERANK_ENDPOINT"
ENV_MODEL = "NOTEAGENT_RAG_RERANK_MODEL"
ENV_CANDIDATES = "NOTEAGENT_RAG_RERANK_CANDIDATES"
ENV_TOP_N = "NOTEAGENT_RAG_RERANK_TOP_N"
ENV_API_KEY = "NOTEAGENT_RAG_RERANK_API_KEY"
ENV_TIMEOUT = "NOTEAGENT_RAG_RERANK_TIMEOUT"


class RerankConfigError(ValueError):
    """The rerank settings are incomplete or contradictory."""


@dataclass(frozen=True, slots=True)
class RerankConfig:
    """Where the reranker is and how many candidates it sees."""

    enabled: bool = False
    endpoint: str = ""
    model: str = ""
    candidates: int = DEFAULT_CANDIDATE_LIMIT
    top_n: int = DEFAULT_TOP_N
    api_key: str = ""
    timeout_s: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        if self.candidates <= 0:
            raise RerankConfigError(
                "the rerank candidate count must be positive"
            )
        if self.top_n <= 0:
            raise RerankConfigError(
                "the rerank result count must be positive"
            )
        if self.timeout_s <= 0:
            raise RerankConfigError(
                "the rerank timeout must be positive"
            )


def load_rerank_config(
    *, environ: Mapping[str, str] | None = None
) -> RerankConfig:
    """Read rerank settings; the service is off unless explicitly enabled."""
    source = os.environ if environ is None else environ
    return RerankConfig(
        enabled=read_boolean(
            source, ENV_ENABLED, False, error=RerankConfigError
        ),
        endpoint=source.get(ENV_ENDPOINT, "").strip(),
        model=source.get(ENV_MODEL, "").strip(),
        candidates=read_integer(
            source,
            ENV_CANDIDATES,
            DEFAULT_CANDIDATE_LIMIT,
            error=RerankConfigError,
        ),
        top_n=read_integer(
            source, ENV_TOP_N, DEFAULT_TOP_N,
            error=RerankConfigError,
        ),
        api_key=source.get(ENV_API_KEY, "").strip(),
        timeout_s=read_number(
            source, ENV_TIMEOUT, DEFAULT_TIMEOUT,
            error=RerankConfigError,
        ),
    )
