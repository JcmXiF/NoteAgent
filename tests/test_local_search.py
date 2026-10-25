from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.notes.store import create_note
from noteagent.project.init import init_project
from noteagent.wiki.local_search import search_project


def _write(layout, relative: str, text: str) -> None:
    path = layout.root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ProjectSearchTests(unittest.TestCase):
    def test_a_page_a_remark_and_a_source_are_all_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write(
                layout,
                "wiki/concepts/kl.md",
                "---\ntype: concept\ntitle: KL divergence\n---\n\nKL measures a distance.\n",
            )
            _write(layout, "wiki/concepts/other.md", "---\ntitle: Other\n---\n\nnothing\n")
            create_note(layout, page="wiki/concepts/kl.md", body="KL is not symmetric")
            _write(layout, "raw/web/article.md", "# Notes on KL\n\nsome body\n")

            found = search_project(layout, "KL")

            self.assertEqual([hit["path"] for hit in found["pages"]], ["wiki/concepts/kl.md"])
            self.assertEqual(found["notes"][0]["id"] != "", True)
            self.assertEqual(found["sources"][0]["path"], "raw/web/article.md")
            self.assertEqual(found["counts"], {"pages": 1, "notes": 1, "sources": 1})

    def test_a_title_match_outranks_a_body_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write(layout, "wiki/alpha.md", "---\ntitle: Alpha\n---\n\nnothing to see\n")
            _write(layout, "wiki/beta.md", "---\ntitle: Beta\n---\n\nnothing\n")
            _write(layout, "wiki/gamma.md", "---\ntitle: Gamma\n---\n\nalpha appears in the body\n")

            found = search_project(layout, "alpha")

            self.assertEqual(
                [hit["path"] for hit in found["pages"]],
                ["wiki/alpha.md", "wiki/gamma.md"],
            )
            self.assertEqual(found["pages"][0]["snippet"], "")
            self.assertIn("alpha", found["pages"][1]["snippet"])

    def test_nothing_is_a_search_for_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            found = search_project(layout, "   ")
            self.assertEqual(found["pages"], [])
            self.assertEqual(found["counts"], {"pages": 0, "notes": 0, "sources": 0})

    def test_a_binary_source_matches_by_its_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.raw / "pdf" / "attention.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-1.4 not really")
            _write(layout, "wiki/x.md", "---\ntitle: X\n---\n\nnothing\n")

            found = search_project(layout, "attention")

            self.assertEqual([hit["path"] for hit in found["sources"]], ["raw/pdf/attention.pdf"])
            self.assertEqual(found["sources"][0]["snippet"], "")

    def test_the_limit_says_how_much_was_left_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            for index in range(4):
                _write(
                    layout,
                    f"wiki/page-{index}.md",
                    f"---\ntitle: page {index}\n---\n\nshared word\n",
                )

            found = search_project(layout, "shared", per_kind=2)

            self.assertEqual(len(found["pages"]), 2)
            self.assertEqual(found["counts"]["pages"], 4)


if __name__ == "__main__":
    unittest.main()
