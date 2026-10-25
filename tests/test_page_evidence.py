from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.page_evidence import (
    EVIDENCE_HEADING,
    MAX_PASSAGE_CHARS,
    evidence_for_page,
    page_passages,
)
from noteagent.project.layout import default_layout

IDENTITY = "raw/web/entropy.html"
STAMPED = """<!DOCTYPE html>
<html>
<body>
<article>
<p data-na-block="b1">熵是接收的每条消息中包含的信息的平均量。</p>
<p data-na-block="b2">联合熵满足 H(X,Y) 不超过 H(X)+H(Y)。</p>
<p data-na-block="b3">这一块没有被引用。</p>
</article>
</body>
</html>
"""

PAGE = """---
title: 熵
created: 2026-10-01
updated: 2026-10-01
sources: ["raw/web/entropy.html#b1", "raw/web/entropy.html#b2"]
---

熵是不确定性的度量。
"""


class EvidenceForPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = default_layout(Path(self._tmp.name))
        source = self.layout.root / IDENTITY
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(STAMPED, encoding="utf-8")

    def test_resolves_each_cited_block(self) -> None:
        text = evidence_for_page(self.layout, PAGE)
        self.assertTrue(text.startswith(EVIDENCE_HEADING))
        self.assertIn(f"`{IDENTITY}#b1`", text)
        self.assertIn("平均量", text)
        self.assertIn(f"`{IDENTITY}#b2`", text)
        self.assertIn("H(X)+H(Y)", text)

    def test_does_not_show_an_uncited_block(self) -> None:
        text = evidence_for_page(self.layout, PAGE)
        self.assertNotIn("没有被引用", text)
        self.assertNotIn("#b3", text)

    def test_a_page_without_sources_gets_nothing(self) -> None:
        page = "---\ntitle: 熵\n---\n\n正文。\n"
        self.assertEqual(evidence_for_page(self.layout, page), "")

    def test_a_locator_without_a_block_is_not_whole_file_evidence(self) -> None:
        page = '---\ntitle: 熵\nsources: ["raw/web/entropy.html"]\n---\n\n正文。\n'
        self.assertEqual(evidence_for_page(self.layout, page), "")

    def test_a_source_that_is_gone_is_skipped(self) -> None:
        page = '---\ntitle: 熵\nsources: ["raw/web/missing.html#b1"]\n---\n\n正文。\n'
        self.assertEqual(evidence_for_page(self.layout, page), "")

    def test_duplicates_are_read_once(self) -> None:
        page = (
            "---\ntitle: 熵\n"
            'sources: ["raw/web/entropy.html#b1", "raw/web/entropy.html#b1"]\n'
            "---\n\n正文。\n"
        )
        self.assertEqual(evidence_for_page(self.layout, page).count("#b1"), 1)

    def test_a_long_passage_is_cut_and_marked(self) -> None:
        source = self.layout.root / IDENTITY
        source.write_text(
            '<p data-na-block="b1">' + ("熵" * (MAX_PASSAGE_CHARS + 400)) + "</p>",
            encoding="utf-8",
        )
        page = '---\ntitle: 熵\nsources: ["raw/web/entropy.html#b1"]\n---\n\n正文。\n'
        text = evidence_for_page(self.layout, page)
        self.assertIn("…", text)
        self.assertLess(len(text), MAX_PASSAGE_CHARS + 400)

    def test_the_budget_stops_before_a_passage_that_would_overflow(self) -> None:
        page = "---\ntitle: 熵\nsources: [\"raw/web/entropy.html#b1\", \"raw/web/entropy.html#b2\"]\n---\n\n正文。\n"
        text = evidence_for_page(self.layout, page, max_chars=60)
        self.assertIn("#b1", text)
        self.assertNotIn("#b2", text)

    def test_the_resolver_is_the_one_the_merger_takes(self) -> None:
        passages = page_passages(self.layout)
        self.assertEqual(passages(PAGE), evidence_for_page(self.layout, PAGE))


if __name__ == "__main__":
    unittest.main()
