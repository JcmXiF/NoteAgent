from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.project.doctor import (
    doctor_after_write,
    doctor_payload,
    doctor_project,
    session_unknown_clip_warnings,
    slug_maps_from_pages,
)
from noteagent.project.init import init_project


def _write_concept(layout, name: str, body: str, **fm: str) -> Path:
    path = layout.wiki / "concepts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    title = fm.get("title", name)
    sources = fm.get("sources", "[]")
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\nsources: {sources}\n---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


LONG = "Body text here is long enough to pass the short-body lint threshold."


class DoctorPersistTests(unittest.TestCase):
    def test_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "kl",
                LONG,
                title="KL",
                sources='["raw/web/missing.html#b1"]',
            )
            codes = {item.code for item in doctor_project(layout).findings}
            self.assertIn("source.missing", codes)

    def test_title_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(layout, "one", LONG, title="Same")
            _write_concept(layout, "two", LONG, title="Same")
            codes = {item.code for item in doctor_project(layout).findings}
            self.assertIn("title.duplicate", codes)

    def test_orphan_skips_source_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(layout, "alive", LONG, title="Alive")
            summary = layout.wiki / "sources" / "clip.md"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(
                "---\ntype: source\ntitle: Clips\nsources: []\n---\n\n"
                f"# Clips\n\n{LONG}\n",
                encoding="utf-8",
            )
            report = doctor_project(layout)
            orphans = [item for item in report.findings if item.code == "link.orphan"]
            self.assertTrue(any(item.path == "wiki/concepts/alive.md" for item in orphans))
            self.assertFalse(
                any(
                    item.path and item.path.startswith("wiki/sources/")
                    for item in orphans
                )
            )

    def test_inbound_clears_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "hub",
                f"{LONG}\n\nSee [[concepts/spoke]].",
                title="Hub",
            )
            _write_concept(layout, "spoke", LONG, title="Spoke")
            orphans = {
                item.path
                for item in doctor_project(layout).findings
                if item.code == "link.orphan"
            }
            self.assertIn("wiki/concepts/hub.md", orphans)
            self.assertNotIn("wiki/concepts/spoke.md", orphans)

    def test_mark_clip_bad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "marked",
                f"{LONG}\n\n<!-- na-clip -->\n<!-- na-clip:@@@ -->\n",
                title="Marked",
            )
            bad = [
                item
                for item in doctor_project(layout).findings
                if item.code == "mark.clip_bad"
            ]
            self.assertGreaterEqual(len(bad), 2)

    def test_a_clip_mark_from_another_session_is_not_called_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "old",
                f"{LONG}\n\n<!-- na-clip:deadbeef-dead-beef-dead-beefdeadbeef -->\n",
                title="Old",
            )
            codes = {item.code for item in doctor_project(layout).findings}
            self.assertNotIn("clip.unknown", codes)
            self.assertNotIn("mark.clip_bad", codes)

    def test_slug_collision_helper(self) -> None:
        slug_to_rel, collisions = slug_maps_from_pages(
            [
                ("wiki/concepts/foo.md", "a"),
                ("wiki/concepts/foo.MD", "b"),
            ]
        )
        self.assertEqual(slug_to_rel["concepts/foo"], "wiki/concepts/foo.md")
        self.assertEqual(
            collisions["concepts/foo"],
            ["wiki/concepts/foo.md", "wiki/concepts/foo.MD"],
        )

    def test_session_unknown_clip_warnings(self) -> None:
        warnings = session_unknown_clip_warnings(
            [
                ("wiki/concepts/kl.md", "<!-- na-clip:c1 -->\n<!-- na-clip:other -->\n"),
                ("wiki/sources/clips.md", "<!-- na-clip:ghost -->\n"),
                ("wiki/log.md", "<!-- na-clip:ghost -->\n"),
            ],
            {"c1"},
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("other", warnings[0])
        self.assertIn("wiki/concepts/kl.md", warnings[0])

    def test_doctor_after_write_skips_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertIsNone(doctor_after_write(layout, dry_run=True))
            self.assertIsNone(doctor_after_write(layout, dry_run=False, cache_hit=True))
            report = doctor_after_write(layout, dry_run=False)
            self.assertIsNotNone(report)

    def test_payload_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "thin",
                "short",
                title="Thin",
                sources='["raw/web/missing.html"]',
            )
            payload = doctor_payload(doctor_project(layout), limit=1)
            assert payload is not None
            self.assertEqual(len(payload["findings"]), 1)
            self.assertTrue(payload["truncated"])
            self.assertGreater(payload["findingCount"], 1)

    def test_findings_carry_the_figures_an_interface_words(self) -> None:
        # A finding says what is wrong in prose, in one language, for a log. The
        # code and its figures are what let an interface say the same thing in the
        # reader's own language, so they travel with it.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "k",
                "[[a]] [[b]]",
                title="K",
                sources='["raw/web/missing.html"]',
            )
            report = doctor_project(layout)

            broken = [item for item in report.findings if item.code == "link.broken"]
            self.assertEqual(
                [dict(item.params)["target"] for item in broken], ["a", "b"]
            )

            source = next(item for item in report.findings if item.code == "source.missing")
            self.assertEqual(
                dict(source.params)["locator"], "raw/web/missing.html"
            )

            short = next(item for item in report.findings if item.code == "body.short")
            self.assertEqual(short.message, f"body only {dict(short.params)['chars']} chars")

    def test_payload_sends_params_as_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_concept(
                layout,
                "k",
                "[[a]]",
                title="K",
                sources='["raw/web/missing.html"]',
            )
            payload = doctor_payload(doctor_project(layout))
            assert payload is not None
            by_code = {item["code"]: item for item in payload["findings"]}
            self.assertEqual(by_code["link.broken"]["params"], {"target": "a"})
            self.assertEqual(
                by_code["source.missing"]["params"],
                {"locator": "raw/web/missing.html"},
            )


if __name__ == "__main__":
    unittest.main()
