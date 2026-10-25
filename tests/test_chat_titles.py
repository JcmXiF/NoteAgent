"""Titles belong to the reader's language, and ranges belong to the core.

Two things the core hands over rather than deciding for the interface:

* what an unnamed conversation is called — the core stores no name at all, and the
  interface says so in whatever language the reader is using;
* how far each chat knob may be turned — sent with the values, so the interface
  never keeps its own copy of the numbers.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.api.chat import limits_payload
from noteagent.chat.limits import (
    default_limits,
    limits_bounds,
    limits_from_dict,
    limits_to_dict,
    load_limits,
)
from noteagent.chat.store import (
    LEGACY_DEFAULT_TITLE,
    append_turn,
    conversation_from_dict,
    conversation_to_dict,
    create_conversation,
    load_conversation,
    list_conversations,
    save_conversation,
)
from noteagent.project.init import init_project


class ConversationTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = init_project(Path(self._tmp.name) / "proj")
        self.limits = load_limits(self.layout)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_new_conversation_has_no_name_at_all(self) -> None:
        created = create_conversation(self.layout)
        # Not a placeholder name: nothing, so the interface can say it in its own words.
        self.assertEqual(created.title, "")
        stored = json.loads(self.layout.chat_path(created.id).read_text(encoding="utf-8"))
        self.assertEqual(stored["title"], "")
        self.assertNotEqual(stored["title"], LEGACY_DEFAULT_TITLE)

    def test_the_name_comes_from_the_first_question(self) -> None:
        created = create_conversation(self.layout)
        asked = append_turn(created, role="user", content="什么是联合熵？", limits=self.limits)
        self.assertEqual(asked.title, "什么是联合熵？")

    def test_a_name_is_plain_text(self) -> None:
        created = create_conversation(self.layout)
        asked = append_turn(
            created,
            role="user",
            content="请解释 [[concepts/joint-entropy]]，并提到 [[互信息|MI]]。",
            limits=self.limits,
        )
        # A title is a line of prose: the brackets are markup, so they are not in it.
        self.assertNotIn("[[", asked.title)
        self.assertNotIn("]]", asked.title)
        self.assertIn("concepts/joint-entropy", asked.title)
        self.assertIn("MI", asked.title)

    def test_runs_of_space_in_a_question_become_one(self) -> None:
        created = create_conversation(self.layout)
        asked = append_turn(
            created, role="user", content="第一行\n\n  第二行", limits=self.limits
        )
        self.assertEqual(asked.title, "第一行 第二行")

    def test_an_answer_does_not_name_the_conversation(self) -> None:
        created = create_conversation(self.layout)
        answered = append_turn(
            created, role="assistant", content="联合熵是……", limits=self.limits
        )
        self.assertEqual(answered.title, "")

    def test_a_name_the_reader_gave_is_kept(self) -> None:
        created = create_conversation(self.layout, "熵的讨论")
        self.assertEqual(created.title, "熵的讨论")
        asked = append_turn(created, role="user", content="继续", limits=self.limits)
        # Asking something does not overwrite a name that is already there.
        self.assertEqual(asked.title, "熵的讨论")

    def test_a_file_from_before_reads_as_unnamed(self) -> None:
        # The old files stored the placeholder itself, and it was also the sentinel
        # for "not named yet", so it means the same as an empty title.
        raw = {
            "version": 1,
            "id": "c-legacy",
            "title": LEGACY_DEFAULT_TITLE,
            "updatedAt": "2026-01-01T00:00:00Z",
            "messages": [
                {
                    "id": "m-1",
                    "role": "user",
                    "content": "什么是条件熵？",
                    "createdAt": "2026-01-01T00:00:00Z",
                }
            ],
        }
        self.layout.chats_dir.mkdir(parents=True, exist_ok=True)
        self.layout.chat_path("c-legacy").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )

        loaded = load_conversation(self.layout, "c-legacy")
        # Name taken from what was asked, rather than from the placeholder.
        self.assertEqual(loaded.title, "什么是条件熵？")
        self.assertEqual(list_conversations(self.layout)[0].title, "什么是条件熵？")

    def test_a_named_file_from_before_keeps_its_name(self) -> None:
        parsed = conversation_from_dict(
            {"id": "c-named", "title": "我的笔记", "messages": []},
            conversation_id="c-named",
        )
        self.assertEqual(parsed.title, "我的笔记")

    def test_an_empty_name_round_trips_as_empty(self) -> None:
        created = create_conversation(self.layout)
        again = conversation_from_dict(
            conversation_to_dict(created), conversation_id=created.id
        )
        self.assertEqual(again.title, "")
        saved = save_conversation(self.layout, again, limits=self.limits)
        self.assertEqual(saved.title, "")

    def test_a_question_that_is_only_a_link_still_names_it(self) -> None:
        created = create_conversation(self.layout)
        asked = append_turn(
            created, role="user", content="[[concepts/entropy]]", limits=self.limits
        )
        self.assertEqual(asked.title, "concepts/entropy")


class LimitsBoundsTests(unittest.TestCase):
    def test_every_knob_has_a_range_in_the_payload(self) -> None:
        payload = limits_payload(default_limits())
        bounds = payload["bounds"]
        values = {key for key in payload if key != "bounds"}
        self.assertEqual(set(bounds), values)
        for key, span in bounds.items():
            self.assertLess(span["min"], span["max"], key)
            # The range has to accept the value the core itself starts from.
            self.assertGreaterEqual(payload[key], span["min"], key)
            self.assertLessEqual(payload[key], span["max"], key)

    def test_the_published_ranges_are_the_ones_that_are_enforced(self) -> None:
        # A copy of these numbers anywhere else is a copy that can drift; this checks
        # that what is published is what the core actually accepts.
        base = limits_to_dict(default_limits())
        for key, span in limits_bounds().items():
            with self.subTest(key):
                accepted_low = dict(base, **{key: span["min"]})
                accepted_high = dict(base, **{key: span["max"]})
                self.assertTrue(limits_from_dict(accepted_low))
                self.assertTrue(limits_from_dict(accepted_high))

                refused = (
                    dict(base, **{key: span["min"] - 1}),
                    dict(base, **{key: span["max"] + 1}),
                )
                for payload in refused:
                    with self.assertRaises(ValueError, msg=key):
                        limits_from_dict(payload)


if __name__ == "__main__":
    unittest.main()
