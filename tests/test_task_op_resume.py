"""A finished task op resumes the session at the executor boundary.

The write is not a tool call and not a crashed run: the executor finished an op
the conversation had stopped waiting for, and the session continues from the op's
bounded delta. The transcript records the delivery, so a duplicate delivery or a
restart cannot continue the same op twice.
"""

from __future__ import annotations

import unittest

from agent_fakes import FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.events import DoneEvent
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime, RunRegistry
from noteagent.agent.runtime import TaskOpResume


def _write_delta(**overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "status": "written",
        "page": "wiki/entropy.md",
        "slug": "entropy",
        "title": "Entropy",
        "action": "create",
        "summary": "written wiki/entropy.md (create, 244 bytes)",
        "reason": "",
        "written": ["wiki/entropy.md"],
        "skipped": [],
        "failed": [],
        "added": 3,
        "removed": 0,
    }
    values.update(overrides)
    return values


class TaskOpResumeKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = FakeSessionStore()
        self.sessions.create("s1", "Writing")
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.model = FauxModelClient.text("页面写好了。")
        self.runtime = KernelRuntime(
            model=self.model,
            sessions=self.sessions,
            clock=self.clock,
            ids=self.ids,
        )

    def resume(self, **overrides) -> TaskOpResume:
        values: dict[str, object] = {
            "session_id": "s1",
            "op_id": "op-1",
            "kind": "write",
            "status": "done",
            "rev": 4,
            "result": _write_delta(),
        }
        values.update(overrides)
        return TaskOpResume(**values)  # type: ignore[arg-type]

    def task_ops(self) -> list[dict]:
        return [
            entry
            for entry in self.sessions.entries["s1"]
            if entry.get("type") == "task_op_result"
        ]

    def test_a_finished_op_continues_the_session_with_its_bounded_delta(self) -> None:
        events: list[object] = []

        result = self.runtime.execute(self.resume(), events.append)

        self.assertEqual(result.status, "completed")
        self.assertIsInstance(events[-1], DoneEvent)
        delivered = self.task_ops()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["opId"], "op-1")
        self.assertEqual(delivered[0]["kind"], "write")
        self.assertEqual(delivered[0]["status"], "done")
        self.assertEqual(delivered[0]["rev"], 4)
        self.assertEqual(delivered[0]["result"]["page"], "wiki/entropy.md")

        # The model reads the delta as an observation, then answers on the same
        # session; the answer is what a reconnecting reader sees.
        prompt = "\n".join(
            message.content for message in self.model.requests[0].messages
        )
        self.assertIn("op-1", prompt)
        self.assertIn("task rev 4", prompt)
        self.assertIn("wiki/entropy.md", prompt)
        answers = [
            entry
            for entry in self.sessions.entries["s1"]
            if entry.get("type") == "message"
            and entry.get("role") == "assistant"
        ]
        self.assertEqual(answers[-1]["content"], "页面写好了。")

    def test_a_duplicate_delivery_does_not_resume_twice(self) -> None:
        self.runtime.execute(self.resume(), lambda _event: None)
        calls = len(self.model.requests)

        events: list[object] = []
        result = self.runtime.execute(self.resume(), events.append)

        self.assertEqual(result.status, "completed")
        # The second delivery read the transcript and ended without a model
        # call: an op may continue the conversation once.
        self.assertEqual(len(self.model.requests), calls)
        self.assertIsInstance(events[-1], DoneEvent)
        self.assertEqual(len(self.task_ops()), 1)

    def test_the_delta_shown_to_the_model_is_whitelisted_and_bounded(self) -> None:
        self.runtime.execute(
            self.resume(
                result=_write_delta(
                    effectIds=["effect-secret"],
                    diff="SECRET-DIFF-TEXT",
                    task={"ops": [{"id": "op-1"}]},
                )
            ),
            lambda _event: None,
        )

        prompt = "\n".join(
            message.content for message in self.model.requests[0].messages
        )
        self.assertIn("written wiki/entropy.md", prompt)
        # The effect ids, the page text and the task shape stay in the task and
        # its op log; the model gets the bounded delta and may inspect for more.
        self.assertNotIn("effect-secret", prompt)
        self.assertNotIn("SECRET-DIFF-TEXT", prompt)
        self.assertNotIn('"ops"', prompt)

    def test_a_preclaimed_run_slot_is_released_when_the_resume_ends(self) -> None:
        registry = RunRegistry()
        runtime = KernelRuntime(
            model=self.model,
            sessions=self.sessions,
            clock=self.clock,
            ids=self.ids,
            registry=registry,
        )
        claim = registry.claim("s1")

        runtime.execute(self.resume(claim=claim), lambda _event: None)

        self.assertFalse(registry.is_busy("s1"))

    def test_a_resume_for_a_missing_session_fails_without_writing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.runtime.execute(
                self.resume(session_id="gone"), lambda _event: None
            )
        self.assertEqual(self.model.requests, [])

    def test_a_failed_op_resumes_with_its_failure_as_the_delta(self) -> None:
        scripted = FauxModelClient.text("写失败了。")
        runtime = KernelRuntime(
            model=scripted,
            sessions=self.sessions,
            clock=self.clock,
            ids=self.ids,
        )

        result = runtime.execute(
            self.resume(
                status="failed",
                result={
                    "status": "failed",
                    "errorCode": "op_failed",
                    "error": "the disc is full",
                },
            ),
            lambda _event: None,
        )

        self.assertEqual(result.status, "completed")
        prompt = "\n".join(
            message.content for message in scripted.requests[0].messages
        )
        self.assertIn("failed", prompt)
        self.assertIn("the disc is full", prompt)


if __name__ == "__main__":
    unittest.main()
