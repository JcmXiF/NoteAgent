from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.project.init import init_project
from noteagent.wiki.graph import is_graph_content_page, wiki_graph_payload

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


def _write_page(root: Path, rel: str, body: str, *, fields: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fields}\n---\n\n{body}\n", encoding="utf-8")


class WikiGraphTests(unittest.TestCase):
    def test_body_links_count_and_ghosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            _write_page(
                layout.root,
                "wiki/concepts/entropy.md",
                "See [[concepts/kl]] and again [[concepts/kl|KL]].\n"
                "Missing [[concepts/ghost-page]]. Self [[concepts/entropy]].",
                fields="type: concept\ntitle: Entropy",
            )
            _write_page(
                layout.root,
                "wiki/concepts/kl.md",
                "Back to [[entropy]].",
                fields="type: concept\ntitle: KL",
            )
            _write_page(
                layout.root,
                "wiki/index.md",
                "Star [[concepts/entropy]] [[concepts/kl]]",
                fields="type: index\ntitle: Index",
            )
            _write_page(
                layout.root,
                "wiki/sources/page-clips.md",
                "Not a graph node [[concepts/entropy]]",
                fields="type: source\ntitle: Clips",
            )
            _write_page(
                layout.root,
                "wiki/concepts/related-only.md",
                "No body links here.",
                fields="type: concept\ntitle: Related Only\nrelated: [concepts/entropy]",
            )

            payload = wiki_graph_payload(layout)
            by_id = {item["id"]: item for item in payload["nodes"]}
            edges = {
                (item["source"], item["target"]): item["count"]
                for item in payload["edges"]
            }

            self.assertIn("concepts/entropy", by_id)
            self.assertIn("concepts/kl", by_id)
            self.assertIn("concepts/related-only", by_id)
            self.assertIn("concepts/ghost-page", by_id)
            self.assertNotIn("index", by_id)
            self.assertFalse(
                any(str(item["id"]).startswith("sources/") for item in payload["nodes"])
            )

            self.assertEqual(by_id["concepts/entropy"]["title"], "Entropy")
            self.assertEqual(by_id["concepts/entropy"]["type"], "note")
            self.assertFalse(by_id["concepts/entropy"]["ghost"])
            self.assertEqual(by_id["concepts/entropy"]["path"], "wiki/concepts/entropy.md")

            ghost = by_id["concepts/ghost-page"]
            self.assertTrue(ghost["ghost"])
            self.assertIsNone(ghost["path"])
            self.assertEqual(ghost["type"], "ghost")

            self.assertEqual(edges[("concepts/entropy", "concepts/kl")], 2)
            self.assertEqual(edges[("concepts/kl", "concepts/entropy")], 1)
            self.assertEqual(edges[("concepts/entropy", "concepts/ghost-page")], 1)
            self.assertNotIn(("concepts/related-only", "concepts/entropy"), edges)
            self.assertNotIn(("concepts/entropy", "concepts/entropy"), edges)

            self.assertEqual(by_id["concepts/related-only"]["degree"], 0)
            self.assertEqual(by_id["concepts/entropy"]["degree"], 2)
            self.assertEqual(by_id["concepts/kl"]["degree"], 1)

    def test_drops_source_and_nav_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "demo")
            _write_page(
                layout.root,
                "wiki/concepts/span.md",
                "See [[sources/page-clips]] and [[overview]] and [[wiki/index]].",
                fields="type: concept\ntitle: Span",
            )
            _write_page(
                layout.root,
                "wiki/sources/page-clips.md",
                "clips",
                fields="type: source\ntitle: Clips",
            )
            payload = wiki_graph_payload(layout)
            self.assertEqual(len(payload["nodes"]), 1)
            self.assertEqual(payload["nodes"][0]["id"], "concepts/span")
            self.assertEqual(payload["edges"], [])

    @unittest.skipIf(TestClient is None, "fastapi not installed")
    def test_graph_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            _write_page(
                layout.root,
                "wiki/concepts/span.md",
                "See [[concepts/missing]].",
                fields="type: concept\ntitle: Span",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                missing = client.get("/api/projects/nope/graph")
                self.assertEqual(missing.status_code, 404)
                found = client.get("/api/projects/demo/graph")
                self.assertEqual(found.status_code, 200, found.text)
                payload = found.json()
                self.assertEqual(payload["project"], "demo")
                ids = {item["id"] for item in payload["nodes"]}
                self.assertIn("concepts/span", ids)
                self.assertIn("concepts/missing", ids)

    def test_folder_filter(self) -> None:
        self.assertTrue(is_graph_content_page("wiki/a.md"))
        self.assertTrue(is_graph_content_page("wiki/concepts/a.md"))
        self.assertTrue(is_graph_content_page("wiki/custom/a.md"))
        self.assertFalse(is_graph_content_page("wiki/sources/a.md"))
        self.assertFalse(is_graph_content_page("wiki/index.md"))
        self.assertFalse(is_graph_content_page("raw/a.md"))


if __name__ == "__main__":
    unittest.main()
