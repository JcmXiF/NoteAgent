from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.clip_ledger import raw_source_path
from noteagent.ingest.clips import durable_source_file, source_locator
from noteagent.project.doctor import doctor_project
from noteagent.project.identity import source_identity
from noteagent.project.init import init_project
from noteagent.project.raw_kinds import is_raw_path, raw_kind


class RawKindTests(unittest.TestCase):
    def test_a_new_source_names_its_kind(self) -> None:
        self.assertEqual(raw_kind("raw/web/page.html"), "web")
        self.assertEqual(raw_kind("raw/pdf/paper.pdf"), "pdf")
        self.assertEqual(raw_kind("raw/office/deck.pptx"), "office")
        self.assertEqual(raw_kind("raw/media/fig.png"), "media")

    def test_a_legacy_source_is_read_by_its_kind_folder(self) -> None:
        self.assertEqual(raw_kind("raw/sources/web/example.com/page.html"), "web")
        self.assertEqual(raw_kind("raw/sources/pdf/paper.pdf"), "pdf")

    def test_a_path_without_a_kind_says_so(self) -> None:
        self.assertEqual(raw_kind("raw/notes.md"), "")
        self.assertEqual(raw_kind("raw/demo/a.md"), "")
        self.assertEqual(raw_kind("wiki/page.md"), "")

    def test_only_paths_under_raw_are_raw(self) -> None:
        self.assertTrue(is_raw_path("raw/web/a.html"))
        self.assertTrue(is_raw_path("raw/a.md"))
        self.assertFalse(is_raw_path("wiki/raw.md"))
        self.assertFalse(is_raw_path("raw/"))
        self.assertFalse(is_raw_path("raw"))


class SourceIdentityTests(unittest.TestCase):
    def test_a_project_file_is_named_by_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.raw / "web" / "page.html"
            path.parent.mkdir(parents=True)
            path.write_text("<html></html>", encoding="utf-8")
            self.assertEqual(
                source_identity(path, project_root=layout.root), "raw/web/page.html"
            )

    def test_a_legacy_file_keeps_the_path_it_has(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.raw / "sources" / "web" / "page.html"
            path.parent.mkdir(parents=True)
            path.write_text("<html></html>", encoding="utf-8")
            self.assertEqual(
                source_identity(path, project_root=layout.root),
                "raw/sources/web/page.html",
            )

    def test_a_file_outside_keeps_its_part_from_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            outside = Path(tmp) / "staging" / "raw" / "web" / "page.html"
            outside.parent.mkdir(parents=True)
            outside.write_text("<html></html>", encoding="utf-8")
            self.assertEqual(
                source_identity(outside, project_root=layout.root), "raw/web/page.html"
            )

    def test_a_file_with_no_raw_part_is_known_by_its_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            loose = Path(tmp) / "staging" / "page.html"
            loose.parent.mkdir(parents=True)
            loose.write_text("<html></html>", encoding="utf-8")
            self.assertEqual(
                source_identity(loose, project_root=layout.root), "page.html"
            )


class LegacyLayoutTests(unittest.TestCase):
    """An unmigrated project keeps reading: nothing here refuses its paths."""

    def _legacy_file(self, layout) -> Path:
        path = layout.raw / "sources" / "web" / "example.com" / "kl.html"
        path.parent.mkdir(parents=True)
        path.write_text("<html></html>", encoding="utf-8")
        return path

    def test_its_identity_and_locators_are_the_paths_it_has(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = self._legacy_file(layout)
            identity = source_identity(path, project_root=layout.root)
            self.assertEqual(identity, "raw/sources/web/example.com/kl.html")
            self.assertEqual(
                source_locator(identity, "b1"),
                "raw/sources/web/example.com/kl.html#b1",
            )

    def test_its_files_are_still_durable_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = self._legacy_file(layout)
            identity = "raw/sources/web/example.com/kl.html"
            self.assertEqual(durable_source_file(layout, identity), path.resolve())
            self.assertEqual(raw_source_path(f"{identity}#b1"), identity)


class RawFolderTests(unittest.TestCase):
    def test_init_makes_raw_without_a_kind_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertTrue(layout.raw.is_dir())
            self.assertEqual(list(layout.raw.iterdir()), [])

    def test_doctor_names_an_empty_kind_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.raw / "web").mkdir()
            report = doctor_project(layout)
            empty = [f for f in report.findings if f.code == "layout.emptyKindFolder"]
            self.assertEqual(len(empty), 1)
            self.assertEqual(empty[0].path, "raw/web")
            self.assertEqual(dict(empty[0].params)["folder"], "web")

    def test_a_kind_folder_with_a_file_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.raw / "web").mkdir()
            (layout.raw / "web" / "page.html").write_text("<html></html>", encoding="utf-8")
            codes = {f.code for f in doctor_project(layout).findings}
            self.assertNotIn("layout.emptyKindFolder", codes)


if __name__ == "__main__":
    unittest.main()
