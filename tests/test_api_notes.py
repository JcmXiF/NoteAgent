from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


@contextmanager
def served(repo: Path):
    with patch.object(projects_mod, "REPO_ROOT", repo):
        yield TestClient(create_app())


def wiki_bytes(layout) -> dict[str, bytes]:
    """Everything the agent owns, so a test can say it came through untouched."""
    found: dict[str, bytes] = {}
    for root in (layout.wiki, layout.raw):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                found[path.relative_to(layout.root).as_posix()] = path.read_bytes()
    return found


@unittest.skipIf(TestClient is None, "fastapi not installed")
class NoteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.layout = init_project(self.repo / "projects" / "alpha")
        page = self.layout.wiki / "concepts" / "x.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# X\n\n正文。\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def create(self, client, **overrides):
        body = {"page": "wiki/concepts/x.md", "body": "这是笔记。\n"}
        body.update(overrides)
        return client.post("/api/projects/alpha/notes", json=body)

    def test_a_note_is_created_listed_read_changed_and_removed(self) -> None:
        before = wiki_bytes(self.layout)
        with served(self.repo) as client:
            created = self.create(client)
            self.assertEqual(created.status_code, 200, created.text)
            note = created.json()
            self.assertEqual(note["page"], "wiki/concepts/x.md")
            self.assertEqual(note["body"], "这是笔记。\n")
            self.assertEqual(note["anchor"], "")
            self.assertTrue(note["created"] and note["updated"])
            self.assertTrue(
                (
                    self.layout.notes
                    / "concepts"
                    / "x"
                    / f"{note['id']}.md"
                ).is_file()
            )

            listed = client.get(
                "/api/projects/alpha/notes", params={"page": "wiki/concepts/x.md"}
            )
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([item["id"] for item in listed.json()["notes"]], [note["id"]])
            self.assertEqual(listed.json()["page"], "wiki/concepts/x.md")

            fetched = client.get(f"/api/projects/alpha/notes/{note['id']}")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json(), note)

            changed = client.put(
                f"/api/projects/alpha/notes/{note['id']}",
                json={"body": "改过。\n", "anchor": "前言"},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["body"], "改过。\n")
            self.assertEqual(changed.json()["anchor"], "前言")
            self.assertEqual(changed.json()["created"], note["created"])

            removed = client.delete(f"/api/projects/alpha/notes/{note['id']}")
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json(), {"deleted": note["id"]})
            self.assertEqual(
                client.get(f"/api/projects/alpha/notes/{note['id']}").status_code, 404
            )

        self.assertEqual(wiki_bytes(self.layout), before)
        self.assertFalse((self.layout.notes / "concepts").exists())

    def test_listing_every_note_needs_no_page(self) -> None:
        with served(self.repo) as client:
            first = self.create(client).json()
            second = self.create(client, body="第二条。\n").json()
            listed = client.get("/api/projects/alpha/notes")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["page"], "")
            self.assertEqual(
                [item["id"] for item in listed.json()["notes"]], [first["id"], second["id"]]
            )

    def test_listing_a_page_with_no_notes_is_empty_not_an_error(self) -> None:
        with served(self.repo) as client:
            listed = client.get(
                "/api/projects/alpha/notes", params={"page": "wiki/concepts/ghost.md"}
            )
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["notes"], [])

    def test_an_empty_note_is_refused_and_writes_nothing(self) -> None:
        with served(self.repo) as client:
            for body in ("", "   ", "\n\n"):
                with self.subTest(body=body):
                    refused = self.create(client, body=body)
                    self.assertEqual(refused.status_code, 400, refused.text)
            self.assertFalse(self.layout.notes.exists())

    def test_clearing_an_existing_note_is_refused_rather_than_silently_a_delete(self) -> None:
        with served(self.repo) as client:
            note = self.create(client).json()
            refused = client.put(
                f"/api/projects/alpha/notes/{note['id']}", json={"body": ""}
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertEqual(
                client.get(f"/api/projects/alpha/notes/{note['id']}").json()["body"],
                note["body"],
            )

    def test_a_note_about_a_page_that_is_not_there_is_refused(self) -> None:
        with served(self.repo) as client:
            missing = self.create(client, page="wiki/concepts/ghost.md")
            self.assertEqual(missing.status_code, 404, missing.text)
            self.assertFalse(self.layout.notes.exists())

    def test_a_page_outside_the_wiki_is_refused(self) -> None:
        with served(self.repo) as client:
            outside = self.create(client, page="raw/x.md")
            self.assertEqual(outside.status_code, 400, outside.text)
            escape = self.create(client, page="wiki/../raw/x.md")
            self.assertEqual(escape.status_code, 400, escape.text)
            bad_page_list = client.get(
                "/api/projects/alpha/notes", params={"page": "raw/x.md"}
            )
            self.assertEqual(bad_page_list.status_code, 400, bad_page_list.text)

    def test_a_note_id_that_is_not_a_name_is_refused(self) -> None:
        with served(self.repo) as client:
            # A name shaped like a path never arrives: the path is normalised
            # before routing, and what a name may contain is checked where it
            # would become one (see the store's own tests for that side).
            for note_id in ("not-an-id", "20261010-195700", "20261010-195700-ABCD"):
                with self.subTest(note_id=note_id):
                    self.assertEqual(
                        client.get(f"/api/projects/alpha/notes/{note_id}").status_code, 400
                    )

    def test_absent_notes_are_not_found(self) -> None:
        with served(self.repo) as client:
            gone = "20261010-195700-ab12"
            self.assertEqual(client.get(f"/api/projects/alpha/notes/{gone}").status_code, 404)
            self.assertEqual(
                client.put(
                    f"/api/projects/alpha/notes/{gone}", json={"body": "笔记\n"}
                ).status_code,
                404,
            )
            self.assertEqual(
                client.delete(f"/api/projects/alpha/notes/{gone}").status_code, 404
            )

    def test_a_change_keeps_the_fields_it_was_not_given(self) -> None:
        with served(self.repo) as client:
            note = self.create(client, anchor="前言", quote="原话").json()
            changed = client.put(
                f"/api/projects/alpha/notes/{note['id']}", json={"body": "改过。\n"}
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["anchor"], "前言")
            self.assertEqual(changed.json()["quote"], "原话")
            self.assertEqual(changed.json()["page"], "wiki/concepts/x.md")

    def test_the_page_a_note_keeps_about_is_not_the_clients_to_change(self) -> None:
        with served(self.repo) as client:
            other = self.layout.wiki / "concepts" / "y.md"
            other.write_text("# Y\n\n正文。\n", encoding="utf-8")
            note = self.create(client).json()
            changed = client.put(
                f"/api/projects/alpha/notes/{note['id']}",
                json={"body": "改过。\n", "page": "wiki/concepts/y.md"},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["page"], "wiki/concepts/x.md")
            self.assertEqual(
                [item["id"] for item in client.get(
                    "/api/projects/alpha/notes", params={"page": "wiki/concepts/y.md"}
                ).json()["notes"]],
                [],
            )

    def test_a_note_whose_page_is_gone_still_reads(self) -> None:
        with served(self.repo) as client:
            note = self.create(client).json()
            (self.layout.wiki / "concepts" / "x.md").unlink()
            fetched = client.get(f"/api/projects/alpha/notes/{note['id']}")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json()["page"], "wiki/concepts/x.md")

    def test_notes_of_two_pages_do_not_mix(self) -> None:
        with served(self.repo) as client:
            other = self.layout.wiki / "concepts" / "y.md"
            other.write_text("# Y\n\n正文。\n", encoding="utf-8")
            mine = self.create(client).json()
            theirs = self.create(client, page="wiki/concepts/y.md").json()
            self.assertEqual(
                [
                    item["id"]
                    for item in client.get(
                        "/api/projects/alpha/notes",
                        params={"page": "wiki/concepts/y.md"},
                    ).json()["notes"]
                ],
                [theirs["id"]],
            )
            self.assertEqual(
                [
                    item["id"]
                    for item in client.get(
                        "/api/projects/alpha/notes",
                        params={"page": "wiki/concepts/x.md"},
                    ).json()["notes"]
                ],
                [mine["id"]],
            )
            self.assertEqual(len(client.get("/api/projects/alpha/notes").json()["notes"]), 2)


if __name__ == "__main__":
    unittest.main()
