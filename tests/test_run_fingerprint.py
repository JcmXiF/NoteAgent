"""What a run assumes about its project, recorded so a resume can notice drift."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.project.layout import ProjectLayout
from noteagent.project.run_fingerprint import project_fingerprint


class ProjectFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        (self.layout.wiki / "concepts").mkdir(parents=True)
        (self.layout.raw / "web").mkdir(parents=True)
        self.page = self.layout.wiki / "concepts" / "entropy.md"
        self.raw = self.layout.raw / "web" / "page.html"
        self.page.write_text(
            "---\ntitle: Entropy\n---\n\n熵衡量不确定性。\n",
            encoding="utf-8",
        )
        self.raw.write_text("<p>source</p>", encoding="utf-8")

    def fingerprint(self, config=None):
        return project_fingerprint(self.layout, config=config or {"model": "faux"})

    def test_it_is_deterministic_and_has_the_four_parts(self) -> None:
        first = self.fingerprint()
        second = self.fingerprint()

        self.assertEqual(first, second)
        self.assertEqual(first["projectRoot"], str(self.layout.root.resolve()))
        self.assertTrue(first["wikiGeneration"])
        self.assertTrue(first["sourceFingerprint"])
        self.assertTrue(first["configHash"])

    def test_a_wiki_change_moves_only_the_wiki_generation(self) -> None:
        before = self.fingerprint()

        self.page.write_text(
            "---\ntitle: Entropy\n---\n\n熵衡量不确定性，也衡量平均信息量。\n",
            encoding="utf-8",
        )
        after = self.fingerprint()

        self.assertNotEqual(before["wikiGeneration"], after["wikiGeneration"])
        self.assertEqual(before["sourceFingerprint"], after["sourceFingerprint"])
        self.assertEqual(before["configHash"], after["configHash"])

    def test_a_raw_change_moves_only_the_source_fingerprint(self) -> None:
        before = self.fingerprint()

        self.raw.write_text("<p>a different source</p>", encoding="utf-8")
        after = self.fingerprint()

        self.assertNotEqual(before["sourceFingerprint"], after["sourceFingerprint"])
        self.assertEqual(before["wikiGeneration"], after["wikiGeneration"])
        self.assertEqual(before["configHash"], after["configHash"])

    def test_a_config_change_moves_only_the_config_hash(self) -> None:
        before = self.fingerprint({"model": "faux"})

        after = self.fingerprint({"model": "another"})

        self.assertNotEqual(before["configHash"], after["configHash"])
        self.assertEqual(before["wikiGeneration"], after["wikiGeneration"])
        self.assertEqual(before["sourceFingerprint"], after["sourceFingerprint"])

    def test_it_keeps_no_secret_in_clear(self) -> None:
        fingerprint = self.fingerprint({"model": "faux", "api_key": "sk-secret"})

        self.assertNotIn("sk-secret", " ".join(fingerprint.values()))


if __name__ == "__main__":
    unittest.main()
