"""The optional cross-encoder pass over the fused candidates.

RRF is cheap and order-only; a reranker reads the query and each candidate
together and can reorder the fifty candidates the fusion produced. It is off by
default and never allowed to break search: a missing endpoint, an HTTP error, a
timeout, a short score list or a non-finite score all leave the RRF order in
place and record why in the result.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from noteagent.rag.env import endpoint_path
from noteagent.rag.fusion import DEFAULT_CANDIDATE_LIMIT, FusedHit
from noteagent.rag.rerank_config import (
    DEFAULT_TOP_N,
    ENV_API_KEY,
    ENV_CANDIDATES,
    ENV_ENABLED,
    ENV_ENDPOINT,
    ENV_MODEL,
    ENV_TIMEOUT,
    ENV_TOP_N,
    RerankConfig,
    RerankConfigError,
    load_rerank_config,
)
from noteagent.util.redact import redact

__all__ = [
    "DEFAULT_TOP_N",
    "ENV_API_KEY",
    "ENV_CANDIDATES",
    "ENV_ENABLED",
    "ENV_ENDPOINT",
    "ENV_MODEL",
    "ENV_TIMEOUT",
    "ENV_TOP_N",
    "HttpReranker",
    "REASON_DISABLED",
    "REASON_FAILED",
    "REASON_NO_CANDIDATES",
    "REASON_TOP_N",
    "RerankConfig",
    "RerankConfigError",
    "RerankError",
    "RerankResult",
    "Reranker",
    "load_rerank_config",
    "rerank_candidates",
]

REASON_DISABLED = "disabled"
REASON_FAILED = "failed"
REASON_NO_CANDIDATES = "no_candidates"
REASON_TOP_N = "top_n"


class RerankError(RuntimeError):
    """The reranker could not produce a usable ordering."""


class Reranker(Protocol):
    """One score per document, in the order the documents were given."""

    def rerank(
        self, query: str, documents: Sequence[str]
    ) -> Sequence[float]: ...


@dataclass(frozen=True, slots=True)
class RerankResult:
    """The ordered hits, whether the reranker decided the order, and why not."""

    hits: tuple[FusedHit, ...]
    reranked: bool
    #: A code (``disabled``/``failed``/``no_candidates``/``top_n``), not prose.
    reason: str = ""
    #: The exception text behind ``reason="failed"``, for diagnostics.
    message: str = ""
    scores: tuple[float, ...] = ()


def rerank_candidates(
    query: str,
    fused_hits: Sequence[FusedHit],
    reranker: Reranker | None,
    *,
    candidates: int = DEFAULT_CANDIDATE_LIMIT,
    top_n: int = DEFAULT_TOP_N,
) -> RerankResult:
    """Rerank the first *candidates* fused pages to *top_n*.

    Any failure returns the RRF order, never an exception: the caller's search
    must keep working when the optional service does not.
    """
    if top_n <= 0:
        return RerankResult((), False, REASON_TOP_N)
    selected = tuple(fused_hits)[: max(0, candidates)]
    if not selected:
        return RerankResult((), False, REASON_NO_CANDIDATES)
    fallback = selected[:top_n]
    if reranker is None:
        return RerankResult(fallback, False, REASON_DISABLED)

    documents = [hit.document_text for hit in selected]
    try:
        raw = list(reranker.rerank(query, documents))
        if len(raw) != len(documents):
            raise RerankError(
                f"the reranker returned {len(raw)} scores "
                f"for {len(documents)} documents"
            )
        scores = [_finite_score(value) for value in raw]
    except Exception as err:  # noqa: BLE001 - fallback is the contract
        return RerankResult(
            fallback,
            False,
            REASON_FAILED,
            str(err) or type(err).__name__,
        )

    order = sorted(
        range(len(selected)),
        key=lambda index: (-scores[index], index),
    )[:top_n]
    return RerankResult(
        hits=tuple(selected[index] for index in order),
        reranked=True,
        scores=tuple(scores[index] for index in order),
    )


class HttpReranker:
    """A Cohere-style ``/rerank`` client; a TEI result list is accepted too.

    The endpoint is a base URL (``.../v1`` or the service root); ``/rerank`` is
    appended unless it is already there. The request names the model, the query
    and the documents, and asks for a score for every document.
    """

    def __init__(
        self,
        config: RerankConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        endpoint = config.endpoint.strip()
        self._config = config
        self._url = endpoint_path(endpoint, "rerank") if endpoint else ""
        self._client = client

    def rerank(
        self, query: str, documents: Sequence[str]
    ) -> list[float]:
        if not self._url:
            raise RerankError("the rerank endpoint is not configured")
        items = list(documents)
        if not items:
            return []
        if self._client is not None:
            return self._rerank_with(self._client, query, items)
        with httpx.Client() as client:
            return self._rerank_with(client, query, items)

    def _rerank_with(
        self,
        client: httpx.Client,
        query: str,
        documents: list[str],
    ) -> list[float]:
        key = self._config.api_key.strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict[str, object] = {
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        model = self._config.model.strip()
        if model:
            payload["model"] = model
        try:
            response = client.post(
                self._url,
                headers=headers,
                json=payload,
                timeout=self._config.timeout_s,
            )
        except httpx.HTTPError as err:
            raise RerankError(
                f"rerank request failed: {err}"
            ) from err
        if response.status_code >= 400:
            body = redact(response.text[:800], (key,))
            raise RerankError(
                f"rerank HTTP {response.status_code}: {body}"
            )
        try:
            data = response.json()
        except ValueError as err:
            raise RerankError(
                "the reranker did not answer JSON: "
                + response.text[:200]
            ) from err
        return _scores_by_index(data, len(documents))


def _finite_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RerankError("the reranker returned a non-numeric score")
    number = float(value)
    if not math.isfinite(number):
        raise RerankError("the reranker returned a non-finite score")
    return number


def _scores_by_index(payload: object, expected: int) -> list[float]:
    if isinstance(payload, dict):
        results = payload.get("results")
    elif isinstance(payload, list):
        results = payload
    else:
        results = None
    if not isinstance(results, list):
        raise RerankError(
            "the reranker response has no results list"
        )
    scores = [0.0] * expected
    seen: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise RerankError(
                "each rerank result must be a JSON object"
            )
        index = item.get("index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < expected
        ):
            raise RerankError(
                "the reranker response has an unusable index"
            )
        value = item.get("relevance_score", item.get("score"))
        scores[index] = _finite_score(value)
        seen.add(index)
    if len(seen) != expected:
        raise RerankError(
            f"the reranker returned {len(seen)} of {expected} scores"
        )
    return scores
