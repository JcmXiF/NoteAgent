"""A tool is its ``ToolSpec``: what the model may call, and what the runtime owns.

The spec carries the description the model reads, the JSON schema its arguments
are checked against, the side effect it is allowed to have, and the handler that
does the work. The registry is the only place a spec is registered, so a caller
can find a tool, list the enabled ones, or turn them into model function calls
without a central dispatch table.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from threading import Event
from typing import Literal, TypeAlias

from noteagent.agent.clock import Clock
from noteagent.agent.model import ToolCall
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tool_schema import ToolArgumentError, validate_arguments

#: What a call is allowed to touch.
SideEffect: TypeAlias = Literal["read", "network", "human"]

#: The names a model may be offered.
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: The parts of a tool's ``meta`` an interface may see. Everything else a handler
#: puts there is the handler's own scratch space and stays out of the event.
OBSERVATION_FIELDS = (
    "title",
    "target",
    "url",
    "path",
    "slug",
    "scope",
    "query",
    "match",
    "hits",
    "missing",
    "unsafe",
    "error",
    "timeout",
    "omitted",
    "truncated",
    "not_source",
    "needs_human",
    "job",
    "fidelity",
    "http_status",
    "action",
    "wrote_wiki",
)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Something the runtime noticed about a result, for the interface or log."""

    severity: Literal["info", "warning", "error"] = "info"
    code: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A result in three channels: content for the model, meta for the UI."""

    content: str
    meta: Mapping[str, object] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    def observation(self, tool: str) -> dict[str, object]:
        """The card this result shows: a whitelist, not the handler's meta.

        ``meta`` is the handler's own scratch space, so a key added there must
        not silently widen what leaves the core. The model's text stays behind:
        a card says what was touched, not what it said.
        """
        card: dict[str, object] = {"tool": tool}
        for key in OBSERVATION_FIELDS:
            if key in self.meta:
                card[key] = self.meta[key]
        if self.diagnostics:
            card["diagnostics"] = [
                {
                    "severity": item.severity,
                    "code": item.code,
                    "message": item.message,
                }
                for item in self.diagnostics
            ]
        return card


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a handler may rely on while it runs."""

    session_id: str
    run_id: str = ""
    cancel: Event | None = None
    clock: Clock | None = None


ToolHandler: TypeAlias = Callable[[Mapping[str, object], ToolContext], ToolResult]


@dataclass(frozen=True, slots=True)
class OutputLimits:
    """How much a tool may return, counted and size-bounded.

    ``max_chars_total`` is the registry's hard cap, because it can count one
    string. ``max_items`` and ``max_chars_per_item`` are the handler's budget:
    only it knows where one item ends and the next begins, so it builds within
    them and the registry catches whatever is left over.
    """

    max_items: int = 6
    max_chars_per_item: int = 8_000
    max_chars_total: int = 24_000


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One thing the model may call."""

    name: str
    description: str
    parameters: Mapping[str, object]
    handler: ToolHandler
    side_effect: SideEffect = "read"
    timeout_s: float = 30.0
    output_limits: OutputLimits = field(default_factory=OutputLimits)
    enabled: bool = True
    argument_hint: str = ""


class UnknownToolError(LookupError):
    """A name the registry does not hold."""


class ToolRegistry:
    """Every tool the run may offer, in the order they were registered."""

    def __init__(self, tools: Iterable[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if not TOOL_NAME.match(tool.name):
            raise ValueError(f"invalid tool name: {tool.name!r}")
        if not tool.description.strip():
            raise ValueError(f"tool {tool.name!r} has no description")
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolSpec:
        tool = self.get(name)
        if tool is None:
            raise UnknownToolError(name)
        return tool

    def enabled(self) -> tuple[ToolSpec, ...]:
        return tuple(tool for tool in self._tools.values() if tool.enabled)

    def model_tools(self) -> tuple[dict[str, object], ...]:
        """The enabled tools as the OpenAI-compatible function list."""
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                },
            }
            for tool in self.enabled()
        )

    def dispatch(
        self,
        name: str,
        arguments: str | Mapping[str, object],
        context: ToolContext,
    ) -> ToolResult:
        """Check one call, run it within its time, and bound what it returns.

        A bad call is an observation, not an exception: the loop continues and
        the model decides whether to correct it. A cancellation is the one thing
        that is not an observation, because the run must end rather than read it.
        """
        prepared = self._prepare(name, arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        spec, values = prepared
        call = _PendingCall(spec)
        call.start(values, context)
        return self._finish(spec, call.outcome(spec.timeout_s, context.cancel))

    def dispatch_many(
        self, calls: Sequence[ToolCall], context: ToolContext
    ) -> tuple[ToolResult, ...]:
        """Run one round's calls at the same time, answering in the asked order.

        The model writes its calls in the order it means them; the log keeps that
        order even when a later call finishes first, so the prompt and the cards
        read the same way on every replay.
        """
        if not calls:
            return ()
        prepared = [self._prepare(call.name, call.arguments) for call in calls]
        pending: dict[int, tuple[_PendingCall, ToolSpec]] = {}
        results: list[ToolResult | None] = [None] * len(calls)
        for index, item in enumerate(prepared):
            if isinstance(item, ToolResult):
                results[index] = item
                continue
            spec, values = item
            call = _PendingCall(spec)
            call.start(values, context)
            pending[index] = (call, spec)
        for index, (call, spec) in pending.items():
            results[index] = self._finish(
                spec, call.outcome(spec.timeout_s, context.cancel)
            )
        finished = [result for result in results if result is not None]
        if len(finished) != len(calls):  # pragma: no cover - defensive
            raise RuntimeError("a tool call produced no result")
        return tuple(finished)

    def _prepare(
        self, name: str, arguments: str | Mapping[str, object]
    ) -> tuple[ToolSpec, dict[str, object]] | ToolResult:
        spec = self.get(name)
        if spec is None or not spec.enabled:
            return _error(name, f"unknown tool: {name}", code="unknown_tool")
        values = _arguments(name, arguments)
        if isinstance(values, ToolResult):
            return values
        try:
            validate_arguments(spec.parameters, values)
        except ToolArgumentError as err:
            return _error(
                name,
                f"invalid arguments for {name}: {err}",
                code="invalid_arguments",
            )
        return spec, values

    def _finish(self, spec: ToolSpec, result: ToolResult) -> ToolResult:
        """Apply the one cap the registry can count without knowing the items."""
        limit = spec.output_limits.max_chars_total
        meta = dict(result.meta)
        diagnostics = list(result.diagnostics)
        content = result.content
        if limit > 0 and len(content) > limit:
            marker = f"\n\n[truncated at {limit} characters]"
            if len(marker) >= limit:
                content = content[:limit]
            else:
                content = content[: limit - len(marker)] + marker
            meta["truncated"] = True
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "truncated",
                    f"{spec.name} returned more than {limit} characters",
                )
            )
        return ToolResult(content, meta, tuple(diagnostics))


class _PendingCall:
    """One handler running on its own thread, so a timeout can be enforced.

    The thread is a daemon: a handler that ignores its cancellation and never
    returns must not keep the process alive after the run has moved on.
    """

    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec
        self._result: ToolResult | None = None
        self._failure: BaseException | None = None
        self._arguments: Mapping[str, object] = {}
        self._context = ToolContext("")
        self._thread = threading.Thread(
            target=self._run,
            name=f"tool:{spec.name}",
            daemon=True,
        )

    def start(self, arguments: Mapping[str, object], context: ToolContext) -> None:
        self._arguments = arguments
        self._context = context
        self._started_at = time.monotonic()
        self._thread.start()

    def outcome(self, timeout_s: float, cancel: Event | None = None) -> ToolResult:
        # The budget starts when the call does, not when its turn in the batch
        # comes up: a fast tool behind a slow sibling must still be bounded.
        deadline = self._started_at + timeout_s
        while self._thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _error(
                    self._spec.name,
                    f"tool {self._spec.name} timed out after {timeout_s:g}s",
                    code="timeout",
                    timeout=True,
                )
            # A tool that ignores its cancellation must not hold the run for the
            # rest of its timeout; the stop is checked between short waits.
            self._thread.join(min(0.05, remaining))
            if self._thread.is_alive() and cancel is not None and cancel.is_set():
                raise RunCancelled("the run was cancelled")
        failure = self._failure
        if failure is not None:
            if isinstance(failure, RunCancelled):
                raise failure
            if not isinstance(failure, Exception):
                raise failure
            return _error(
                self._spec.name,
                f"tool error: {failure}",
                code="handler_error",
            )
        if self._result is None:
            return _error(
                self._spec.name,
                f"tool {self._spec.name} returned no result",
                code="handler_error",
            )
        return self._result

    def _run(self) -> None:
        try:
            self._result = self._spec.handler(self._arguments, self._context)
        except BaseException as err:  # noqa: BLE001 - reported to the loop
            self._failure = err


def _arguments(
    name: str, raw: str | Mapping[str, object]
) -> dict[str, object] | ToolResult:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as err:
        return _error(
            name, f"invalid JSON for {name}: {err.msg}", code="invalid_arguments"
        )
    if not isinstance(data, Mapping):
        return _error(
            name,
            f"invalid arguments for {name}: arguments must be an object",
            code="invalid_arguments",
        )
    return dict(data)


def _error(
    name: str,
    message: str,
    *,
    code: str,
    timeout: bool = False,
) -> ToolResult:
    meta: dict[str, object] = {"title": name, "error": True}
    if timeout:
        meta["timeout"] = True
    return ToolResult(
        content=message,
        meta=meta,
        diagnostics=(Diagnostic("error", code, message),),
    )
