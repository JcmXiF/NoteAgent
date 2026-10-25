from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.auto_ingest import resolve_ingest_sources
from noteagent.project.init import init_project


class ResolveIngestSourcesTests(unittest.TestCase):
    def _seed(self, root: Path, relative: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.name}\n", encoding="utf-8")
        return path

    def test_expands_directory_and_dedupes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            a = self._seed(root, "raw/demo/a.md")
            b = self._seed(root, "raw/demo/b.md")
            resolved = resolve_ingest_sources(
                layout,
                [root / "raw" / "demo", a],
            )
            paths = {p.resolve() for p in resolved}
            self.assertEqual(paths, {a.resolve(), b.resolve()})
            self.assertEqual(len(resolved), 2)

    def test_accepts_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            a = self._seed(root, "raw/a.md")
            b = self._seed(root, "raw/sub/b.md")
            resolved = resolve_ingest_sources(layout, [a, b])
            self.assertEqual([p.resolve() for p in resolved], [a.resolve(), b.resolve()])

    def test_missing_path_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            with self.assertRaises(FileNotFoundError):
                resolve_ingest_sources(layout, [root / "raw" / "nope.md"])


if __name__ == "__main__":
    unittest.main()
