"""Legacy ``preview:`` selections: stored, rewritten, and still the same plan.

An old project's selections are keyed by their page URL until the original is
stored under ``raw/``. Storing it rewrites the key on save; the plan was made
against the same blocks, so the rewrite must not throw the plan away.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.ingest import ingest_result_payload
from noteagent.ingest.clips import load_clip_store
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.pipeline.materials import MaterialLog, MaterialTransition
from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]

PREVIEW = "preview:https://example.com/kl"
STORED = "raw/web/example.com/kl.html"

PLAN = {
    "intent": "KL散度",
    "candidates": [
        {
            "slug": "kl-divergence",
            "title": "KL散度",
            "coverage": "write",
            "wikiPath": "wiki/concepts/kl-divergence.md",
            "collectionIds": ["c1"],
            "outline": [
                {
                    "id": "s1",
                    "title": "定义",
                    "collectionId": "c1",
                    "blockIds": ["b1"],
                }
            ],
        }
    ],
}


def _collection(source_key: str, *, block_ids: list[str] | None = None) -> dict:
    return {
        "id": "c1",
        "sourceKey": source_key,
        "sourceLabel": "KL",
        "blockIds": ["b1"] if block_ids is None else block_ids,
        "blocks": [
            {
                "id": "b1",
                "tag": "p",
                "text": "KL 是相对熵。",
                "preview": "KL 是相对熵。",
            },
            {"id": "b2", "tag": "p", "text": "第二条。", "preview": "第二条。"},
        ],
    }


def _put(client, collections: list[dict], topic_map: dict | None) -> dict:
    response = client.put(
        "/api/projects/demo/clips",
        json={
            "intent": "KL散度",
            "collections": collections,
            "topicMap": topic_map,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _store_at(layout, relative: str) -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html></html>", encoding="utf-8")


GENERATION = """---FILE: wiki/concepts/kl-divergence.md---
---
type: concept
title: KL散度
created: 2026-11-18
updated: 2026-11-18
tags: []
related: []
sources: []
---

# KL散度

KL 散度衡量两个分布的差异。
---END FILE---
"""


def _compile_job(layout, confirmed, **kwargs):
    """The compile the manual endpoint asks for, without a model."""
    store = load_clip_store(layout)
    assert store.topic_map is not None
    result = ingest_confirmed(
        layout,
        confirmed=confirmed,
        intent=store.intent,
        topic_map=store.topic_map,
        store=store,
        generation_text=GENERATION,
        cancel=kwargs.get("cancel"),
    )
    return {
        "results": [ingest_result_payload(result, dry_run=False)],
        "errors": [],
    }


_TEXT = (
    "KL 散度衡量两个概率分布之间的差异，它来自信息论，"
    "在变分推断与机器学习里都用来逼近一个分布。"
) * 10

_ARTICLE = (
    "<html><body><article>"
    f'<p data-na-block="b1">{_TEXT}</p>'
    "</article></body></html>"
)


class LegacyPreviewTests(unittest.TestCase):
    def test_storing_the_original_then_remapping_keeps_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                saved = client.post(
                    "/api/projects/demo/acquire/save",
                    json={
                        "captures": [
                            {
                                "url": "https://example.com/kl",
                                "title": "KL",
                                "markdown": _TEXT,
                                "html": _ARTICLE,
                                "captureMethod": "trafilatura-html",
                                "fidelity": "ok",
                            }
                        ]
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                written = saved.json()["written"]
                self.assertEqual(len(written), 1)
                stored = written[0]
                self.assertTrue((layout.root / stored).is_file())

                _put(client, [_collection(PREVIEW)], None)
                first = _put(client, [_collection(PREVIEW)], PLAN)
                self.assertIsNotNone(first["topicMap"])

                # This is the order the manual compile uses: store the original,
                # rewrite the key, persist, then compile against the stored plan.
                remapped = _put(
                    client,
                    [_collection(stored)],
                    first["topicMap"],
                )

                self.assertIsNotNone(remapped["topicMap"])
                self.assertEqual(
                    remapped["collections"][0]["sourceKey"], stored
                )
                on_disk = client.get("/api/projects/demo/clips").json()
                self.assertIsNotNone(on_disk["topicMap"])
                self.assertEqual(
                    on_disk["collections"][0]["sourceKey"], stored
                )

                with (
                    patch("noteagent.api.app.ensure_llm_configured"),
                    patch(
                        "noteagent.api.topic_gate.run_ingest_confirmed_job",
                        side_effect=_compile_job,
                    ),
                ):
                    compiled = client.post(
                        "/api/projects/demo/ingest-confirmed",
                        json={
                            "intent": "KL散度",
                            "confirmed": [
                                {
                                    "name": "KL散度",
                                    "slug": "kl-divergence",
                                    "wikiPath": "wiki/concepts/kl-divergence.md",
                                    "action": "write",
                                    "sectionIds": [],
                                }
                            ],
                        },
                    )

                self.assertEqual(compiled.status_code, 200, compiled.text)
                self.assertIn("event: done", compiled.text)
                page_path = layout.wiki / "concepts" / "kl-divergence.md"
                self.assertTrue(page_path.is_file())
                sources = parse_inline_list(
                    parse_page(page_path.read_text(encoding="utf-8")).fields.get(
                        "sources", "[]"
                    )
                )
                self.assertIn(f"{stored}#b1", sources)

    def test_rewriting_a_preview_key_to_its_stored_path_keeps_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            _store_at(layout, STORED)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                _put(client, [_collection(PREVIEW)], None)
                first = _put(client, [_collection(PREVIEW)], PLAN)
                self.assertIsNotNone(first["topicMap"])

                rewritten = _put(
                    client,
                    [_collection(STORED)],
                    first["topicMap"],
                )

                self.assertIsNotNone(rewritten["topicMap"])
                self.assertEqual(
                    rewritten["topicMap"]["candidates"][0]["slug"],
                    "kl-divergence",
                )
                self.assertEqual(rewritten["collections"][0]["sourceKey"], STORED)

    def test_a_rewritten_key_is_not_a_human_frame_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            _store_at(layout, STORED)
            log = MaterialLog(layout.materials_log)
            log.append(
                MaterialTransition(
                    at="2026-11-10T09:00:00Z",
                    material=STORED,
                    state="framed",
                    run_id="pr-1",
                )
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                _put(client, [_collection(PREVIEW)], None)
                first = _put(client, [_collection(PREVIEW)], PLAN)
                _put(client, [_collection(STORED)], first["topicMap"])

            human = [
                item for item in log.transitions() if item.source == "human"
            ]
            self.assertEqual(human, [])

    def test_changing_the_blocks_still_clears_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            _store_at(layout, STORED)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                _put(client, [_collection(PREVIEW)], None)
                first = _put(client, [_collection(PREVIEW)], PLAN)

                changed = _put(
                    client,
                    [_collection(STORED, block_ids=["b2"])],
                    first["topicMap"],
                )

                self.assertIsNone(changed["topicMap"])

    def test_a_non_preview_source_change_still_clears_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                _put(client, [_collection(STORED)], None)
                first = _put(client, [_collection(STORED)], PLAN)

                moved = _put(
                    client,
                    [_collection("raw/web/example.com/other.html")],
                    first["topicMap"],
                )

                self.assertIsNone(moved["topicMap"])


if __name__ == "__main__":
    unittest.main()
