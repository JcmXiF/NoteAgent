"""The eval harness's one change for quality cases: the wiki is a parameter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from evals.harness import CaseRun
except ImportError:  # pragma: no cover - CI installs the API extra
    CaseRun = None  # type: ignore[assignment]


@unittest.skipIf(CaseRun is None, "fastapi not installed")
class CaseRunPagesTests(unittest.TestCase):
    def test_custom_pages_replace_the_default_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRun(
                Path(tmp),
                pages=[
                    (
                        "concepts/entropy",
                        "Entropy",
                        "熵衡量不确定性。",
                    )
                ],
            )
            try:
                self.assertTrue(
                    (run.layout.wiki / "concepts/entropy.md").is_file()
                )
                self.assertFalse(
                    (run.layout.wiki / "concepts/information.md").exists()
                )
            finally:
                run.close()

    def test_the_default_pages_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRun(Path(tmp))
            try:
                self.assertTrue(
                    (run.layout.wiki / "concepts/entropy.md").is_file()
                )
            finally:
                run.close()
