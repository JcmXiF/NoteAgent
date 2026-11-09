"""The loop: one question in, streamed events out, the turn committed at the end."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_fakes import FixedClock, GatedModel, SequentialIds
from noteagent.agent.events import (
    DoneEvent,
    ErrorEvent,
    ObservationEvent,
    StatusEvent,
    TokenEvent,
    WaitEvent,
    is_terminal,
)
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.jobs import JobArtifact, JobResult
from noteagent.agent.kernel import MAX_ABORTED_CHARS, KernelRuntime, RunRegistry
from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelDoneEvent,
    ModelErrorEvent,
    ModelStartEvent,
    ModelToolCallEnd,
    ToolCall,
)
from noteagent.agent.projection import MISSING_RESULT, project
from noteagent.agent.runtime import BusyError, Resume, RunCancelled, UserInput
from noteagent.agent.tools import ToolRegistry, ToolResult, ToolSpec
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


def _echo(arguments, context):
    return ToolResult(
        content=f"echo: {arguments['text']}",
        meta={"title": arguments["text"], "slug": "echo"},
    )


class RunRegistryTests(unittest.TestCase):
    def test_a_shared_registry_refuses_a_second_run(self) -> None:
        registry = RunRegistry()
        registry.claim("s1")

        self.assertTrue(registry.is_busy("s1"))
        with self.assertRaises(BusyError):
            registry.claim("s1")

        registry.release("s1")
        self.assertFalse(registry.is_busy("s1"))

    def test_a_cancel_after_the_run_ended_does_not_stop_the_next_one(self) -> None:
        registry = RunRegistry()
        registry.claim("s1")
        registry.release("s1")

        registry.cancel("s1")
        event = registry.claim("s1")

        self.assertFalse(event.is_set())
        registry.release("s1")


class KernelRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def runtime(self, model, tools: ToolRegistry | None = None) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=tools or ToolRegistry(),
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )

    def test_a_question_streams_the_answer_and_commits_it(self) -> None:
        self.store.append(
            "s1",
            {"type": "system_section", "sections": {"identity": "You are NoteAgent."}},
        )
        model = FauxModelClient.text("熵衡量不确定性", chunks=["熵衡量", "不确定性"])
        runtime = self.runtime(model)
        events = []

        result = runtime.execute(UserInput("s1", "什么是熵？"), events.append)

        self.assertEqual(result.status, "completed")
        self.assertEqual(events[0].kind, "status")
        self.assertEqual(
            "".join(event.text for event in events if isinstance(event, TokenEvent)),
            "熵衡量不确定性",
        )
        terminals = [event for event in events if is_terminal(event)]
        self.assertEqual(len(terminals), 1)
        self.assertIsInstance(terminals[0], DoneEvent)

        loaded = self.store.load("s1")
        self.assertEqual(
            [entry["role"] for entry in loaded.entries if entry["type"] == "message"],
            ["user", "assistant"],
        )
        self.assertEqual(loaded.entries[-1]["content"], "熵衡量不确定性")
        self.assertEqual(loaded.title, "什么是熵？")

        request = model.requests[0]
        self.assertEqual(request.messages[0], Message("system", "You are NoteAgent."))
        self.assertEqual(request.messages[1].content, "什么是熵？")

    def test_a_tool_round_is_committed_and_the_answer_follows_it(self) -> None:
        first = FauxModelClient.tool_call(
            "t1", "echo", '{"text":"hi"}', text="我先查一下。"
        )
        second = FauxModelClient.text("答案是 hi")
        model = FauxModelClient(first.script, second.script)
        tools = ToolRegistry(
            [
                ToolSpec(
                    name="echo",
                    description="Echo the text.",
                    parameters={"type": "object"},
                    handler=_echo,
                )
            ]
        )
        runtime = self.runtime(model, tools)
        events = []

        result = runtime.execute(UserInput("s1", "回声"), events.append)

        self.assertEqual(result.status, "completed")
        self.assertTrue(
            any(
                isinstance(event, TokenEvent) and event.reset
                for event in events
            )
        )
        self.assertTrue(
            any(
                isinstance(event, ObservationEvent)
                and event.payload().get("title") == "hi"
                and event.payload().get("tool") == "echo"
                for event in events
            )
        )

        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual(
            [entry["role"] for entry in messages],
            ["user", "assistant", "toolResult", "assistant"],
        )
        self.assertEqual(messages[1]["toolCalls"][0]["id"], "t1")
        self.assertEqual(messages[2]["content"], "echo: hi")
        self.assertEqual(messages[3]["content"], "答案是 hi")
        self.assertEqual(len(model.requests), 2)
        self.assertTrue(model.requests[0].tools)

    def test_two_tool_calls_in_one_round_run_together_and_commit_in_order(self) -> None:
        together = threading.Barrier(2, timeout=2)

        def meet(arguments, _context):
            together.wait()
            return ToolResult(
                content=f"echo: {arguments['text']}",
                meta={"title": arguments["text"], "slug": "echo"},
            )

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="echo",
                    description="Echo the text.",
                    parameters={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                    handler=meet,
                )
            ]
        )
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelToolCallEnd(ToolCall("t1", "echo", '{"text": "one"}')),
                ModelToolCallEnd(ToolCall("t2", "echo", '{"text": "two"}')),
                ModelDoneEvent(AssistantMessage(stop_reason="tool_calls")),
            ],
            FauxModelClient.text("答案是 one 和 two").script,
        )
        events = []

        result = self.runtime(model, tools).execute(
            UserInput("s1", "两页"), events.append
        )

        self.assertEqual(result.status, "completed")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual(
            [entry["role"] for entry in messages],
            ["user", "assistant", "toolResult", "toolResult", "assistant"],
        )
        self.assertEqual(
            [entry["toolCallId"] for entry in messages[2:4]], ["t1", "t2"]
        )
        self.assertEqual(
            [entry["content"] for entry in messages[2:4]],
            ["echo: one", "echo: two"],
        )

        cards = [
            event.payload()
            for event in events
            if isinstance(event, ObservationEvent)
        ]
        self.assertEqual([card["title"] for card in cards], ["one", "two"])

        # The cards live on the step that asked for them, so a reload shows
        # what was looked at even before the answer is read.
        transcript = project(self.store.load("s1").entries).transcript
        step = next(item for item in transcript if item.is_step)
        self.assertEqual([card["title"] for card in step.cards], ["one", "two"])

    def test_a_cancelled_batch_leaves_each_unanswered_call_missing(self) -> None:
        started = threading.Event()

        def wait_for_cancel(_arguments, context):
            started.set()
            context.cancel.wait(5)
            raise RunCancelled("stopped")

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="slow",
                    description="Wait until the run is stopped.",
                    parameters={"type": "object"},
                    handler=wait_for_cancel,
                )
            ]
        )
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelToolCallEnd(ToolCall("t1", "slow", "{}")),
                ModelToolCallEnd(ToolCall("t2", "slow", "{}")),
                ModelDoneEvent(AssistantMessage(stop_reason="tool_calls")),
            ]
        )
        runtime = self.runtime(model, tools)
        results, thread = self._start(runtime, lambda _event: None)
        self.assertTrue(started.wait(2), "the tool never ran")
        runtime.cancel("s1")
        self._join(thread)

        self.assertEqual(results[0].status, "cancelled")
        tool_messages = [
            message
            for message in project(self.store.load("s1").entries).messages
            if message.role == "tool"
        ]
        self.assertEqual(
            [message.content for message in tool_messages],
            [MISSING_RESULT, MISSING_RESULT],
        )

    def test_an_unknown_tool_is_reported_and_the_loop_continues(self) -> None:
        first = FauxModelClient.tool_call("t1", "nope", "{}")
        second = FauxModelClient.text("答案")
        model = FauxModelClient(first.script, second.script)
        events = []

        result = self.runtime(model).execute(UserInput("s1", "试试"), events.append)

        self.assertEqual(result.status, "completed")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual(messages[2]["content"], "unknown tool: nope")
        self.assertTrue(messages[2]["meta"]["error"])
        self.assertTrue(
            any(
                isinstance(event, ObservationEvent)
                and event.payload().get("error") is True
                for event in events
            )
        )

    def test_a_model_error_ends_the_run_without_saving_an_answer(self) -> None:
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelErrorEvent("model_error", "the model refused", retryable=True),
            ]
        )
        events = []

        result = self.runtime(model).execute(UserInput("s1", "问题"), events.append)

        self.assertEqual(result.status, "failed")
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertTrue(events[-1].retryable)
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual([entry["role"] for entry in messages], ["user"])

    def test_an_empty_answer_is_a_failure_not_a_turn(self) -> None:
        model = FauxModelClient(
            [ModelStartEvent("faux"), ModelDoneEvent(AssistantMessage())]
        )
        events = []

        result = self.runtime(model).execute(UserInput("s1", "问题"), events.append)

        self.assertEqual(result.status, "failed")
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertEqual(events[-1].code, "model_error")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual([entry["role"] for entry in messages], ["user"])

    def test_a_proposal_records_one_wait_and_ends_the_run(self) -> None:
        model = FauxModelClient.tool_call(
            "p1",
            "propose",
            '{"job":"plan","interface":"source","intent":"plan entropy"}',
            text="我来安排。",
        )
        runtime = self.runtime(model)
        events = []

        result = runtime.execute(UserInput("s1", "安排一下"), events.append)

        self.assertEqual(result.status, "waiting")
        self.assertTrue(result.wait_id)
        wait_events = [event for event in events if isinstance(event, WaitEvent)]
        self.assertEqual(len(wait_events), 1)
        self.assertEqual(
            wait_events[0].payload()["job"],
            "plan",
        )
        self.assertEqual(wait_events[0].payload()["interface"], "source")
        self.assertEqual(wait_events[0].payload()["completion"], "start_plan")
        self.assertEqual(wait_events[0].payload()["status"], "proposed")
        self.assertIsInstance(events[-1], DoneEvent)
        self.assertEqual(events[-1].run["status"], "waiting")

        entries = self.store.load("s1").entries
        self.assertEqual(
            [entry["type"] for entry in entries], ["message", "message", "wait_proposed"]
        )
        self.assertEqual(entries[1]["content"], "我来安排。")
        self.assertEqual(entries[1]["stopReason"], "propose")
        self.assertEqual(entries[2]["id"], result.wait_id)
        self.assertEqual(entries[2]["job"], "plan")
        self.assertEqual(entries[2]["intent"], "plan entropy")

        wait = runtime.wait_state("s1")
        assert wait is not None
        self.assertEqual(wait.status, "proposed")
        self.assertEqual(
            [tool["function"]["name"] for tool in model.requests[0].tools],
            ["propose"],
        )

    def test_a_second_proposal_is_refused_while_the_first_is_waiting(self) -> None:
        first = FauxModelClient.tool_call(
            "p1", "propose", '{"job":"plan","interface":"source"}'
        )
        runtime = self.runtime(first)
        runtime.execute(UserInput("s1", "安排一下"), lambda _event: None)

        second = FauxModelClient.tool_call(
            "p2", "propose", '{"job":"compile","interface":"plan_board"}'
        )
        third = FauxModelClient.text("先把当前的等待处理掉。")
        runtime = KernelRuntime(
            model=FauxModelClient(second.script, third.script),
            sessions=self.store,
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )
        events = []

        result = runtime.execute(UserInput("s1", "再安排一个"), events.append)

        self.assertEqual(result.status, "completed")
        refusal = next(
            event for event in events if isinstance(event, ObservationEvent)
        )
        self.assertTrue(refusal.payload()["error"])
        self.assertEqual(refusal.payload()["job"], "plan")
        waits = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "wait_proposed"
        ]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["job"], "plan")

    def test_a_batch_that_mixes_propose_with_a_tool_is_refused_whole(self) -> None:
        ran = threading.Event()

        def should_not_run(_arguments, _context):
            ran.set()
            return ToolResult(content="ran", meta={"title": "echo"})

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="echo",
                    description="Echo the text.",
                    parameters={"type": "object"},
                    handler=should_not_run,
                )
            ]
        )
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelToolCallEnd(ToolCall("t1", "echo", "{}")),
                ModelToolCallEnd(
                    ToolCall("p1", "propose", '{"job":"plan","interface":"source"}')
                ),
                ModelDoneEvent(AssistantMessage(stop_reason="tool_calls")),
            ],
            FauxModelClient.text("分开再来。").script,
        )
        events = []

        result = self.runtime(model, tools).execute(
            UserInput("s1", "安排"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(ran.is_set(), "a refused batch must not run any of its calls")
        self.assertEqual(
            [
                entry["type"]
                for entry in self.store.load("s1").entries
                if entry["type"] == "wait_proposed"
            ],
            [],
        )
        tool_results = [
            entry
            for entry in self.store.load("s1").entries
            if entry.get("role") == "toolResult"
        ]
        self.assertEqual(len(tool_results), 2)
        self.assertTrue(all(entry["meta"]["error"] for entry in tool_results))
        cards = [
            event.payload()
            for event in events
            if isinstance(event, ObservationEvent)
        ]
        self.assertEqual([card["tool"] for card in cards], ["echo", "propose"])
        self.assertTrue(all(card["error"] for card in cards))

    def test_an_invalid_proposal_is_an_observation_and_the_loop_continues(self) -> None:
        proposal = FauxModelClient.tool_call(
            "p1", "propose", '{"job":"plan","interface":"compile_board"}'
        )
        answer = FauxModelClient.text("那次提议的地点不对。")
        runtime = KernelRuntime(
            model=FauxModelClient(proposal.script, answer.script),
            sessions=self.store,
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )
        events = []

        result = runtime.execute(UserInput("s1", "安排"), events.append)

        self.assertEqual(result.status, "completed")
        refusal = next(
            event for event in events if isinstance(event, ObservationEvent)
        )
        self.assertTrue(refusal.payload()["error"])
        self.assertIn("plan", refusal.payload()["diagnostics"][0]["message"])
        self.assertEqual(
            [
                entry
                for entry in self.store.load("s1").entries
                if entry["type"] == "wait_proposed"
            ],
            [],
        )
        self.assertIsNone(runtime.wait_state("s1"))

    def test_a_new_proposal_is_allowed_after_the_wait_was_cancelled(self) -> None:
        from noteagent.agent.wait_store import SessionWaitStore

        # One id generator stands in for the process-wide one: a new proposal
        # must not reuse the id of the wait it supersedes.
        ids = SequentialIds()
        first = FauxModelClient.tool_call(
            "p1", "propose", '{"job":"plan","interface":"source"}'
        )
        runtime = KernelRuntime(
            model=first,
            sessions=self.store,
            clock=FixedClock(),
            ids=ids,
            model_name="faux",
        )
        result = runtime.execute(UserInput("s1", "安排"), lambda _event: None)
        SessionWaitStore(self.store).cancel("s1", result.wait_id)

        second = FauxModelClient.tool_call(
            "p2", "propose", '{"job":"compile","interface":"plan_board"}'
        )
        runtime = KernelRuntime(
            model=second,
            sessions=self.store,
            clock=FixedClock(),
            ids=ids,
            model_name="faux",
        )

        again = runtime.execute(UserInput("s1", "改成编译"), lambda _event: None)

        self.assertEqual(again.status, "waiting")
        wait = runtime.wait_state("s1")
        assert wait is not None
        self.assertEqual(wait.status, "proposed")
        self.assertEqual(wait.job, "compile")
        self.assertNotEqual(again.wait_id, result.wait_id)

    def complete_plan(
        self,
        wait_id: str = "w1",
        *,
        summary: str = "planned 2 pages",
        artifacts: tuple[JobArtifact, ...] = (),
    ) -> SessionWaitStore:
        waits = SessionWaitStore(self.store)
        waits.propose(
            "s1",
            WaitState(
                wait_id=wait_id,
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="start_plan",
            ),
        )
        waits.enter("s1", wait_id, "job-9")
        waits.finish(
            "s1",
            wait_id,
            JobResult(
                job="plan",
                status="succeeded",
                summary=summary,
                artifacts=artifacts,
            ),
        )
        return waits

    def test_a_pending_wait_is_named_in_the_prompt(self) -> None:
        SessionWaitStore(self.store).propose(
            "s1",
            WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="start_plan",
            ),
        )
        model = FauxModelClient.text("先等你开始规划。")

        result = self.runtime(model).execute(
            UserInput("s1", "现在呢"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        system = model.requests[0].messages[0]
        self.assertEqual(system.role, "system")
        self.assertIn("## Run", system.content)
        self.assertIn("status: waiting", system.content)
        self.assertIn("pending wait", system.content)
        self.assertIn("job: plan", system.content)

    def test_a_resume_run_reads_the_job_result_and_answers(self) -> None:
        self.complete_plan(
            summary="planned 2 pages",
            artifacts=(
                JobArtifact("write", "concepts/entropy", "Entropy"),
                JobArtifact("mention", "concepts/information", "Information"),
            ),
        )
        model = FauxModelClient.text("规划完成，接下来可以编译。")
        runtime = self.runtime(model)

        result = runtime.execute(Resume("s1", "w1"), lambda _event: None)

        self.assertEqual(result.status, "completed")
        entries = self.store.load("s1").entries
        self.assertEqual(
            [entry["type"] for entry in entries],
            [
                "wait_proposed",
                "wait_entered",
                "wait_completed",
                "resume_started",
                "message",
            ],
        )
        self.assertEqual(entries[-1]["role"], "assistant")
        self.assertEqual(entries[-1]["content"], "规划完成，接下来可以编译。")

        messages = model.requests[0].messages
        self.assertIn("last job result", messages[0].content)
        joined = "\n".join(message.content for message in messages)
        self.assertIn("Job result (plan, completed)", joined)
        self.assertIn("planned 2 pages", joined)
        self.assertIn("write:concepts/entropy", joined)

    def test_a_resume_is_not_run_twice(self) -> None:
        self.complete_plan()
        first = self.runtime(FauxModelClient.text("第一次继续。"))
        first.execute(Resume("s1", "w1"), lambda _event: None)

        second_model = FauxModelClient.text("不该再继续。")
        second = self.runtime(second_model)
        events = []
        result = second.execute(Resume("s1", "w1"), events.append)

        self.assertEqual(result.status, "completed")
        self.assertEqual(second_model.requests, [])
        self.assertIsInstance(events[-1], DoneEvent)
        answers = [
            entry
            for entry in self.store.load("s1").entries
            if entry.get("type") == "message"
            and entry.get("role") == "assistant"
        ]
        self.assertEqual(len(answers), 1)

    def test_a_resume_waits_until_the_job_is_finished(self) -> None:
        waits = SessionWaitStore(self.store)
        waits.propose(
            "s1",
            WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="start_plan",
            ),
        )
        model = FauxModelClient.text("不该跑。")
        runtime = self.runtime(model)

        with self.assertRaises(ValueError):
            runtime.execute(Resume("s1", "w1"), lambda _event: None)

        waits.enter("s1", "w1", "job-9")
        with self.assertRaises(ValueError):
            runtime.execute(Resume("s1", "w1"), lambda _event: None)

        self.assertEqual(model.requests, [])
        self.assertFalse(
            any(
                entry["type"] == "resume_started"
                for entry in self.store.load("s1").entries
            )
        )

    def test_a_cancelled_job_still_resumes_the_chat(self) -> None:
        waits = SessionWaitStore(self.store)
        waits.propose(
            "s1",
            WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="start_plan",
            ),
        )
        waits.enter("s1", "w1", "job-9")
        waits.finish(
            "s1",
            "w1",
            JobResult(
                job="plan",
                status="cancelled",
                summary="stopped by the job",
            ),
        )
        model = FauxModelClient.text("作业取消了。")

        result = self.runtime(model).execute(
            Resume("s1", "w1"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertTrue(model.requests)
        joined = "\n".join(
            message.content for message in model.requests[0].messages
        )
        self.assertIn("Job result (plan, cancelled)", joined)

    def test_a_wait_stopped_before_its_job_does_not_resume(self) -> None:
        waits = SessionWaitStore(self.store)
        waits.propose(
            "s1",
            WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion="start_plan",
            ),
        )
        waits.cancel("s1", "w1")
        model = FauxModelClient.text("不该跑。")

        with self.assertRaises(ValueError):
            self.runtime(model).execute(Resume("s1", "w1"), lambda _event: None)

        self.assertEqual(model.requests, [])

    def test_after_a_plan_the_model_can_propose_compile(self) -> None:
        self.complete_plan()
        model = FauxModelClient.tool_call(
            "p1",
            "propose",
            '{"job":"compile","interface":"plan_board"}',
            text="规划好了，开始编译。",
        )
        runtime = self.runtime(model)

        result = runtime.execute(Resume("s1", "w1"), lambda _event: None)

        self.assertEqual(result.status, "waiting")
        wait = runtime.wait_state("s1")
        assert wait is not None
        self.assertEqual(wait.job, "compile")
        self.assertEqual(wait.status, "proposed")
        self.assertTrue(
            any(
                entry["type"] == "resume_started"
                for entry in self.store.load("s1").entries
            )
        )

    def _start(self, runtime, sink):
        """Run one question in the background, so the test can stop it."""
        results = []
        thread = threading.Thread(
            target=lambda: results.append(
                runtime.execute(UserInput("s1", "问题"), sink)
            ),
            daemon=True,
        )
        thread.start()
        return results, thread

    def _join(self, thread) -> None:
        thread.join(5)
        self.assertFalse(thread.is_alive(), "the run kept going after the cancel")

    def test_a_cancelled_run_keeps_what_it_streamed_as_an_aborted_answer(self) -> None:
        model = GatedModel("部分")
        events: list[object] = []
        first_token = threading.Event()

        def sink(event) -> None:
            events.append(event)
            if isinstance(event, TokenEvent) and event.text == "部分":
                first_token.set()

        runtime = self.runtime(model)
        results, thread = self._start(runtime, sink)
        # The model streams before the stop, so there is something to keep.
        self.assertTrue(first_token.wait(2), "the first piece was never streamed")
        runtime.cancel("s1")
        model.release.set()
        self._join(thread)

        self.assertEqual(results[0].status, "cancelled")
        self.assertTrue(
            any(
                isinstance(event, ErrorEvent) and event.code == "cancelled"
                for event in events
            )
        )
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual([entry["role"] for entry in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "部分")
        self.assertEqual(messages[-1]["stopReason"], "aborted")

        # The next run reads the same log through the projection, so what was
        # kept is visible there as an answer that stopped.
        transcript = project(self.store.load("s1").entries).transcript
        self.assertEqual(transcript[-1].stop_reason, "aborted")

    def test_an_aborted_answer_is_bounded(self) -> None:
        streamed = "长" * (MAX_ABORTED_CHARS + 500)
        model = GatedModel(streamed)
        first_token = threading.Event()

        def sink(event) -> None:
            if isinstance(event, TokenEvent) and event.text:
                first_token.set()

        runtime = self.runtime(model)
        results, thread = self._start(runtime, sink)
        self.assertTrue(first_token.wait(2), "the first piece was never streamed")
        runtime.cancel("s1")
        model.release.set()
        self._join(thread)

        self.assertEqual(results[0].status, "cancelled")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual(len(messages[-1]["content"]), MAX_ABORTED_CHARS)
        self.assertEqual(messages[-1]["content"], streamed[:MAX_ABORTED_CHARS])

    def test_cancelling_before_any_text_saves_no_assistant_turn(self) -> None:
        model = GatedModel("")
        thinking = threading.Event()

        def sink(event) -> None:
            if isinstance(event, StatusEvent):
                thinking.set()

        runtime = self.runtime(model)
        results, thread = self._start(runtime, sink)
        self.assertTrue(thinking.wait(2), "the run never started")
        runtime.cancel("s1")
        model.release.set()
        self._join(thread)

        self.assertEqual(results[0].status, "cancelled")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual([entry["role"] for entry in messages], ["user"])

    def test_a_cancel_during_a_tool_call_does_not_repeat_its_preamble(self) -> None:
        started = threading.Event()

        def wait_for_cancel(_arguments, context):
            started.set()
            context.cancel.wait(5)
            raise RunCancelled("stopped")

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="slow",
                    description="Wait until the run is stopped.",
                    parameters={"type": "object"},
                    handler=wait_for_cancel,
                )
            ]
        )
        model = FauxModelClient.tool_call("t1", "slow", "{}", text="先查一下。")
        runtime = self.runtime(model, tools)

        results, thread = self._start(runtime, lambda _event: None)
        self.assertTrue(started.wait(2), "the tool never ran")
        runtime.cancel("s1")
        self._join(thread)

        self.assertEqual(results[0].status, "cancelled")
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual([entry["role"] for entry in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["toolCalls"][0]["id"], "t1")
        self.assertNotIn("stopReason", messages[-1])


if __name__ == "__main__":
    unittest.main()
