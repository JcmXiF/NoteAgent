"""The hand-built ReAct loop: model, tool rounds, and the turn boundary.

The loop reads the session through the projection, asks the model for one turn,
streams what it writes, and commits an entry only at a boundary. A tool call is a
step: the preamble is taken back from the answer, its result is committed, and
the next round starts from what the log now says.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from threading import Event, Lock

from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds, iso_timestamp
from noteagent.agent.events import (
    DoneEvent,
    ErrorEvent,
    ObservationEvent,
    StatusEvent,
    TokenEvent,
    WaitEvent,
)
from noteagent.agent.model import (
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ModelStreamError,
    ModelTextDelta,
    ToolCall,
    collect,
)
from noteagent.agent.projection import project
from noteagent.agent.propose import (
    PROPOSE_NAME,
    PROPOSE_TOOL,
    Proposal,
    ProposeError,
    parse_proposal,
)
from noteagent.agent.runtime import (
    BusyError,
    EventSink,
    Resume,
    Retry,
    RunCancelled,
    RunRequest,
    RunResult,
    UserInput,
    busy_message,
)
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import Diagnostic, ToolContext, ToolRegistry, ToolResult
from noteagent.agent.wait import WaitState, completion_for, is_active
from noteagent.agent.wait_store import SessionWaitStore

#: An answer stopped mid-stream is kept only as far as this. A cancellation must
#: not be able to put an unbounded partial into the log.
MAX_ABORTED_CHARS = 8_000

#: What one model turn may ask for before the rest is refused. A model that
#: writes a hundred calls in one batch is not doing the work in an order a
#: reader can follow, and every call costs a result entry.
MAX_TOOL_CALLS_PER_ROUND = 8


class RunRegistry:
    """The sessions with a run, shared by every runtime in the process.

    A runtime per request would otherwise keep its busy set to itself, and two
    requests would both believe the session was free. The registry is the one
    place that answers, and the one place a cancel is delivered.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._busy: set[str] = set()
        self._cancel_events: dict[str, Event] = {}

    def claim(self, session_id: str) -> Event:
        with self._lock:
            if session_id in self._busy:
                raise BusyError(busy_message(session_id))
            event = Event()
            self._busy.add(session_id)
            self._cancel_events[session_id] = event
            return event

    def release(self, session_id: str) -> None:
        with self._lock:
            self._busy.discard(session_id)
            self._cancel_events.pop(session_id, None)

    def cancel(self, session_id: str) -> None:
        """Stop the session's run, if one is still running."""
        with self._lock:
            event = self._cancel_events.get(session_id)
        if event is not None:
            event.set()

    def is_busy(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._busy


class KernelRuntime:
    """One session's loop, with the model, tools and log it was given."""

    def __init__(
        self,
        *,
        model: ModelClient,
        sessions: SessionStore,
        tools: ToolRegistry | None = None,
        waits: WaitStore | None = None,
        clock: Clock | None = None,
        ids: Ids | None = None,
        registry: RunRegistry | None = None,
        model_name: str = "",
        max_tool_rounds: int = 6,
    ) -> None:
        self._model = model
        self._sessions = sessions
        self._tools = tools or ToolRegistry()
        # A wait is read from the same log as the turns, so the default keeps
        # the runtime and the session view looking at one record.
        self._waits = waits if waits is not None else SessionWaitStore(sessions)
        self._clock = clock or SystemClock()
        self._ids = ids or UuidIds()
        self._registry = registry or RunRegistry()
        self._model_name = model_name
        self._max_tool_rounds = max_tool_rounds

    def execute(self, request: RunRequest, sink: EventSink) -> RunResult:
        if isinstance(request, Resume):
            raise ValueError("resume arrives with the wait slice")
        if isinstance(request, Retry):
            raise ValueError("no route opens a retry yet")
        session_id = request.session_id
        cancel = self._registry.claim(session_id)
        run_id = self._ids.new_id("run")
        try:
            return self._run_user(request, sink, run_id, cancel)
        finally:
            self._registry.release(session_id)

    def cancel(self, session_id: str) -> None:
        self._registry.cancel(session_id)

    def wait_state(self, session_id: str) -> WaitState | None:
        return self._waits.current(session_id)

    def is_busy(self, session_id: str) -> bool:
        return self._registry.is_busy(session_id)

    def _run_user(
        self,
        request: UserInput,
        sink: EventSink,
        run_id: str,
        cancel: Event,
    ) -> RunResult:
        session_id = request.session_id
        content = request.content.strip()
        if not content:
            raise ValueError("question is empty")
        # What the current model call has streamed but not yet committed. A stop
        # keeps it, so the reader can see where the answer ended.
        partial: list[str] = []
        try:
            _check_cancel(cancel)
            self._sessions.append(
                session_id, {"type": "message", "role": "user", "content": content}
            )
            sink(StatusEvent("thinking", "think"))
            messages = self._prompt(session_id)
            tool_rounds = 0

            while True:
                _check_cancel(cancel)
                forced = tool_rounds >= self._max_tool_rounds
                if forced:
                    sink(StatusEvent("answering from the tool results", "answer"))
                offered = () if forced else self._offered_tools()
                model_request = ModelRequest(
                    model=self._model_name,
                    messages=messages,
                    tools=offered,
                    session_id=session_id,
                    cancel=cancel,
                )
                partial.clear()
                try:
                    assistant = collect(
                        _tap(self._model.stream(model_request), sink, cancel, partial)
                    )
                except ModelStreamError as err:
                    if err.code == "cancelled":
                        # One cancellation path, whichever layer noticed first.
                        raise RunCancelled("run cancelled") from err
                    sink(
                        ErrorEvent(
                            err.code,
                            str(err),
                            retryable=err.retryable,
                        )
                    )
                    return RunResult(session_id, run_id, "failed", error=str(err))

                if assistant.tool_calls and not forced:
                    calls = assistant.tool_calls
                    refusal: ToolResult | None = None
                    if len(calls) == 1 and calls[0].name == PROPOSE_NAME:
                        proposal, refusal = self._read_proposal(session_id, calls[0])
                        if proposal is not None:
                            # A control action is not a tool round: the prose is
                            # the answer's last words, the wait is the outcome,
                            # and the run ends here. Nothing is dispatched.
                            text = assistant.content.strip()
                            if text:
                                self._sessions.append(
                                    session_id,
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": text,
                                        "stopReason": "propose",
                                    },
                                )
                            wait = self._record_wait(session_id, proposal)
                            sink(WaitEvent(wait))
                            sink(
                                DoneEvent(
                                    {"id": session_id},
                                    {"id": run_id, "status": "waiting"},
                                )
                            )
                            return RunResult(
                                session_id,
                                run_id,
                                "waiting",
                                wait_id=wait.wait_id,
                            )
                    # A refused proposal and an ordinary batch share one tail:
                    # the preamble becomes a step, every call gets its result,
                    # and the next round reads the log.
                    sink(TokenEvent("", reset=True))
                    self._commit_tool_calls(session_id, calls, assistant.content)
                    # The preamble is now part of the tool-call entry, so a stop
                    # from here on must not commit it a second time.
                    partial.clear()
                    results = (
                        (refusal,)
                        if refusal is not None
                        else self._round_results(session_id, run_id, calls, cancel)
                    )
                    self._commit_results(session_id, calls, results, sink)
                    tool_rounds += 1
                    messages = self._prompt(session_id)
                    sink(StatusEvent("thinking", "think"))
                    continue

                answer = assistant.content.strip()
                if not answer:
                    message = "the model returned no answer"
                    sink(ErrorEvent("model_error", message))
                    return RunResult(session_id, run_id, "failed", error=message)
                entry: dict[str, object] = {
                    "type": "message",
                    "role": "assistant",
                    "content": answer,
                    "stopReason": assistant.stop_reason or "stop",
                }
                if assistant.usage is not None:
                    entry["usage"] = {
                        "inputTokens": assistant.usage.input_tokens,
                        "outputTokens": assistant.usage.output_tokens,
                        "cacheReadTokens": assistant.usage.cache_read_tokens,
                    }
                self._sessions.append(session_id, entry)
                sink(
                    DoneEvent(
                        {"id": session_id}, {"id": run_id, "status": "completed"}
                    )
                )
                return RunResult(session_id, run_id, "completed")
        except RunCancelled:
            return self._cancelled_result(
                session_id, run_id, sink, "".join(partial)
            )

    def _offered_tools(self) -> tuple[dict[str, object], ...]:
        """The tools the model may call, with the control action beside them."""
        return (PROPOSE_TOOL,) + self._tools.model_tools()

    def _read_proposal(
        self, session_id: str, call: ToolCall
    ) -> tuple[Proposal | None, ToolResult | None]:
        """The checked proposal, or the observation that says why not."""
        try:
            proposal = parse_proposal(call.arguments)
        except ProposeError as err:
            return None, self._refused(
                call, f"invalid proposal: {err}", "invalid_proposal"
            )
        active = self._waits.current(session_id)
        if is_active(active):
            assert active is not None
            return None, self._refused(
                call,
                (
                    f"this session is already waiting on {active.job}; "
                    "finish or stop it first"
                ),
                "already_waiting",
                job=active.job,
            )
        return proposal, None

    def _record_wait(self, session_id: str, proposal: Proposal) -> WaitState:
        """Commit the wait a checked proposal asks for, and return it."""
        wait = WaitState(
            wait_id=self._ids.new_id("wait"),
            job=proposal.job,
            interface=proposal.interface,
            status="proposed",
            proposed_at=iso_timestamp(self._clock),
            completion=completion_for(proposal.job),
            intent=proposal.intent,
            reason=proposal.reason,
        )
        self._waits.propose(session_id, wait)
        return wait

    def _round_results(
        self,
        session_id: str,
        run_id: str,
        calls: tuple[ToolCall, ...],
        cancel: Event,
    ) -> tuple[ToolResult, ...]:
        """One result per call, in the model's order.

        A batch that has propose beside another call is refused whole: the
        control action is only meaningful alone, and running the rest would
        leave the model believing half of it happened.
        """
        if any(call.name == PROPOSE_NAME for call in calls):
            message = (
                "propose must be alone in its batch; nothing in this batch was run"
            )
            return tuple(
                self._refused(call, message, "propose_not_alone") for call in calls
            )
        within = calls[:MAX_TOOL_CALLS_PER_ROUND]
        results = list(
            self._tools.dispatch_many(
                within,
                ToolContext(
                    session_id=session_id,
                    run_id=run_id,
                    cancel=cancel,
                    clock=self._clock,
                ),
            )
        )
        for call in calls[MAX_TOOL_CALLS_PER_ROUND:]:
            # A result is still owed for every call in the batch, so pairing
            # holds and the model can see what was refused.
            message = (
                "too many tool calls in one round "
                f"(max {MAX_TOOL_CALLS_PER_ROUND})"
            )
            results.append(
                ToolResult(
                    content=message,
                    meta={"title": call.name, "error": True},
                    diagnostics=(
                        Diagnostic("error", "too_many_calls", message),
                    ),
                )
            )
        return tuple(results)

    def _refused(
        self,
        call: ToolCall,
        message: str,
        code: str,
        *,
        job: str = "",
    ) -> ToolResult:
        """A call that did not run, as both the model and the reader see it."""
        meta: dict[str, object] = {"title": call.name, "error": True}
        if call.name == PROPOSE_NAME:
            meta["needs_human"] = True
        if job:
            meta["job"] = job
        return ToolResult(
            content=message,
            meta=meta,
            diagnostics=(Diagnostic("error", code, message),),
        )

    def _commit_tool_calls(
        self,
        session_id: str,
        calls: tuple[ToolCall, ...],
        content: str,
    ) -> None:
        self._sessions.append(
            session_id,
            {
                "type": "message",
                "role": "assistant",
                "content": content,
                "toolCalls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in calls
                ],
            },
        )

    def _commit_results(
        self,
        session_id: str,
        calls: tuple[ToolCall, ...],
        results: tuple[ToolResult, ...],
        sink: EventSink,
    ) -> None:
        for call, result in zip(calls, results, strict=True):
            self._commit_result(session_id, call.id, call.name, result)
            sink(ObservationEvent(result.observation(call.name)))

    def _commit_result(
        self,
        session_id: str,
        call_id: str,
        name: str,
        result: ToolResult,
    ) -> None:
        """Write one tool result, with the card the reader saw for it."""
        entry: dict[str, object] = {
            "type": "message",
            "role": "toolResult",
            "toolCallId": call_id,
            "name": name,
            "content": result.content,
            "meta": result.observation(name),
        }
        if result.diagnostics:
            entry["diagnostics"] = [
                {
                    "severity": item.severity,
                    "code": item.code,
                    "message": item.message,
                }
                for item in result.diagnostics
            ]
        self._sessions.append(session_id, entry)

    def _cancelled_result(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
        partial: str,
    ) -> RunResult:
        """Keep the answer that was being written, then end the run."""
        text = partial.strip()
        if text:
            self._sessions.append(
                session_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text[:MAX_ABORTED_CHARS],
                    "stopReason": "aborted",
                },
            )
        sink(ErrorEvent("cancelled", "the run was cancelled", retryable=True))
        return RunResult(session_id, run_id, "cancelled")

    def _prompt(self, session_id: str) -> tuple[Message, ...]:
        snapshot = self._sessions.load(session_id)
        projection = project(snapshot.entries)
        parts = [text for text in projection.sections.values() if text.strip()]
        if projection.summary.strip():
            parts.append("Summary of earlier conversation:\n" + projection.summary)
        messages = list(projection.messages)
        if parts:
            messages.insert(0, Message("system", "\n\n".join(parts)))
        return tuple(messages)

def _tap(
    stream: Iterable[ModelEvent],
    sink: EventSink,
    cancel: Event,
    partial: list[str],
) -> Iterator[ModelEvent]:
    """Report text as it arrives, keep what a stop would keep, pass it on."""
    for event in stream:
        _check_cancel(cancel)
        if isinstance(event, ModelTextDelta):
            partial.append(event.text)
            sink(TokenEvent(event.text))
        yield event


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise RunCancelled("run cancelled")
