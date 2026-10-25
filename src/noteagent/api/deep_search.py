"""HTTP helpers for a Deep Search round.

The session itself lives in the wiki module; this is the transport, so the same
action can be asked for either as one blocking call or as a stream that says what
is being worked on and can be abandoned.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from noteagent.api.sse import iter_job_sse
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.deep_search import act_session

#: Which step of a round each action belongs to, so a caller can group the
#: progress it is shown without knowing the action names.
_ACTION_STEP = {
    "start": "start",
    "proposeWhitelist": "whitelist",
    "togglePage": "whitelist",
    "acceptWhitelist": "whitelist",
    "proposeBlocks": "blocks",
    "toggleBlock": "blocks",
    "selectAllBlocks": "blocks",
    "acceptBlocks": "blocks",
    "enterPlan": "plan",
    "toggleCard": "plan",
    "markIngested": "ingest",
    "redo": "redo",
    "restart": "restart",
    "end": "end",
}


def run_act_job(
    layout: ProjectLayout,
    body: dict[str, Any],
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
    on_status=None,
    cancel=None,
) -> dict[str, object]:
    """
    Applies one action, reporting what it is about to do.

    The action itself is the same call the blocking route makes: this adds telling
    the caller where the work is, and letting it stop between rounds.
    """
    action = str(body.get("action") or "").strip()
    if on_status is not None:
        step = _ACTION_STEP.get(action)
        if step:
            # For the job's own log, not for display: the reader is told which step
            # by the id, and says it in its own words.
            on_status(f"deep-search: {action}", step)
    return act_session(
        layout,
        body,
        script=script,
        use_llm=use_llm,
        on_status=on_status,
        cancel=cancel,
    )


def iter_act_sse(
    layout: ProjectLayout,
    body: dict[str, Any],
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> Iterator[bytes]:
    """Runs one action as a stream: ``status`` while it works, then ``done`` or ``error``."""
    action = str(body.get("action") or "").strip()

    def worker(on_status, cancel, _emit):
        return run_act_job(
            layout,
            body,
            script=script,
            use_llm=use_llm,
            on_status=on_status,
            cancel=cancel,
        )

    return iter_job_sse(worker, default_step_id=_ACTION_STEP.get(action))


__all__ = ["iter_act_sse", "run_act_job"]
