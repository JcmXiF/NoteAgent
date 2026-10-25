from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.ingest import resolve_http_ingest_paths, run_ingest_job
from noteagent.api.projects import use_project
from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.project.init import init_project

FIXTURE = Path(__file__).parent / "fixtures" / "sample_generation_blocks.txt"


class HttpIngestPathTests(unittest.TestCase):
    def test_rejects_wiki_and_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.raw / "ok.md").write_text("# hi\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_http_ingest_paths(layout, ["wiki/index.md"])
            with self.assertRaises(ValueError):
                resolve_http_ingest_paths(layout, ["raw/../wiki/index.md"])
            found = resolve_http_ingest_paths(layout, ["raw/ok.md"])
            self.assertEqual(len(found), 1)

    def test_job_compiles_injected_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = layout.raw / "web" / "bellman.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                "# Bellman\n\nValue functions satisfy a recursive consistency.\n",
                encoding="utf-8",
            )
            payload = run_ingest_job(
                layout,
                ["raw/web/bellman.md"],
                analysis_text="## Subjects worth a page\n- Bellman equation\n",
                generation_text=FIXTURE.read_text(encoding="utf-8"),
            )
            self.assertEqual(payload["errors"], [])
            results = payload["results"]
            self.assertEqual(len(results), 1)
            written = {item["path"] for item in results[0]["written"]}
            self.assertIn("wiki/bellman-equation.md", written)
            self.assertTrue(
                (layout.root / "wiki" / "bellman-equation.md").is_file()
            )


try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ProductApiTests(unittest.TestCase):
    def test_use_named_save_and_ingest_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "alpha")
            beta = init_project(repo / "projects" / "beta")
            (beta.raw / "note.md").write_text("# n\n", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                missing = client.post("/api/projects/use", json={"name": "nope"})
                self.assertEqual(missing.status_code, 404)

                used = client.post("/api/projects/use", json={"name": "beta"})
                self.assertEqual(used.status_code, 200)
                self.assertTrue((repo / ".noteagent-project").is_file())
                listed = client.get("/api/projects").json()["projects"]
                active = {item["name"]: item["active"] for item in listed}
                self.assertTrue(active["beta"])
                self.assertFalse(active["alpha"])

                unscoped = client.post("/api/acquire/save", json={"captures": []})
                self.assertEqual(unscoped.status_code, 400)

                save = client.post(
                    "/api/projects/beta/acquire/save",
                    json={
                        "captures": [
                            {
                                "url": "https://example.com/kl",
                                "title": "KL",
                                "markdown": "KL散度是两个概率分布之间的差异度量。" * 20,
                                "html": "<!DOCTYPE html><html><body><article><p>"
                                + ("KL散度是两个概率分布之间的差异度量。" * 20)
                                + "</p></article></body></html>",
                                "captureMethod": "trafilatura-html",
                                "fidelity": "ok",
                                "reasons": [],
                                "httpStatus": 200,
                                "finalUrl": "https://example.com/kl",
                                "charCount": 400,
                            }
                        ]
                    },
                )
                self.assertEqual(save.status_code, 200, save.text)
                written = save.json()["written"]
                self.assertEqual(len(written), 1)
                self.assertTrue((beta.root / written[0]).is_file())

                blocked = client.post(
                    "/api/projects/beta/ingest",
                    json={"paths": ["wiki/overview.md"]},
                )
                self.assertEqual(blocked.status_code, 400)

                with (
                    patch("noteagent.api.app.ensure_llm_configured"),
                    patch(
                        "noteagent.api.ingest.run_ingest_job",
                        return_value={"results": [], "errors": []},
                    ),
                ):
                    streamed = client.post(
                        "/api/projects/beta/ingest",
                        json={"paths": ["raw/note.md"]},
                    )
                self.assertEqual(streamed.status_code, 200, streamed.text)
                self.assertIn("event: done", streamed.text)

    def test_delete_and_restore_through_the_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "alpha")
            (layout.raw / "web").mkdir(parents=True, exist_ok=True)
            (layout.raw / "web" / "a.md").write_text("# a\n", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())

                stats = client.get("/api/projects/alpha/stats")
                self.assertEqual(stats.status_code, 200, stats.text)
                self.assertEqual(stats.json()["sources"], 1)

                removed = client.post(
                    "/api/projects/alpha/delete",
                    json={"confirmName": "alpha"},
                )
                self.assertEqual(removed.status_code, 200, removed.text)
                trash_id = removed.json()["trashId"]

                items = client.get("/api/trash").json()["items"]
                self.assertEqual([item["id"] for item in items], [trash_id])

                restored = client.post(f"/api/trash/{trash_id}/restore")
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertTrue(
                    (repo / "projects" / "alpha" / "raw" / "web" / "a.md").is_file()
                )

    def test_restoring_a_page_puts_it_back_in_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "alpha")
            page = layout.wiki / "topic.md"
            page.write_text("# Topic\n\nRead again.\n", encoding="utf-8")
            rebuild_wiki_index(layout)
            self.assertIn("[[topic]]", layout.wiki_index.read_text(encoding="utf-8"))

            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                removed = client.post(
                    "/api/projects/alpha/file/delete",
                    json={"path": "wiki/topic.md"},
                )
                self.assertEqual(removed.status_code, 200, removed.text)
                # The index is rebuilt from what is on disk, so the page leaves it.
                self.assertNotIn(
                    "[[topic]]", layout.wiki_index.read_text(encoding="utf-8")
                )

                restored = client.post(f"/api/trash/{removed.json()['trashId']}/restore")
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertTrue(page.is_file())
                self.assertIn(
                    "[[topic]]", layout.wiki_index.read_text(encoding="utf-8")
                )

    def test_use_project_helper_matches_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            payload = use_project("demo", repo_root=repo)
            self.assertEqual(payload["name"], "demo")
            data = json.loads((repo / ".noteagent-project").read_text(encoding="utf-8"))
            self.assertIn("projects/demo", data["path"].replace("\\", "/"))

    def test_create_reset_delete_via_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                denied = client.post("/api/projects", json={"name": "use"})
                self.assertEqual(denied.status_code, 400, denied.text)

                created = client.post("/api/projects", json={"name": "vault"})
                self.assertEqual(created.status_code, 200, created.text)
                self.assertTrue((repo / "projects" / "vault" / "raw").is_dir())

                clash = client.post("/api/projects", json={"name": "vault"})
                self.assertEqual(clash.status_code, 409)

                concept = repo / "projects" / "vault" / "wiki" / "concepts" / "x.md"
                concept.parent.mkdir(parents=True, exist_ok=True)
                concept.write_text("# x\n", encoding="utf-8")

                preview = client.post(
                    "/api/projects/vault/reset",
                    json={"dryRun": True},
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(concept.is_file())

                refused = client.post(
                    "/api/projects/vault/reset",
                    json={"confirmName": "nope"},
                )
                self.assertEqual(refused.status_code, 400)
                self.assertTrue(concept.is_file())

                reset = client.post(
                    "/api/projects/vault/reset",
                    json={"confirmName": "vault"},
                )
                self.assertEqual(reset.status_code, 200, reset.text)
                self.assertFalse(concept.exists())

                init_ok = client.post("/api/projects/vault/init")
                self.assertEqual(init_ok.status_code, 200, init_ok.text)

                no_confirm = client.post(
                    "/api/projects/vault/delete",
                    json={"confirmName": "vault-typo"},
                )
                self.assertEqual(no_confirm.status_code, 400)
                self.assertTrue((repo / "projects" / "vault").is_dir())

                deleted = client.post(
                    "/api/projects/vault/delete",
                    json={"confirmName": "vault"},
                )
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertFalse((repo / "projects" / "vault").exists())

    def test_clips_persist_and_topic_gate_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            beta = init_project(repo / "projects" / "beta")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                empty = client.get("/api/projects/beta/clips")
                self.assertEqual(empty.status_code, 200, empty.text)
                self.assertEqual(empty.json()["collections"], [])
                self.assertEqual(empty.json()["decisions"], [])
                self.assertEqual(empty.json()["choices"], [])

                denied = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": [
                            {
                                "id": "c1",
                                "sourceKey": "wiki/concepts/kl.md",
                                "sourceLabel": "no",
                                "blockIds": ["b1"],
                                "blocks": [
                                    {
                                        "id": "b1",
                                        "tag": "p",
                                        "text": "no",
                                        "preview": "no",
                                    }
                                ],
                            }
                        ],
                    },
                )
                self.assertEqual(denied.status_code, 400)

                saved = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": [
                            {
                                "id": "c1",
                                "sourceKey": "raw/web/example.com/kl.html",
                                "sourceLabel": "KL",
                                "blockIds": ["b1"],
                                "blocks": [
                                    {
                                        "id": "b1",
                                        "tag": "p",
                                        "text": "KL 是相对熵。",
                                        "preview": "KL 是相对熵。",
                                    }
                                ],
                            }
                        ],
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertTrue((beta.meta / "clips.json").is_file())
                self.assertFalse((beta.raw / "clips.json").exists())
                got = client.get("/api/projects/beta/clips")
                self.assertEqual(got.json()["intent"], "KL散度")
                self.assertEqual(len(got.json()["collections"]), 1)
                self.assertEqual(got.json()["decisions"], [])

                gated = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": got.json()["collections"],
                        "decisions": [
                            {
                                "slug": "kl散度",
                                "action": "write",
                                "sectionIds": ["s1"],
                            }
                        ],
                    },
                )
                self.assertEqual(gated.status_code, 200, gated.text)
                self.assertEqual(gated.json()["decisions"][0]["action"], "write")

                omitted = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": got.json()["collections"],
                    },
                )
                self.assertEqual(omitted.status_code, 200, omitted.text)
                self.assertEqual(omitted.json()["decisions"][0]["action"], "write")

                cleared = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": got.json()["collections"],
                        "decisions": [],
                    },
                )
                self.assertEqual(cleared.status_code, 200, cleared.text)
                self.assertEqual(cleared.json()["decisions"], [])

                answered = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": got.json()["collections"],
                        "choices": [
                            {
                                "slug": "kl散度",
                                "action": "mention",
                                "sectionIds": ["s1"],
                            }
                        ],
                    },
                )
                self.assertEqual(answered.status_code, 200, answered.text)
                self.assertEqual(answered.json()["choices"][0]["action"], "mention")

                # The board is its own field, so a caller that says nothing about it
                # leaves it as it was rather than clearing it.
                again = client.put(
                    "/api/projects/beta/clips",
                    json={
                        "intent": "KL散度",
                        "collections": got.json()["collections"],
                    },
                )
                self.assertEqual(again.status_code, 200, again.text)
                self.assertEqual(again.json()["choices"][0]["action"], "mention")

                with (
                    patch("noteagent.api.app.ensure_llm_configured"),
                    patch(
                        "noteagent.api.topic_gate.run_topic_map_job",
                        return_value={
                            "intent": "KL散度",
                            "primary": {
                                "name": "KL散度",
                                "slug": "kl-divergence",
                                "wikiPath": "wiki/concepts/kl-divergence.md",
                                "relationToPrimary": "primary",
                                "collectionIds": ["c1"],
                                "blockIds": ["b1"],
                                "evidence": "[b1]",
                                "outline": [],
                            },
                            "alsoInSources": [],
                            "notes": "",
                        },
                    ),
                ):
                    mapped = client.post(
                        "/api/projects/beta/topic-map",
                        json={"intent": "KL散度"},
                    )
                self.assertEqual(mapped.status_code, 200, mapped.text)
                self.assertIn("event: done", mapped.text)

                with (
                    patch("noteagent.api.app.ensure_llm_configured"),
                    patch(
                        "noteagent.api.topic_gate.run_topic_map_job",
                        return_value={
                            "intent": "修订",
                            "primary": {
                                "name": "KL散度",
                                "slug": "kl-divergence",
                                "wikiPath": "wiki/concepts/kl-divergence.md",
                                "relationToPrimary": "primary",
                                "collectionIds": ["c1"],
                                "blockIds": ["b1"],
                                "evidence": "[b1]",
                                "outline": [],
                            },
                            "alsoInSources": [],
                            "notes": "",
                        },
                    ) as scoped_job,
                    patch(
                        "noteagent.api.topic_gate.run_ingest_confirmed_job"
                    ) as ingest_job,
                ):
                    scoped = client.post(
                        "/api/projects/beta/topic-map",
                        json={
                            "intent": "",
                            "sourceIdentity": "raw/web/example.com/kl.html",
                        },
                    )
                self.assertEqual(scoped.status_code, 200, scoped.text)
                self.assertEqual(
                    scoped_job.call_args.kwargs["source_identity"],
                    "raw/web/example.com/kl.html",
                )
                ingest_job.assert_not_called()

                with (
                    patch("noteagent.api.app.ensure_llm_configured"),
                    patch(
                        "noteagent.api.topic_gate.run_ingest_confirmed_job",
                        return_value={"results": [], "errors": []},
                    ),
                ):
                    compiled = client.post(
                        "/api/projects/beta/ingest-confirmed",
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

    def test_ingest_recovery_get_does_not_compile(self) -> None:
        from noteagent.ingest.ingest_recovery import remember_confirmed_job

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            beta = init_project(repo / "projects" / "beta")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                job_id = remember_confirmed_job(
                    beta,
                    intent="KL散度",
                    confirmed=[
                        {
                            "name": "KL散度",
                            "slug": "kl-divergence",
                            "wikiPath": "wiki/concepts/kl-divergence.md",
                            "action": "write",
                            "sectionIds": ["s1"],
                        }
                    ],
                    write_paths=["wiki/concepts/kl-divergence.md"],
                )
                client = TestClient(create_app())
                with (
                    patch("noteagent.ingest.confirmed.ingest_confirmed") as ingest,
                    patch("noteagent.api.topic_gate.run_ingest_confirmed_job") as job,
                ):
                    listed = client.get("/api/projects/beta/ingest-recovery")
                self.assertEqual(listed.status_code, 200, listed.text)
                ingest.assert_not_called()
                job.assert_not_called()
                body = listed.json()
                self.assertEqual(body["openCount"], 1)
                self.assertEqual(body["items"][0]["id"], job_id)
                dropped = client.post(
                    "/api/projects/beta/ingest-recovery/discard",
                    json={"id": job_id},
                )
                self.assertEqual(dropped.status_code, 200, dropped.text)
                self.assertEqual(dropped.json()["openCount"], 0)

    def test_from_rendered_preview_then_save(self) -> None:
        article = (
            "<html><head><title>相对熵</title></head><body><article>"
            "<h1>相对熵</h1><p>"
            + ("KL散度是两个概率分布之间的差异度量。" * 20)
            + "</p></article></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            beta = init_project(repo / "projects" / "beta")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                unscoped = client.post(
                    "/api/acquire/from-rendered",
                    json={"url": "https://example.com/kl", "html": article},
                )
                self.assertEqual(unscoped.status_code, 400)

                missing = client.post(
                    "/api/projects/nope/acquire/from-rendered",
                    json={"url": "https://example.com/kl", "html": article},
                )
                self.assertEqual(missing.status_code, 404)

                preview = client.post(
                    "/api/projects/beta/acquire/from-rendered",
                    json={
                        "url": "https://example.com/kl",
                        "html": article,
                    },
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                payload = preview.json()
                capture = payload["captures"][0]
                self.assertEqual(capture["captureMethod"], "rendered-html")
                self.assertEqual(capture["fidelity"], "ok")
                self.assertIsNone(capture["httpStatus"])
                self.assertTrue(capture["blocks"])
                self.assertFalse((beta.raw / "web").exists())

                saved = client.post(
                    "/api/projects/beta/acquire/save",
                    json={"captures": [capture]},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                written = saved.json()["written"]
                self.assertEqual(len(written), 1)
                disk = (beta.root / written[0]).read_text(encoding="utf-8")
                self.assertIn("rendered-html", disk)
                self.assertIn("data-na-block", disk)

                skipped = client.post(
                    "/api/projects/beta/acquire/from-rendered",
                    json={
                        "url": "file:///tmp/a.html",
                        "html": article,
                    },
                )
                self.assertEqual(skipped.status_code, 200, skipped.text)
                self.assertEqual(skipped.json()["captures"][0]["fidelity"], "failed")


if __name__ == "__main__":
    unittest.main()
