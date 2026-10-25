from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from noteagent.api.app import create_app

INDEX_HTML = "<!doctype html><html><body><div id='root'></div></body></html>\n"


def _build(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8", newline="\n")
    assets = root / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text(
        "console.log('app')\n", encoding="utf-8", newline="\n"
    )
    return root


@contextmanager
def served(dist: Path | None):
    """An app that would find *dist* as the built interface, or none at all."""
    with patch("noteagent.api.app.resolve_web_dist", return_value=dist):
        yield TestClient(create_app())


class ServingTheBuiltInterfaceTests(unittest.TestCase):
    def test_the_root_answers_with_the_built_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with served(_build(Path(tmp) / "dist")) as client:
                response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])
            self.assertEqual(response.text, INDEX_HTML)

    def test_hashed_assets_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with served(_build(Path(tmp) / "dist")) as client:
                response = client.get("/assets/index-abc123.js")

            self.assertEqual(response.status_code, 200)
            self.assertIn("console.log", response.text)

    def test_api_routes_still_answer(self) -> None:
        # The mount at "/" takes whatever the routes did not, so this pins that
        # registering it did not swallow the API.
        with tempfile.TemporaryDirectory() as tmp:
            with served(_build(Path(tmp) / "dist")) as client:
                health = client.get("/api/health")

            self.assertEqual(health.json(), {"status": "ok"})

    def test_an_unknown_api_path_is_not_answered_with_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with served(_build(Path(tmp) / "dist")) as client:
                response = client.get("/api/nope")

            self.assertEqual(response.status_code, 404)
            self.assertNotIn("<div id='root'>", response.text)

    def test_an_unknown_page_is_not_answered_with_the_index(self) -> None:
        # There is no client-side routing, so a missing file must stay missing
        # rather than turn into the application.
        with tempfile.TemporaryDirectory() as tmp:
            with served(_build(Path(tmp) / "dist")) as client:
                response = client.get("/no-such-page")

            self.assertEqual(response.status_code, 404)
            self.assertNotIn("<div id='root'>", response.text)


class NoBuildTests(unittest.TestCase):
    def test_the_root_says_what_to_do_instead_of_only_failing(self) -> None:
        with served(None) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertIn("npm run build", response.text)

    def test_the_api_still_answers_without_a_build(self) -> None:
        # A build is about the interface, not about the service being useful: the
        # shell looks for /api/health before it loads any page.
        with served(None) as client:
            response = client.get("/api/health")

        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
