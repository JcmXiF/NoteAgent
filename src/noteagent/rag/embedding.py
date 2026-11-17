"""Turn text into the unit vectors the retrieval index stores and searches.

The endpoint is OpenAI-compatible and locally hosted; this module owns the
contract both sides must keep: the query prefix, L2 normalization, the dimension
check, batching, retries, and the split of a text the server calls too long.
Nothing here reads a provider key from a file, and nothing here reaches a
service the caller did not configure.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from noteagent.rag.config import (
    EmbeddingConfig,
    EmbeddingConfigError,
    EmbeddingError,
)
from noteagent.rag.env import endpoint_path
from noteagent.util.redact import redact

__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingUsage",
    "OpenAIEmbeddingClient",
    "Vector",
    "normalize_vector",
]

#: One embedding, already normalized so cosine is a dot product.
Vector = tuple[float, ...]

#: Statuses where the server is busy or broken; retrying may help.
_RETRY_STATUSES = frozenset({429})

#: Statuses that may mean the server cannot take a batch; one text may work.
_BATCH_STATUSES = frozenset({400, 413, 422})

#: A batch response with the wrong length may mean the server ignored the batch.
_TOO_LONG_MARKERS = (
    "context length",
    "context window",
    "maximum context",
    "max tokens",
    "max_tokens",
    "token limit",
    "too many tokens",
    "input is too long",
    "input too long",
    "exceeds the maximum",
)


@runtime_checkable
class EmbeddingClient(Protocol):
    """What the index and the retriever need from an embedding backend."""

    dim: int

    def embed(
        self, texts: Sequence[str], *, query: bool = False
    ) -> list[Vector]: ...


def normalize_vector(values: Sequence[float]) -> Vector:
    """Check a vector and return it at unit length.

    A zero vector has no direction, so it cannot be compared with cosine; a
    non-finite value would poison every distance it touches. Both are refused
    here rather than at the index, where the cause would be harder to see.
    """
    numbers: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingError("an embedding must contain only numbers")
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingError("an embedding contains a non-finite value")
        numbers.append(number)
    norm = math.sqrt(sum(number * number for number in numbers))
    if norm == 0.0:
        raise EmbeddingError("an embedding has zero length")
    return tuple(number / norm for number in numbers)


class _Transient(Exception):
    """A failure worth retrying: a timeout, a busy server, a broken response."""


class _Permanent(Exception):
    """A refusal retrying will not fix; ``too_long`` may still be split."""

    def __init__(
        self, message: str, *, status: int = 0, too_long: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.too_long = too_long


class _VectorCountMismatch(Exception):
    """A successful response whose vector count does not match the input."""


@dataclass(frozen=True, slots=True)
class _Job:
    """One text waiting to be embedded, and what the endpoint will receive."""

    text: str
    prepared: str


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """What a client has sent: HTTP requests made and tokens reported."""

    calls: int = 0
    tokens: int = 0


class OpenAIEmbeddingClient:
    """An embedding client for ``/v1/embeddings`` on a local service.

    One ``embed`` call is one provider phase: documents share the page budget,
    and a query has its own short one. *client*, *sleep* and *clock* are test
    seams; production leaves them out and the adapter owns them.
    """

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        endpoint = config.endpoint.strip()
        if not endpoint:
            raise EmbeddingConfigError(
                "the embedding endpoint is not configured"
            )
        self._config = config
        self._url = endpoint_path(endpoint, "embeddings")
        self._client = client
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._usage_calls = 0
        self._usage_tokens = 0
        self.dim = config.dim

    @property
    def usage(self) -> EmbeddingUsage:
        """A snapshot of the requests and tokens spent so far."""
        return EmbeddingUsage(self._usage_calls, self._usage_tokens)

    def embed(
        self, texts: Sequence[str], *, query: bool = False
    ) -> list[Vector]:
        """Embed every text, keeping the input order and unit length."""
        _refuse_bare_string(texts)
        items = list(texts)
        if not items:
            return []
        jobs = [
            _Job(text, self._prepare(text, query)) for text in items
        ]
        budget = (
            self._config.query_timeout_s
            if query
            else self._config.page_timeout_s
        )
        deadline = self._clock() + budget
        if self._client is not None:
            return self._embed_in_batches(
                self._client, jobs, query, deadline
            )
        with httpx.Client() as client:
            return self._embed_in_batches(
                client, jobs, query, deadline
            )

    def _embed_in_batches(
        self,
        client: httpx.Client,
        jobs: list[_Job],
        query: bool,
        deadline: float,
    ) -> list[Vector]:
        vectors: list[Vector] = []
        size = self._config.batch_size
        for start in range(0, len(jobs), size):
            if deadline - self._clock() <= 0:
                raise self._budget_error(query)
            vectors.extend(
                self._embed_batch(
                    client,
                    jobs[start : start + size],
                    query,
                    deadline,
                )
            )
        return vectors

    def _embed_batch(
        self,
        client: httpx.Client,
        batch: list[_Job],
        query: bool,
        deadline: float,
    ) -> list[Vector]:
        prepared = [job.prepared for job in batch]
        try:
            return self._request(client, prepared, query, deadline)
        except _VectorCountMismatch as err:
            if len(batch) == 1:
                raise _wrong_count_error() from err
            return [
                self._embed_one(client, job.text, query, deadline)
                for job in batch
            ]
        except _Permanent as err:
            if len(batch) > 1 and err.status in _BATCH_STATUSES:
                return [
                    self._embed_one(
                        client, job.text, query, deadline
                    )
                    for job in batch
                ]
            if len(batch) == 1 and err.too_long:
                return [
                    self._embed_split(
                        client, batch[0].text, query, deadline
                    )
                ]
            raise EmbeddingError(str(err)) from err

    def _embed_one(
        self,
        client: httpx.Client,
        text: str,
        query: bool,
        deadline: float,
    ) -> Vector:
        prepared = self._prepare(text, query)
        try:
            return self._request(
                client, [prepared], query, deadline
            )[0]
        except _VectorCountMismatch as err:
            raise _wrong_count_error() from err
        except _Permanent as err:
            if err.too_long:
                return self._embed_split(
                    client, text, query, deadline
                )
            raise EmbeddingError(str(err)) from err

    def _embed_split(
        self,
        client: httpx.Client,
        text: str,
        query: bool,
        deadline: float,
    ) -> Vector:
        """Halve a text the server called too long, then average the halves.

        The average is normalized again, so the result stands in the same
        cosine space as every other vector. The split is recursive because a
        half can still be over the endpoint's limit.
        """
        if len(text) < 2:
            raise EmbeddingError(
                "the embedding endpoint rejected a text too short to split"
            )
        middle = len(text) // 2
        halves = [text[:middle], text[middle:]]
        vectors = [
            self._embed_one(client, half, query, deadline)
            for half in halves
        ]
        count = len(vectors)
        mean = tuple(
            sum(values) / count for values in zip(*vectors, strict=True)
        )
        return normalize_vector(mean)

    def _request(
        self,
        client: httpx.Client,
        texts: list[str],
        query: bool,
        deadline: float,
    ) -> list[Vector]:
        last = "no attempt"
        attempts = self._config.retries + 1
        for attempt in range(attempts):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise self._budget_error(query)
            try:
                return self._post(client, texts, remaining)
            except _Transient as err:
                last = str(err)
                if attempt + 1 < attempts:
                    self._sleep(min(1.0, 0.2 * (2**attempt)))
        raise EmbeddingError(
            f"embedding endpoint failed after {attempts} attempts: {last}"
        )

    def _budget_error(self, query: bool) -> EmbeddingError:
        limit = (
            self._config.query_timeout_s
            if query
            else self._config.page_timeout_s
        )
        kind = "query" if query else "page"
        return EmbeddingError(
            f"the embedding {kind} exceeded its {limit:g}s budget"
        )

    def _post(
        self,
        client: httpx.Client,
        texts: list[str],
        timeout: float,
    ) -> list[Vector]:
        key = self._config.api_key.strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._usage_calls += 1
        try:
            response = client.post(
                self._url,
                headers=headers,
                json={"model": self._config.model, "input": texts},
                timeout=timeout,
            )
        except httpx.HTTPError as err:
            raise _Transient(f"embedding request failed: {err}") from err

        if response.status_code >= 400:
            body = redact(response.text[:800], (key,))
            message = f"embedding HTTP {response.status_code}: {body}"
            if (
                response.status_code in _RETRY_STATUSES
                or response.status_code >= 500
            ):
                raise _Transient(message)
            raise _Permanent(
                message,
                status=response.status_code,
                too_long=_too_long(body),
            )
        try:
            payload = response.json()
        except ValueError as err:
            raise EmbeddingError(
                "the embedding endpoint did not answer JSON: "
                + response.text[:200]
            ) from err
        self._add_tokens(payload)
        return self._parse(payload, len(texts))

    def _add_tokens(self, payload: object) -> None:
        """Add what the service reported, when it reports anything."""
        if not isinstance(payload, dict):
            return
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        tokens = usage.get("total_tokens", usage.get("prompt_tokens", 0))
        if isinstance(tokens, bool) or not isinstance(tokens, (int, float)):
            return
        if tokens > 0:
            self._usage_tokens += int(tokens)

    def _parse(self, payload: object, expected: int) -> list[Vector]:
        if not isinstance(payload, dict):
            raise EmbeddingError(
                "the embedding response must be a JSON object"
            )
        data = payload.get("data")
        if not isinstance(data, list):
            raise EmbeddingError(
                "the embedding response has no data list"
            )
        if len(data) != expected:
            raise _VectorCountMismatch()

        items: list[object] = list(data)
        if items and all(
            isinstance(item, dict) and "index" in item for item in items
        ):
            try:
                items = sorted(
                    items, key=lambda item: int(item["index"])
                )
            except (TypeError, ValueError) as err:
                raise EmbeddingError(
                    "the embedding response has an unusable index"
                ) from err
            indices = [int(item["index"]) for item in items]
            if indices != list(range(expected)):
                raise EmbeddingError(
                    "the embedding response has duplicate or missing indices"
                )

        vectors: list[Vector] = []
        for item in items:
            if not isinstance(item, dict):
                raise EmbeddingError(
                    "each embedding result must be a JSON object"
                )
            vectors.append(self._vector(item.get("embedding")))
        return vectors

    def _vector(self, values: object) -> Vector:
        if not isinstance(values, (list, tuple)):
            raise EmbeddingError("an embedding must be a list of numbers")
        if len(values) != self.dim:
            raise EmbeddingError(
                f"expected {self.dim} dimensions, got {len(values)}"
            )
        return normalize_vector(values)

    def _prepare(self, text: str, query: bool) -> str:
        prefix = self._config.query_prefix
        if query and prefix:
            return prefix.replace("{query}", text)
        return text


def _wrong_count_error() -> EmbeddingError:
    return EmbeddingError(
        "the embedding endpoint returned the wrong number of vectors"
    )


def _refuse_bare_string(texts: object) -> None:
    """A string is a sequence of characters; one text is still a list of one."""
    if isinstance(texts, str):
        raise TypeError(
            "embed expects a sequence of texts, not one string"
        )


def _too_long(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in _TOO_LONG_MARKERS)
