from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.project.doctor import doctor_project
from noteagent.project.init import init_project


class LegacyOverviewTests(unittest.TestCase):
    def test_doctor_reports_leftover_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.legacy_overview.write_text("# Overview\n", encoding="utf-8")
            report = doctor_project(layout)
            codes = {f.code for f in report.findings}
            self.assertIn("layout.overviewLeftover", codes)

    def test_doctor_finds_ghost_and_broken_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            concept = layout.wiki / "concepts" / "alive.md"
            concept.parent.mkdir(parents=True, exist_ok=True)
            concept.write_text(
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]] and [[concepts/alive]].\n",
                encoding="utf-8",
            )
            layout.wiki_index.write_text(
                "# Wiki Index\n\n## Concepts\n\n"
                "- [[concepts/alive]] — Alive\n"
                "- [[concepts/ghost]] — Ghost\n",
                encoding="utf-8",
            )
            report = doctor_project(layout)
            codes = {f.code for f in report.findings}
            self.assertIn("index.ghost", codes)
            self.assertIn("link.broken", codes)
            self.assertGreaterEqual(report.errors, 1)


if __name__ == "__main__":
    unittest.main()
