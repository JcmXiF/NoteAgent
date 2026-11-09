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
from noteagent.agent.compaction import (
    CompactionLimits,
    bounded_summary,
    choose_cut,
    compaction_entry,
    estimate_tokens,
    render_transcript,
    trim_tool_results,
)
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
from noteagent.agent.store import SessionSnapshot, SessionStore, WaitStore
from noteagent.agent.tools import Diagnostic, ToolContext, ToolRegistry, ToolResult
from noteagent.agent.wait import (
    WaitState,
    completion_for,
    is_active,
    render_run_section,
)
from noteagent.agent.wait_store import (
    SessionWaitStore,
    current_wait,
    has_resumed,
)

#: An answer stopped mid-stream is kept only as far as this. A cancellation must
#: not be able to put an unbounded partial into the log.
MAX_ABORTED_CHARS = 8_000

#: What one model turn may ask for before the rest is refused. A model that
#: writes a hundred calls in one batch is not doing the work in an order a
#: reader can follow, and every call costs a result entry.
MAX_TOOL_CALLS_PER_ROUND = 8

#: What the summary request is told to keep and drop. It is a summarizer, not a
#: second pair of hands: it may read the old transcript, never the project.
SUMMARY_INSTRUCTIONS = (
    "Summarize the earlier part of this conversation so it can be left out of "
    "the context. Keep: the reader's goals, constraints, decisions and open "
    "questions; important tool results (pages read or searched, job results); "
    "the current task state, pending waits or jobs, and the next step; project "
    "constraints that must not be forgotten. Drop: raw tool output, repeated "
    "material, and intermediate reasoning. Write a compact summary in the "
    "language of the conversation."
)


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
        compaction: CompactionLimits | None = None,
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
        self._compaction = compaction or CompactionLimits()

    def execute(self, request: RunRequest, sink: EventSink) -> RunResult:
        if isinstance(request, Retry):
            raise ValueError("no route opens a retry yet")
        session_id = request.session_id
        cancel = self._registry.claim(session_id)
        run_id = self._ids.new_id("run")
        try:
            if isinstance(request, Resume):
                return self._run_resume(request, sink, run_id, cancel)
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
        try:
            _check_cancel(cancel)
            self._sessions.append(
                session_id, {"type": "message", "role": "user", "content": content}
            )
        except RunCancelled:
            return self._cancelled_result(session_id, run_id, sink, "")
        return self._run_loop(session_id, run_id, sink, cancel)

    def _run_resume(
        self,
        request: Resume,
        sink: EventSink,
        run_id: str,
        cancel: Event,
    ) -> RunResult:
        """Continue a session after the job it waited on came back.

        A resume is a run like any other, except that its turn begins with the
        job result the log now holds rather than with something a person said.
        The marker makes a second delivery of the same completion a no-op.
        """
        session_id = request.session_id
        try:
            _check_cancel(cancel)
            snapshot = self._sessions.load(session_id)
            wait = current_wait(snapshot.entries)
            if wait is None or wait.wait_id != request.wait_id:
                raise ValueError(
                    f"session {session_id} is not waiting on {request.wait_id}"
                )
            # A job that was cancelled still has a result: the cancellation.
            # A wait the human stopped before starting has no job and no
            # continuation to run.
            finished = wait.status in ("completed", "failed") or (
                wait.status == "cancelled" and bool(wait.job_id)
            )
            if not finished:
                raise ValueError(
                    f"wait {request.wait_id} is {wait.status}, not finished"
                )
            if has_resumed(snapshot.entries, request.wait_id):
                # The completion was delivered twice: the job may only continue
                # the conversation once.
                sink(
                    DoneEvent(
                        {"id": session_id}, {"id": run_id, "status": "completed"}
                    )
                )
                return RunResult(session_id, run_id, "completed")
            self._sessions.append(
                session_id,
                {"type": "resume_started", "waitId": request.wait_id},
            )
        except RunCancelled:
            return self._cancelled_result(session_id, run_id, sink, "")
        return self._run_loop(session_id, run_id, sink, cancel)

    def _run_loop(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
        cancel: Event,
    ) -> RunResult:
        # What the current model call has streamed but not yet committed. A stop
        # keeps it, so the reader can see where the answer ended.
        partial: list[str] = []
        try:
            sink(StatusEvent("thinking", "think"))
            tool_rounds = 0
            overflow_retried = False

            while True:
                _check_cancel(cancel)
                try:
                    self._compact_if_needed(session_id, sink, cancel)
                except ModelStreamError as err:
                    return self._compaction_failure(
                        session_id, run_id, sink, err
                    )
                messages = self._prompt(session_id)
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
                    if err.code == "context_overflow" and not overflow_retried:
                        overflow_retried = True
                        try:
                            compacted = self._compact_if_needed(
                                session_id, sink, cancel, force=True
                            )
                        except ModelStreamError as summary_err:
                            return self._compaction_failure(
                                session_id, run_id, sink, summary_err
                            )
                        if compacted:
                            # The prompt is smaller now; ask once more.
                            continue
                    sink(
                        ErrorEvent(
                            err.code,
                            str(err),
                            retryable=err.retryable,
                        )
                    )
                    return RunResult(session_id, run_id, "failed", error=str(err))

                # This turn answered, so a later overflow (after a tool round,
                # say) gets its own compaction and retry.
                overflow_retried = False
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

    def _compaction_failure(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
        err: ModelStreamError,
    ) -> RunResult:
        """End the run because the summary call could not finish."""
        if err.code == "cancelled":
            raise RunCancelled("run cancelled") from err
        sink(
            ErrorEvent(
                "compaction_failed",
                str(err),
                retryable=err.retryable,
            )
        )
        return RunResult(session_id, run_id, "failed", error=str(err))

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

    def compact(self, session_id: str, instructions: str = "") -> bool:
        """Summarize the older history now, on the reader's request.

        A manual compaction claims the session like a run; it may not race a
        conversation that is still writing.
        """
        cancel = self._registry.claim(session_id)
        try:
            return self._compact_if_needed(
                session_id,
                lambda _event: None,
                cancel,
                force=True,
                instructions=instructions,
            )
        finally:
            self._registry.release(session_id)

    def _compact_if_needed(
        self,
        session_id: str,
        sink: EventSink,
        cancel: Event,
        *,
        force: bool = False,
        instructions: str = "",
    ) -> bool:
        snapshot = self._sessions.load(session_id)
        cut_id = choose_cut(snapshot.entries, self._compaction)
        if cut_id is None:
            return False
        prompt = self._prompt_from(snapshot)
        tokens_before = estimate_tokens(prompt, self._compaction.chars_per_token)
        if not force and tokens_before < self._compaction.threshold_tokens:
            return False
        cut_index = next(
            (
                index
                for index, entry in enumerate(snapshot.entries)
                if entry.get("id") == cut_id
            ),
            None,
        )
        if cut_index is None:
            return False
        old = project(snapshot.entries[:cut_index])
        transcript = render_transcript(
            trim_tool_results(old.messages, self._compaction)
        )
        context = ""
        if old.summary.strip():
            context += "Earlier summary:\n" + old.summary.strip() + "\n\n"
        context += transcript
        # The summary request has to fit the same window. Keep the opening
        # (goals, constraints) and the end (the turns nearest the cut).
        max_chars = (
            self._compaction.threshold_tokens
            * self._compaction.chars_per_token
        )
        if len(context) > max_chars:
            head = max_chars // 4
            tail = max_chars - head
            context = (
                context[:head]
                + "\n[middle of the earlier conversation omitted]\n"
                + context[-tail:]
            )
        system = SUMMARY_INSTRUCTIONS
        if instructions.strip():
            system += (
                "\n\nAdditional instructions from the reader:\n"
                + instructions.strip()
            )
        run_section = render_run_section(current_wait(snapshot.entries))
        if run_section.strip():
            system += "\n\nCurrent run state:\n" + run_section
        sink(StatusEvent("compacting earlier turns", "think"))
        summary_message = collect(
            self._model.stream(
                ModelRequest(
                    model=self._model_name,
                    messages=(
                        Message("system", system),
                        Message("user", context or "(no earlier messages)"),
                    ),
                    max_tokens=self._compaction.max_summary_tokens,
                    session_id=session_id,
                    cancel=cancel,
                )
            )
        )
        summary = bounded_summary(
            summary_message.content, self._compaction
        )
        if not summary:
            return False
        usage = None
        if summary_message.usage is not None:
            usage = {
                "inputTokens": summary_message.usage.input_tokens,
                "outputTokens": summary_message.usage.output_tokens,
                "cacheReadTokens": summary_message.usage.cache_read_tokens,
            }
        self._sessions.append(
            session_id,
            compaction_entry(summary, cut_id, tokens_before, usage),
        )
        return True

    def _prompt(self, session_id: str) -> tuple[Message, ...]:
        return self._prompt_from(self._sessions.load(session_id))

    def _prompt_from(self, snapshot: SessionSnapshot) -> tuple[Message, ...]:
        projection = project(snapshot.entries)
        parts = [text for text in projection.sections.values() if text.strip()]
        if projection.summary.strip():
            parts.append("Summary of earlier conversation:\n" + projection.summary)
        run_section = render_run_section(current_wait(snapshot.entries))
        if run_section.strip():
            parts.append(run_section)
        messages = list(
            trim_tool_results(projection.messages, self._compaction)
        )
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
