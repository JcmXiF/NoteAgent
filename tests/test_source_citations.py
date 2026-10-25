from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.clips import ClipCollection, ClipStore, save_clip_store
from noteagent.ingest.source_citations import (
    is_citing_wiki_page,
    list_source_citations,
    parse_clip_sections,
    parse_clip_summary_collections,
    scan_clip_marks,
    source_citations_payload,
    split_source_locator,
)
from noteagent.project.init import init_project


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SourceCitationsTests(unittest.TestCase):
    def test_split_locator(self) -> None:
        self.assertEqual(
            split_source_locator(r"raw\web\ex\kl.html#b2"),
            ("raw/web/ex/kl.html", "b2"),
        )
        self.assertEqual(
            split_source_locator("raw/web/ex/kl.html"),
            ("raw/web/ex/kl.html", None),
        )

    def test_skip_maintenance_pages(self) -> None:
        self.assertTrue(is_citing_wiki_page("wiki/concepts/kl.md"))
        self.assertFalse(is_citing_wiki_page("wiki/index.md"))
        self.assertFalse(is_citing_wiki_page("wiki/overview.md"))
        self.assertFalse(is_citing_wiki_page("wiki/log.md"))
        self.assertFalse(is_citing_wiki_page("wiki/sources/kl-clips.md"))
        self.assertFalse(is_citing_wiki_page("raw/page.html"))

    def test_parse_clip_sections(self) -> None:
        sections = parse_clip_sections(
            "# 忽略\n"
            "<!-- na-clip:c-a -->\n"
            "## 定义\n"
            "一段\n"
            "<!-- na-clip:c-b -->\n"
            "### 推导\n"
            "另一段\n"
        )
        self.assertEqual(
            [(item.heading, item.collection_id) for item in sections],
            [("定义", "c-a"), ("推导", "c-b")],
        )

    # A marker owns the heading directly below it. Blank lines may separate the
    # two; anything else may not. These cases are the contract both readers and
    # both languages answer to; see `noteagent/ingest/marker_lines.py`.

    def test_blank_lines_may_separate_marker_and_heading(self) -> None:
        sections = parse_clip_sections("<!-- na-clip:c-a -->\n\n\n## 定义\n正文\n")
        self.assertEqual(
            [(item.heading, item.collection_id) for item in sections],
            [("定义", "c-a")],
        )

    def test_prose_under_a_marker_is_not_read_on_to_a_later_heading(self) -> None:
        body = "## 词源\n<!-- na-clip:c-a -->\n一段正文\n\n## 为什么取对数\n正文\n"
        self.assertEqual(parse_clip_sections(body), [])

    def test_a_marker_must_hold_its_line(self) -> None:
        body = "正文 <!-- na-clip:c-a -->\n## 定义\n正文\n"
        self.assertEqual(parse_clip_sections(body), [])

    def test_the_page_title_is_not_a_section(self) -> None:
        body = "<!-- na-clip:c-a -->\n# 信息熵\n\n## 定义\n正文\n"
        self.assertEqual(parse_clip_sections(body), [])

    def test_stacked_markers_bind_only_the_one_nearest_the_heading(self) -> None:
        body = "<!-- na-clip:c-a -->\n<!-- na-clip:c-b -->\n## 定义\n正文\n"
        self.assertEqual(
            [(item.heading, item.collection_id) for item in parse_clip_sections(body)],
            [("定义", "c-b")],
        )

    def test_marker_syntax_is_read_case_insensitively(self) -> None:
        sections = parse_clip_sections("<!-- NA-CLIP:c-a B1, B2 -->\n## 定义\n")
        self.assertEqual(
            [(item.heading, item.collection_id, item.block_ids) for item in sections],
            [("定义", "c-a", ("b1", "b2"))],
        )

    def test_a_comment_between_marker_and_heading_ends_the_wait(self) -> None:
        body = "<!-- na-clip:c-a -->\n<!-- note -->\n## 定义\n正文\n"
        self.assertEqual(parse_clip_sections(body), [])
        marks = scan_clip_marks(body)
        self.assertEqual([(item.collection_id, item.heading) for item in marks], [("c-a", None)])

    def test_scan_reports_a_marker_that_owns_no_heading(self) -> None:
        marks = scan_clip_marks(
            "<!-- na-clip:c-a -->\n一段正文\n" "<!-- na-clip:c-b -->\n## 定义\n"
        )
        self.assertEqual(
            [(item.collection_id, item.heading) for item in marks],
            [("c-a", None), ("c-b", "定义")],
        )


    def test_index_unique_notes_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            html = "raw/web/example.com/kl.html"
            other = "raw/web/example.com/entropy.html"
            _write(
                layout.wiki / "concepts" / "kl.md",
                "---\n"
                "type: concept\n"
                "title: KL散度\n"
                f'sources: ["{html}#b1", "{html}#b2", "{html}#b1"]\n'
                "---\n\n正文\n",
            )
            _write(
                layout.wiki / "concepts" / "entropy.md",
                "---\n"
                "type: concept\n"
                "title: 信息熵\n"
                f'sources: ["{html}#b2", "{other}"]\n'
                "---\n\n正文\n",
            )
            _write(
                layout.wiki / "overview.md",
                "---\ntype: overview\ntitle: Overview\n"
                f'sources: ["{html}#b9"]\n---\n',
            )
            _write(
                layout.wiki / "sources" / "kl-clips.md",
                "---\ntype: source\ntitle: clips\n"
                f'sources: ["{html}#b1"]\n---\n',
            )

            cited = list_source_citations(layout)
            kl_notes = {note.path: note for note in cited.notes[html]}
            self.assertEqual(set(kl_notes), {
                "wiki/concepts/entropy.md",
                "wiki/concepts/kl.md",
            })
            self.assertEqual(kl_notes["wiki/concepts/kl.md"].title, "KL散度")
            kl_marks = [item for item in cited.marks[html] if item.path.endswith("kl.md")]
            self.assertEqual(len(kl_marks), 1)
            self.assertEqual(kl_marks[0].block_ids, ("b1", "b2"))
            entropy_html = [
                item for item in cited.marks[html] if item.path.endswith("entropy.md")
            ]
            self.assertEqual(entropy_html[0].block_ids, ("b2",))
            self.assertEqual(cited.marks[other][0].block_ids, ())
            self.assertEqual(cited.notes[other][0].path, "wiki/concepts/entropy.md")

            payload = source_citations_payload(layout)
            sources = payload["sources"]
            assert isinstance(sources, dict)
            bundle = sources[html]
            listed = {item["path"]: item for item in bundle["notes"]}
            self.assertEqual(len(listed), 2)
            kl_payload = [
                item for item in bundle["marks"] if item["path"] == "wiki/concepts/kl.md"
            ]
            self.assertEqual(kl_payload[0]["blockIds"], ["b1", "b2"])

    def test_two_sections_same_html_stay_separate_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            html = "raw/web/example.com/kl.html"
            save_clip_store(
                layout,
                ClipStore(
                    intent="demo",
                    collections=(
                        ClipCollection(
                            id="c-def",
                            source_key=html,
                            source_label="kl",
                            block_ids=("b1", "b2"),
                            blocks=(),
                        ),
                        ClipCollection(
                            id="c-app",
                            source_key=html,
                            source_label="kl",
                            block_ids=("b3", "b4"),
                            blocks=(),
                        ),
                    ),
                ),
            )
            _write(
                layout.wiki / "concepts" / "kl.md",
                "---\n"
                "type: concept\n"
                "title: KL散度\n"
                f'sources: ["{html}#b1", "{html}#b2", "{html}#b3", "{html}#b4"]\n'
                "---\n\n"
                "<!-- na-clip:c-def -->\n"
                "## 定义\n"
                "第一段\n\n"
                "<!-- na-clip:c-app -->\n"
                "## 应用\n"
                "第二段\n",
            )

            cited = list_source_citations(layout)
            self.assertEqual(len(cited.notes[html]), 1)
            self.assertEqual(cited.notes[html][0].path, "wiki/concepts/kl.md")
            marks = cited.marks[html]
            self.assertEqual(len(marks), 2)
            by_heading = {item.heading: item for item in marks}
            self.assertEqual(by_heading["定义"].collection_id, "c-def")
            self.assertEqual(by_heading["定义"].block_ids, ("b1", "b2"))
            self.assertEqual(by_heading["应用"].collection_id, "c-app")
            self.assertEqual(by_heading["应用"].block_ids, ("b3", "b4"))
            self.assertNotEqual(marks[0].id, marks[1].id)

    def test_collections_without_clip_comments_still_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            html = "raw/web/example.com/kl.html"
            save_clip_store(
                layout,
                ClipStore(
                    intent="demo",
                    collections=(
                        ClipCollection(
                            id="c-a",
                            source_key=html,
                            source_label="kl",
                            block_ids=("b1", "b2"),
                            blocks=(),
                        ),
                        ClipCollection(
                            id="c-b",
                            source_key=html,
                            source_label="kl",
                            block_ids=("b3",),
                            blocks=(),
                        ),
                    ),
                ),
            )
            _write(
                layout.wiki / "concepts" / "kl.md",
                "---\n"
                "type: concept\n"
                "title: KL散度\n"
                f'sources: ["{html}#b1", "{html}#b2", "{html}#b3"]\n'
                "---\n\n正文\n",
            )
            marks = list_source_citations(layout).marks[html]
            self.assertEqual(len(marks), 2)
            by_id = {item.collection_id: item for item in marks}
            self.assertEqual(by_id["c-a"].block_ids, ("b1", "b2"))
            self.assertEqual(by_id["c-b"].block_ids, ("b3",))

    def test_index_reads_sources_through_leftover_file_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            html = "raw/web/example.com/entropy.html"
            _write(
                layout.wiki / "concepts" / "entropy.md",
                "---\n"
                "---FILE: wiki/concepts/entropy.md\n"
                "---\n"
                "type: concept\n"
                "title: 信息熵\n"
                f'sources: ["{html}#b4"]\n'
                "---\n\n正文\n",
            )
            cited = list_source_citations(layout)
            self.assertEqual(list(cited.notes), [html])
            self.assertEqual(cited.notes[html][0].title, "信息熵")
            self.assertEqual(cited.marks[html][0].block_ids, ("b4",))


    def test_parse_clip_summary_skips_preview_table_rows(self) -> None:
        found = parse_clip_summary_collections(
            "| 既有版本 | `bd057185-425a-442a-8155-16be0d6048fa` | "
            "`preview:https://zh.wikipedia.org/zh-hans/x` | b2, b3 |\n"
            "| 新生成版本 | `f57a159c-060a-4672-90a0-45c5b5ffc27a` | "
            "`raw/web/joint.html` | b2, b3, b5 |\n"
            "- 定义 (`7da90415-be75-45d6-935c-f46c9b4961bd`): "
            "`raw/web/entropy.html` (b4, b5, b6)\n"
        )
        by_id = {item.id: item for item in found}
        self.assertEqual(set(by_id), {
            "f57a159c-060a-4672-90a0-45c5b5ffc27a",
            "7da90415-be75-45d6-935c-f46c9b4961bd",
        })
        self.assertEqual(by_id["f57a159c-060a-4672-90a0-45c5b5ffc27a"].block_ids, ("b2", "b3", "b5"))
        self.assertEqual(
            by_id["7da90415-be75-45d6-935c-f46c9b4961bd"].source_key,
            "raw/web/entropy.html",
        )

    def test_compiled_collections_survive_preview_clip_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            html = "raw/web/example.com/entropy.html"
            cid = "7da90415-be75-45d6-935c-f46c9b4961bd"
            _write(
                layout.wiki / "sources" / "信息熵-clips.md",
                "---\ntype: source\ntitle: clips\nsources: []\n---\n\n"
                "## Collections\n\n"
                f"- 定义 (`{cid}`): `{html}` (b4, b5, b6)\n",
            )
            _write(
                layout.wiki / "concepts" / "entropy.md",
                "---\n"
                "type: concept\n"
                "title: 信息熵\n"
                f'sources: ["{html}#b4", "{html}#b5", "{html}#b6"]\n'
                "---\n\n"
                f"<!-- na-clip:{cid} -->\n"
                "## 定义\n"
                "一段\n\n"
                f"<!-- na-clip:{cid} -->\n"
                "## 由来\n"
                "另一段\n",
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent="Jensen不等式wiki",
                    collections=(
                        ClipCollection(
                            id="preview-only",
                            source_key="preview:https://example.org/jensen",
                            source_label="jensen",
                            block_ids=("b2",),
                            blocks=(),
                        ),
                    ),
                ),
            )
            cited = list_source_citations(layout)
            marks = cited.marks[html]
            self.assertEqual(len(cited.notes[html]), 1)
            self.assertEqual(len(marks), 2)
            by_heading = {item.heading: item for item in marks}
            self.assertEqual(by_heading["定义"].collection_id, cid)
            self.assertEqual(by_heading["定义"].block_ids, ("b4", "b5", "b6"))
            self.assertEqual(by_heading["由来"].collection_id, cid)
            payload = source_citations_payload(layout)
            ids = {item["id"] for item in payload["collections"]}
            self.assertEqual(ids, {cid})
            self.assertNotIn("preview-only", ids)


if __name__ == "__main__":
    unittest.main()
