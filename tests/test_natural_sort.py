from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.io.read_source import (
    list_markdown_sources,
    list_project_sources,
    natural_sort_key,
)
from noteagent.project.init import init_project


class NaturalSortTests(unittest.TestCase):
    def test_numeric_order_beats_lexicographic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("1.md", "2.md", "10.md", "11.md"):
                (root / name).write_text("# x\n", encoding="utf-8")
            names = [p.name for p in list_markdown_sources(root, recursive=False)]
            self.assertEqual(names, ["1.md", "2.md", "10.md", "11.md"])

    def test_key_orders_lesson_style_names(self) -> None:
        paths = [
            Path("raw/10. det.md"),
            Path("raw/2. mul.md"),
            Path("raw/1. intro.md"),
        ]
        ordered = sorted(paths, key=natural_sort_key)
        self.assertEqual(
            [p.name for p in ordered],
            ["1. intro.md", "2. mul.md", "10. det.md"],
        )

    def test_project_sources_use_natural_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            for name in ("10. a.md", "2. b.md", "1. c.md"):
                path = layout.raw / name
                path.write_text("# x\n", encoding="utf-8")
            names = [p.name for p in list_project_sources(layout)]
            self.assertEqual(names, ["1. c.md", "2. b.md", "10. a.md"])


if __name__ == "__main__":
    unittest.main()
