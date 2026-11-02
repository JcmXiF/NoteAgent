from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx

from noteagent.acquire.cards import cards_from_hits, favicon_for_host
from noteagent.acquire.run_search import WebSearchRun, run_web_search
from noteagent.acquire.search_config import (
    NATIVE_SEARCH_PROVIDERS,
    NativeSearchConfig,
    NativeSearchConfigMissing,
    NativeSearchError,
    NativeSearchRejected,
    NativeSearchUnreachable,
    SearchConfig,
    SearchConfigError,
    SearchError,
    SearchModeUnavailable,
    build_native_config,
    load_search_config,
    native_search_available,
    provider_for_host,
)
from noteagent.acquire.tools.providers.anthropic_native import (
    native_search as anthropic_native_search,
)
from noteagent.acquire.tools.providers.brave import (
    normalize_brave_search_lang,
    parse_brave_payload,
)
from noteagent.acquire.tools.providers.openai_native import native_search
from noteagent.acquire.tools.providers.serper import parse_serper_payload
from noteagent.acquire.tools.web_search import web_search
from noteagent.acquire.types import ProviderNotConfiguredError, SerpHit
from noteagent.api.search import card_to_dict
from noteagent.project.init import init_project
from noteagent.util.run_log import RunLogger, run_log_path


class ParseProviderTests(unittest.TestCase):
    def test_parse_brave(self) -> None:
        hits = parse_brave_payload(
            {
                "web": {
                    "results": [
                        {
                            "title": "Kullback–Leibler divergence",
                            "url": "https://en.wikipedia.org/wiki/KL",
                            "description": "A measure of difference.",
                            "profile": {"name": "Wikipedia"},
                            "meta_url": {
                                "hostname": "en.wikipedia.org",
                                "favicon": "https://imgs.search.brave.com/wiki.ico",
                            },
                        },
                        {"title": "skip me"},
                    ]
                }
            }
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].site, "en.wikipedia.org")
        self.assertEqual(hits[0].site_name, "Wikipedia")
        self.assertEqual(hits[0].favicon, "https://imgs.search.brave.com/wiki.ico")
        self.assertIn("measure", hits[0].snippet)

    def test_parse_serper(self) -> None:
        hits = parse_serper_payload(
            {
                "organic": [
                    {
                        "title": "知乎专栏",
                        "link": "https://zhuanlan.zhihu.com/p/1",
                        "snippet": "讲解",
                        "sitename": "知乎",
                        "favicon": "https://example.com/zhihu.ico",
                    }
                ]
            }
        )
        self.assertEqual(hits[0].url, "https://zhuanlan.zhihu.com/p/1")
        self.assertEqual(hits[0].site, "zhuanlan.zhihu.com")
        self.assertEqual(hits[0].site_name, "知乎")
        self.assertEqual(hits[0].favicon, "https://example.com/zhihu.ico")


class WebSearchDispatchTests(unittest.TestCase):
    def test_brave_mock_http(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("api.search.brave.com", str(request.url))
            self.assertEqual(request.headers.get("X-Subscription-Token"), "tok")
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Wiki KL",
                                "url": "https://zh.wikipedia.org/wiki/KL",
                                "description": "定义",
                                "profile": {"name": "Wikipedia"},
                            }
                        ]
                    }
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = SearchConfig(provider="brave", api_key="tok")
        hits = web_search("KL散度", top_k=5, config=cfg, client=client)
        self.assertEqual(len(hits), 1)
        run = run_web_search("KL散度", top_k=5, config=cfg, client=client)
        self.assertEqual(run.cards[0].site, "zh.wikipedia.org")
        self.assertEqual(run.cards[0].site_name, "Wikipedia")
        self.assertTrue(run.cards[0].favicon)

    def test_brave_sends_locale_and_product_presets(self) -> None:
        request_url: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_url["url"] = str(request.url)
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "T",
                                "url": "https://example.com/1",
                                "description": "d",
                            }
                        ]
                    }
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = SearchConfig(
            provider="brave",
            api_key="tok",
            country="CN",
            search_lang="zh",
            ui_lang="zh-CN",
        )
        web_search("测试", top_k=5, config=cfg, client=client)
        qs = parse_qs(urlsplit(request_url["url"]).query)
        self.assertEqual(qs["q"], ["测试"])
        self.assertEqual(qs["count"], ["5"])
        self.assertEqual(qs["country"], ["CN"])
        self.assertEqual(qs["search_lang"], ["zh-hans"])
        self.assertEqual(qs["ui_lang"], ["zh-CN"])
        self.assertEqual(qs["result_filter"], ["web"])
        self.assertEqual(qs["text_decorations"], ["false"])
        self.assertNotIn("offset", qs)
        self.assertNotIn("safesearch", qs)
        self.assertNotIn("freshness", qs)
        self.assertNotIn("extra_snippets", qs)
        self.assertNotIn("summary", qs)
        self.assertNotIn("goggles", qs)
        self.assertNotIn("units", qs)

    def test_brave_search_lang_aliases(self) -> None:
        self.assertEqual(normalize_brave_search_lang("zh"), "zh-hans")
        self.assertEqual(normalize_brave_search_lang("zh-CN"), "zh-hans")
        self.assertEqual(normalize_brave_search_lang("zh-hant"), "zh-hant")
        self.assertEqual(normalize_brave_search_lang("ja"), "jp")
        self.assertEqual(normalize_brave_search_lang("en"), "en")

    def test_serper_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="nope")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = SearchConfig(provider="serper", api_key="tok")
        with self.assertRaises(SearchError):
            web_search("KL", config=cfg, client=client)

    def test_cards_cap(self) -> None:
        hits = [
            SerpHit(title=f"t{i}", url=f"https://example.com/{i}", snippet="")
            for i in range(20)
        ]
        cards = cards_from_hits(hits, query="q", limit=3)
        self.assertEqual(len(cards), 3)
        self.assertIn("example.com", cards[0].favicon)
        self.assertEqual(cards[0].site_name, "example.com")

    def test_favicon_prefers_api_url(self) -> None:
        self.assertEqual(
            favicon_for_host("zhihu.com", "https://cdn.example/z.png"),
            "https://cdn.example/z.png",
        )
        self.assertIn("zhihu.com", favicon_for_host("zhihu.com"))


NATIVE_PAYLOAD = {
    "status": "completed",
    "output": [
        {
            "type": "web_search_call",
            "results": [
                {
                    "type": "text_result",
                    "title": "Wiki",
                    "url": "https://zh.wikipedia.org/wiki/KL",
                    "snippet": "定义",
                },
                {
                    "type": "text_result",
                    "title": "Other",
                    "url": "https://example.com/a",
                    "snippet": "s",
                },
            ],
        },
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "KL is a measure",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "start_index": 0,
                            "end_index": 6,
                            "url": "https://zh.wikipedia.org/wiki/KL",
                            "title": "Wiki",
                        },
                        {
                            "type": "url_citation",
                            "start_index": 6,
                            "end_index": 15,
                            "url": "https://example.com/b",
                            "title": "B",
                        },
                    ],
                }
            ],
        },
    ],
}


ANTHROPIC_PAYLOAD = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "KL divergence"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "title": "Wiki",
                    "url": "https://zh.wikipedia.org/wiki/KL",
                    "encrypted_content": "x",
                    "page_age": "2026-01-01",
                },
                {
                    "type": "web_search_result",
                    "title": "Other",
                    "url": "https://example.com/a",
                    "encrypted_content": "y",
                },
            ],
        },
        {
            "type": "text",
            "text": "KL is a measure of difference.",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://zh.wikipedia.org/wiki/KL",
                    "title": "Wiki",
                    "cited_text": "定义",
                    "encrypted_index": "z",
                },
                {
                    "type": "web_search_result_location",
                    "url": "https://example.com/b",
                    "title": "B",
                    "cited_text": "a measure",
                },
            ],
        },
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 20},
}


def native_config() -> NativeSearchConfig:
    return NativeSearchConfig(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-openai",
        model="gpt-4o-mini",
    )


def anthropic_config() -> NativeSearchConfig:
    return NativeSearchConfig(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key="sk-ant",
        model="claude-sonnet-5-5",
    )


#: One entry per provider with a native adapter: how to configure it, what it
#: answers, and the URL that answer comes from.
NATIVE_PROVIDERS = {
    "openai": {
        "config": native_config,
        "payload": NATIVE_PAYLOAD,
        "path": "/responses",
        "url": "https://api.openai.com/v1/responses",
    },
    "anthropic": {
        "config": anthropic_config,
        "payload": ANTHROPIC_PAYLOAD,
        "path": "/messages",
        "url": "https://api.anthropic.com/v1/messages",
    },
}


class CliSearchTests(unittest.TestCase):
    def test_the_cli_applies_the_document_before_searching(self) -> None:
        from noteagent.__main__ import cmd_acquire_search

        run = WebSearchRun(query="q", provider="openai", hits=[], cards=[])
        with (
            patch("noteagent.settings.install.install_view") as applied,
            patch(
                "noteagent.acquire.run_search.run_web_search",
                return_value=run,
            ) as searched,
        ):
            code = cmd_acquire_search("q", top_k=1, project=None, run_log=False)

        self.assertEqual(code, 0)
        applied.assert_called_once()
        searched.assert_called_once()


class NativeSupportTests(unittest.TestCase):
    def test_every_supported_provider_has_an_adapter(self) -> None:
        from noteagent.acquire.run_search import NATIVE_ADAPTERS

        self.assertEqual(set(NATIVE_ADAPTERS), set(NATIVE_SEARCH_PROVIDERS))

    def test_a_native_host_names_its_provider(self) -> None:
        self.assertEqual(provider_for_host("https://api.openai.com/v1"), "openai")
        self.assertEqual(provider_for_host("https://api.anthropic.com/v1"), "anthropic")
        self.assertEqual(provider_for_host("https://api.deepseek.com/v1"), "")
        self.assertEqual(provider_for_host("http://["), "")

    def test_native_is_available_for_a_supported_provider(self) -> None:
        self.assertTrue(native_search_available(provider="openai", base_url=""))
        self.assertTrue(native_search_available(provider="anthropic", base_url=""))
        self.assertFalse(native_search_available(provider="deepseek", base_url=""))
        self.assertTrue(
            native_search_available(provider="", base_url="https://api.openai.com/v1")
        )
        self.assertTrue(
            native_search_available(
                provider="", base_url="https://api.anthropic.com/v1"
            )
        )
        self.assertFalse(
            native_search_available(provider="", base_url="https://example.com")
        )

    def test_a_native_connection_needs_every_part_and_caps_its_wait(self) -> None:
        ready = build_native_config(
            "openai", "https://api.openai.com/v1", "sk", "m", 120
        )
        self.assertIsNotNone(ready)
        assert ready is not None
        self.assertEqual(ready.timeout_seconds, 60)

        short = build_native_config(
            "openai", "https://api.openai.com/v1", "sk", "m", 30
        )
        self.assertIsNotNone(short)
        assert short is not None
        self.assertEqual(short.timeout_seconds, 30)

        self.assertIsNone(build_native_config("openai", "", "sk", "m"))
        self.assertIsNone(
            build_native_config("deepseek", "https://api.deepseek.com/v1", "sk", "m")
        )

    def test_an_anthropic_connection_is_built_like_any_other(self) -> None:
        ready = build_native_config(
            "anthropic", "https://api.anthropic.com/v1", "sk-ant", "claude-sonnet-5-5"
        )
        self.assertIsNotNone(ready)
        assert ready is not None
        self.assertEqual(ready.provider, "anthropic")


class OpenAINativeAdapterTests(unittest.TestCase):
    def test_request_shape_auth_and_mapping(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=NATIVE_PAYLOAD)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        hits = native_search(
            "KL divergence",
            top_k=3,
            config=native_config(),
            client=client,
        )

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(seen["auth"], "Bearer sk-openai")
        body = seen["body"]
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["input"], "KL divergence")
        self.assertEqual(body["include"], ["web_search_call.results"])

        self.assertEqual(
            [hit.url for hit in hits],
            [
                "https://zh.wikipedia.org/wiki/KL",
                "https://example.com/a",
                "https://example.com/b",
            ],
        )
        self.assertEqual(hits[0].snippet, "定义")
        self.assertEqual(hits[2].snippet, "a measure")
        self.assertEqual(hits[0].site, "zh.wikipedia.org")

    def test_a_citation_fills_a_missing_snippet(self) -> None:
        payload = {
            "output": [
                {
                    "type": "web_search_call",
                    "results": [
                        {
                            "type": "text_result",
                            "title": "Wiki",
                            "url": "https://zh.wikipedia.org/wiki/KL",
                        }
                    ],
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "KL is a measure",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "start_index": 0,
                                    "end_index": 6,
                                    "url": "https://zh.wikipedia.org/wiki/KL",
                                    "title": "Wiki",
                                }
                            ],
                        }
                    ],
                },
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        hits = native_search("KL", top_k=3, config=native_config(), client=client)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].snippet, "KL is")


class NativeAdapterFailureTests(unittest.TestCase):
    def _adapters(self):
        return (
            ("openai", native_search, native_config),
            ("anthropic", anthropic_native_search, anthropic_config),
        )

    def test_failures_are_named(self) -> None:
        cases = (
            (401, NativeSearchRejected),
            (403, NativeSearchRejected),
            (500, NativeSearchError),
        )
        for provider, adapter, make_config in self._adapters():
            for status, error in cases:
                with self.subTest(provider=provider, status=status):

                    def handler(
                        request: httpx.Request, status=status
                    ) -> httpx.Response:
                        return httpx.Response(status, text="no")

                    client = httpx.Client(transport=httpx.MockTransport(handler))
                    with self.assertRaises(error):
                        adapter(
                            "q", top_k=3, config=make_config(), client=client
                        )

    def test_a_transport_failure_is_named(self) -> None:
        for provider, adapter, make_config in self._adapters():
            with self.subTest(provider=provider):

                def handler(request: httpx.Request) -> httpx.Response:
                    raise httpx.ConnectError("down")

                client = httpx.Client(transport=httpx.MockTransport(handler))
                with self.assertRaises(NativeSearchUnreachable):
                    adapter("q", top_k=3, config=make_config(), client=client)

    def test_a_malformed_answer_is_named(self) -> None:
        for provider, adapter, make_config in self._adapters():
            with self.subTest(provider=provider):

                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, text="not JSON")

                client = httpx.Client(transport=httpx.MockTransport(handler))
                with self.assertRaises(NativeSearchError):
                    adapter("q", top_k=3, config=make_config(), client=client)


class AnthropicNativeAdapterTests(unittest.TestCase):
    def test_request_shape_auth_and_mapping(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["api_key"] = request.headers.get("x-api-key")
            seen["version"] = request.headers.get("anthropic-version")
            seen["authorization"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=ANTHROPIC_PAYLOAD)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        hits = anthropic_native_search(
            "KL divergence",
            top_k=3,
            config=anthropic_config(),
            client=client,
        )

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["api_key"], "sk-ant")
        self.assertEqual(seen["version"], "2023-06-01")
        self.assertIsNone(seen["authorization"])
        body = seen["body"]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertEqual(body["messages"], [{"role": "user", "content": "KL divergence"}])
        self.assertEqual(
            body["tools"],
            [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        )

        self.assertEqual(
            [hit.url for hit in hits],
            [
                "https://zh.wikipedia.org/wiki/KL",
                "https://example.com/a",
                "https://example.com/b",
            ],
        )
        self.assertEqual(hits[0].snippet, "定义")
        self.assertEqual(hits[2].snippet, "a measure")
        self.assertEqual(hits[0].site, "zh.wikipedia.org")

    def test_a_tool_error_block_is_named(self) -> None:
        payload = {
            "type": "message",
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {
                        "type": "web_search_tool_result_error",
                        "error_code": "max_uses_exceeded",
                    },
                }
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(NativeSearchError) as caught:
            anthropic_native_search(
                "q", top_k=3, config=anthropic_config(), client=client
            )
        self.assertIn("max_uses_exceeded", str(caught.exception))


class NativeResolverTests(unittest.TestCase):
    def _client(
        self, *, native_status: int = 200, provider: str = "openai"
    ) -> tuple[httpx.Client, list[str]]:
        spec = NATIVE_PROVIDERS[provider]
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if str(request.url).endswith(spec["path"]):
                if native_status == 200:
                    return httpx.Response(200, json=spec["payload"])
                return httpx.Response(native_status, text="no")
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Brave",
                                "url": "https://brave.example/1",
                                "description": "b",
                            }
                        ]
                    }
                },
            )

        return httpx.Client(transport=httpx.MockTransport(handler)), calls

    def _config(
        self,
        mode: str,
        *,
        connection: bool = True,
        llm_provider: str = "openai",
        provider: str = "brave",
        api_key: str = "tok",
    ) -> SearchConfig:
        native = None
        if connection and llm_provider in NATIVE_PROVIDERS:
            native = NATIVE_PROVIDERS[llm_provider]["config"]()
        return SearchConfig(
            provider=provider,
            api_key=api_key,
            mode=mode,
            llm_provider=llm_provider,
            native=native,
        )

    def test_auto_prefers_native(self) -> None:
        for provider, spec in NATIVE_PROVIDERS.items():
            with self.subTest(provider=provider):
                client, calls = self._client(provider=provider)
                run = run_web_search(
                    "KL",
                    config=self._config("auto", llm_provider=provider),
                    client=client,
                )
                self.assertEqual(run.provider, provider)
                self.assertEqual(run.cards[0].source, provider)
                self.assertEqual(calls, [spec["url"]])

    def test_external_never_calls_native(self) -> None:
        for provider, spec in NATIVE_PROVIDERS.items():
            with self.subTest(provider=provider):
                client, calls = self._client(provider=provider)
                run = run_web_search(
                    "KL",
                    config=self._config("external", llm_provider=provider),
                    client=client,
                )
                self.assertEqual(run.provider, "brave")
                self.assertEqual(run.cards[0].source, "brave")
                self.assertEqual(len(calls), 1)
                self.assertNotIn(spec["path"], calls[0])

    def test_native_never_falls_back(self) -> None:
        for provider, spec in NATIVE_PROVIDERS.items():
            with self.subTest(provider=provider):
                client, calls = self._client(provider=provider)
                run = run_web_search(
                    "KL",
                    config=self._config("native", llm_provider=provider),
                    client=client,
                )
                self.assertEqual(run.provider, provider)
                self.assertEqual(calls, [spec["url"]])

    def test_native_failure_never_falls_back(self) -> None:
        for provider, spec in NATIVE_PROVIDERS.items():
            with self.subTest(provider=provider):
                client, calls = self._client(native_status=401, provider=provider)
                with self.assertRaises(NativeSearchRejected):
                    run_web_search(
                        "KL",
                        config=self._config("native", llm_provider=provider),
                        client=client,
                    )
                self.assertEqual(calls, [spec["url"]])

    def test_auto_falls_back_when_native_fails(self) -> None:
        for provider, spec in NATIVE_PROVIDERS.items():
            with self.subTest(provider=provider):
                client, calls = self._client(native_status=401, provider=provider)
                run = run_web_search(
                    "KL",
                    config=self._config("auto", llm_provider=provider),
                    client=client,
                )
                self.assertEqual(run.provider, "brave")
                self.assertEqual(run.cards[0].source, "brave")
                self.assertTrue(any(url.endswith(spec["path"]) for url in calls))
                self.assertTrue(any("api.search.brave.com" in url for url in calls))

    def test_auto_keeps_the_native_error_when_no_external_is_configured(self) -> None:
        for provider in NATIVE_PROVIDERS:
            with self.subTest(provider=provider):
                client, _calls = self._client(native_status=401, provider=provider)
                with self.assertRaises(NativeSearchRejected):
                    run_web_search(
                        "KL",
                        config=self._config(
                            "auto",
                            llm_provider=provider,
                            provider="none",
                            api_key="",
                        ),
                        client=client,
                    )

    def test_native_without_a_model_connection_is_named(self) -> None:
        for provider in NATIVE_PROVIDERS:
            with self.subTest(provider=provider):
                client, calls = self._client(provider=provider)
                with self.assertRaises(NativeSearchConfigMissing):
                    run_web_search(
                        "KL",
                        config=self._config(
                            "native", connection=False, llm_provider=provider
                        ),
                        client=client,
                    )
                self.assertEqual(calls, [])

    def test_auto_with_neither_path_is_named(self) -> None:
        client, calls = self._client()
        with self.assertRaises(ProviderNotConfiguredError):
            run_web_search(
                "KL",
                config=SearchConfig(provider="none", api_key="", mode="auto"),
                client=client,
            )
        self.assertEqual(calls, [])


class SearchModeTests(unittest.TestCase):
    def _client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "T",
                                "url": "https://example.com/1",
                                "description": "d",
                            }
                        ]
                    }
                },
            )

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_auto_and_external_use_the_external_provider(self) -> None:
        for mode in ("auto", "external"):
            with self.subTest(mode=mode):
                cfg = SearchConfig(provider="brave", api_key="tok", mode=mode)
                run = run_web_search("KL", config=cfg, client=self._client())
                self.assertEqual(len(run.cards), 1)
                self.assertEqual(run.cards[0].source, "brave")

    def test_native_is_refused_until_an_adapter_exists(self) -> None:
        cfg = SearchConfig(provider="brave", api_key="tok", mode="native")
        with self.assertRaises(SearchModeUnavailable) as caught:
            run_web_search("KL", config=cfg, client=self._client())
        self.assertEqual(caught.exception.code, "provider.nativeUnavailable")

    def test_a_card_names_the_source_that_produced_it(self) -> None:
        cfg = SearchConfig(provider="brave", api_key="tok")
        run = run_web_search("KL", config=cfg, client=self._client())
        self.assertEqual(card_to_dict(run.cards[0])["source"], "brave")

    def test_the_mode_is_read_from_the_environment(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "NOTEAGENT_SEARCH_PROVIDER": "brave",
                    "NOTEAGENT_SEARCH_API_KEY": "tok",
                    "NOTEAGENT_SEARCH_MODE": "external",
                },
            ),
            patch("noteagent.acquire.search_config.load_dotenv"),
        ):
            self.assertEqual(load_search_config().mode, "external")

    def test_an_unknown_mode_is_refused(self) -> None:
        with (
            patch.dict(os.environ, {"NOTEAGENT_SEARCH_MODE": "sideways"}),
            patch("noteagent.acquire.search_config.load_dotenv"),
        ):
            with self.assertRaises(SearchConfigError):
                load_search_config()


class SearchRunLogTests(unittest.TestCase):
    def test_success_writes_start_and_end(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Wiki KL",
                                "url": "https://zh.wikipedia.org/wiki/KL",
                                "description": "定义",
                            }
                        ]
                    }
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = SearchConfig(provider="brave", api_key="secret-token-must-not-log")
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            run = run_web_search(
                "KL散度",
                top_k=5,
                config=cfg,
                client=client,
                run_logger=logger,
                caller="cli",
            )
            log_path = run_log_path(layout)
            blob = log_path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in blob.splitlines() if line.strip()]
        names = [event["event"] for event in events]
        self.assertEqual(names, ["search.start", "search.end"])
        self.assertEqual(events[0]["component"], "acquire")
        self.assertEqual(events[0]["caller"], "cli")
        self.assertEqual(events[1]["hits"], 1)
        self.assertEqual(events[1]["urls"], [run.cards[0].url])
        self.assertNotIn("secret-token-must-not-log", blob)
        self.assertNotIn("定义", blob)

    def test_http_error_writes_search_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="nope")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = SearchConfig(provider="serper", api_key="tok")
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            with self.assertRaises(SearchError):
                run_web_search(
                    "KL",
                    config=cfg,
                    client=client,
                    run_logger=logger,
                    caller="api",
                )
            events = [
                json.loads(line)
                for line in run_log_path(layout).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(
            [event["event"] for event in events],
            ["search.start", "search.error"],
        )
        self.assertEqual(events[1]["error"], "SearchError")
        self.assertEqual(events[1]["caller"], "api")


if __name__ == "__main__":
    unittest.main()
