from __future__ import annotations

import unittest

from noteagent.ingest.frontmatter import (
    ensure_wiki_frontmatter,
    parse_page,
    reemit_page,
    render_page,
    replace_page_body,
    set_frontmatter_scalar,
    strip_leftover_file_wrapper,
)
from noteagent.ingest.page_merge import merged_frontmatter


class FrontmatterYamlTests(unittest.TestCase):
    def test_parses_block_lists(self) -> None:
        page = parse_page(
            "---\n"
            "type: concept\n"
            "title: Span\n"
            "tags:\n"
            "  - linear-algebra\n"
            "  - subspace\n"
            "---\n\n# Span\n"
        )
        self.assertTrue(page.has_frontmatter)
        self.assertEqual(page.fields["type"], "concept")
        self.assertEqual(page.fields["tags"], "[linear-algebra, subspace]")
        rendered = render_page(page.fields, page.body)
        self.assertIn("tags: [linear-algebra, subspace]", rendered)

    def test_repairs_opening_fence_without_key_whitelist(self) -> None:
        raw = (
            "status: draft\n"
            "title: Custom\n"
            "---\n\n# Custom\n"
        )
        result = ensure_wiki_frontmatter(raw)
        self.assertEqual(result.code, "repaired")
        self.assertTrue(result.content.startswith("---\n"))
        self.assertEqual(parse_page(result.content).fields["status"], "draft")

    def test_valid_page_has_no_code(self) -> None:
        result = ensure_wiki_frontmatter(
            "---\ntype: concept\ntitle: X\n---\n\n# X\n"
        )
        self.assertIsNone(result.code)
        self.assertTrue(result.content.startswith("---\n"))

    def test_parse_skips_leftover_file_wrapper(self) -> None:
        raw = (
            "---\n"
            "---FILE: wiki/concepts/entropy.md\n"
            "---\n"
            "type: concept\n"
            "title: 信息熵\n"
            'sources: ["raw/web/ex.html#b4"]\n'
            "---\n\n"
            "# 信息熵\n"
        )
        page = parse_page(raw)
        self.assertTrue(page.has_frontmatter)
        self.assertEqual(page.fields["type"], "concept")
        self.assertEqual(page.fields["title"], "信息熵")
        self.assertIn("raw/web/ex.html#b4", page.fields["sources"])
        self.assertNotIn("---FILE", page.fields)
        self.assertTrue(page.body.lstrip().startswith("# 信息熵"))

    def test_strip_file_wrapper_is_idempotent_on_clean_pages(self) -> None:
        clean = "---\ntype: concept\ntitle: X\n---\n\n# X\n"
        self.assertEqual(strip_leftover_file_wrapper(clean), clean)

    def test_ensure_does_not_wrap_file_opener_as_yaml(self) -> None:
        raw = (
            "---FILE: wiki/concepts/entropy.md\n"
            "---\n"
            "type: concept\n"
            "title: X\n"
            "---\n\n# X\n"
        )
        result = ensure_wiki_frontmatter(raw)
        self.assertEqual(result.code, "repaired")
        self.assertTrue(result.content.startswith("---\n"))
        self.assertNotIn("---FILE:", result.content)
        page = parse_page(result.content)
        self.assertEqual(page.fields["type"], "concept")
        self.assertEqual(page.fields["title"], "X")
        self.assertTrue(page.body.lstrip().startswith("# X"))


class FrontmatterBytesTests(unittest.TestCase):
    """Frontmatter is re-emitted as written, not re-serialised from the parse."""

    PAGE = (
        "---\n"
        "type: concept\n"
        'title: "特殊矩阵的特征值: 上"\n'
        "created: 2026-01-01\n"
        "tags:\n"
        "  - linear-algebra\n"
        "  - subspace\n"
        "sources: [raw/one.md]\n"
        "---\n"
        "\n"
        "# 特征值\n"
    )

    def test_reemit_returns_the_page_unchanged(self) -> None:
        self.assertEqual(reemit_page(parse_page(self.PAGE)), self.PAGE)

    def test_replace_body_keeps_frontmatter_bytes(self) -> None:
        rewritten = replace_page_body(self.PAGE, "# 换了一节\n")
        self.assertTrue(rewritten.startswith("---\n"))
        self.assertIn('title: "特殊矩阵的特征值: 上"\n', rewritten)
        self.assertIn("tags:\n  - linear-algebra\n  - subspace\n", rewritten)
        self.assertTrue(rewritten.endswith("\n\n# 换了一节\n"))
        self.assertNotIn("# 特征值", rewritten)

    def test_unchanged_field_is_a_no_op(self) -> None:
        same = set_frontmatter_scalar(self.PAGE, "title", "特殊矩阵的特征值: 上")
        self.assertIs(same, self.PAGE)

    def test_edit_touches_one_line_and_keeps_quoting(self) -> None:
        edited = set_frontmatter_scalar(self.PAGE, "updated", "2026-10-03")
        self.assertIn("updated: 2026-10-03\n", edited)
        self.assertIn('title: "特殊矩阵的特征值: 上"\n', edited)
        self.assertIn("created: 2026-01-01\n", edited)
        self.assertIn("tags:\n  - linear-algebra\n  - subspace\n", edited)
        # The new line lands after the key it follows, not at the end of the block.
        self.assertLess(edited.index("created:"), edited.index("updated:"))

    def test_block_list_is_replaced_whole(self) -> None:
        edited = set_frontmatter_scalar(self.PAGE, "tags", "[a, b]")
        self.assertIn("tags: [a, b]\n", edited)
        self.assertNotIn("- linear-algebra", edited)
        self.assertNotIn("- subspace", edited)
        self.assertEqual(parse_page(edited).fields["tags"], "[a, b]")

    def test_missing_field_is_inserted_before_the_closing_fence(self) -> None:
        edited = set_frontmatter_scalar(self.PAGE, "related", "[x]")
        self.assertEqual(parse_page(edited).fields["related"], "[x]")
        self.assertTrue(edited.startswith("---\n"))
        self.assertEqual(parse_page(edited).body, "# 特征值\n")
        # The separator between fence and body is bytes of the prefix, so the
        # page still re-emits unchanged apart from the new line.
        self.assertIn("related: [x]\n---\n\n# 特征值\n", edited)

    def test_a_scalar_that_needs_quotes_still_parses_back(self) -> None:
        edited = set_frontmatter_scalar(self.PAGE, "title", "熵: 一个度量")
        self.assertEqual(parse_page(edited).fields["title"], "熵: 一个度量")
        self.assertIn('title: "熵: 一个度量"\n', edited)

    def test_block_list_page_survives_a_self_union(self) -> None:
        self.assertEqual(merged_frontmatter(self.PAGE, self.PAGE), self.PAGE)


if __name__ == "__main__":
    unittest.main()


