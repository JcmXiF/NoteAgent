from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.chat.limits import default_limits
from noteagent.chat.tools import observe_tool
from noteagent.project.init import init_project

LONG = "Body text here is long enough to pass the short-body lint threshold."


def _write_concept(layout, name: str, body: str, **fm: str) -> Path:
    path = layout.wiki / "concepts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    title = fm.get("title", name)
    tags = fm.get("tags")
    tag_line = f"tags: {tags}\n" if tags is not None else ""
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n{tag_line}sources: []\n---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _wiki_search(layout, query: str):
    return observe_tool(
        "search",
        layout,
        {"query": query, "scope": "wiki"},
        default_limits(),
    )


class ChatWikiSearchTests(unittest.TestCase):
    def test_title_hit_ranks_above_body_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(layout, "named", f"{LONG}\nUnrelated filler.", title="KL散度")
            _write_concept(
                layout,
                "other",
                f"{LONG}\nKL散度 is defined as a relative entropy.",
                title="相对熵",
            )
            hits = _wiki_search(layout, "KL散度")
            slugs = [item.meta.get("slug") for item in hits if item.meta.get("slug")]
            self.assertEqual(slugs[:2], ["concepts/named", "concepts/other"])
            self.assertEqual(hits[0].meta.get("match"), "title")
            self.assertEqual(hits[1].meta.get("match"), "body")
            self.assertIn("excerpt:", hits[1].content)
            self.assertIn("KL散度", hits[1].content)

    def test_body_only_hit_includes_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "backup",
                f"{LONG}\nThe Bellman backup copies the max over actions.",
                title="动态规划",
            )
            hits = _wiki_search(layout, "Bellman backup")
            self.assertEqual(hits[0].meta.get("match"), "body")
            self.assertEqual(hits[0].meta.get("slug"), "concepts/backup")
            self.assertIn("Bellman backup", hits[0].content)

    def test_does_not_search_raw_or_frontmatter_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "plain",
                LONG,
                title="Alpha",
                tags="[UNIQUE_FM_TOKEN]",
            )
            raw = layout.raw / "web" / "secret.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("UNIQUE_RAW_TOKEN appears only in raw.", encoding="utf-8")
            missing_raw = _wiki_search(layout, "UNIQUE_RAW_TOKEN")
            missing_fm = _wiki_search(layout, "UNIQUE_FM_TOKEN")
            self.assertTrue(missing_raw[0].meta.get("missing"))
            self.assertTrue(missing_fm[0].meta.get("missing"))
            self.assertIn("slug, title, or body", missing_raw[0].content)
