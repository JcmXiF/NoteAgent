"""A pipeline run as it happens, streamed to whoever started it.

The run is a file, not a live object: an Agent connecting from another process
reads the same state the interface does. The stream reads the run
revision and its materials, and says what changed; it ends when the run does.

The stream observes the run; it does not own it. A client that goes away does
not cancel the run, and reconnecting with the same run id picks the state up
where it stands.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from noteagent.api.sse import KEEPALIVE_SECONDS
from noteagent.pipeline.run import TERMINAL_STATUSES
from noteagent.pipeline.service import PipelineService

#: How often the stream reads the run. A stage is a model call, so this is a
#: read of a local file, not a busy loop.
POLL_SECONDS = 0.25


def iter_pipeline_events(
    service: PipelineService,
    run_id: str,
) -> Iterator[bytes]:
    """The run's events, from its state now until it settles.

    A finished run answers with its final events and the stream closes, so an
    Agent that reconnects after a restart still learns the result.
    """
    seq = 0

    def frame(kind: str, data: dict[str, Any]) -> bytes:
        nonlocal seq
        seq += 1
        return _frame(kind, {**data, "seq": seq})

    seen_stage: tuple[str, str] | None = None
    seen_detail: dict[str, object] = {}
    seen_materials: list[dict[str, object]] | None = None
    seen_waiting_revision = -1
    last_write = time.monotonic()
    while True:
        run = service.get(run_id)
        if run is None:
            yield frame(
                "error",
                {
                    "code": "pipeline.notFound",
                    "message": f"no pipeline run: {run_id}",
                    "retryable": False,
                },
            )
            return

        stage = (run.stage, run.status)
        if stage != seen_stage:
            seen_stage = stage
            last_write = time.monotonic()
            yield frame(
                "stage",
                {"runId": run.id, "stage": run.stage, "status": run.status},
            )

        materials = [
            state.payload() for state in service.materials_for_run(run.id)
        ]
        if materials != seen_materials:
            seen_materials = materials
            last_write = time.monotonic()
            yield frame(
                "material", {"runId": run.id, "materials": materials}
            )

        if run.status in TERMINAL_STATUSES:
            if run.status == "succeeded":
                yield frame(
                    "result",
                    {
                        "runId": run.id,
                        "status": run.status,
                        "result": dict(run.result),
                    },
                )
            elif run.status != "cancelled":
                # A cancellation is not an error; its stage event already said so.
                yield frame(
                    "error",
                    {
                        "runId": run.id,
                        "status": run.status,
                        "code": "job_failed",
                        "message": run.error or run.status,
                        "retryable": False,
                    },
                )
            yield frame("done", {"runId": run.id, "status": run.status})
            return

        if time.monotonic() - last_write >= KEEPALIVE_SECONDS:
            last_write = time.monotonic()
            yield b": keepalive\n\n"
        time.sleep(POLL_SECONDS)


def _frame(kind: str, data: dict[str, Any]) -> bytes:
    blob = json.dumps(data, ensure_ascii=False)
    return f"event: {kind}\ndata: {blob}\n\n".encode("utf-8")


__all__ = [
    "POLL_SECONDS",
    "iter_pipeline_events",
]
