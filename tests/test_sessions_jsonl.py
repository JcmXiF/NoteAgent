"""A session is one append-only file, and reading it never changes it."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore, SessionFormatError

NOW = "2026-11-10T09:00:00Z"


def _header(session_id: str = "s1", version: int = 1) -> dict[str, object]:
    return {
        "v": version,
        "type": "session",
        "id": session_id,
        "createdAt": NOW,
    }


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout,
            clock=FixedClock(datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)),
            ids=SequentialIds(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_lines(self, *lines: str) -> Path:
        self.layout.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self.layout.session_path("s1")
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return path


class SessionRoundTripTests(_StoreCase):
    def test_a_session_round_trips_through_jsonl(self) -> None:
        created = self.store.create("s1", "Entropy")

        self.assertEqual(created.id, "s1")
        self.assertEqual(created.title, "Entropy")
        self.assertEqual(created.version, 1)
        self.assertEqual([entry["type"] for entry in created.entries], ["session_info"])

        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "什么是熵？"}
        )
        loaded = self.store.load("s1")

        self.assertEqual(loaded.id, "s1")
        self.assertEqual(loaded.title, "Entropy")
        entry = loaded.entries[-1]
        self.assertEqual(entry["type"], "message")
        self.assertEqual(entry["role"], "user")
        self.assertEqual(entry["content"], "什么是熵？")
        self.assertEqual(entry["v"], 1)
        self.assertEqual(entry["parentId"], created.entries[-1]["id"])
        self.assertTrue(str(entry["id"]).startswith("e-"))
        self.assertEqual(entry["ts"], NOW)

    def test_an_unnamed_session_takes_its_first_question(self) -> None:
        self.store.create("s1")
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "什么是熵？"}
        )

        self.assertEqual(self.store.load("s1").title, "什么是熵？")

    def test_each_entry_follows_the_one_before_it(self) -> None:
        self.store.create("s1")
        self.store.append("s1", {"type": "message", "role": "user", "content": "a"})
        first = self.store.load("s1").entries[0]
        self.store.append("s1", {"type": "message", "role": "assistant", "content": "b"})
        second = self.store.load("s1").entries[1]
        self.assertEqual(second["parentId"], first["id"])

    def test_create_refuses_a_session_that_already_exists(self) -> None:
        self.store.create("s1")
        with self.assertRaises(FileExistsError):
            self.store.create("s1")

    def test_rename_appends_a_session_info_entry(self) -> None:
        self.store.create("s1")
        self.store.rename("s1", "  New name  ")
        loaded = self.store.load("s1")
        self.assertEqual(loaded.title, "New name")
        self.assertEqual(loaded.entries[-1]["type"], "session_info")
        self.assertEqual(loaded.entries[-1]["title"], "New name")

    def test_delete_removes_the_file(self) -> None:
        self.store.create("s1")
        path = self.layout.session_path("s1")
        self.store.delete("s1")
        self.assertFalse(path.exists())
        with self.assertRaises(FileNotFoundError):
            self.store.load("s1")

    def test_the_index_is_written_and_rebuilt(self) -> None:
        self.store.create("s1", "First")
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "q"}
        )

        listed = self.store.list()

        index = self.layout.sessions_dir / "index.json"
        self.assertTrue(index.is_file())
        data = json.loads(index.read_text(encoding="utf-8"))
        self.assertEqual(data["v"], 1)
        self.assertEqual([item["id"] for item in data["sessions"]], ["s1"])
        self.assertEqual(data["sessions"][0]["messageCount"], 1)
        index.unlink()

        again = self.store.list()

        self.assertEqual([item.id for item in again], ["s1"])
        self.assertTrue(index.is_file())
        self.assertEqual(listed, again)

    def test_a_corrupt_index_is_rebuilt(self) -> None:
        self.store.create("s1", "First")
        index = self.layout.sessions_dir / "index.json"
        self.store.list()
        index.write_text("{not json", encoding="utf-8")

        self.assertEqual([item.id for item in self.store.list()], ["s1"])
        self.assertEqual(
            json.loads(index.read_text(encoding="utf-8"))["sessions"][0]["id"],
            "s1",
        )

    def test_a_file_that_appeared_since_the_index_is_listed(self) -> None:
        self.store.create("s1")
        self.store.list()
        path = self.layout.sessions_dir / "s2.jsonl"
        path.write_text(json.dumps(_header("s2")) + "\n", encoding="utf-8")

        self.assertEqual(
            {item.id for item in self.store.list()}, {"s1", "s2"}
        )

    def test_delete_updates_the_index(self) -> None:
        self.store.create("s1")
        self.store.create("s2")
        self.store.list()

        self.store.delete("s1")

        self.assertEqual([item.id for item in self.store.list()], ["s2"])
        index = json.loads(
            (self.layout.sessions_dir / "index.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["id"] for item in index["sessions"]], ["s2"])

    def test_the_list_counts_visible_messages(self) -> None:
        self.store.create("s2", "Second")
        self.store.append("s2", {"type": "message", "role": "user", "content": "q"})
        self.store.append("s2", {"type": "message", "role": "assistant", "content": "a"})
        self.store.append("s2", {"type": "system_section", "sections": {"identity": "x"}})
        self.store.create("s1", "First")

        listed = {item.id: item for item in self.store.list()}

        self.assertEqual(listed["s2"].title, "Second")
        self.assertEqual(listed["s2"].message_count, 2)
        self.assertEqual(listed["s2"].updated_at, NOW)
        self.assertEqual(listed["s1"].message_count, 0)


class SessionReadingTests(_StoreCase):
    def test_a_trailing_partial_line_is_dropped(self) -> None:
        self.write_lines(
            json.dumps(_header()),
            json.dumps(
                {
                    "v": 1,
                    "id": "e1",
                    "parentId": "s1",
                    "ts": NOW,
                    "type": "message",
                    "role": "user",
                    "content": "kept",
                }
            ),
        )
        path = self.layout.session_path("s1")
        path.write_text(
            path.read_text(encoding="utf-8") + '{"v":1,"id":"e2"',
            encoding="utf-8",
        )

        loaded = self.store.load("s1")

        self.assertEqual([entry["id"] for entry in loaded.entries], ["e1"])

    def test_a_partial_multibyte_tail_is_dropped_before_decoding(self) -> None:
        path = self.write_lines(
            json.dumps(_header()),
            json.dumps(
                {
                    "v": 1,
                    "id": "e1",
                    "parentId": "s1",
                    "ts": NOW,
                    "type": "message",
                    "role": "user",
                    "content": "kept",
                }
            ),
        )
        with path.open("ab") as handle:
            handle.write(b'{"type":"message","content":"\xe4\xb8')

        loaded = self.store.load("s1")

        self.assertEqual([entry["id"] for entry in loaded.entries], ["e1"])

    def test_append_repairs_a_partial_tail_before_writing(self) -> None:
        self.write_lines(
            json.dumps(_header()),
            json.dumps(
                {
                    "v": 1,
                    "id": "e1",
                    "parentId": "s1",
                    "ts": NOW,
                    "type": "message",
                    "role": "user",
                    "content": "kept",
                }
            ),
        )
        path = self.layout.session_path("s1")
        path.write_text(
            path.read_text(encoding="utf-8") + '{"v":1,"id":"e2"',
            encoding="utf-8",
        )

        self.store.append("s1", {"type": "message", "role": "assistant", "content": "new"})

        loaded = self.store.load("s1")
        self.assertEqual([entry.get("content") for entry in loaded.entries], ["kept", "new"])

    def test_an_unknown_entry_type_is_kept_and_warned(self) -> None:
        self.write_lines(
            json.dumps(_header()),
            json.dumps(
                {
                    "v": 1,
                    "id": "e1",
                    "ts": NOW,
                    "type": "future_thing",
                }
            ),
        )

        with self.assertLogs("noteagent.sessions.jsonl", level="WARNING") as logs:
            loaded = self.store.load("s1")

        self.assertEqual(len(loaded.entries), 1)
        self.assertIn("future_thing", "\n".join(logs.output))

    def test_a_file_without_a_header_is_refused(self) -> None:
        self.write_lines(json.dumps({"type": "message", "role": "user", "content": "x"}))

        with self.assertRaises(SessionFormatError):
            self.store.load("s1")

    def test_an_unknown_version_is_refused(self) -> None:
        self.write_lines(json.dumps(_header(version=99)))

        with self.assertRaises(SessionFormatError):
            self.store.load("s1")

    def test_an_entry_with_a_bad_id_is_refused(self) -> None:
        self.write_lines(
            json.dumps(_header()),
            json.dumps(
                {
                    "v": 1,
                    "id": 5,
                    "ts": NOW,
                    "type": "message",
                    "role": "user",
                    "content": "x",
                }
            ),
        )

        with self.assertRaises(SessionFormatError):
            self.store.load("s1")

    def test_a_middle_line_that_is_not_json_is_refused(self) -> None:
        self.write_lines(
            json.dumps(_header()),
            json.dumps({"v": 1, "id": "e1", "ts": NOW, "type": "message", "role": "user", "content": "a"}),
            "{not json",
            json.dumps({"v": 1, "id": "e2", "ts": NOW, "type": "message", "role": "assistant", "content": "b"}),
        )

        with self.assertRaises(SessionFormatError):
            self.store.load("s1")

    def test_a_session_id_must_be_a_safe_name(self) -> None:
        for name in ("", "../escape", "a/b", "index", "x" * 81):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.load(name)

    def test_loading_a_missing_session_is_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.store.load("missing")


if __name__ == "__main__":
    unittest.main()
