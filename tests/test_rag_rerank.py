"""Optional rerank: reordering, fallback, candidate trimming and the client."""

from __future__ import annotations

import json
import math
import unittest

import httpx

from fakes import FakeCredentials

from noteagent.rag.bm25 import LexicalHit
from noteagent.rag.faux import FakeReranker
from noteagent.rag.fusion import FusedHit
from noteagent.rag.rerank import (
    ENV_CANDIDATES,
    ENV_ENABLED,
    ENV_ENDPOINT,
    ENV_MODEL,
    ENV_TOP_N,
    HttpReranker,
    RerankConfig,
    RerankConfigError,
    RerankError,
    load_rerank_config,
    rerank_candidates,
)
from noteagent.rag.vector import ChunkScore, VectorHit


def fused(
    page_id: str,
    text: str,
    *,
    kind: str = "wiki",
    heading: str = "",
) -> FusedHit:
    lexical = LexicalHit(
        chunk_id=f"{kind}:{page_id}#0",
        page_id=page_id,
        kind=kind,
        chunk_index=0,
        chunk_text=text,
        heading_path=heading,
        score=1.0,
        matched=("body",),
        exact=False,
    )
    return FusedHit(
        page_id=page_id,
        kind=kind,
        score=1.0,
        exact=False,
        lexical=lexical,
    )


def with_vector(page_id: str, text: str, vector_text: str) -> FusedHit:
    lexical = LexicalHit(
        chunk_id=f"wiki:{page_id}#0",
        page_id=page_id,
        kind="wiki",
        chunk_index=0,
        chunk_text=text,
        heading_path="",
        score=1.0,
        matched=("body",),
        exact=False,
    )
    best = ChunkScore(
        chunk_id=f"wiki:{page_id}#1",
        page_id=page_id,
        kind="wiki",
        chunk_index=1,
        chunk_text=vector_text,
        heading_path="",
        similarity=0.8,
    )
    return FusedHit(
        page_id=page_id,
        kind="wiki",
        score=1.0,
        exact=False,
        lexical=lexical,
        vector=VectorHit(
            page_id=page_id, kind="wiki", score=0.8, best=best
        ),
    )


class ConfigTests(unittest.TestCase):
    def test_the_default_is_disabled(self) -> None:
        loaded = load_rerank_config(
            environ={}, credential_store=FakeCredentials()
        )

        self.assertFalse(loaded.enabled)
        self.assertEqual(loaded.endpoint, "")
        self.assertEqual(loaded.model, "")
        self.assertEqual(loaded.candidates, 50)
        self.assertEqual(loaded.top_n, 10)
        self.assertEqual(loaded.timeout_s, 10.0)

    def test_the_environment_overrides_the_defaults(self) -> None:
        loaded = load_rerank_config(
            environ={
                ENV_ENABLED: "true",
                ENV_ENDPOINT: " http://host/v1 ",
                ENV_MODEL: " qwen3-reranker ",
                ENV_CANDIDATES: "20",
                ENV_TOP_N: "5",
            },
            credential_store=FakeCredentials(),
        )

        self.assertTrue(loaded.enabled)
        self.assertEqual(loaded.endpoint, "http://host/v1")
        self.assertEqual(loaded.model, "qwen3-reranker")
        self.assertEqual(loaded.candidates, 20)
        self.assertEqual(loaded.top_n, 5)

    def test_a_bad_boolean_is_refused(self) -> None:
        with self.assertRaises(RerankConfigError):
            load_rerank_config(
                environ={ENV_ENABLED: "maybe"},
                credential_store=FakeCredentials(),
            )

    def test_bad_numbers_are_refused(self) -> None:
        for name, value in (
            (ENV_CANDIDATES, "0"),
            (ENV_TOP_N, "-1"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(RerankConfigError):
                    load_rerank_config(
                        environ={name: value},
                        credential_store=FakeCredentials(),
                    )

    def test_top_n_must_be_positive(self) -> None:
        with self.assertRaises(RerankConfigError):
            RerankConfig(top_n=0)


class OrchestrationTests(unittest.TestCase):
    def test_the_candidates_are_reordered_and_trimmed(self) -> None:
        candidates = [
            fused("a", "one good"),
            fused("b", "bad"),
            fused("c", "good good"),
        ]
        fake = FakeReranker(
            lambda query, document: float(document.count("good"))
        )

        result = rerank_candidates(
            "query", candidates, fake, top_n=2
        )

        self.assertTrue(result.reranked)
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["c", "a"]
        )
        self.assertEqual(result.scores, (2.0, 1.0))

    def test_an_unconfigured_endpoint_falls_back(self) -> None:
        reranker = HttpReranker(
            RerankConfig(enabled=True, endpoint="")
        )

        result = rerank_candidates(
            "query", [fused("a", "one")], reranker
        )

        self.assertFalse(result.reranked)
        self.assertEqual(result.reason, "failed")
        self.assertIn("endpoint", result.message)

    def test_a_failure_keeps_the_rrf_order(self) -> None:
        candidates = [fused("a", "one"), fused("b", "two")]
        fake = FakeReranker(error=RerankError("down"))

        result = rerank_candidates(
            "query", candidates, fake, top_n=2
        )

        self.assertFalse(result.reranked)
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )
        self.assertEqual(result.reason, "failed")
        self.assertIn("down", result.message)
        self.assertEqual(result.scores, ())

    def test_a_short_score_list_falls_back(self) -> None:
        class ShortReranker:
            def rerank(self, query, documents):
                return [1.0]

        result = rerank_candidates(
            "query", [fused("a", "one"), fused("b", "two")],
            ShortReranker(), top_n=2,
        )

        self.assertFalse(result.reranked)
        self.assertEqual(result.reason, "failed")
        self.assertIn("scores", result.message)

    def test_a_non_finite_score_falls_back(self) -> None:
        fake = FakeReranker(
            lambda query, document: math.nan
        )

        result = rerank_candidates(
            "query", [fused("a", "one")], fake
        )

        self.assertFalse(result.reranked)

    def test_no_reranker_uses_the_rrf_order(self) -> None:
        candidates = [fused("a", "one"), fused("b", "two")]

        result = rerank_candidates(
            "query", candidates, None, top_n=1
        )

        self.assertFalse(result.reranked)
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a"]
        )
        self.assertEqual(result.reason, "disabled")

    def test_empty_candidates_do_nothing(self) -> None:
        fake = FakeReranker()

        result = rerank_candidates("query", [], fake)

        self.assertEqual(result.hits, ())
        self.assertFalse(result.reranked)
        self.assertEqual(fake.calls, [])

    def test_the_candidate_limit_is_respected(self) -> None:
        candidates = [
            fused(f"p{index:02d}", f"document {index}")
            for index in range(60)
        ]
        fake = FakeReranker()

        rerank_candidates(
            "query", candidates, fake, candidates=50
        )

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(fake.calls[0][1]), 50)

    def test_ties_keep_the_input_order(self) -> None:
        candidates = [fused("a", "one"), fused("b", "two")]
        fake = FakeReranker(lambda query, document: 0.0)

        result = rerank_candidates("query", candidates, fake)

        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )

    def test_the_document_carries_the_page_and_both_chunks(self) -> None:
        candidates = [with_vector("a", "body text", "vector text")]
        fake = FakeReranker()

        rerank_candidates("query", candidates, fake)

        document = fake.calls[0][1][0]
        self.assertIn("a", document)
        self.assertIn("body text", document)
        self.assertIn("vector text", document)

    def test_a_mapping_scores_known_documents(self) -> None:
        fake = FakeReranker({"good document": 1.0})

        scores = fake.rerank(
            "query", ["good document", "other document"]
        )

        self.assertEqual(scores, [1.0, 0.0])

    def test_top_n_zero_returns_nothing(self) -> None:
        result = rerank_candidates(
            "query", [fused("a", "one")], FakeReranker(), top_n=0
        )

        self.assertEqual(result.hits, ())


class HttpRerankerTests(unittest.TestCase):
    def config(self, **overrides: object) -> RerankConfig:
        values: dict[str, object] = {
            "endpoint": "http://host/v1",
            "model": "qwen3-reranker",
            "timeout_s": 10.0,
        }
        values.update(overrides)
        return RerankConfig(**values)  # type: ignore[arg-type]

    def client(
        self,
        handler,
        **overrides: object,
    ) -> tuple[HttpReranker, list[dict], list[httpx.Request]]:
        requests: list[httpx.Request] = []
        payloads: list[dict] = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payloads.append(json.loads(request.content))
            return handler(request)

        http = httpx.Client(transport=httpx.MockTransport(wrapped))
        return HttpReranker(self.config(**overrides), client=http), payloads, requests

    def test_the_request_and_endpoint_are_what_the_service_expects(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.1},
                        {"index": 0, "relevance_score": 0.9},
                    ]
                },
            )

        reranker, payloads, requests = self.client(
            handler, api_key="secret"
        )

        scores = reranker.rerank("query", ["first", "second"])

        self.assertEqual(scores, [0.9, 0.1])
        self.assertEqual(str(requests[0].url), "http://host/v1/rerank")
        self.assertEqual(
            requests[0].headers["authorization"], "Bearer secret"
        )
        self.assertEqual(payloads[0]["model"], "qwen3-reranker")
        self.assertEqual(payloads[0]["query"], "query")
        self.assertEqual(payloads[0]["documents"], ["first", "second"])
        self.assertEqual(payloads[0]["top_n"], 2)

    def test_a_top_level_tei_list_is_accepted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"index": 0, "score": 0.4},
                    {"index": 1, "score": 0.7},
                ],
            )

        reranker, _payloads, _requests = self.client(handler)

        self.assertEqual(
            reranker.rerank("query", ["first", "second"]),
            [0.4, 0.7],
        )

    def test_a_truncated_response_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [{"index": 0, "relevance_score": 0.9}]
                },
            )

        reranker, _payloads, _requests = self.client(handler)

        with self.assertRaises(RerankError):
            reranker.rerank("query", ["first", "second"])

    def test_an_http_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "busy"})

        reranker, _payloads, _requests = self.client(handler)

        with self.assertRaises(RerankError):
            reranker.rerank("query", ["first"])

    def test_a_malformed_response_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"nope": True})

        reranker, _payloads, _requests = self.client(handler)

        with self.assertRaises(RerankError):
            reranker.rerank("query", ["first"])

    def test_an_empty_document_list_makes_no_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        reranker, _payloads, requests = self.client(handler)

        self.assertEqual(reranker.rerank("query", []), [])
        self.assertEqual(requests, [])

    def test_an_endpoint_already_ending_in_rerank_is_not_doubled(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"results": [{"index": 0, "score": 1.0}]}
            )

        reranker, _payloads, requests = self.client(
            handler, endpoint="http://host/rerank"
        )

        reranker.rerank("query", ["first"])

        self.assertEqual(str(requests[0].url), "http://host/rerank")

    def test_the_timeout_is_used(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"results": [{"index": 0, "score": 1.0}]}
            )

        reranker, _payloads, requests = self.client(
            handler, timeout_s=2.5
        )

        reranker.rerank("query", ["first"])

        self.assertEqual(
            requests[0].extensions["timeout"]["read"], 2.5
        )


if __name__ == "__main__":
    unittest.main()
