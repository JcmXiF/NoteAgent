"""The Deep Search stream says what the blocking call does, and can be stopped.

The two are asked for the same action with the model replaced by an injected
script, so what is compared is the transport and not the model: a round that has
been streamed must land on the same session as one that was not.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from noteagent.api.deep_search import iter_act_sse, run_act_job
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled
from noteagent.wiki.deep_search import act_session, load_session


def _events(blob: bytes) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in blob.decode("utf-8").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        kind = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if kind and data:
            out.append((kind, json.loads("\n".join(data))))
    return out


def _script(slug: str, title: str) -> dict:
    """What the model would have answered, as the shape the parser accepts."""
    return {
        "pages": [
            {
                "slug": slug,
                "title": title,
                "action": "add",
                "reason": "probe",
                "accepted": True,
            }
        ],
        "edges": [],
    }


class DeepSearchStreamTests(unittest.TestCase):
    def _project(self, tmp: str) -> object:
        layout = init_project(Path(tmp) / "proj")
        # A finished sentence to name as the seed, so `start` has something real.
        (layout.wiki / "concepts").mkdir(parents=True, exist_ok=True)
        (layout.wiki / "concepts" / "seed.md").write_text(
            "# Seed\n\n正文足够长，可以作为深搜的种子。\n", encoding="utf-8"
        )
        return layout

    def test_stream_and_blocking_call_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            body = {
                "action": "start",
                "mode": "explore",
                "step": 2,
                "seed": {"kind": "page", "path": "wiki/concepts/seed.md"},
            }
            blocking = act_session(layout, dict(body), use_llm=False)
            self.assertEqual(blocking["step"], 2)

            # The streamed call starts from the beginning again, so clear first.
            act_session(layout, {"action": "clear"})
            streamed_events = _events(
                b"".join(
                    iter_act_sse(
                        layout,
                        dict(body),
                        script=_script("concepts/候选", "候选"),
                        use_llm=False,
                    )
                )
            )
            kinds = [kind for kind, _ in streamed_events]
            self.assertIn("done", kinds)
            self.assertNotIn("error", kinds)

            done = next(data for kind, data in streamed_events if kind == "done")
            self.assertEqual(done["step"], 2)

    def test_stream_reports_which_step_it_is_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            act_session(
                layout,
                {
                    "action": "start",
                    "mode": "explore",
                    "step": 2,
                    "seed": {"kind": "page", "path": "wiki/concepts/seed.md"},
                },
                use_llm=False,
            )
            events = _events(
                b"".join(
                    iter_act_sse(
                        layout,
                        {"action": "proposeWhitelist"},
                        script=_script("concepts/候选", "候选"),
                        use_llm=False,
                    )
                )
            )
            status = [data for kind, data in events if kind == "status"]
            self.assertEqual(status[0]["stepId"], "whitelist")

    def test_a_bad_action_is_an_error_event_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            events = _events(
                b"".join(iter_act_sse(layout, {"action": "notAnAction"}, use_llm=False))
            )
            errors = [data for kind, data in events if kind == "error"]
            self.assertEqual(len(errors), 1)
            self.assertIn("action must be", errors[0]["detail"])
            self.assertFalse(any(kind == "done" for kind, _ in events))

    def test_the_job_reports_a_step_for_the_action_it_was_given(self) -> None:
        # `run_act_job` is what a caller uses when it wants the same reporting
        # without a stream.
        seen: list[tuple[str, str | None]] = []

        def on_status(message: str, step_id: str | None = None, **_: object) -> None:
            seen.append((message, step_id))

        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            run_act_job(
                layout,
                {
                    "action": "start",
                    "mode": "explore",
                    "step": 2,
                    "seed": {"kind": "page", "path": "wiki/concepts/seed.md"},
                },
                use_llm=False,
                on_status=on_status,
            )
        self.assertEqual(seen[0][1], "start")

    def test_stopping_before_the_model_call_leaves_the_session_alone(self) -> None:
        # A caller that has gone away must not have its round advanced: the check
        # happens before the work, so the session is as it was.
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            act_session(
                layout,
                {
                    "action": "start",
                    "mode": "explore",
                    "step": 2,
                    "seed": {"kind": "page", "path": "wiki/concepts/seed.md"},
                },
                use_llm=False,
            )
            before = load_session(layout)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                run_act_job(
                    layout,
                    {"action": "proposeWhitelist"},
                    use_llm=True,
                    cancel=cancel,
                )
            after = load_session(layout)
            self.assertEqual(after.step, before.step)
            self.assertEqual(
                [item.slug for item in after.whitelist],
                [item.slug for item in before.whitelist],
            )


if __name__ == "__main__":
    unittest.main()
