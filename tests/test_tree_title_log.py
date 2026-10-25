"""A title that could not be read is written down, not silently dropped."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.api.files import list_project_tree
from noteagent.project.init import init_project
from noteagent.util.run_log import run_log_path


class TreeTitleProblemTests(unittest.TestCase):
    def test_an_unreadable_document_is_reported_in_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            broken = layout.raw / "web" / "broken-000000000001.pdf"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_bytes(b"not a pdf at all")

            items = list_project_tree(layout)
            entry = next(
                item for item in items if item["path"].endswith("broken-000000000001.pdf")
            )
            # The listing still works, and the file is still listed.
            self.assertEqual(entry["title"], "")

            log = run_log_path(layout)
            self.assertTrue(log.is_file(), "expected a run log")
            events = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            warnings = [item for item in events if item.get("event") == "warning"]
            self.assertTrue(warnings, f"expected a warning, got {events}")
            reported = warnings[-1]
            self.assertIn("title", reported["message"])
            self.assertEqual(reported["path"], "raw/web/broken-000000000001.pdf")
            self.assertTrue(reported["reason"])

    def test_a_document_without_a_title_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = layout.raw / "web" / "a-000000000001.html"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_bytes(b"<html><head><meta charset=\"utf-8\"></head></html>")

            list_project_tree(layout)
            log = run_log_path(layout)
            if log.is_file():
                events = [
                    json.loads(line)
                    for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual(
                    [item for item in events if item.get("event") == "warning"], []
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
