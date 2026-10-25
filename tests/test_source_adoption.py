"""A source the notes already cite is not a new source."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.fingerprint import adopt_cited_sources, scan_stale
from noteagent.ingest.compile_cache import read_records
from noteagent.ingest.revision_inbox import revision_inbox_payload
from noteagent.project.init import init_project
from noteagent.util.run_log import run_log_path

PAGE = """---
type: concept
title: 熵
sources: ["raw/web/old-000000000001.html"]
---

# 熵

从 [[raw/web/old-000000000001.html#b1]] 来的。
"""


def write_source(
    layout,
    relative: str,
    body: str = "<html><head><title>t</title></head></html>",
) -> str:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return relative


def write_page(layout, relative: str, text: str = PAGE) -> str:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


class AdoptCitedSourcesTests(unittest.TestCase):
    def test_a_cited_source_with_no_fingerprint_is_no_longer_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")

            # Before: the scan can only read "no record" as "never written from".
            kinds = {item.kind for item in scan_stale(layout) if item.identity == source}
            self.assertEqual(kinds, {"raw_created"})

            adopted = adopt_cited_sources(layout)

            self.assertEqual(adopted, (source,))
            self.assertIn(source, read_records(layout))
            self.assertEqual(
                [item.kind for item in scan_stale(layout) if item.identity == source], []
            )

    def test_a_source_nothing_cites_is_still_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            alone = write_source(layout, "raw/web/new-000000000002.html")
            write_page(layout, "wiki/concepts/entropy.md")

            self.assertEqual(adopt_cited_sources(layout), ())
            kinds = {item.kind for item in scan_stale(layout) if item.identity == alone}
            self.assertEqual(kinds, {"raw_created"})

    def test_the_record_it_writes_names_the_pages_that_cite_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")
            adopt_cited_sources(layout)

            entry = read_records(layout)[source]
            self.assertIn("wiki/concepts/entropy.md", entry.wrote)

    def test_a_change_after_adopting_is_reported(self) -> None:
        # What adopting buys: the source is watched from then on.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")
            adopt_cited_sources(layout)

            write_source(layout, source, "<html><head><title>changed</title></head></html>")

            kinds = {item.kind for item in scan_stale(layout) if item.identity == source}
            self.assertEqual(kinds, {"raw_modified"})

    def test_it_does_nothing_when_every_source_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            write_page(layout, "wiki/concepts/entropy.md")
            self.assertEqual(adopt_cited_sources(layout), ())

    def test_adopting_twice_changes_nothing_the_second_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")

            self.assertEqual(len(adopt_cited_sources(layout)), 1)
            self.assertEqual(adopt_cited_sources(layout), ())


class InboxTests(unittest.TestCase):
    def test_the_inbox_no_longer_calls_a_cited_source_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")

            payload = revision_inbox_payload(layout)

            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["openCount"], 0)

    def test_the_inbox_says_what_it_adopted(self) -> None:
        # An assumption this large belongs in the log, where a wrong one can be found.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            write_source(layout, "raw/web/old-000000000001.html")
            write_page(layout, "wiki/concepts/entropy.md")

            revision_inbox_payload(layout)

            log = run_log_path(layout)
            self.assertTrue(log.is_file())
            events = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            adopted = [item for item in events if item.get("event") == "fingerprints.adopted"]
            self.assertTrue(adopted, f"expected the log to say so, got {events}")
            self.assertEqual(adopted[-1]["sources"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
