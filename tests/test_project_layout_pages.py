from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.project.doctor import doctor_project
from noteagent.project.init import init_project


def _write(layout, relative: str, *, title: str, body: str = "") -> Path:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class ProjectLayoutPagesTests(unittest.TestCase):
    def test_init_creates_no_type_folders_or_source_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertTrue(layout.wiki.is_dir())
            for folder in (
                "concepts",
                "entities",
                "queries",
                "comparisons",
                "synthesis",
                "sources",
            ):
                self.assertFalse((layout.wiki / folder).exists(), folder)
            index = layout.wiki_index.read_text(encoding="utf-8")
            self.assertIn("## Sources", index)
            self.assertIn("## Notes", index)
            self.assertNotIn("## Concepts", index)

    def test_doctor_flags_a_legacy_folder_without_asking_for_a_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write(
                layout,
                "wiki/concepts/entropy.md",
                title="Entropy",
                body="See [[concepts/kl]].",
            )
            report = doctor_project(layout)
            codes = [item.code for item in report.findings]
            self.assertNotIn("fm.no_type", codes)
            legacy = [item for item in report.findings if item.code == "layout.legacyFolder"]
            self.assertEqual(len(legacy), 1)
            self.assertEqual(legacy[0].severity, "info")
            self.assertEqual(dict(legacy[0].params)["folder"], "concepts")

    def test_a_flat_note_is_not_flagged_as_a_legacy_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write(layout, "wiki/entropy.md", title="Entropy")
            _write(layout, "wiki/sources/paper.md", title="Paper")
            codes = {item.code for item in doctor_project(layout).findings}
            self.assertNotIn("layout.legacyFolder", codes)
            self.assertNotIn("fm.no_type", codes)


if __name__ == "__main__":
    unittest.main()
