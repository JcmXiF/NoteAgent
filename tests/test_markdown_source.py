from __future__ import annotations

import unittest

from noteagent.acquire.adapters.markdown_source import (
    markdown_blocks,
    markdown_document,
)

SAMPLE = """---
title: 熵
source: https://example.com/entropy
---

# 熵

熵是接收的每条消息中包含的信息的平均量，又被称为信息熵。

![示意](entropy.png)

## 公式

```python
def kl(p, q):
    return sum(p * log(p / q))
```

> 引用的一段话
> 第二行

- 第一条
- 第二条
  跨行说明

| 名称 | 含义 |
| --- | --- |
| H | 熵 |
"""


class MarkdownBlocksTests(unittest.TestCase):
    def test_frontmatter_is_not_a_block(self) -> None:
        blocks = markdown_blocks(SAMPLE)
        text = "\n".join(block.text for block in blocks)
        self.assertNotIn("source: https://example.com", text)

    def test_ids_are_in_document_order(self) -> None:
        blocks = markdown_blocks(SAMPLE)
        self.assertEqual([block.id for block in blocks], [f"m{i}" for i in range(1, len(blocks) + 1)])

    def test_structure_becomes_tags(self) -> None:
        blocks = markdown_blocks(SAMPLE)
        tags = [block.tag for block in blocks]
        self.assertEqual(
            tags,
            [
                "h1",
                "p",
                "p",
                "h2",
                "pre",
                "blockquote",
                "li",
                "li",
                "table",
            ],
        )

    def test_markers_are_not_text(self) -> None:
        blocks = markdown_blocks(SAMPLE)
        by_id = {block.id: block for block in blocks}
        self.assertEqual(by_id["m1"].text, "熵")
        self.assertNotIn("#", by_id["m2"].text)
        self.assertIn("def kl(p, q):", by_id["m5"].text)
        self.assertEqual(by_id["m6"].text, "引用的一段话 第二行")
        self.assertEqual(by_id["m7"].text, "第一条")
        self.assertIn("跨行说明", by_id["m8"].text)
        self.assertEqual(by_id["m9"].text, "名称 | 含义\nH | 熵")

    def test_ids_are_stable_across_reads(self) -> None:
        first = [(block.id, block.tag, block.text) for block in markdown_blocks(SAMPLE)]
        second = [(block.id, block.tag, block.text) for block in markdown_blocks(SAMPLE)]
        self.assertEqual(first, second)

    def test_empty_input(self) -> None:
        self.assertEqual(markdown_blocks(""), [])
        self.assertEqual(markdown_blocks("---\ntitle: x\n---\n"), [])


class MarkdownDocumentTests(unittest.TestCase):
    def test_every_block_is_marked_and_escaped(self) -> None:
        html = markdown_document("<script>alert(1)</script>\n\n正文", title="t")
        self.assertIn('data-na-block="m1"', html)
        self.assertIn('data-na-block="m2"', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_headings_and_pre_keep_their_elements(self) -> None:
        html = markdown_document(SAMPLE, title="熵")
        self.assertIn('<h1 id="na-m1" data-na-block="m1">熵</h1>', html)
        self.assertIn('<pre id="na-m5" data-na-block="m5">', html)
        self.assertIn('<table id="na-m9" data-na-block="m9">', html)


if __name__ == "__main__":
    unittest.main()
