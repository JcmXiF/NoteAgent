from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.write_file_blocks import write_file_blocks
from noteagent.project.config import save_active_project
from noteagent.project.init import init_project
from noteagent.util.run_log import RunLogger, run_log_path


def _page(
    *,
    title: str = "Demo",
    sources: str,
    body: str,
    tags: str = "[a]",
) -> str:
    return (
        "---\n"
        "type: concept\n"
        f"title: {title}\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        f"tags: {tags}\n"
        f"sources: {sources}\n"
        "---\n"
        f"\n{body}"
    )


def _load_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class RunLogTests(unittest.TestCase):
    def test_disabled_logger_writes_nothing(self) -> None:
        logger = RunLogger.disabled()
        logger.append("status", message="noop")
        self.assertIsNone(logger.path)

    def test_write_logs_merge_replace_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            logger = RunLogger.for_layout(layout)

            first = FileBlock(
                path="wiki/concepts/demo.md",
                content=_page(
                    sources='["raw/one.md"]',
                    body="# From one\n\n" + ("alpha " * 40) + "\n",
                ),
            )
            write_file_blocks(
                layout,
                [first],
                source_identity="raw/one.md",
                run_logger=logger,
            )

            # Multi-source merge via mock merger
            second = FileBlock(
                path="wiki/concepts/demo.md",
                content=_page(
                    title="Renamed",
                    sources='["raw/two.md"]',
                    body="# From two\n\n" + ("beta " * 40) + "\n",
                    tags="[b]",
                ),
            )

            def merger(existing: str, incoming: str, identity: str) -> str:
                return _page(
                    title="Merged",
                    sources='["raw/two.md"]',
                    body="# Merged body\n\n" + ("gamma " * 50) + "\n",
                )

            write_file_blocks(
                layout,
                [second],
                source_identity="raw/two.md",
                merger=merger,
                today=lambda: "2026-08-11",
                run_logger=logger,
            )

            # Single-source replace
            third = FileBlock(
                path="wiki/concepts/solo.md",
                content=_page(
                    title="Solo",
                    sources='["raw/solo.md"]',
                    body="# Obsolete\n",
                ),
            )
            write_file_blocks(
                layout,
                [third],
                source_identity="raw/solo.md",
                run_logger=logger,
            )
            fourth = FileBlock(
                path="wiki/concepts/solo.md",
                content=_page(
                    title="Solo2",
                    sources='["raw/solo.md"]',
                    body="# Corrected\n",
                ),
            )
            write_file_blocks(
                layout,
                [fourth],
                source_identity="raw/solo.md",
                merger=lambda *_: "should-not-run",
                today=lambda: "2026-08-11",
                run_logger=logger,
            )

            # Multi-source fallback (no merger)
            fifth = FileBlock(
                path="wiki/concepts/demo.md",
                content=_page(
                    sources='["raw/three.md"]',
                    body="# From three\n\n" + ("delta " * 40) + "\n",
                ),
            )
            write_file_blocks(
                layout,
                [fifth],
                source_identity="raw/three.md",
                merger=None,
                today=lambda: "2026-08-11",
                run_logger=logger,
            )

            log_path = run_log_path(layout)
            self.assertTrue(log_path.is_file())
            events = _load_events(log_path)
            modes = [
                e["mode"]
                for e in events
                if e.get("event") == "page_write"
            ]
            self.assertIn("create", modes)
            self.assertIn("merge", modes)
            self.assertIn("replace", modes)
            self.assertIn("merge-fallback", modes)

            warnings = [
                e for e in events if e.get("event") == "warning"
            ]
            self.assertTrue(
                any("keeps its body" in e.get("message", "") for e in warnings)
            )

    def test_for_active_project_opens_valid_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = init_project(root / "proj")
            save_active_project(layout.root, config_dir=root)
            logger = RunLogger.for_active_project(start=root)
            self.assertTrue(logger.enabled)
            self.assertEqual(logger.path, run_log_path(layout))
            logger.append("search.start", component="acquire", query="t")
            events = _load_events(run_log_path(layout))
            self.assertEqual(events[0]["event"], "search.start")

    def test_for_active_project_disabled_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger.for_active_project(start=Path(tmp))
            self.assertFalse(logger.enabled)
            self.assertIsNone(logger.path)


if __name__ == "__main__":
    unittest.main()
