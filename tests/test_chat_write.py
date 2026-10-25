from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.topic_gate import run_topic_map_job
from noteagent.chat.limits import default_limits
from noteagent.chat.tools import observe_tool
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    GateDecision,
    load_clip_store,
    save_clip_store,
)
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal

MAP = {
    "intent": "KL散度",
    "primary": {
        "name": "KL散度",
        "slug": "kl-divergence",
        "wikiPath": "wiki/concepts/kl-divergence.md",
        "relationToPrimary": "primary",
        "collectionIds": ["c1"],
        "blockIds": ["b1"],
        "evidence": "[b1]",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"], "collectionId": "c1"}],
    },
    "alsoInSources": [],
    "notes": "",
}


def _fingerprint(root: Path) -> tuple[tuple[str, str], ...]:
    files = sorted(root.rglob("*.md"))
    return tuple(
        (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        for path in files
    )


def _store(*, topic_map=None, decisions=()) -> ClipStore:
    return ClipStore(
        intent="KL散度",
        collections=(
            ClipCollection(
                id="c1",
                source_key="raw/web/example.com/kl.html",
                source_label="KL",
                block_ids=("b1",),
                blocks=(
                    DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                ),
            ),
        ),
        topic_map=topic_map,
        decisions=decisions,
    )


class ChatWriteGateTests(unittest.TestCase):
    def test_propose_without_collections_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            before = _fingerprint(layout.wiki)
            obs = observe_tool(
                "write",
                layout,
                {"action": "propose_topic_map", "intent": "KL散度"},
                default_limits(),
            )[0]
            self.assertTrue(obs.meta.get("needs_human"))
            self.assertFalse(obs.meta.get("wrote_wiki"))
            self.assertEqual(_fingerprint(layout.wiki), before)

    def test_ingest_ignores_invented_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(layout, _store(topic_map=MAP))
            before = _fingerprint(layout.wiki)
            with patch("noteagent.chat.tools.write.run_ingest_confirmed_job") as job:
                obs = observe_tool(
                    "write",
                    layout,
                    {
                        "action": "ingest_confirmed",
                        "confirmed": [
                            {
                                "name": "KL散度",
                                "slug": "kl-divergence",
                                "wikiPath": "wiki/concepts/kl-divergence.md",
                                "action": "write",
                                "sectionIds": ["s1"],
                            }
                        ],
                    },
                    default_limits(),
                )[0]
            job.assert_not_called()
            self.assertTrue(obs.meta.get("needs_human"))
            self.assertFalse(obs.meta.get("wrote_wiki"))
            self.assertEqual(_fingerprint(layout.wiki), before)
            self.assertEqual(load_clip_store(layout).decisions, ())

    def test_propose_clears_decisions_without_writing_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(
                layout,
                _store(
                    topic_map=MAP,
                    decisions=(
                        GateDecision(
                            slug="kl-divergence",
                            action="write",
                            section_ids=("s1",),
                        ),
                    ),
                ),
            )
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p>KL</p>\n", encoding="utf-8")
            before = _fingerprint(layout.wiki)
            payload = run_topic_map_job(
                layout, intent="KL散度", map_text=json.dumps(MAP)
            )
            self.assertEqual(payload["primary"]["slug"], "kl-divergence")
            loaded = load_clip_store(layout)
            self.assertEqual(loaded.decisions, ())
            self.assertIsNotNone(loaded.topic_map)
            self.assertEqual(_fingerprint(layout.wiki), before)

    def test_chat_propose_ignores_map_text_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(layout, _store())
            with patch(
                "noteagent.chat.tools.write.run_topic_map_job",
                return_value=MAP,
            ) as job:
                obs = observe_tool(
                    "write",
                    layout,
                    {
                        "action": "propose_topic_map",
                        "intent": "KL散度",
                        "map_text": "not-used",
                    },
                    default_limits(),
                )[0]
            job.assert_called_once()
            self.assertEqual(job.call_args.kwargs.get("intent"), "KL散度")
            self.assertNotIn("map_text", job.call_args.kwargs)
            self.assertTrue(obs.meta.get("needs_human"))
            self.assertFalse(obs.meta.get("wrote_wiki"))

    def test_ingest_uses_persisted_write_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(
                layout,
                _store(
                    topic_map=MAP,
                    decisions=(
                        GateDecision(
                            slug="kl-divergence",
                            action="write",
                            section_ids=("s1",),
                        ),
                    ),
                ),
            )
            with patch(
                "noteagent.chat.tools.write.run_ingest_confirmed_job",
                return_value={
                    "results": [
                        {
                            "written": [
                                {"path": "wiki/concepts/kl-divergence.md"},
                            ],
                            "doctor": {
                                "errors": 0,
                                "warnings": 1,
                                "findingCount": 1,
                                "truncated": False,
                                "findings": [
                                    {
                                        "severity": "info",
                                        "code": "link.orphan",
                                        "message": "no inbound wikilinks",
                                        "path": "wiki/concepts/kl-divergence.md",
                                    }
                                ],
                            },
                        }
                    ],
                    "errors": [],
                },
            ) as job:
                obs = observe_tool(
                    "write",
                    layout,
                    {
                        "action": "ingest_confirmed",
                        "confirmed": [
                            {
                                "slug": "invented-page",
                                "action": "write",
                                "sectionIds": ["nope"],
                            }
                        ],
                    },
                    default_limits(),
                )[0]
            job.assert_called_once()
            confirmed = job.call_args[0][1]
            slugs = {item["slug"]: item["action"] for item in confirmed}
            self.assertEqual(slugs["kl-divergence"], "write")
            self.assertNotIn("invented-page", slugs)
            self.assertTrue(obs.meta.get("wrote_wiki"))
            self.assertEqual(
                obs.meta.get("written"),
                ["wiki/concepts/kl-divergence.md"],
            )
            self.assertIn("doctor: 0 error / 1 warning / 1 finding", obs.content)
            self.assertIn("link.orphan", obs.content)

    def test_propose_empty_intent_still_plans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(layout, _store(topic_map=None))
            loaded = load_clip_store(layout)
            self.assertEqual(loaded.intent, "KL散度")
            with patch(
                "noteagent.chat.tools.write.run_topic_map_job",
                return_value=MAP,
            ) as job:
                obs = observe_tool(
                    "write",
                    layout,
                    {"action": "propose_topic_map", "intent": ""},
                    default_limits(),
                )[0]
            job.assert_called_once()
            self.assertEqual(job.call_args.kwargs.get("intent"), "")
            self.assertTrue(obs.meta.get("needs_human"))
            self.assertFalse(obs.meta.get("wrote_wiki"))


    def test_propose_rejects_preview_collections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(
                layout,
                ClipStore(
                    intent="KL散度",
                    collections=(
                        ClipCollection(
                            id="c1",
                            source_key="preview:https://example.com/kl",
                            source_label="KL",
                            block_ids=("b1",),
                            blocks=(
                                DerivedBlock(
                                    id="b1",
                                    tag="p",
                                    text="KL 是相对熵。",
                                    preview="KL",
                                ),
                            ),
                        ),
                    ),
                    topic_map=None,
                ),
            )
            with self.assertRaises(Refusal) as ctx:
                run_topic_map_job(layout, intent="KL散度", map_text=json.dumps(MAP))
            self.assertEqual(ctx.exception.code, "source.missingOriginal")
            obs = observe_tool(
                "write",
                layout,
                {"action": "propose_topic_map", "intent": "KL散度"},
                default_limits(),
            )[0]
            self.assertTrue(obs.meta.get("error"))
            self.assertTrue(obs.content)
            self.assertFalse(obs.meta.get("wrote_wiki"))


if __name__ == "__main__":
    unittest.main()
