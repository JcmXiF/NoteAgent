from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    save_clip_store,
)
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.fingerprint import (
    KIND_RAW_CREATED,
    KIND_RAW_DELETED,
    KIND_RAW_MODIFIED,
)
from noteagent.ingest.revision_inbox import (
    STATUS_IGNORED,
    acknowledge_deleted,
    ignore_inbox_item,
    inbox_file_path,
    revision_inbox_payload,
    sync_revision_inbox,
)
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


class RevisionInboxTests(unittest.TestCase):
    def test_sync_opens_created_without_writing_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.raw / "orphan.md").write_text("# x\n", encoding="utf-8")
            index = layout.wiki_index.read_text(encoding="utf-8")
            with (
                patch("noteagent.ingest.confirmed.ingest_confirmed") as ingest,
                patch("noteagent.api.topic_gate.run_topic_map_job") as mapped,
            ):
                payload = sync_revision_inbox(layout, force=True)
            ingest.assert_not_called()
            mapped.assert_not_called()
            self.assertEqual(payload["openCount"], 1)
            self.assertEqual(payload["items"][0]["kind"], KIND_RAW_CREATED)
            self.assertTrue(payload["items"][0]["token"])
            self.assertEqual(
                layout.wiki_index.read_text(encoding="utf-8"), index
            )
            self.assertTrue(inbox_file_path(layout).is_file())

    def test_ignore_persists_until_token_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.raw / "orphan.md"
            path.write_text("# x\n", encoding="utf-8")
            first = sync_revision_inbox(layout, force=True)
            identity = first["items"][0]["identity"]
            ignored = ignore_inbox_item(layout, identity, KIND_RAW_CREATED)
            self.assertEqual(ignored["openCount"], 0)
            again = sync_revision_inbox(layout, force=True)
            self.assertEqual(again["openCount"], 0)
            disk = json.loads(inbox_file_path(layout).read_text(encoding="utf-8"))
            self.assertEqual(disk["items"][0]["status"], STATUS_IGNORED)
            path.write_text("# y\n", encoding="utf-8")
            reopened = sync_revision_inbox(layout, force=True)
            self.assertEqual(reopened["openCount"], 1)
            self.assertEqual(reopened["items"][0]["kind"], KIND_RAW_CREATED)

    def test_ack_deleted_does_not_remove_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _compile(layout)
            page = layout.root / "wiki" / "concepts" / "kl-divergence.md"
            self.assertTrue(page.is_file())
            raw = layout.root / IDENTITY
            raw.unlink()
            payload = sync_revision_inbox(layout, force=True)
            kinds = {item["kind"] for item in payload["items"]}
            self.assertIn(KIND_RAW_DELETED, kinds)
            closed = acknowledge_deleted(layout, IDENTITY)
            self.assertEqual(closed["openCount"], 0)
            self.assertTrue(page.is_file())
            still = sync_revision_inbox(layout, force=True)
            self.assertEqual(still["openCount"], 0)

    def test_compile_closes_modified_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _compile(layout)
            raw = layout.root / IDENTITY
            raw.write_text(
                STAMPED.replace("相对熵", "相对熵！"),
                encoding="utf-8",
            )
            opened = sync_revision_inbox(layout, force=True)
            kinds = {item["kind"] for item in opened["items"]}
            self.assertIn(KIND_RAW_MODIFIED, kinds)
            _compile(layout, html=raw.read_text(encoding="utf-8"))
            closed = revision_inbox_payload(layout)
            self.assertEqual(closed["openCount"], 0)


try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class RevisionInboxApiTests(unittest.TestCase):
    def test_get_ignore_and_missing_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            (layout.raw / "orphan.md").write_text("# x\n", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                missing = client.get("/api/projects/nope/revision-inbox")
                self.assertEqual(missing.status_code, 404)
                found = client.get("/api/projects/demo/revision-inbox")
                self.assertEqual(found.status_code, 200)
                body = found.json()
                self.assertEqual(body["openCount"], 1)
                identity = body["items"][0]["identity"]
                ignored = client.post(
                    "/api/projects/demo/revision-inbox/ignore",
                    json={"identity": identity, "kind": KIND_RAW_CREATED},
                )
                self.assertEqual(ignored.status_code, 200)
                self.assertEqual(ignored.json()["openCount"], 0)
                again = client.get("/api/projects/demo/revision-inbox")
                self.assertEqual(again.json()["openCount"], 0)


if __name__ == "__main__":
    unittest.main()
