from __future__ import annotations

import unittest

from noteagent.acquire.derive import extract_derived_blocks, format_derived_text


SAMPLE = """
<!DOCTYPE html>
<html>
<body>
<article>
<p id="na-b1" data-na-block="b1">熵是接收的每条消息中包含的信息的平均量。</p>
<p id="na-b2" data-na-block="b2">公式
<img src="data:image/png;base64,abc" alt="H(X)"> 如下。</p>
<figure id="na-b3" data-na-block="b3">
  <img data-na-asset="missing" alt="">
  <figcaption>示意图</figcaption>
</figure>
<blockquote id="na-b4" data-na-block="b4">
  <p id="na-b5" data-na-block="b5">内层引用段落。</p>
</blockquote>
<pre id="na-b6" data-na-block="b6">def kl(p, q):
    return sum(p * log(p / q))
</pre>
</article>
</body>
</html>
"""


class DeriveBlocksTests(unittest.TestCase):
    def test_leaves_only_and_image_markers(self) -> None:
        blocks = extract_derived_blocks(SAMPLE)
        ids = [block.id for block in blocks]
        self.assertEqual(ids, ["b1", "b2", "b3", "b5", "b6"])
        by_id = {block.id: block for block in blocks}
        self.assertIn("平均量", by_id["b1"].text)
        self.assertIn("[图已本地化 H(X)]", by_id["b2"].text)
        self.assertEqual(by_id["b3"].text, "[图缺失 示意图]")
        self.assertEqual(by_id["b5"].text, "内层引用段落。")
        self.assertIn("def kl(p, q):", by_id["b6"].text)
        self.assertIn("return sum", by_id["b6"].text)

    def test_format_selected(self) -> None:
        blocks = extract_derived_blocks(SAMPLE)
        text = format_derived_text(blocks, ["b5", "b1"])
        self.assertTrue(text.startswith("[b1] "))
        self.assertIn("[b5] 内层引用段落。", text)
        self.assertNotIn("[b2]", text)

    def test_empty_html(self) -> None:
        self.assertEqual(extract_derived_blocks(""), [])
        self.assertEqual(format_derived_text([]), "")


if __name__ == "__main__":
    unittest.main()
