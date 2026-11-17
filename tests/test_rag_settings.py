"""The RAG switch: off by default, env-driven when on, key from the store."""

from __future__ import annotations

import unittest

from fakes import FakeCredentials

from noteagent.rag.config import ENV_BATCH, ENV_ENDPOINT, ENV_MODEL
from noteagent.rag.rerank_config import (
    ENV_ENABLED as ENV_RERANK_ENABLED,
    ENV_ENDPOINT as ENV_RERANK_ENDPOINT,
    ENV_MODEL as ENV_RERANK_MODEL,
)
from noteagent.rag.settings import (
    ENV_ENABLED,
    RagConfigError,
    load_rag_settings,
)
from noteagent.settings.credentials import (
    RAG_EMBEDDING_KEY,
    RAG_RERANK_KEY,
)


class RagSettingsTests(unittest.TestCase):
    def test_off_is_the_default_and_does_not_touch_the_store(self) -> None:
        loaded = load_rag_settings(
            environ={},
            credential_store=FakeCredentials(available=False),
        )

        self.assertFalse(loaded.enabled)
        self.assertEqual(loaded.embedding.endpoint, "")
        self.assertFalse(loaded.rerank.enabled)

    def test_the_environment_turns_it_on_and_configures_both_services(self) -> None:
        loaded = load_rag_settings(
            environ={
                ENV_ENABLED: "true",
                ENV_ENDPOINT: "http://embedding/v1",
                ENV_MODEL: "qwen3-embedding",
                ENV_BATCH: "8",
                ENV_RERANK_ENABLED: "yes",
                ENV_RERANK_ENDPOINT: "http://rerank/v1",
                ENV_RERANK_MODEL: "qwen3-reranker",
            },
            credential_store=FakeCredentials(),
        )

        self.assertTrue(loaded.enabled)
        self.assertEqual(
            loaded.embedding.endpoint, "http://embedding/v1"
        )
        self.assertEqual(
            loaded.embedding.model, "qwen3-embedding"
        )
        self.assertEqual(loaded.embedding.batch_size, 8)
        self.assertTrue(loaded.rerank.enabled)
        self.assertEqual(
            loaded.rerank.endpoint, "http://rerank/v1"
        )
        self.assertEqual(loaded.rerank.model, "qwen3-reranker")

    def test_the_embedding_key_comes_from_the_credential_store(self) -> None:
        store = FakeCredentials()
        store.values[RAG_EMBEDDING_KEY] = "stored-key"

        loaded = load_rag_settings(
            environ={
                ENV_ENABLED: "true",
                ENV_ENDPOINT: "http://embedding/v1",
            },
            credential_store=store,
        )

        self.assertEqual(loaded.embedding.api_key, "stored-key")

    def test_the_rerank_key_comes_from_the_credential_store_too(self) -> None:
        store = FakeCredentials()
        store.values[RAG_RERANK_KEY] = "rerank-key"

        loaded = load_rag_settings(
            environ={
                ENV_ENABLED: "true",
                ENV_ENDPOINT: "http://embedding/v1",
                ENV_RERANK_ENABLED: "true",
                ENV_RERANK_ENDPOINT: "http://rerank/v1",
            },
            credential_store=store,
        )

        self.assertEqual(loaded.rerank.api_key, "rerank-key")

    def test_a_bad_boolean_is_refused(self) -> None:
        with self.assertRaises(RagConfigError):
            load_rag_settings(
                environ={ENV_ENABLED: "maybe"},
                credential_store=FakeCredentials(),
            )


if __name__ == "__main__":
    unittest.main()
