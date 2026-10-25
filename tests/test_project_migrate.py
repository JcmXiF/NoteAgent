from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.clips import load_clip_store
from noteagent.project.doctor import doctor_project
from noteagent.project.init import init_project, is_valid_project
from noteagent.project.migrate import (
    format_migration_result,
    migrate_project,
    plan_migration,
)
from noteagent.util.refusal import Refusal


def _write(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


def _page(title: str, body: str, sources: list[str] | None = None) -> str:
    entries = ", ".join(f'"{item}"' for item in (sources or []))
    return (
        "---\n"
        f"title: {title}\n"
        f"sources: [{entries}]\n"
        "---\n"
        "\n"
        f"{body}\n"
    )


def _note(page: str, body: str, *, note_id: str = "20260101-000000-abcd") -> str:
    return (
        "---\n"
        f"page: {page}\n"
        'anchor: ""\n'
        'quote: ""\n'
        '"created": "2026-01-01T00:00:00.000000+08:00"\n'
        '"updated": "2026-01-01T00:00:00.000000+08:00"\n'
        "---\n"
        "\n"
        f"{body}\n"
    )


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "legacy"
        self.layout = init_project(self.root)
        self.addCleanup(self._tmp.cleanup)

    # -- fixture ---------------------------------------------------------

    def _build_legacy(self) -> None:
        root = self.layout.root
        _write(
            root / "raw/sources/web/a.example/entropy.html",
            "<html><body><p id='b1'>entropy</p></body></html>",
        )
        _write(
            root / "raw/sources/web/b.example/heat.html",
            "<html><body><p id='b1'>heat</p></body></html>",
        )
        _write(root / "raw/sources/pdf/paper.pdf", "%PDF-1.4")

        _write(
            root / "wiki/concepts/entropy.md",
            _page(
                "Entropy",
                "Entropy is a measure of disorder. See [[entities/heat]] and "
                "[[concepts/entropy|Entropy]].",
                ["raw/sources/web/a.example/entropy.html#b1"],
            ),
        )
        _write(
            root / "wiki/entities/heat.md",
            _page(
                "Heat",
                "Heat is energy in transit between bodies. Back to "
                "[[concepts/entropy]].",
                ["raw/sources/web/b.example/heat.html#b1"],
            ),
        )
        _write(
            root / "wiki/sources/entropy.md",
            _page("Entropy source", "The page this note is drawn from."),
        )
        _write(
            root / "wiki/sources/entropy-clips.md",
            _page(
                "Entropy clips",
                "- Entropy (`11111111-1111-1111-1111-111111111111`): "
                "`raw/sources/web/a.example/entropy.html` (b1)\n"
                "- `raw/sources/web/a.example/entropy.html#b1`\n",
            ),
        )
        _write(root / "wiki/overview.md", "# Overview\n")
        _write(
            root / "notes/concepts/entropy/20260101-000000-abcd.md",
            _note(
                "wiki/concepts/entropy.md",
                "This links back to [[concepts/entropy]] and cites "
                "`raw/sources/web/a.example/entropy.html#b1`.",
            ),
        )

        _write(
            root / ".noteagent/media/raw/sources/web/a.example/entropy.html/figure.png",
            "png",
            encoding="utf-8",
        )
        _write(
            root / ".noteagent/derived/raw/sources/pdf/paper.pdf.json",
            json.dumps({"blocks": []}),
        )

        clips = {
            "version": 1,
            "intent": "",
            "collections": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "sourceKey": "raw/sources/web/a.example/entropy.html",
                    "sourceLabel": "Entropy",
                    "blockIds": ["b1"],
                    "blocks": [
                        {
                            "id": "b1",
                            "tag": "p",
                            "text": "entropy is quoted raw/sources/web/a.example/entropy.html#b1",
                            "preview": "entropy",
                        }
                    ],
                    "targetSlug": "concepts/entropy",
                }
            ],
            "topicMap": {
                "intent": "",
                "candidates": [
                    {
                        "title": "Entropy",
                        "slug": "concepts/entropy",
                        "wikiPath": "wiki/concepts/entropy.md",
                    }
                ],
                "primary": {
                    "title": "Entropy",
                    "slug": "concepts/entropy",
                    "wikiPath": "wiki/concepts/entropy.md",
                },
                "alsoInSources": [
                    {
                        "title": "Heat",
                        "slug": "entities/heat",
                        "wikiPath": "wiki/entities/heat.md",
                    }
                ],
                "edges": [
                    {
                        "from": "concepts/entropy",
                        "to": "entities/heat",
                        "relation": "related",
                    }
                ],
                "notes": "raw/sources/web/a.example/entropy.html#b1 is quoted here",
            },
            "decisions": [{"slug": "concepts/entropy", "action": "write"}],
            "choices": [{"slug": "entities/heat", "action": "mention"}],
        }
        _write(root / ".noteagent/clips.json", json.dumps(clips))

        deep_search = {
            "version": 2,
            "roundId": "",
            "mode": "",
            "step": 0,
            "intent": "",
            "seed": {
                "kind": "page",
                "path": "wiki/concepts/entropy.md",
                "slug": "concepts/entropy",
                "title": "Entropy",
            },
            "whitelist": [
                {
                    "title": "Heat",
                    "slug": "entities/heat",
                    "wikiPath": "wiki/entities/heat.md",
                    "action": "add",
                },
                {
                    "title": "Not yet",
                    "slug": "concepts/not-yet",
                    "wikiPath": "wiki/concepts/not-yet.md",
                    "action": "add",
                },
            ],
            "overlayEdges": [
                {"source": "concepts/entropy", "target": "entities/heat", "relation": ""}
            ],
            "proposedBlocks": [
                {
                    "pageSlug": "concepts/entropy",
                    "locator": "raw/sources/web/a.example/entropy.html#b1",
                    "preview": "entropy",
                }
            ],
            "cards": [],
            "stashedIntent": "",
            "stashedTopicMap": {
                "candidates": [
                    {
                        "title": "Entropy",
                        "slug": "concepts/entropy",
                        "wikiPath": "wiki/concepts/entropy.md",
                    }
                ]
            },
            "stashedDecisions": [{"slug": "concepts/entropy", "action": "write"}],
        }
        _write(root / ".noteagent/deep-search.json", json.dumps(deep_search))

        cache = {
            "version": 2,
            "sources": {
                "raw/sources/web/a.example/entropy.html": {
                    "content_hash": "abc",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "files_written": ["wiki/concepts/entropy.md"],
                }
            },
        }
        _write(root / ".noteagent/ingest-cache.json", json.dumps(cache))
        _write(
            root / ".noteagent/chat-limits.json",
            json.dumps(
                {
                    "version": 1,
                    "historyLimit": 4,
                    "maxPurposeChars": 1_500,
                    "maxSchemaChars": 900,
                }
            ),
        )
        _write(root / "purpose.md", "This wiki explains thermodynamics.\n")
        _write(root / "schema.md", "concepts/ entities/ queries/\n")

    # -- happy path ------------------------------------------------------

    def test_migration_moves_files_and_rewrites_every_stored_path(self) -> None:
        self._build_legacy()
        dry = migrate_project(self.layout, dry_run=True)
        self.assertTrue(dry.dry_run)
        self.assertTrue(dry.changed)
        self.assertTrue((self.root / "raw/sources/web/a.example/entropy.html").is_file())
        self.assertFalse((self.root / "raw/web/entropy.html").exists())

        result = migrate_project(self.layout)

        self.assertEqual(len(result.raw_moves), 3)
        self.assertTrue((self.root / "raw/web/entropy.html").is_file())
        self.assertTrue((self.root / "raw/web/heat.html").is_file())
        self.assertTrue((self.root / "raw/pdf/paper.pdf").is_file())
        self.assertFalse((self.root / "raw/sources").exists())

        self.assertEqual(len(result.page_moves), 2)
        self.assertTrue((self.root / "wiki/entropy.md").is_file())
        self.assertTrue((self.root / "wiki/heat.md").is_file())
        self.assertFalse((self.root / "wiki/concepts").exists())
        self.assertFalse((self.root / "wiki/entities").exists())

        entropy = (self.root / "wiki/entropy.md").read_text(encoding="utf-8")
        self.assertIn('"raw/web/entropy.html#b1"', entropy)
        self.assertIn("[[heat]]", entropy)
        self.assertIn("[[entropy|Entropy]]", entropy)
        self.assertNotIn("concepts/", entropy)
        heat = (self.root / "wiki/heat.md").read_text(encoding="utf-8")
        self.assertIn("[[entropy]]", heat)
        self.assertIn('"raw/web/heat.html#b1"', heat)

        ledger = (self.root / "wiki/sources/entropy-clips.md").read_text(encoding="utf-8")
        self.assertIn("`raw/web/entropy.html#b1`", ledger)
        self.assertNotIn("raw/sources/", ledger)

        note = self.root / "notes/entropy/20260101-000000-abcd.md"
        self.assertTrue(note.is_file())
        self.assertFalse((self.root / "notes/concepts").exists())
        note_text = note.read_text(encoding="utf-8")
        self.assertIn("page: wiki/entropy.md", note_text)
        self.assertIn("[[entropy]]", note_text)
        self.assertIn("`raw/web/entropy.html#b1`", note_text)

        self.assertTrue(
            (
                self.root
                / ".noteagent/media/raw/web/entropy.html/figure.png"
            ).is_file()
        )
        self.assertFalse((self.root / ".noteagent/media/raw/sources").exists())
        self.assertTrue(
            (self.root / ".noteagent/derived/raw/pdf/paper.pdf.json").is_file()
        )
        self.assertFalse((self.root / ".noteagent/derived/raw/sources").exists())

        store = load_clip_store(self.layout)
        self.assertEqual(
            store.collections[0].source_key, "raw/web/entropy.html"
        )
        self.assertEqual(store.collections[0].target_slug, "entropy")
        self.assertEqual(store.collections[0].blocks[0].text, clips_text())
        self.assertEqual(store.decisions[0].slug, "entropy")
        self.assertEqual(store.choices[0].slug, "heat")
        topic_map = store.topic_map or {}
        self.assertEqual(topic_map["candidates"][0]["slug"], "entropy")
        self.assertEqual(topic_map["candidates"][0]["wikiPath"], "wiki/entropy.md")
        self.assertEqual(topic_map["alsoInSources"][0]["slug"], "heat")
        self.assertEqual(topic_map["edges"][0]["from"], "entropy")
        self.assertEqual(topic_map["edges"][0]["to"], "heat")
        self.assertEqual(
            topic_map["notes"],
            "raw/sources/web/a.example/entropy.html#b1 is quoted here",
        )

        deep = json.loads(
            (self.root / ".noteagent/deep-search.json").read_text(encoding="utf-8")
        )
        self.assertEqual(deep["seed"]["path"], "wiki/entropy.md")
        self.assertEqual(deep["seed"]["slug"], "entropy")
        self.assertEqual(deep["whitelist"][0]["slug"], "heat")
        self.assertEqual(deep["whitelist"][0]["wikiPath"], "wiki/heat.md")
        # A page the round proposed but no one has written yet still moves out
        # of a folder that will not exist after this run.
        self.assertEqual(deep["whitelist"][1]["slug"], "not-yet")
        self.assertEqual(deep["whitelist"][1]["wikiPath"], "wiki/not-yet.md")
        self.assertEqual(deep["overlayEdges"][0]["source"], "entropy")
        self.assertEqual(deep["overlayEdges"][0]["target"], "heat")
        self.assertEqual(
            deep["proposedBlocks"][0]["locator"], "raw/web/entropy.html#b1"
        )
        self.assertEqual(
            deep["stashedTopicMap"]["candidates"][0]["slug"], "entropy"
        )

        # The cache's rows described the old layout, so the file itself is gone
        # rather than rewritten; the next compile records fresh rows.
        self.assertFalse((self.root / ".noteagent/ingest-cache.json").exists())
        self.assertFalse((self.root / ".noteagent/compile-cache.json").exists())

        self.assertFalse((self.root / "purpose.md").exists())
        self.assertFalse((self.root / "schema.md").exists())
        self.assertFalse((self.root / "wiki/overview.md").exists())
        self.assertEqual(
            (self.root / "config/brief.md").read_text(encoding="utf-8").strip(),
            "This wiki explains thermodynamics.",
        )
        limits = json.loads(
            (self.root / "config/chat-limits.json").read_text(encoding="utf-8")
        )
        self.assertEqual(limits["maxBriefChars"], 1_500)
        self.assertEqual(limits["historyLimit"], 4)
        self.assertFalse((self.root / ".noteagent/chat-limits.json").exists())

        index = (self.root / "wiki/index.md").read_text(encoding="utf-8")
        self.assertIn("[[entropy]]", index)
        self.assertIn("[[heat]]", index)
        self.assertNotIn("concepts/", index)
        self.assertTrue(result.index_rebuilt)

        second = migrate_project(self.layout)
        self.assertFalse(second.changed)
        self.assertTrue(plan_migration(self.layout).empty)

    def test_a_link_to_an_unwritten_page_leaves_the_retired_folder(self) -> None:
        _write(
            self.root / "wiki/concepts/entropy.md",
            _page(
                "Entropy",
                "Entropy is a measure of disorder, linked from "
                "[[concepts/not-yet]] and [[concepts/not-yet|Not yet]].",
            ),
        )
        migrate_project(self.layout)
        text = (self.root / "wiki/entropy.md").read_text(encoding="utf-8")
        self.assertIn("[[not-yet]]", text)
        self.assertIn("[[not-yet|Not yet]]", text)
        self.assertNotIn("concepts/", text)

    def test_migrated_project_reports_only_clean_health(self) -> None:
        self._build_legacy()
        migrate_project(self.layout)
        report = doctor_project(self.layout)
        codes = [finding.code for finding in report.findings]
        for code in (
            "layout.legacyFolder",
            "layout.legacyRaw",
            "source.missing",
            "link.broken",
            "slug.duplicate",
            "index.missing",
        ):
            self.assertNotIn(code, codes)
        note = self.root / "notes/entropy/20260101-000000-abcd.md"
        self.assertTrue(note.is_file())

    # -- collisions ------------------------------------------------------

    def test_page_slug_collision_stops_before_any_write(self) -> None:
        self._build_legacy()
        _write(
            self.root / "wiki/entities/entropy.md",
            _page("Entropy again", "Another page with the same file name."),
        )
        before = _tree(self.root)
        with self.assertRaises(Refusal) as caught:
            migrate_project(self.layout)
        self.assertEqual(caught.exception.code, "project.migrateCollisions")
        self.assertIn("wiki/entropy.md", str(caught.exception))
        self.assertIn("wiki/concepts/entropy.md", str(caught.exception))
        self.assertIn("wiki/entities/entropy.md", str(caught.exception))
        self.assertEqual(_tree(self.root), before)

        dry = migrate_project(self.layout, dry_run=True)
        self.assertTrue(dry.blocked)
        targets = {item.target for item in dry.collisions}
        self.assertIn("wiki/entropy.md", targets)
        self.assertIn("Collisions", format_migration_result(dry))
        self.assertEqual(_tree(self.root), before)

    def test_raw_file_collision_is_reported(self) -> None:
        _write(
            self.root / "raw/sources/web/a.example/same.html",
            "<html>a</html>",
        )
        _write(
            self.root / "raw/sources/web/b.example/same.html",
            "<html>b</html>",
        )
        plan = plan_migration(self.layout)
        self.assertTrue(plan.blocked)
        collision = next(
            item for item in plan.collisions if item.target == "raw/web/same.html"
        )
        self.assertIn("raw/sources/web/a.example/same.html", collision.sources)
        self.assertIn("raw/sources/web/b.example/same.html", collision.sources)

    def test_an_occupied_target_is_a_collision(self) -> None:
        _write(self.root / "raw/sources/web/example.com/page.html", "<html></html>")
        _write(self.root / "raw/web/page.html", "<html>already here</html>")
        plan = plan_migration(self.layout)
        self.assertTrue(plan.blocked)
        self.assertEqual(plan.collisions[0].target, "raw/web/page.html")

    # -- startup detection ----------------------------------------------

    def test_an_unmigrated_project_opens_and_says_what_to_run(self) -> None:
        self._build_legacy()
        self.assertTrue(is_valid_project(self.layout.root))
        report = doctor_project(self.layout)
        findings = [item for item in report.findings if item.code == "layout.legacyRaw"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("noteagent project migrate", findings[0].message)
        self.assertEqual(findings[0].path, "raw/sources")

    def test_legacy_limits_out_of_range_keep_their_neighbours(self) -> None:
        _write(
            self.root / ".noteagent/chat-limits.json",
            json.dumps(
                {
                    "version": 1,
                    "historyLimit": 4,
                    "maxPurposeChars": 999_999,
                    "maxSchemaChars": 900,
                }
            ),
        )
        migrate_project(self.layout)
        limits = json.loads(
            (self.root / "config/chat-limits.json").read_text(encoding="utf-8")
        )
        self.assertEqual(limits["historyLimit"], 4)
        self.assertEqual(limits["maxBriefChars"], 40_000)

    def test_missing_brief_is_left_missing(self) -> None:
        _write(self.root / "raw/sources/web/example.com/page.html", "<html></html>")
        migrate_project(self.layout)
        self.assertFalse((self.root / "config/brief.md").exists())


def clips_text() -> str:
    return "entropy is quoted raw/sources/web/a.example/entropy.html#b1"


def _tree(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
