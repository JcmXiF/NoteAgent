"""The bounded framing loop: openings, expansions, a cap and a budget."""

from __future__ import annotations

import unittest

from noteagent.pipeline.frame_model import MAX_FRAMES, ModelFraming

LONG = "熵是不确定性的度量，它衡量一个随机变量的平均信息量。"

BLOCKS = [
    {"id": "b1", "tag": "h2", "text": "定义", "preview": ""},
    {"id": "b2", "tag": "p", "text": LONG, "preview": ""},
    {"id": "b3", "tag": "p", "text": "香农把它引入信息论。", "preview": ""},
    {"id": "b4", "tag": "p", "text": "它是可加的。", "preview": ""},
]


def _model(replies: list[str], **options) -> tuple[ModelFraming, list]:
    calls: list[list[tuple[str, str]]] = []

    def complete(messages):
        calls.append(list(messages))
        return replies[min(len(calls) - 1, len(replies) - 1)]

    return ModelFraming(ask=complete, **options), calls


def _frames(model: ModelFraming):
    return model.frames(
        material="raw/web/entropy-a1.html",
        title="熵",
        kind="html",
        blocks=BLOCKS,
    )


class ModelFramingTests(unittest.TestCase):
    def test_the_first_request_shows_openings_not_full_text(self) -> None:
        model, calls = _model(['{"frames": [["b1", "b2"]]}'])

        result = _frames(model)

        self.assertEqual(result.frames, (("b1", "b2"),))
        opening = calls[0][1][1]
        self.assertIn("b2", opening)
        self.assertNotIn(LONG, opening)
        self.assertIn(LONG[:19], opening)

    def test_an_expansion_returns_the_full_text(self) -> None:
        model, calls = _model(
            ['{"expand": ["b2"]}', '{"frames": [["b2"]]}']
        )

        result = _frames(model)

        self.assertEqual(result.calls, 2)
        self.assertEqual(result.chars, len(LONG))
        self.assertEqual(result.frames, (("b2",),))
        self.assertIn(LONG, calls[1][-1][1])

    def test_frames_beyond_eight_are_omitted(self) -> None:
        frames = ",".join(['["b1"]', '["b2"]'] * 5)
        model, _calls = _model(['{"frames": [' + frames + "]}"])

        result = _frames(model)

        self.assertEqual(len(result.frames), MAX_FRAMES)
        self.assertEqual(result.omitted, 2)

    def test_a_round_budget_still_asks_for_frames(self) -> None:
        model, calls = _model(
            [
                '{"expand": ["b2"]}',
                '{"expand": ["b3"]}',
                '{"frames": [["b1"]]}',
            ],
            rounds=2,
        )

        result = _frames(model)

        self.assertFalse(result.failed)
        self.assertEqual(result.frames, (("b1",),))
        self.assertEqual(result.calls, 3)
        self.assertEqual(
            result.chars, len(LONG) + len(BLOCKS[2]["text"])
        )
        self.assertIn("budget", calls[-1][-1][1].lower())

    def test_a_character_budget_still_asks_for_frames(self) -> None:
        model, _calls = _model(
            ['{"expand": ["b2"]}', '{"frames": [["b1"]]}'], chars=5
        )

        result = _frames(model)

        self.assertFalse(result.failed)
        self.assertEqual(result.frames, (("b1",),))
        self.assertEqual(result.calls, 2)
        self.assertEqual(result.chars, 0)

    def test_a_reply_that_is_not_json_is_a_fallback(self) -> None:
        model, _calls = _model(["I think these passages belong together."])

        result = _frames(model)

        self.assertTrue(result.failed)
        self.assertEqual(result.frames, ())

    def test_unknown_ids_are_dropped(self) -> None:
        model, _calls = _model(['{"frames": [["nope", "b1"]]}'])

        result = _frames(model)

        self.assertEqual(result.frames, (("b1",),))


if __name__ == "__main__":
    unittest.main()
