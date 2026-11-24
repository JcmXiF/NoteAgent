"""The wiki as the agent may touch it: pages by slug, and search over them.

Both tools are read-only and both stop at the wiki's edge: ``raw/`` is the
material, ``notes/`` is the reader's own layer, and neither is a wiki page.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.agent.tools import ToolContext
from noteagent.project.layout import ProjectLayout
from noteagent.rag.config import EmbeddingConfig
from noteagent.rag.faux import FakeEmbedder
from noteagent.rag.result import RagHit, RagSearchResult
from noteagent.rag.search import RagSearchBackend
from noteagent.rag.settings import RagSettings
from noteagent.wiki.agent_tools import WikiToolLimits, wiki_tools


def _write(layout: ProjectLayout, relative: str, body: str) -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _page(title: str, body: str) -> str:
    return f'---\ntitle: "{title}"\n---\n\n{body}\n'


class FakeRag:
    """A retrieval backend whose one answer, or failure, the test chooses."""

    def __init__(self, result=None, *, error=None):
        self.result = result or RagSearchResult((), "lexical")
        self.error = error
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.result


class WikiToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        _write(
            self.layout,
            "wiki/concepts/entropy.md",
            _page("Entropy", "熵衡量不确定性。[[information]]"),
        )
        _write(
            self.layout,
            "wiki/concepts/information.md",
            _page("Information", "信息是不确定性的减少。"),
        )
        _write(self.layout, "wiki/other.md", _page("Other", "无关的一页。"))
        _write(self.layout, "wiki/index.md", "# Index\n\n熵这个词也在这里。")
        _write(self.layout, "raw/secret.md", "raw 里的秘密：熵。")
        _write(self.layout, "notes/private.md", "我的备注：熵。")
        self.registry = wiki_tools(self.layout)

    def dispatch(self, name: str, arguments: str):
        return self.registry.dispatch(name, arguments, ToolContext("s1"))

    def test_only_the_two_read_tools_are_offered(self) -> None:
        self.assertEqual(
            [tool.name for tool in self.registry.enabled()],
            ["wiki_read", "search"],
        )
        for tool in self.registry.enabled():
            self.assertEqual(tool.side_effect, "read")

    def test_no_write_or_shell_tool_is_offered(self) -> None:
        names = {tool.name for tool in self.registry.enabled()}

        self.assertFalse(
            names
            & {
                "write",
                "run",
                "shell",
                "bash",
                "exec",
                "python",
                "fetch",
                "web_fetch",
            }
        )
        # The old propose control action is gone; no handler can start a job.
        self.assertIsNone(self.registry.get("propose"))

    def test_traversal_spellings_are_not_pages(self) -> None:
        for target in (
            "../../etc/passwd",
            "wiki/../../raw/secret.md",
            "/etc/passwd",
            r"C:\Windows\win.ini",
            "./../raw/secret.md",
            "..%2f..%2fraw/secret.md",
        ):
            with self.subTest(target=target):
                result = self.dispatch(
                    "wiki_read", json.dumps({"slug": target})
                )
                self.assertTrue(result.meta.get("missing"))
                self.assertNotIn("秘密", result.content)

    def test_a_symlink_out_of_the_wiki_is_not_a_page(self) -> None:
        outside = self.layout.root / "outside.md"
        outside.write_text("# Outside\n\n秘密。", encoding="utf-8")
        link = self.layout.wiki / "escape.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not create symlinks here")

        result = self.dispatch("wiki_read", '{"slug": "escape"}')

        self.assertTrue(result.meta.get("missing"))
        self.assertNotIn("秘密", result.content)

    def test_a_page_is_read_by_slug_with_its_title_and_body(self) -> None:
        result = self.dispatch("wiki_read", '{"slug": "concepts/entropy"}')

        self.assertIn("Entropy", result.content)
        self.assertIn("熵衡量不确定性。", result.content)
        self.assertNotIn("title:", result.content)
        self.assertEqual(result.meta["path"], "wiki/concepts/entropy.md")
        self.assertEqual(result.meta["slug"], "concepts/entropy")
        self.assertNotIn("truncated", result.meta)

    def test_a_wikilink_spelling_resolves_to_the_same_page(self) -> None:
        result = self.dispatch("wiki_read", '{"slug": "[[entropy]]"}')

        self.assertEqual(result.meta["path"], "wiki/concepts/entropy.md")

    def test_a_page_that_is_not_there_is_said_so_without_raising(self) -> None:
        result = self.dispatch("wiki_read", '{"slug": "concepts/nope"}')

        self.assertTrue(result.meta["missing"])
        self.assertIn("no wiki page", result.content)

    def test_raw_and_notes_are_not_wiki_pages(self) -> None:
        for target in ("raw/secret", "notes/private", "wiki/index"):
            with self.subTest(target=target):
                result = self.dispatch("wiki_read", f'{{"slug": "{target}"}}')
                self.assertTrue(result.meta["missing"])
                self.assertNotIn("秘密", result.content)
                self.assertNotIn("备注", result.content)

    def test_a_path_outside_the_project_is_not_found(self) -> None:
        result = self.dispatch("wiki_read", '{"slug": "../../etc/passwd"}')

        self.assertTrue(result.meta["missing"])

    def test_a_long_page_is_cut_to_the_per_page_budget(self) -> None:
        _write(self.layout, "wiki/long.md", _page("Long", "长" * 2_000))
        registry = wiki_tools(self.layout, limits=WikiToolLimits(max_page_chars=300))

        result = registry.dispatch(
            "wiki_read", '{"slug": "long"}', ToolContext("s1")
        )

        self.assertLessEqual(len(result.content), 300)
        self.assertTrue(result.meta["truncated"])
        self.assertEqual(result.diagnostics[0].code, "truncated")

    def test_a_page_longer_than_the_old_budget_is_read_whole_by_default(
        self,
    ) -> None:
        body = "长" * 12_000
        _write(self.layout, "wiki/long.md", _page("Long", body))

        result = self.dispatch("wiki_read", '{"slug": "long"}')

        self.assertEqual(result.meta["totalChars"], 12_000)
        self.assertNotIn("truncated", result.meta)
        self.assertNotIn("nextOffset", result.meta)
        self.assertEqual(result.content.count("长"), 12_000)

    def test_a_long_page_can_be_read_in_slices_to_the_tail(self) -> None:
        body = "甲" * 1_000 + "尾部标记"
        _write(self.layout, "wiki/long.md", _page("Long", body))
        registry = wiki_tools(
            self.layout, limits=WikiToolLimits(max_page_chars=300)
        )
        offset = 0
        seen = 0
        tail_seen = False
        for _ in range(20):
            result = registry.dispatch(
                "wiki_read",
                json.dumps({"slug": "long", "offset": offset}),
                ToolContext("s1"),
            )
            self.assertLessEqual(len(result.content), 300)
            self.assertEqual(result.meta["totalChars"], 1_004)
            self.assertEqual(result.meta["offset"], offset)
            seen += result.content.count("甲")
            tail_seen = tail_seen or "尾部标记" in result.content
            if not result.meta.get("truncated"):
                break
            self.assertEqual(
                result.meta["nextOffset"],
                offset + result.content.count("甲"),
            )
            offset = result.meta["nextOffset"]
        else:
            self.fail("the page never finished")
        self.assertTrue(tail_seen)
        self.assertEqual(seen, 1_000)

    def test_offset_and_limit_read_the_tail_in_one_call(self) -> None:
        body = "0123456789" * 100
        _write(self.layout, "wiki/digits.md", _page("Digits", body))

        result = self.dispatch(
            "wiki_read", '{"slug": "digits", "offset": 995, "limit": 100}'
        )

        self.assertIn("56789", result.content)
        self.assertEqual(result.meta["totalChars"], 1_000)
        self.assertEqual(result.meta["offset"], 995)
        self.assertNotIn("truncated", result.meta)

    def test_an_offset_past_the_end_says_where_the_page_ends(self) -> None:
        body = "短页" * 100
        _write(self.layout, "wiki/short.md", _page("Short", body))

        result = self.dispatch(
            "wiki_read", '{"slug": "short", "offset": 100000}'
        )

        self.assertEqual(result.meta["totalChars"], 200)
        self.assertIn("no text at offset 100000", result.content)
        self.assertIn("200 characters", result.content)
        self.assertEqual(
            result.diagnostics[0].code, "offset_past_end"
        )
        self.assertNotIn("truncated", result.meta)

    def test_a_negative_offset_is_refused_by_the_schema(self) -> None:
        result = self.dispatch(
            "wiki_read", '{"slug": "entropy", "offset": -1}'
        )

        self.assertTrue(result.meta["error"])
        self.assertIn("invalid arguments", result.content)

    def test_a_zero_limit_is_refused_by_the_schema(self) -> None:
        result = self.dispatch(
            "wiki_read", '{"slug": "entropy", "limit": 0}'
        )

        self.assertTrue(result.meta["error"])
        self.assertIn("invalid arguments", result.content)

    def test_a_tiny_limit_still_advances_the_offset(self) -> None:
        body = "0123456789" * 100
        _write(self.layout, "wiki/digits.md", _page("Digits", body))

        result = self.dispatch(
            "wiki_read", '{"slug": "digits", "limit": 1}'
        )

        self.assertTrue(result.meta["truncated"])
        self.assertGreater(result.meta["nextOffset"], 0)

    def test_search_finds_pages_by_slug_title_and_body(self) -> None:
        by_body = self.dispatch("search", '{"query": "不确定性"}')

        self.assertIn("[[concepts/entropy]]", by_body.content)
        self.assertIn("[[concepts/information]]", by_body.content)
        self.assertEqual(by_body.meta["scope"], "wiki")
        self.assertGreaterEqual(by_body.meta["hits"], 2)

    def test_search_never_returns_raw_or_notes(self) -> None:
        result = self.dispatch("search", '{"query": "熵衡量"}')

        self.assertIn("[[concepts/entropy]]", result.content)
        self.assertNotIn("secret", result.content)
        self.assertNotIn("private", result.content)
        self.assertNotIn("index", result.content)

    def test_a_search_with_no_match_says_so(self) -> None:
        result = self.dispatch("search", '{"query": "量子引力"}')

        self.assertTrue(result.meta["missing"])
        self.assertIn("no wiki pages matched", result.content)

    def test_a_hybrid_result_replaces_the_lexical_search(self) -> None:
        result = RagSearchResult(
            (RagHit("concepts/entropy", "熵摘录", "title", 0.9),),
            "hybrid",
        )
        registry = wiki_tools(self.layout, rag=FakeRag(result))

        answer = registry.dispatch(
            "search", '{"query": "熵"}', ToolContext("s1")
        )

        self.assertIn("[[concepts/entropy]]", answer.content)
        self.assertIn("熵摘录", answer.content)
        self.assertEqual(answer.meta["mode"], "hybrid")
        self.assertEqual(answer.meta["hits"], 1)
        self.assertFalse(answer.meta["degraded"])

    def test_a_degraded_result_with_hits_keeps_them(self) -> None:
        result = RagSearchResult(
            (RagHit("concepts/entropy", "熵摘录", "title", 0.9),),
            "hybrid",
            True,
            ("rerank failed: down",),
        )
        registry = wiki_tools(self.layout, rag=FakeRag(result))

        answer = registry.dispatch(
            "search", '{"query": "熵"}', ToolContext("s1")
        )

        self.assertIn("[[concepts/entropy]]", answer.content)
        self.assertEqual(answer.meta["mode"], "hybrid")
        self.assertTrue(answer.meta["degraded"])
        self.assertEqual(
            [item.code for item in answer.diagnostics],
            ["rag_degraded"],
        )

    def test_a_degraded_result_falls_back_to_lexical(self) -> None:
        result = RagSearchResult(
            (), "lexical", True, ("index cold",)
        )
        registry = wiki_tools(self.layout, rag=FakeRag(result))

        answer = registry.dispatch(
            "search", '{"query": "不确定性"}', ToolContext("s1")
        )

        self.assertIn("[[concepts/entropy]]", answer.content)
        self.assertEqual(answer.meta["mode"], "lexical")
        self.assertTrue(answer.meta["degraded"])
        self.assertEqual(
            [item.code for item in answer.diagnostics],
            ["rag_fallback"],
        )

    def test_a_raising_backend_falls_back_to_lexical(self) -> None:
        registry = wiki_tools(
            self.layout, rag=FakeRag(error=RuntimeError("boom"))
        )

        answer = registry.dispatch(
            "search", '{"query": "不确定性"}', ToolContext("s1")
        )

        self.assertIn("[[concepts/entropy]]", answer.content)
        self.assertTrue(answer.meta["degraded"])
        self.assertIn("boom", answer.diagnostics[0].message)

    def test_the_search_contract_is_unchanged_by_rag(self) -> None:
        plain = wiki_tools(self.layout).get("search")
        hybrid = wiki_tools(
            self.layout,
            rag=FakeRag(RagSearchResult((), "lexical")),
        ).get("search")

        self.assertIsNotNone(plain)
        self.assertIsNotNone(hybrid)
        assert plain is not None and hybrid is not None
        self.assertEqual(plain.name, hybrid.name)
        self.assertEqual(plain.description, hybrid.description)
        self.assertEqual(plain.parameters, hybrid.parameters)
        self.assertEqual(plain.side_effect, hybrid.side_effect)
        self.assertEqual(plain.timeout_s, hybrid.timeout_s)

    def test_rag_off_adds_no_mode_to_the_result(self) -> None:
        result = self.dispatch("search", '{"query": "不确定性"}')

        self.assertNotIn("mode", result.meta)

    def test_the_real_backend_wires_through_the_tool(self) -> None:
        settings = RagSettings(
            enabled=True,
            embedding=EmbeddingConfig(
                endpoint="http://fake",
                model="fake-model",
                dim=4,
            ),
        )
        backend = RagSearchBackend(
            self.layout,
            settings,
            embedder=FakeEmbedder(dim=4),
            clock=lambda: 0.0,
            sync_interval_s=0.0,
            sync_runner=lambda work: work(),
        )
        registry = wiki_tools(self.layout, rag=backend)

        cold = registry.dispatch(
            "search", '{"query": "不确定性"}', ToolContext("s1")
        )
        self.assertIn("[[concepts/entropy]]", cold.content)
        self.assertEqual(cold.meta["mode"], "lexical")
        self.assertTrue(cold.meta["degraded"])

        warm = registry.dispatch(
            "search", '{"query": "不确定性"}', ToolContext("s1")
        )
        self.assertEqual(warm.meta["mode"], "hybrid")
        self.assertFalse(warm.meta["degraded"])
        self.assertIn("[[concepts/entropy]]", warm.content)

    def test_search_keeps_its_hit_and_excerpt_budget(self) -> None:
        for number in range(6):
            _write(
                self.layout,
                f"wiki/budget-{number}.md",
                _page(f"Budget {number}", "预算 " + "词" * 500),
            )
        registry = wiki_tools(
            self.layout,
            limits=WikiToolLimits(max_hits=3, excerpt_chars=40),
        )

        result = registry.dispatch(
            "search", '{"query": "预算"}', ToolContext("s1")
        )

        self.assertEqual(result.meta["hits"], 3)
        self.assertEqual(result.meta["omitted"], "budget")
        self.assertIn("more matches omitted", result.content)
        self.assertEqual(result.diagnostics[0].code, "truncated")
        for line in result.content.splitlines():
            if "excerpt:" in line:
                self.assertLessEqual(len(line), len("excerpt: ") + 40)

    def test_an_empty_search_is_refused_by_the_schema(self) -> None:
        result = self.dispatch("search", '{"query": ""}')

        self.assertTrue(result.meta["error"])
        self.assertIn("characters", result.content)

    def test_a_whitespace_search_is_refused_by_the_schema(self) -> None:
        result = self.dispatch("search", '{"query": "   "}')

        self.assertTrue(result.meta["error"])
        self.assertIn("match", result.content)

    def test_wiki_read_requires_a_slug(self) -> None:
        result = self.dispatch("wiki_read", "{}")

        self.assertTrue(result.meta["error"])
        self.assertIn("required", result.content)


if __name__ == "__main__":
    unittest.main()
