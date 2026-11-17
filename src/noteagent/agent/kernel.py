"""The hand-built ReAct loop: model, tool rounds, and the turn boundary.

The loop reads the session through the projection, asks the model for one turn,
streams what it writes, and commits an entry only at a boundary. A tool call is a
step: the preamble is taken back from the answer, its result is committed, and
the next round starts from what the log now says.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Iterator, Mapping
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
    RunEvent,
    StatusEvent,
    TokenEvent,
    WaitEvent,
)
from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ModelRetryEvent,
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
from noteagent.agent.run_marker import (
    CANCELLED,
    FAILED,
    FINISHED,
    INTERRUPTED,
    RUNNING,
    WAITING,
    RunFingerprint,
    RunMarker,
    last_marker,
    marker_entry,
    missing_tool_calls,
)
from noteagent.agent.runtime import (
    BusyError,
    EventSink,
    Resume,
    Retry,
    RunCancelled,
    RunPaused,
    RunRef,
    RunRequest,
    RunResult,
    RunResume,
    TraceSink,
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

logger = logging.getLogger(__name__)

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

#: One recovery note after a provider leaked a tool call as text. The note is
#: transient; it is not a step budget.
LEAK_RETRY = (
    "Your previous response contained tool-call markup instead of an answer. "
    "Do not write tool-call markup. If the reader asked for a page/job, make "
    "one valid, lone `propose` call now; otherwise answer in prose from the "
    "evidence above; if a read tool is still needed, say that the tool budget "
    "is exhausted and what is missing."
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
        trace: TraceSink | None = None,
        model_name: str = "",
        #: One safety bound for the whole run: model calls, not tool rounds.
        #: Coding agents usually expose one turn budget and let the loop end on
        #: a final answer or a control action.
        max_turns: int = 50,
        compaction: CompactionLimits | None = None,
        #: Non-secret project/config digests a run marker records. The kernel
        #: stays generic: the composition root decides what the project is.
        fingerprint: Mapping[str, str] | None = None,
    ) -> None:
        self._model = model
        self._trace = trace
        self._sessions = sessions
        self._tools = tools or ToolRegistry()
        # A wait is read from the same log as the turns, so the default keeps
        # the runtime and the session view looking at one record.
        self._waits = waits if waits is not None else SessionWaitStore(sessions)
        self._clock = clock or SystemClock()
        self._ids = ids or UuidIds()
        self._registry = registry or RunRegistry()
        self._model_name = model_name
        self._max_turns = max_turns
        self._compaction = compaction or CompactionLimits()
        self._fingerprint = dict(fingerprint or {})

    def execute(self, request: RunRequest, sink: EventSink) -> RunResult:
        if isinstance(request, Retry):
            raise ValueError("no route opens a retry yet")
        session_id = request.session_id
        cancel = self._registry.claim(session_id)
        pause = request.pause
        run_id = self._ids.new_id("run")
        sink = self._reporting(sink, RunRef(session_id, run_id))
        try:
            if isinstance(request, RunResume):
                return self._run_crash_resume(
                    request, sink, run_id, cancel, pause
                )
            if isinstance(request, Resume):
                return self._run_resume(
                    request, sink, run_id, cancel, pause
                )
            return self._run_user(request, sink, run_id, cancel, pause)
        finally:
            self._registry.release(session_id)

    def _reporting(self, sink: EventSink, run: RunRef) -> EventSink:
        """The sink a run reports to: the trace first, then the caller.

        Tracing is observation, not a condition of the run. A trace that cannot
        be written must not stop an answer, so its failure is logged once and
        the run continues without it.
        """
        trace = self._trace
        if trace is None:
            return sink
        warned = False

        def warn(err: Exception) -> None:
            nonlocal warned
            if not warned:
                warned = True
                logger.warning("the session trace cannot be written: %s", err)

        try:
            trace.started(run)
        except Exception as err:
            warn(err)

        def report(event: RunEvent) -> None:
            try:
                trace.record(run, event)
            except Exception as err:
                warn(err)
            sink(event)

        return report

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
        pause: Event | None = None,
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
        return self._run_loop(session_id, run_id, sink, cancel, pause=pause)

    def _run_resume(
        self,
        request: Resume,
        sink: EventSink,
        run_id: str,
        cancel: Event,
        pause: Event | None = None,
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
        return self._run_loop(session_id, run_id, sink, cancel, pause=pause)

    def _run_crash_resume(
        self,
        request: RunResume,
        sink: EventSink,
        run_id: str,
        cancel: Event,
        pause: Event | None = None,
    ) -> RunResult:
        """Continue a run a crash left at its last committed turn.

        An unfinished tool call has no result; it gets one that says the
        outcome is unknown, and the model is told not to assume it ran. The old
        marker stays where it is; the new run names it in ``resumes``.
        """
        session_id = request.session_id
        try:
            _check_cancel(cancel)
            snapshot = self._sessions.load(session_id)
            marker = last_marker(snapshot.entries)
            if (
                marker is None
                or marker.state != INTERRUPTED
                or marker.run_id != request.run_id
            ):
                raise ValueError(
                    f"session {session_id} is not interrupted on "
                    f"run {request.run_id}"
                )
            missing = missing_tool_calls(
                snapshot.entries, request.run_id
            )
            for call_id, name in missing:
                self._sessions.append(
                    session_id,
                    {
                        "type": "message",
                        "role": "toolResult",
                        "toolCallId": call_id,
                        "name": name,
                        "content": (
                            "outcome unknown: the run was interrupted "
                            "before this result was committed"
                        ),
                        "meta": {"interrupted": True},
                    },
                )
            note = (
                "Resume after an interruption. The tool result(s) above "
                "have an unknown outcome; do not assume they ran."
                if missing
                else (
                    "Resume after an interruption. Continue from the last "
                    "committed turn."
                )
            )
            return self._run_loop(
                session_id,
                run_id,
                sink,
                cancel,
                resumes=request.run_id,
                note=note,
                pause=pause,
            )
        except RunCancelled:
            return self._cancelled_result(session_id, run_id, sink, "")

    def _run_loop(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
        cancel: Event,
        *,
        resumes: str = "",
        note: str = "",
        pause: Event | None = None,
    ) -> RunResult:
        # What the current model call has streamed but not yet committed. A stop
        # keeps it, so the reader can see where the answer ended.
        partial: list[str] = []
        try:
            self._mark(
                session_id,
                run_id,
                RUNNING,
                resumes=resumes,
                note=note,
            )
            sink(StatusEvent("thinking", "think"))
            model_calls = 0
            overflow_retried = False
            leak_retry_used = False
            leak_retry_note = False

            while True:
                _check_cancel(cancel)
                _check_pause(pause)
                model_calls += 1
                if model_calls > self._max_turns:
                    message = (
                        "the run exceeded "
                        f"{self._max_turns} model turns"
                    )
                    self._mark(
                        session_id,
                        run_id,
                        FAILED,
                        note="max_turns_exceeded",
                    )
                    sink(ErrorEvent("max_turns_exceeded", message))
                    return RunResult(
                        session_id, run_id, "failed", error=message
                    )
                try:
                    self._compact_if_needed(
                        session_id, sink, cancel, pause=pause
                    )
                except ModelStreamError as err:
                    return self._compaction_failure(
                        session_id, run_id, sink, err
                    )
                messages = self._prompt(session_id)
                if leak_retry_note:
                    messages = messages + (Message("system", LEAK_RETRY),)
                offered = self._offered_tools()
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
                        _tap(
                            self._model.stream(model_request),
                            sink,
                            cancel,
                            pause,
                            partial,
                        )
                    )
                except ModelStreamError as err:
                    if err.code == "cancelled":
                        # One cancellation path, whichever layer noticed first.
                        raise RunCancelled("run cancelled") from err
                    if err.code == "tool_call_leak" and not leak_retry_used:
                        # The adapter could not recover a leaked tool call.
                        # Ask once more, in prose, with the failure named.
                        leak_retry_used = True
                        leak_retry_note = True
                        sink(TokenEvent("", reset=True))
                        partial.clear()
                        continue
                    if err.code == "context_overflow" and not overflow_retried:
                        overflow_retried = True
                        try:
                            compacted = self._compact_if_needed(
                                session_id,
                                sink,
                                cancel,
                                force=True,
                                pause=pause,
                            )
                        except ModelStreamError as summary_err:
                            return self._compaction_failure(
                                session_id, run_id, sink, summary_err
                            )
                        if compacted:
                            # The prompt is smaller now; ask once more.
                            continue
                    text = "".join(partial).strip()
                    if text:
                        # The provider wrote this much before it failed. Keep
                        # it, but mark it incomplete so nothing reads it as a
                        # finished answer; a retry here would repeat the text.
                        self._sessions.append(
                            session_id,
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": text[:MAX_ABORTED_CHARS],
                                "stopReason": "incomplete",
                            },
                        )
                    self._mark(
                        session_id,
                        run_id,
                        FAILED,
                        note=err.code,
                    )
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
                leak_retry_note = False
                if assistant.tool_calls:
                    calls = assistant.tool_calls
                    refusal: ToolResult | None = None
                    if len(calls) == 1 and calls[0].name == PROPOSE_NAME:
                        proposal, refusal = self._read_proposal(session_id, calls[0])
                        if proposal is not None:
                            return self._wait_for_proposal(
                                session_id,
                                run_id,
                                sink,
                                assistant,
                                proposal,
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
                    sink(StatusEvent("thinking", "think"))
                    continue

                answer = assistant.content.strip()
                if not answer:
                    message = "the model returned no answer"
                    self._mark(
                        session_id,
                        run_id,
                        FAILED,
                        note="model_error",
                    )
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
                self._mark(session_id, run_id, FINISHED)
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
        except RunPaused:
            return self._paused_result(session_id, run_id, sink)
        except Exception as err:
            self._mark(
                session_id,
                run_id,
                FAILED,
                note=f"{type(err).__name__}: {err}"[:200],
            )
            raise

    def _offered_tools(self) -> tuple[dict[str, object], ...]:
        """The tools the model may call, with the control action beside them."""
        return (PROPOSE_TOOL,) + self._tools.model_tools()

    def _toolset_name(self) -> str:
        """A stable name for what this run could do, recorded on its marker."""
        names = ["propose"]
        names.extend(tool.name for tool in self._tools.enabled())
        return "tools:" + ",".join(names)

    def _mark(
        self,
        session_id: str,
        run_id: str,
        state: str,
        *,
        note: str = "",
        resumes: str = "",
        reason: str = "",
    ) -> None:
        """Record where the run stands, without making the marker a condition.

        The transcript is the truth; a marker that cannot be written is a lost
        recovery hint, not a reason to lose the answer that was just committed.
        """
        try:
            snapshot = self._sessions.load(session_id)
            last = snapshot.entries[-1].get("id") if snapshot.entries else ""
            config_hash = _sections_digest(project(snapshot.entries).sections)
            fingerprint = RunFingerprint.from_mapping(
                self._fingerprint,
                model=self._model_name,
                toolset=self._toolset_name(),
                config_hash=config_hash,
            )
            marker = RunMarker(
                run_id=run_id,
                state=state,
                last_entry_id=last if isinstance(last, str) else "",
                fingerprint=fingerprint,
                resumes=resumes,
                note=note,
                reason=reason,
                created_at=iso_timestamp(self._clock),
            )
            self._sessions.append(session_id, marker_entry(marker))
        except Exception:
            logger.exception("the run marker could not be written")

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

    def _wait_for_proposal(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
        assistant: AssistantMessage,
        proposal: Proposal,
    ) -> RunResult:
        """Commit the proposal's prose, record the wait, and end the run."""
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
        self._mark(session_id, run_id, WAITING)
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
        self._mark(session_id, run_id, FAILED, note=err.code)
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
        self._mark(session_id, run_id, CANCELLED, note="cancelled")
        sink(ErrorEvent("cancelled", "the run was cancelled", retryable=True))
        return RunResult(session_id, run_id, "cancelled")

    def _paused_result(
        self,
        session_id: str,
        run_id: str,
        sink: EventSink,
    ) -> RunResult:
        """Stop at the last committed turn; a reader that comes back resumes."""
        self._mark(
            session_id,
            run_id,
            INTERRUPTED,
            reason="client_disconnect",
        )
        sink(
            DoneEvent(
                {"id": session_id},
                {"id": run_id, "status": "interrupted"},
            )
        )
        return RunResult(session_id, run_id, "interrupted")

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
        pause: Event | None = None,
    ) -> bool:
        _check_pause(pause)
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
        _check_pause(pause)
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
    pause: Event | None,
    partial: list[str],
) -> Iterator[ModelEvent]:
    """Report text as it arrives, keep what a stop would keep, pass it on."""
    for event in stream:
        _check_cancel(cancel)
        _check_pause(pause)
        if isinstance(event, ModelRetryEvent):
            sink(
                StatusEvent(
                    f"model call failed ({event.code}); retrying "
                    f"{event.attempt}/{event.max_retries} "
                    f"in {event.delay_s:.1f}s",
                    "think",
                )
            )
        if isinstance(event, ModelTextDelta):
            partial.append(event.text)
            sink(TokenEvent(event.text))
        yield event


def _sections_digest(sections: Mapping[str, str]) -> str:
    """A stable digest of the prompt sections a run was started with."""
    if not sections:
        return ""
    digest = hashlib.sha256()
    for name in sorted(sections):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sections[name].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise RunCancelled("run cancelled")


def _check_pause(pause: Event | None) -> None:
    if pause is not None and pause.is_set():
        raise RunPaused("run paused")
