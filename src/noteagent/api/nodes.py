"""The auto-frame node: one scoped agent run over the writing state.

A node run is an agent run with a toolset cut to the node's job and an
independent session that the chat list does not show. The state is its result:
the frames it selects are the output, and its transcript stays under the
project's sessions for debugging.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.runtime import RunResult, RunResume, UserInput
from noteagent.agent.store import SessionStore
from noteagent.agent.tools import ToolRegistry
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import kernel_runtime
from noteagent.pipeline.lock import ProjectBusy
from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingState, WritingStateStore
from noteagent.writing.tools import get_state_tool, select_frame_tool

#: Node sessions carry this prefix, so the chat list can leave them out.
NODE_PREFIX = "node-"
NODE_TITLE = "Auto-frame"
NODE_ID = re.compile(r"\Anode-[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")


def is_node_session(session_id: str) -> bool:
    return session_id.startswith(NODE_PREFIX)


def valid_node_id(session_id: str) -> bool:
    """Whether a caller may name this node session itself (for cancel)."""
    return NODE_ID.fullmatch(session_id) is not None


def auto_frame_instruction(page_id: str) -> str:
    """The one instruction the auto-frame node gets."""
    return (
        "You are the auto-frame node for the writing state. "
        f"Page {page_id} is selected. "
        f'Call get_state with {{"pageId": "{page_id}"}} to read its blocks. '
        "Group consecutive blocks into 1 to 4 meaningful frames; each frame is "
        "a list of block ids from that page. Call select_frame once per frame "
        "with pageId and blockIds. Do not write prose and do not call any "
        "other tool. When the frames are saved, answer DONE."
    )


def node_tools(deps: AppDeps, layout: ProjectLayout) -> ToolRegistry:
    """The auto-frame node's whole tool surface."""
    tools = ToolRegistry()
    tools.register(get_state_tool(layout))
    tools.register(
        select_frame_tool(
            layout,
            clock=deps.clock,
            ids=deps.ids,
            writer_kind="node",
        )
    )
    return tools


@dataclass(frozen=True, slots=True)
class NodeResult:
    """What one node run leaves for the route to answer with."""

    node_run_id: str
    session_id: str
    status: str
    error: str
    frames: tuple[Mapping[str, object], ...]


def _frames_for(
    state: WritingState, page_id: str
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        dict(frame)
        for frame in state.frames
        if str(frame.get("pageId")) == page_id
    )


def _noop(_event: object) -> None:
    """The node's transcript is the log; the route has no stream to feed."""


def _node_session(
    store: SessionStore, node_id: str, page_id: str
) -> None:
    """Create the hidden session and record what it was started for.

    The node gets no generic system sections: its instruction is its whole
    brief, and the generic sections name tools it cannot call.
    """
    store.create(node_id, NODE_TITLE)
    store.append(
        node_id,
        {"type": "node", "node": "auto-frame", "pageId": page_id},
    )


def _finish(
    writing: WritingStateStore,
    node_id: str,
    page_id: str,
    result: RunResult,
) -> NodeResult:
    if result.status == "failed" or result.error:
        writing.append_attention(
            {
                "kind": "blocked",
                "target": page_id,
                "message": result.error or "the auto-frame node failed",
            }
        )
    return NodeResult(
        node_run_id=result.run_id or node_id,
        session_id=node_id,
        status=result.status,
        error=result.error,
        frames=_frames_for(writing.load(), page_id),
    )


def _with_node_run(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout,
    node_id: str,
    action: Callable[[KernelRuntime], RunResult],
) -> RunResult:
    """Take the node's lease, build its scoped kernel, run, and release."""
    writing = WritingStateStore(layout, clock=deps.clock)
    writing.ensure_writer("node", node_id)
    try:
        runtime = kernel_runtime(
            deps,
            store,
            layout,
            node_tools(deps, layout),
            allow_propose=False,
        )
        return action(runtime)
    finally:
        try:
            writing.release_writer(node_id)
        except ProjectBusy:
            # A holder we cannot reach leaves only the stale lease behind.
            pass


def run_auto_frame(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout,
    page_id: str,
    node_id: str = "",
) -> NodeResult:
    """Run one auto-frame node; its session is new and its result is the state."""
    ident = node_id or deps.ids.new_id("node")

    def action(runtime: KernelRuntime) -> RunResult:
        _node_session(store, ident, page_id)
        return runtime.execute(
            UserInput(ident, auto_frame_instruction(page_id)),
            _noop,
        )

    result = _with_node_run(deps, store, layout, ident, action)
    return _finish(
        WritingStateStore(layout, clock=deps.clock),
        ident,
        page_id,
        result,
    )


def resume_auto_frame(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout,
    node_id: str,
    run_id: str,
) -> NodeResult:
    """Continue one interrupted node run from its last committed turn."""
    result = _with_node_run(
        deps,
        store,
        layout,
        node_id,
        lambda runtime: runtime.execute(
            RunResume(node_id, run_id), _noop
        ),
    )
    return _finish(
        WritingStateStore(layout, clock=deps.clock),
        node_id,
        page_id_for(store, node_id),
        result,
    )


def page_id_for(store: SessionStore, node_id: str) -> str:
    """The page a node was started for, from its own session record."""
    for entry in store.load(node_id).entries:
        if entry.get("type") == "node" and entry.get("pageId"):
            return str(entry["pageId"])
    return ""


__all__ = [
    "NODE_ID",
    "NODE_PREFIX",
    "NODE_TITLE",
    "NodeResult",
    "auto_frame_instruction",
    "is_node_session",
    "node_tools",
    "resume_auto_frame",
    "run_auto_frame",
    "valid_node_id",
]
