from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.api.files import list_project_tree, read_project_text, resolve_project_file
from noteagent.api.projects import get_project_layout, list_projects, use_project
from noteagent.project.init import init_project


class ApiReadonlyTests(unittest.TestCase):
    def test_list_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            projects = repo / "projects"
            layout = init_project(projects / "demo")
            page = layout.wiki / "concepts" / "span.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Span\n---\n\n# Span\n",
                encoding="utf-8",
            )
            (layout.raw / "note.md").write_text("# raw\n", encoding="utf-8")
            (layout.raw / "page.html").write_text(
                '<!DOCTYPE html><html><body>'
                '<p id="na-b1" data-na-block="b1">hi there</p>'
                "</body></html>\n",
                encoding="utf-8",
            )
            (layout.raw / "paper.pdf").write_bytes(b"%PDF-1.4\n")
            (layout.raw / "note.docx").write_bytes(b"PK")
            (layout.raw / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            listed = list_projects(repo)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["name"], "demo")

            switched = use_project("demo", repo_root=repo)
            self.assertTrue(switched["active"])
            self.assertTrue((repo / ".noteagent-project").is_file())
            listed = list_projects(repo)
            self.assertTrue(listed[0]["active"])

            demo = get_project_layout("demo", repo_root=repo)
            tree = list_project_tree(demo)
            paths = {item["path"] for item in tree}
            self.assertIn("wiki/concepts/span.md", paths)
            self.assertIn("raw/note.md", paths)
            self.assertIn("raw/page.html", paths)
            self.assertIn("raw/paper.pdf", paths)
            self.assertIn("raw/note.docx", paths)
            self.assertIn("raw/fig.png", paths)

            payload = read_project_text(demo, "wiki/concepts/span.md")
            self.assertIn("# Span", payload["content"])
            # A wiki page keeps its own reader: only a stored original is shown as
            # blocks, so nothing about a page changes here.
            self.assertNotIn("blocks", payload)
            self.assertNotIn("html", payload)
            raw_markdown = read_project_text(demo, "raw/note.md")
            self.assertEqual(raw_markdown["kind"], "markdown")
            self.assertEqual(raw_markdown["renderer"], "markdown-blocks")
            self.assertEqual(raw_markdown["blocks"][0]["id"], "m1")
            self.assertEqual(raw_markdown["blocks"][0]["tag"], "h1")
            self.assertEqual(raw_markdown["blocks"][0]["text"], "raw")
            self.assertIn('data-na-block="m1"', raw_markdown["html"])
            html_payload = read_project_text(demo, "raw/page.html")
            self.assertEqual(html_payload["blocks"][0]["id"], "b1")
            self.assertEqual(html_payload["blocks"][0]["text"], "hi there")

    def test_path_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(ValueError):
                resolve_project_file(layout, "../secret.md")
            with self.assertRaises(ValueError):
                resolve_project_file(layout, "config/brief.md")


if __name__ == "__main__":
    unittest.main()
