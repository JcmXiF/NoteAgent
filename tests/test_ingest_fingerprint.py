from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    save_clip_store,
)
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.fingerprint import (
    KIND_CLIPS_CHANGED,
    KIND_LOCATOR_BROKEN,
    KIND_OUTPUT_MISSING,
    KIND_RAW_CREATED,
    KIND_RAW_DELETED,
    KIND_RAW_MODIFIED,
    list_raw_identities,
    scan_stale,
)
from noteagent.ingest.compile_cache import read_records
from noteagent.ingest.topic_map import parse_topic_map
from noteagent.project.init import init_project

IDENTITY = "raw/web/example.com/kl.html"
STAMPED = (
    '<p data-na-block="b1">KL 是相对熵。</p>\n'
    '<p data-na-block="b2">信息熵。</p>\n'
)
MAP = {
    "primary": {
        "name": "KL散度",
        "slug": "kl-divergence",
        "wikiPath": "wiki/concepts/kl-divergence.md",
        "collectionIds": ["c1"],
        "blockIds": ["b1"],
        "evidence": "[b1]",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
    },
    "alsoInSources": [],
}
GENERATION = """---FILE: wiki/concepts/kl-divergence.md---
---
type: concept
title: KL散度
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: []
---

# KL散度

相对熵。
---END FILE---
"""


def _store() -> ClipStore:
    return ClipStore(
        intent="KL散度",
        collections=(
            ClipCollection(
                id="c1",
                source_key=IDENTITY,
                source_label="KL",
                block_ids=("b1", "b2"),
                blocks=(
                    DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                    DerivedBlock(id="b2", tag="p", text="信息熵。", preview="熵"),
                ),
            ),
        ),
        topic_map=MAP,
    )


def _compile(layout, html: str = STAMPED):
    path = layout.raw / "web" / "example.com" / "kl.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    store = _store()
    save_clip_store(layout, store)
    topic_map = parse_topic_map(MAP, intent="KL散度", collections=store.collections)
    return ingest_confirmed(
        layout,
        confirmed=[
            {
                "name": "KL散度",
                "slug": "kl-divergence",
                "wikiPath": "wiki/concepts/kl-divergence.md",
                "action": "write",
                "sectionIds": ["s1"],
            }
        ],
        topic_map=topic_map,
        generation_text=GENERATION,
        store=store,
    )


class IngestFingerprintTests(unittest.TestCase):
    def test_confirmed_records_raw_identity_not_clip_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = _compile(layout)
            self.assertEqual(result.source_identity, IDENTITY)
            self.assertFalse(result.cache_hit)
            entries = read_records(layout)
            self.assertNotIn("raw/clip-session", entries)
            self.assertIn(IDENTITY, entries)
            row = entries[IDENTITY]
            self.assertTrue(row.digest)
            self.assertTrue(row.clips)
            self.assertTrue(row.locators)
            written = {path.replace("\\", "/") for path in row.wrote}
            self.assertIn("wiki/concepts/kl-divergence.md", written)
            kinds = {item.kind for item in scan_stale(layout) if item.identity == IDENTITY}
            self.assertEqual(kinds, set())

    def test_confirmed_does_not_skip_on_fingerprint_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            first = _compile(layout)
            self.assertTrue(first.write.written)
            second = _compile(layout)
            self.assertFalse(second.cache_hit)
            self.assertTrue(second.write.written)

    def test_scan_raw_modified_and_locator_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _compile(layout)
            html = layout.root / IDENTITY
            html.write_text(
                '<p data-na-block="b2">信息熵已改。</p>\n',
                encoding="utf-8",
            )
            by_kind = {
                item.kind: item
                for item in scan_stale(layout)
                if item.identity == IDENTITY
            }
            self.assertIn(KIND_RAW_MODIFIED, by_kind)
            self.assertIn(KIND_LOCATOR_BROKEN, by_kind)
            self.assertIn(f"{IDENTITY}#b1", by_kind[KIND_LOCATOR_BROKEN].locators)

    def test_scan_clips_changed_deleted_created_output_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _compile(layout)
            extra = layout.raw / "web" / "other.com" / "new.html"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("<p>new</p>\n", encoding="utf-8")
            cache_dir = layout.raw / ".cache" / "skip.html"
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            cache_dir.write_text("nope\n", encoding="utf-8")
            identities = list_raw_identities(layout)
            self.assertIn(IDENTITY, identities)
            self.assertIn("raw/web/other.com/new.html", identities)
            self.assertFalse(any(".cache" in item for item in identities))

            store = _store()
            narrowed = ClipCollection(
                id=store.collections[0].id,
                source_key=IDENTITY,
                source_label="KL",
                block_ids=("b1",),
                blocks=store.collections[0].blocks,
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent=store.intent,
                    collections=(narrowed,),
                    topic_map=MAP,
                ),
            )
            kinds = {
                item.kind: item
                for item in scan_stale(layout)
            }
            self.assertEqual(
                kinds[KIND_RAW_CREATED].identity, "raw/web/other.com/new.html"
            )
            self.assertIn(KIND_CLIPS_CHANGED, kinds)

            (layout.wiki / "concepts" / "kl-divergence.md").unlink()
            kinds = {item.kind: item for item in scan_stale(layout) if item.identity == IDENTITY}
            self.assertIn(KIND_OUTPUT_MISSING, kinds)
            self.assertIn(
                "wiki/concepts/kl-divergence.md",
                kinds[KIND_OUTPUT_MISSING].locators,
            )

            html = layout.root / IDENTITY
            html.unlink()
            kinds = {item.kind for item in scan_stale(layout) if item.identity == IDENTITY}
            self.assertIn(KIND_RAW_DELETED, kinds)
            self.assertNotIn(KIND_RAW_MODIFIED, kinds)

    def test_dry_run_does_not_record_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.raw / "web" / "example.com" / "kl.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(STAMPED, encoding="utf-8")
            store = _store()
            save_clip_store(layout, store)
            ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "action": "write",
                        "sectionIds": ["s1"],
                    }
                ],
                topic_map=parse_topic_map(
                    MAP, intent="KL散度", collections=store.collections
                ),
                generation_text=GENERATION,
                store=store,
                dry_run=True,
            )
            self.assertEqual(read_records(layout), {})

    def test_scan_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _compile(layout)
            (layout.raw / "orphan.md").write_text("# x\n", encoding="utf-8")
            items = scan_stale(layout)
            self.assertIn(KIND_RAW_CREATED, {item.kind for item in items})
            self.assertTrue(all(item.token for item in items))


if __name__ == "__main__":
    unittest.main()
