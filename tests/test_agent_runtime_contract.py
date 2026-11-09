"""What a run is asked to do, and what it reports while doing it."""

from __future__ import annotations

import unittest

from agent_fakes import FakeModelClient, FakeRuntime
from noteagent.agent.events import (
    DoneEvent,
    ErrorEvent,
    JobResultEvent,
    ObservationEvent,
    StatusEvent,
    TokenEvent,
    WaitEvent,
    is_terminal,
)
from noteagent.agent.jobs import JobArtifact, JobResult
from noteagent.agent.runtime import AgentRuntime, OnBusy, Resume, Retry, UserInput
from noteagent.agent.wait import WaitState


class RunRequestTests(unittest.TestCase):
    def test_a_question_is_rejected_when_the_session_is_already_running(self) -> None:
        # A second question in the same conversation would be a second model loop.
        self.assertEqual(UserInput("s1", "what is entropy?").on_busy, OnBusy.REJECT)

    def test_a_resume_waits_for_the_running_turn(self) -> None:
        # A finished job is an event, not a person: it may not be dropped, so it
        # queues behind whatever the session is doing.
        self.assertEqual(Resume("s1", "w1").on_busy, OnBusy.QUEUE)

    def test_a_retry_is_rejected_unless_it_says_otherwise(self) -> None:
        self.assertEqual(Retry("s1").on_busy, OnBusy.REJECT)

    def test_a_request_can_choose_the_other_policy(self) -> None:
        self.assertEqual(UserInput("s1", "again", on_busy=OnBusy.QUEUE).on_busy, OnBusy.QUEUE)
        self.assertEqual(Resume("s1", "w1", on_busy=OnBusy.REJECT).on_busy, OnBusy.REJECT)


class EventPayloadTests(unittest.TestCase):
    def test_status_names_the_step_only_when_it_has_one(self) -> None:
        self.assertEqual(StatusEvent("thinking").payload(), {"message": "thinking"})
        self.assertEqual(
            StatusEvent("opening pages", "tool").payload(),
            {"message": "opening pages", "stepId": "tool"},
        )

    def test_a_token_carries_a_reset_only_when_it_takes_words_back(self) -> None:
        self.assertEqual(TokenEvent("entropy").payload(), {"text": "entropy"})
        self.assertEqual(
            TokenEvent("", reset=True).payload(),
            {"text": "", "reset": True},
        )

    def test_an_observation_is_the_card_itself(self) -> None:
        card = {"tool": "wiki_read", "title": "Entropy", "slug": "concepts/entropy"}
        self.assertEqual(ObservationEvent(card).payload(), card)

    def test_a_wait_names_the_job_and_where_it_is_finished(self) -> None:
        event = WaitEvent(
            wait=WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="confirm",
            )
        )
        self.assertEqual(
            event.payload(),
            {
                "waitId": "w1",
                "job": "plan",
                "interface": "source",
                "completion": "confirm",
                "status": "proposed",
                "proposedAt": "2026-11-10T09:00:00Z",
            },
        )

    def test_a_job_result_carries_typed_summary_and_artifacts(self) -> None:
        event = JobResultEvent(
            wait_id="w1",
            result=JobResult(
                job="plan",
                status="succeeded",
                summary="planned 3 pages",
                artifacts=(
                    JobArtifact("plan", "concepts/entropy", "Entropy plan"),
                ),
            ),
        )
        self.assertEqual(
            event.payload(),
            {
                "waitId": "w1",
                "status": "succeeded",
                "summary": "planned 3 pages",
                "artifacts": [
                    {"kind": "plan", "id": "concepts/entropy", "label": "Entropy plan"}
                ],
            },
        )

    def test_an_error_carries_a_code_and_whether_retrying_can_help(self) -> None:
        self.assertEqual(
            ErrorEvent("model_error", "the model refused", retryable=True).payload(),
            {
                "code": "model_error",
                "message": "the model refused",
                "retryable": True,
            },
        )
        self.assertEqual(
            ErrorEvent("busy", "a run is in progress", params={"session": "s1"}).payload(),
            {
                "code": "busy",
                "message": "a run is in progress",
                "params": {"session": "s1"},
                "retryable": False,
            },
        )

    def test_done_carries_the_session_and_the_run_when_there_is_one(self) -> None:
        self.assertEqual(
            DoneEvent({"id": "s1"}).payload(),
            {"session": {"id": "s1"}},
        )
        self.assertEqual(
            DoneEvent({"id": "s1"}, {"id": "r1"}).payload(),
            {"session": {"id": "s1"}, "run": {"id": "r1"}},
        )


class TerminalEventTests(unittest.TestCase):
    def test_only_done_and_error_end_a_run(self) -> None:
        self.assertTrue(is_terminal(DoneEvent({"id": "s1"})))
        self.assertTrue(is_terminal(ErrorEvent("model_error", "no answer")))
        for event in (
            StatusEvent("thinking"),
            TokenEvent("answer"),
            ObservationEvent({"tool": "search"}),
        ):
            self.assertFalse(is_terminal(event))


class RuntimeSeamTests(unittest.TestCase):
    def test_a_runtime_is_anything_that_answers_the_four_calls(self) -> None:
        # The API and the tests cross the same seam, so a stand-in only has to
        # offer these four methods rather than inherit an implementation.
        self.assertIsInstance(FakeRuntime(), AgentRuntime)

    def test_a_run_can_be_driven_with_a_list_as_its_sink(self) -> None:
        events = []
        result = FakeRuntime(FakeModelClient()).execute(
            UserInput("s1", "hi"), events.append
        )
        self.assertEqual([event.kind for event in events], ["status", "token", "done"])
        self.assertEqual(result.status, "completed")

    def test_a_run_ends_with_exactly_one_terminal_event(self) -> None:
        events = []
        FakeRuntime(FakeModelClient()).execute(UserInput("s1", "hi"), events.append)
        terminals = [index for index, event in enumerate(events) if is_terminal(event)]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0], len(events) - 1)


if __name__ == "__main__":
    unittest.main()
