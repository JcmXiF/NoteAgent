"""A run's stream going away: the run pauses and the partial is not committed."""

from __future__ import annotations

import time
import unittest

from agent_fakes import FakeSessionStore, FixedClock, GatedModel, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.runtime import RunResume, UserInput
from noteagent.api.runtime_sse import iter_run_sse
from noteagent.api.sessions import session_payload


def _wait_until_idle(runtime: KernelRuntime, session_id: str) -> None:
    deadline = time.monotonic() + 5
    while runtime.is_busy(session_id) and time.monotonic() < deadline:
        time.sleep(0.01)


class RuntimeSseTests(unittest.TestCase):
    def test_a_disconnected_stream_pauses_and_discards_the_partial(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        model = GatedModel("部分")
        runtime = KernelRuntime(
            model=model,
            sessions=store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )

        blocks: list[bytes] = []
        stream = iter_run_sse(runtime, UserInput("s1", "问题"), store=store)
        try:
            for block in stream:
                blocks.append(block)
                if b"event: token" in block:
                    break
        finally:
            # What the response does when the reader is gone.
            stream.close()
        model.release.set()

        _wait_until_idle(runtime, "s1")
        self.assertFalse(runtime.is_busy("s1"), "the run kept going after the close")

        token = next(block for block in blocks if b"event: token" in block)
        self.assertIn("部分", token.decode("utf-8"))
        view = session_payload(store, "s1")
        self.assertEqual([item["role"] for item in view["messages"]], ["user"])
        self.assertEqual(view["state"]["interrupted"]["reason"], "client_disconnect")

    def test_a_stream_that_reached_done_is_finished_not_interrupted(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        runtime = KernelRuntime(
            model=FauxModelClient.text("答案"),
            sessions=store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )

        text = b"".join(
            iter_run_sse(runtime, UserInput("s1", "问题"), store=store)
        ).decode("utf-8")

        self.assertIn("event: done", text)
        view = session_payload(store, "s1")
        self.assertNotIn("interrupted", view["state"])
        self.assertEqual(view["messages"][-1]["content"], "答案")

    def test_a_paused_stream_can_be_resumed(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        model = GatedModel("部分")
        runtime = KernelRuntime(
            model=model,
            sessions=store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        stream = iter_run_sse(runtime, UserInput("s1", "问题"), store=store)
        try:
            for block in stream:
                if b"event: token" in block:
                    break
        finally:
            stream.close()
        model.release.set()
        _wait_until_idle(runtime, "s1")
        run_id = session_payload(store, "s1")["state"]["interrupted"]["runId"]

        resumed = KernelRuntime(
            model=FauxModelClient.text("继续回答"),
            sessions=store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        text = b"".join(
            iter_run_sse(resumed, RunResume("s1", run_id), store=store)
        ).decode("utf-8")

        self.assertIn("继续回答", text)
        view = session_payload(store, "s1")
        self.assertNotIn("interrupted", view["state"])
        self.assertEqual(view["messages"][-1]["content"], "继续回答")

    def test_a_busy_refusal_does_not_cancel_the_run_it_could_not_start(self) -> None:
        # The second connection never owned this run; closing its stream must not
        # reach the run that does own it.
        store = FakeSessionStore()
        store.create("s1")
        model = GatedModel("部分", rest="剩余")
        runtime = KernelRuntime(
            model=model,
            sessions=store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        first = iter_run_sse(runtime, UserInput("s1", "第一问"), store=store)
        try:
            for block in first:
                if b"event: token" in block:
                    break
            second = iter_run_sse(runtime, UserInput("s1", "第二问"), store=store)
            second_text = b"".join(second)
            self.assertIn(b"event: error", second_text)
            self.assertIn(b'"code": "busy"', second_text)

            # The first run is still the one in charge, so it finishes its answer.
            model.release.set()
            _wait_until_idle(runtime, "s1")
            self.assertFalse(runtime.is_busy("s1"))
            messages = session_payload(store, "s1")["messages"]
            self.assertEqual(messages[-1]["content"], "部分剩余")
            self.assertEqual(messages[-1]["stopReason"], "stop")
        finally:
            first.close()
            model.release.set()


if __name__ == "__main__":
    unittest.main()
