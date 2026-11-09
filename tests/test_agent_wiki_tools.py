"""The wiki as the agent may touch it: pages by slug, and search over them.

Both tools are read-only and both stop at the wiki's edge: ``raw/`` is the
material, ``notes/`` is the reader's own layer, and neither is a wiki page.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.agent.tools import ToolContext
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.agent_tools import WikiToolLimits, wiki_tools


def _write(layout: ProjectLayout, relative: str, body: str) -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _page(title: str, body: str) -> str:
    return f'---\ntitle: "{title}"\n---\n\n{body}\n'


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
