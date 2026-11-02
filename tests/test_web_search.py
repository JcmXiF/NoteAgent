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
from noteagent.acquire.run_search import run_web_search
from noteagent.acquire.search_config import (
    SearchConfig,
    SearchConfigError,
    SearchError,
    SearchModeUnavailable,
    load_search_config,
)
from noteagent.acquire.tools.providers.brave import (
    normalize_brave_search_lang,
    parse_brave_payload,
)
from noteagent.acquire.tools.providers.serper import parse_serper_payload
from noteagent.acquire.tools.web_search import web_search
from noteagent.acquire.types import SerpHit
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
