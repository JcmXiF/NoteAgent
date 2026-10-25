from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from noteagent.api.web_static import (
    BuiltInterface,
    resolve_web_dist,
    web_dist_candidates,
)

INDEX = "<!doctype html><html><body></body></html>\n"


def _build(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8", newline="\n")
    return root


class CandidateTests(unittest.TestCase):
    def test_both_places_are_named_checkout_first(self) -> None:
        package = Path("/package/noteagent")
        repo = Path("/checkout")

        checkout, inside = web_dist_candidates(package_dir=package, repo_root=repo)

        self.assertEqual(checkout, repo / "web" / "dist")
        self.assertEqual(inside, package / "web")

    def test_the_directory_holding_this_module_is_the_package(self) -> None:
        # The copy inside the package is what a wheel ships, so it has to sit
        # beside the package's other data, not beside the source tree.
        package = Path(__file__).resolve().parents[1] / "src" / "noteagent"

        _checkout, inside = web_dist_candidates(package_dir=package)

        self.assertEqual(inside, package / "web")


class CachingTests(unittest.IsolatedAsyncioTestCase):
    """What may be cached, stated per file name rather than per server config."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="noteagent-web-"))
        (self.dir / "assets").mkdir()
        (self.dir / "assets" / "app-1.js").write_text("x", encoding="utf-8", newline="\n")
        (self.dir / "index.html").write_text(INDEX, encoding="utf-8", newline="\n")
        (self.dir / "favicon.svg").write_text("<svg/>", encoding="utf-8", newline="\n")
        self.app = BuiltInterface(directory=self.dir, html=True)
        self.scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "scheme": "http",
        }

    def _headers(self, path: str) -> dict[str, str]:
        response = asyncio.run(self.app.get_response(path, self.scope))
        return {key.lower(): value for key, value in response.headers.items()}

    def test_the_entry_page_is_revalidated(self) -> None:
        self.assertEqual(self._headers("index.html")["cache-control"], "no-cache")
        self.assertEqual(self._headers(".")["cache-control"], "no-cache")

    def test_a_hashed_asset_is_kept_for_good(self) -> None:
        for path in ("assets/app-1.js", "assets\\app-1.js"):
            with self.subTest(path=path):
                # `get_path` uses the platform separator; the rule must not.
                self.assertEqual(
                    self._headers(path)["cache-control"],
                    "public, max-age=31536000, immutable",
                )

    def test_anything_else_gets_no_promise(self) -> None:
        self.assertNotIn("cache-control", self._headers("favicon.svg"))


class ResolveTests(unittest.TestCase):
    def test_the_checkout_wins_over_the_copy_inside_the_package(self) -> None:
        # Both exist during development, and only the checkout is rebuilt, so
        # preferring the copy would serve the previous interface silently.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build(root / "checkout" / "web" / "dist")
            _build(root / "package" / "web")

            found = resolve_web_dist(
                package_dir=root / "package", repo_root=root / "checkout"
            )

            self.assertEqual(found, root / "checkout" / "web" / "dist")

    def test_the_copy_inside_the_package_is_used_when_there_is_no_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build(root / "package" / "web")

            found = resolve_web_dist(
                package_dir=root / "package", repo_root=root / "checkout"
            )

            self.assertEqual(found, root / "package" / "web")

    def test_a_directory_without_an_index_is_not_a_build(self) -> None:
        # An empty or half-copied directory must not be served as if it were the
        # interface: the root would then answer 404 for every asset.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checkout" / "web" / "dist").mkdir(parents=True)
            _build(root / "package" / "web")

            found = resolve_web_dist(
                package_dir=root / "package", repo_root=root / "checkout"
            )

            self.assertEqual(found, root / "package" / "web")

    def test_two_directories_without_an_index_are_still_no_build(self) -> None:
        # Half-copied trees on both sides, and neither is the interface.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checkout" / "web" / "dist").mkdir(parents=True)
            (root / "package" / "web").mkdir(parents=True)
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                found = resolve_web_dist(
                    package_dir=root / "package", repo_root=root / "checkout"
                )

            self.assertIsNone(found)

    def test_no_build_anywhere_is_reported_instead_of_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                found = resolve_web_dist(
                    package_dir=root / "package", repo_root=root / "checkout"
                )

            self.assertIsNone(found)
            message = stderr.getvalue()
            self.assertIn("no interface build", message)
            self.assertIn("npm run build", message)
            # Both places are named, so the reason for the miss is visible.
            self.assertIn(str(root / "checkout" / "web" / "dist"), message)
            self.assertIn(str(root / "package" / "web"), message)


if __name__ == "__main__":
    unittest.main()
