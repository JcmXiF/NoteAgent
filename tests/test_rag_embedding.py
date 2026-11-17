"""The embedding client: config, prefix, batching, retries and validation.

Every network test runs against an ``httpx.MockTransport``: the adapter sees
real requests and responses, but no socket is opened and no model is involved.
"""

from __future__ import annotations

import json
import math
import unittest

import httpx

from fakes import FakeCredentials

from noteagent.rag.config import (
    DEFAULT_QUERY_PREFIX,
    ENV_API_KEY,
    ENV_BATCH,
    ENV_CONCURRENCY,
    ENV_ENDPOINT,
    ENV_MODEL,
    ENV_PAGE_TIMEOUT,
    ENV_QUERY_PREFIX,
    ENV_QUERY_TIMEOUT,
    ENV_RETRIES,
    EmbeddingConfig,
    EmbeddingConfigError,
    load_embedding_config,
)
from noteagent.rag.embedding import (
    EmbeddingError,
    EmbeddingUsage,
    OpenAIEmbeddingClient,
)
from noteagent.rag.faux import FakeEmbedder
from noteagent.settings.credentials import RAG_EMBEDDING_KEY

DIM = 2


def config(**overrides: object) -> EmbeddingConfig:
    values: dict[str, object] = {
        "endpoint": "http://localhost:11434/v1",
        "model": "qwen3-embedding:0.6b",
        "query_prefix": "Instruct: test\nQuery: {query}",
        "dim": DIM,
        "batch_size": 64,
        "api_key": "",
        "retries": 3,
        "query_timeout_s": 8.0,
        "page_timeout_s": 300.0,
    }
    values.update(overrides)
    return EmbeddingConfig(**values)  # type: ignore[arg-type]


def response_for(inputs: list[str], vectors: list[list[float]] | None = None) -> httpx.Response:
    data = []
    for index, text in enumerate(inputs):
        vector = (
            vectors[index]
            if vectors is not None
            else [float(len(text)), 1.0]
        )
        data.append({"index": index, "embedding": vector})
    return httpx.Response(200, json={"data": data})


def embedder(
    handler,
    *,
    overrides: dict[str, object] | None = None,
) -> OpenAIEmbeddingClient:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OpenAIEmbeddingClient(
        config(**(overrides or {})),
        client=client,
        sleep=lambda _seconds: None,
    )


class ConfigTests(unittest.TestCase):
    def test_defaults_are_the_local_qwen_contract(self) -> None:
        loaded = load_embedding_config(
            environ={}, credential_store=FakeCredentials()
        )

        self.assertEqual(loaded.endpoint, "")
        self.assertEqual(loaded.model, "Qwen3-Embedding-0.6B")
        self.assertEqual(loaded.query_prefix, DEFAULT_QUERY_PREFIX)
        self.assertIn("Instruct:", loaded.query_prefix)
        self.assertIn("{query}", loaded.query_prefix)
        self.assertEqual(loaded.dim, 1024)
        self.assertEqual(loaded.batch_size, 64)
        self.assertEqual(loaded.concurrency, 1)
        self.assertEqual(loaded.retries, 3)
        self.assertEqual(loaded.query_timeout_s, 8.0)
        self.assertEqual(loaded.page_timeout_s, 300.0)
        self.assertEqual(loaded.api_key, "")

    def test_the_environment_overrides_every_field(self) -> None:
        loaded = load_embedding_config(
            environ={
                ENV_ENDPOINT: " http://host/v1 ",
                ENV_MODEL: " qwen3-embedding:0.6b ",
                ENV_QUERY_PREFIX: "Q: {query}",
                ENV_BATCH: "8",
                ENV_CONCURRENCY: "2",
                ENV_RETRIES: "1",
                ENV_QUERY_TIMEOUT: "1.5",
                ENV_PAGE_TIMEOUT: "9",
                ENV_API_KEY: "env-key",
            },
            credential_store=FakeCredentials(),
        )

        self.assertEqual(loaded.endpoint, "http://host/v1")
        self.assertEqual(loaded.model, "qwen3-embedding:0.6b")
        self.assertEqual(loaded.query_prefix, "Q: {query}")
        self.assertEqual(loaded.dim, 1024)
        self.assertEqual(loaded.batch_size, 8)
        self.assertEqual(loaded.concurrency, 2)
        self.assertEqual(loaded.retries, 1)
        self.assertEqual(loaded.query_timeout_s, 1.5)
        self.assertEqual(loaded.page_timeout_s, 9.0)
        self.assertEqual(loaded.api_key, "env-key")

    def test_a_stored_key_is_used_when_the_environment_has_none(self) -> None:
        store = FakeCredentials()
        store.values[RAG_EMBEDDING_KEY] = "stored-key"

        loaded = load_embedding_config(environ={}, credential_store=store)

        self.assertEqual(loaded.api_key, "stored-key")

    def test_the_environment_key_wins_over_the_store(self) -> None:
        store = FakeCredentials()
        store.values[RAG_EMBEDDING_KEY] = "stored-key"

        loaded = load_embedding_config(
            environ={ENV_API_KEY: "env-key"}, credential_store=store
        )

        self.assertEqual(loaded.api_key, "env-key")

    def test_an_explicitly_empty_env_key_wins_over_the_store(self) -> None:
        store = FakeCredentials()
        store.values[RAG_EMBEDDING_KEY] = "stored-key"

        loaded = load_embedding_config(
            environ={ENV_API_KEY: ""}, credential_store=store
        )

        self.assertEqual(loaded.api_key, "")

    def test_an_unavailable_store_leaves_the_key_empty(self) -> None:
        loaded = load_embedding_config(
            environ={},
            credential_store=FakeCredentials(available=False),
        )

        self.assertEqual(loaded.api_key, "")

    def test_an_empty_prefix_is_allowed(self) -> None:
        loaded = load_embedding_config(
            environ={ENV_QUERY_PREFIX: ""},
            credential_store=FakeCredentials(),
        )

        self.assertEqual(loaded.query_prefix, "")

    def test_bad_numbers_are_refused(self) -> None:
        for name, value in (
            (ENV_BATCH, "0"),
            (ENV_CONCURRENCY, "0"),
            (ENV_RETRIES, "-1"),
            (ENV_QUERY_TIMEOUT, "0"),
            (ENV_PAGE_TIMEOUT, "soon"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(EmbeddingConfigError):
                    load_embedding_config(
                        environ={name: value},
                        credential_store=FakeCredentials(),
                    )

    def test_a_prefix_without_a_query_placeholder_is_refused(self) -> None:
        with self.assertRaises(EmbeddingConfigError):
            EmbeddingConfig(query_prefix="Instruct: no placeholder")

    def test_an_empty_endpoint_is_allowed_by_the_config(self) -> None:
        self.assertEqual(EmbeddingConfig().endpoint, "")


class ClientConstructionTests(unittest.TestCase):
    def test_an_empty_endpoint_is_refused_by_the_client(self) -> None:
        with self.assertRaises(EmbeddingConfigError):
            OpenAIEmbeddingClient(EmbeddingConfig())

    def test_an_endpoint_gets_the_embeddings_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return response_for(json.loads(request.content)["input"])

        embedder(handler).embed(["a"])

        self.assertEqual(seen, ["http://localhost:11434/v1/embeddings"])

    def test_an_endpoint_already_ending_in_embeddings_is_not_doubled(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return response_for(json.loads(request.content)["input"])

        embedder(
            handler,
            overrides={
                "endpoint": "http://localhost:11434/v1/embeddings"
            },
        ).embed(["a"])

        self.assertEqual(
            seen, ["http://localhost:11434/v1/embeddings"]
        )


class PrefixTests(unittest.TestCase):
    def test_a_query_gets_the_instruction_prefix_and_a_document_does_not(self) -> None:
        inputs: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            inputs.extend(payload["input"])
            return response_for(payload["input"])

        client = embedder(handler)
        client.embed(["hello"], query=True)
        client.embed(["hello"], query=False)

        self.assertEqual(
            inputs,
            ["Instruct: test\nQuery: hello", "hello"],
        )

    def test_a_custom_prefix_is_used_verbatim(self) -> None:
        inputs: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            inputs.extend(payload["input"])
            return response_for(payload["input"])

        embedder(
            handler, overrides={"query_prefix": "Q: {query}"}
        ).embed(["hi"], query=True)

        self.assertEqual(inputs, ["Q: hi"])


class VectorTests(unittest.TestCase):
    def test_a_vector_is_l2_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return response_for(
                json.loads(request.content)["input"],
                vectors=[[3.0, 4.0]],
            )

        (vector,) = embedder(handler).embed(["a"])

        self.assertAlmostEqual(vector[0], 0.6)
        self.assertAlmostEqual(vector[1], 0.8)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in vector)), 1.0
        )

    def test_a_bare_string_is_not_a_list_of_texts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        with self.assertRaises(TypeError):
            embedder(handler).embed("hello")  # type: ignore[arg-type]

    def test_an_empty_input_makes_no_request(self) -> None:
        calls: list[object] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return response_for([])

        self.assertEqual(embedder(handler).embed([]), [])
        self.assertEqual(calls, [])

    def test_a_wrong_dimension_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return response_for(
                json.loads(request.content)["input"],
                vectors=[[1.0, 2.0, 3.0]],
            )

        with self.assertRaises(EmbeddingError) as caught:
            embedder(handler).embed(["a"])

        self.assertIn("2", str(caught.exception))
        self.assertIn("3", str(caught.exception))

    def test_a_non_finite_value_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # Raw JSON text: a newer httpx refuses to serialize NaN through
            # the ``json=`` shortcut, but the endpoint can still send it.
            return httpx.Response(
                200,
                content=(
                    '{"data": [{"index": 0, '
                    '"embedding": [NaN, 1.0]}]}'
                ),
                headers={"content-type": "application/json"},
            )

        with self.assertRaises(EmbeddingError):
            embedder(handler).embed(["a"])

    def test_a_zero_vector_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return response_for(
                json.loads(request.content)["input"],
                vectors=[[0.0, 0.0]],
            )

        with self.assertRaises(EmbeddingError):
            embedder(handler).embed(["a"])


class BatchTests(unittest.TestCase):
    def test_inputs_are_sent_in_batches_of_the_configured_size(self) -> None:
        batches: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            batches.append(len(inputs))
            return response_for(inputs)

        vectors = embedder(
            handler, overrides={"batch_size": 2}
        ).embed(["a", "b", "c", "d", "e"])

        self.assertEqual(batches, [2, 2, 1])
        self.assertEqual(len(vectors), 5)

    def test_a_rejected_batch_falls_back_to_one_text_at_a_time(self) -> None:
        batches: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            batches.append(len(inputs))
            if len(inputs) > 1:
                return httpx.Response(
                    400, json={"error": "batch not supported"}
                )
            return response_for(inputs)

        vectors = embedder(handler).embed(["a", "b", "c"])

        self.assertEqual(batches, [3, 1, 1, 1])
        self.assertEqual(len(vectors), 3)

    def test_a_short_batch_response_falls_back_to_singles(self) -> None:
        batches: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            batches.append(len(inputs))
            if len(inputs) > 1:
                return httpx.Response(
                    200,
                    json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
                )
            return response_for(inputs)

        vectors = embedder(handler).embed(["a", "b"])

        self.assertEqual(batches, [2, 1, 1])
        self.assertEqual(len(vectors), 2)

    def test_the_response_index_orders_the_vectors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                },
            )

        first, second = embedder(handler).embed(["a", "b"])

        self.assertEqual(first, (1.0, 0.0))
        self.assertEqual(second, (0.0, 1.0))


class RetryTests(unittest.TestCase):
    def test_a_transient_failure_is_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(500, json={"error": "busy"})
            return response_for(json.loads(request.content)["input"])

        vectors = embedder(handler).embed(["a"])

        self.assertEqual(calls, 3)
        self.assertEqual(len(vectors), 1)

    def test_exhausted_retries_raise(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, json={"error": "busy"})

        with self.assertRaises(EmbeddingError) as caught:
            embedder(handler, overrides={"retries": 2}).embed(["a"])

        self.assertEqual(calls, 3)
        self.assertIn("3 attempts", str(caught.exception))

    def test_a_timeout_is_retried_then_raised(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("slow", request=request)

        with self.assertRaises(EmbeddingError):
            embedder(handler, overrides={"retries": 1}).embed(["a"])

        self.assertEqual(calls, 2)

    def test_a_permanent_error_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401, json={"error": "bad key"})

        with self.assertRaises(EmbeddingError):
            embedder(handler).embed(["a"])

        self.assertEqual(calls, 1)

    def test_the_error_body_is_redacted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"error": "bad key secret-value"}
            )

        with self.assertRaises(EmbeddingError) as caught:
            embedder(
                handler, overrides={"api_key": "secret-value"}
            ).embed(["a"])

        self.assertNotIn("secret-value", str(caught.exception))


class TimeoutTests(unittest.TestCase):
    def test_a_query_uses_the_short_timeout_and_a_page_the_long_one(self) -> None:
        reads: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            reads.append(request.extensions["timeout"]["read"])
            return response_for(json.loads(request.content)["input"])

        client = OpenAIEmbeddingClient(
            config(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
        )
        client.embed(["q"], query=True)
        client.embed(["d"])

        self.assertEqual(reads, [8.0, 300.0])

    def test_the_page_budget_stops_a_later_batch(self) -> None:
        now = [0.0]
        calls = 0

        def clock() -> float:
            return now[0]

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            now[0] += 0.6
            return response_for(json.loads(request.content)["input"])

        client = OpenAIEmbeddingClient(
            config(batch_size=1, page_timeout_s=1.0, retries=0),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=lambda _seconds: None,
            clock=clock,
        )

        with self.assertRaises(EmbeddingError) as caught:
            client.embed(["a", "b", "c"])

        self.assertEqual(calls, 2)
        self.assertIn("budget", str(caught.exception))


class UsageTests(unittest.TestCase):
    def test_every_request_and_reported_token_is_counted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            data = [
                {"index": index, "embedding": [1.0, 0.0]}
                for index, _text in enumerate(inputs)
            ]
            return httpx.Response(
                200,
                json={"data": data, "usage": {"total_tokens": 3}},
            )

        client = embedder(handler, overrides={"batch_size": 1})
        client.embed(["a", "b"])

        self.assertEqual(
            client.usage, EmbeddingUsage(calls=2, tokens=6)
        )

    def test_a_failed_request_is_still_a_call(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "busy"})

        client = embedder(handler, overrides={"retries": 0})
        with self.assertRaises(EmbeddingError):
            client.embed(["a"])

        self.assertEqual(
            client.usage, EmbeddingUsage(calls=1, tokens=0)
        )


class AuthTests(unittest.TestCase):
    def test_a_configured_key_becomes_a_bearer_header(self) -> None:
        headers: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers.append(request.headers.get("authorization"))
            return response_for(json.loads(request.content)["input"])

        embedder(handler, overrides={"api_key": "secret"}).embed(["a"])

        self.assertEqual(headers, ["Bearer secret"])

    def test_without_a_key_the_header_is_absent(self) -> None:
        headers: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers.append(request.headers.get("authorization"))
            return response_for(json.loads(request.content)["input"])

        embedder(handler).embed(["a"])

        self.assertEqual(headers, [None])


class OverlongTests(unittest.TestCase):
    def test_a_text_the_server_calls_too_long_is_halved(self) -> None:
        successful: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            if any(len(text) > 20 for text in inputs):
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "maximum context length exceeded"
                        }
                    },
                )
            successful.extend(inputs)
            return response_for(inputs)

        (vector,) = embedder(handler).embed(["x" * 100])

        self.assertEqual(len(vector), DIM)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in vector)), 1.0
        )
        self.assertEqual(sum(len(text) for text in successful), 100)
        self.assertGreater(len(successful), 1)
        self.assertLessEqual(max(len(text) for text in successful), 20)


class FakeEmbedderTests(unittest.TestCase):
    def test_the_fake_is_deterministic_and_normalized(self) -> None:
        fake = FakeEmbedder(dim=8)

        first = fake.embed(["hello"])[0]
        second = fake.embed(["hello"])[0]

        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in first)), 1.0
        )

    def test_a_query_and_a_document_have_different_vectors(self) -> None:
        fake = FakeEmbedder(dim=8)

        document = fake.embed(["hello"])[0]
        query = fake.embed(["hello"], query=True)[0]

        self.assertNotEqual(document, query)

    def test_different_texts_have_different_vectors(self) -> None:
        fake = FakeEmbedder(dim=8)

        one = fake.embed(["one"])[0]
        two = fake.embed(["two"])[0]

        self.assertNotEqual(one, two)

    def test_the_fake_records_its_calls(self) -> None:
        fake = FakeEmbedder(dim=8)

        fake.embed(["a", "b"], query=True)
        fake.embed([])

        self.assertEqual(
            fake.calls, [(("a", "b"), True), ((), False)]
        )


if __name__ == "__main__":
    unittest.main()
