"""The writing task of the active project, and its production nodes.

The task belongs to the core, so a route does not keep one: it asks for the
active project's service, built with the clock, ids, spawn and registry the app
was composed with. The default nodes wrap the existing acquire search, fetch and
staging area; the task executor only sees pure functions that never touch the
task.
"""

from __future__ import annotations

from collections.abc import Mapping

from noteagent.acquire.run_fetch import run_web_fetch
from noteagent.acquire.run_search import run_web_search
from noteagent.acquire.types import Capture
from noteagent.agent.clock import Clock
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.pipeline.materials import Materials
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import (
    FetchFn,
    MaterialBlock,
    SearchCard,
    SearchFn,
    StagedMaterial,
    StageFn,
    frame_to_dict,
)
from noteagent.task.service import (
    TaskBadSpec,
    TaskConflict,
    TaskNotFound,
    TaskOpFailed,
    TaskService,
)
from noteagent.util.run_log import RunLogger


def task_service(deps: AppDeps) -> TaskService:
    """The active project's task, or the one a test handed in."""
    if deps.tasks is not None:
        return deps.tasks
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError as err:
        raise CodedHTTPException(409, str(err), code="project.notConfigured") from err
    return task_service_for(deps, layout)


def task_service_for(deps: AppDeps, layout: ProjectLayout) -> TaskService:
    """The task service of one named project, for a caller with its layout."""
    if deps.tasks is not None:
        return deps.tasks
    search = (
        deps.task_search
        if deps.task_search is not None
        else default_search(layout)
    )
    fetch = (
        deps.task_fetch if deps.task_fetch is not None else default_fetch(layout)
    )
    stage = (
        deps.task_stage
        if deps.task_stage is not None
        else default_stage(layout, deps.clock)
    )
    return TaskService(
        layout,
        clock=deps.clock,
        ids=deps.ids,
        search=search,
        fetch=fetch,
        stage=stage,
        registry=deps.task_registry,
        spawn=deps.task_spawn,
        wait_s=deps.task_wait_s,
        pick_wait_s=deps.task_pick_wait_s,
    )


def task_view_payload(deps: AppDeps, view: str) -> dict[str, object]:
    """A bounded task view for the interface, with its revision."""
    payload, _rev = task_service(deps).inspect(view)
    return payload


def pick_task_card(
    deps: AppDeps, card_id: str, expected_rev: int
) -> dict[str, object]:
    """A person's pick, as a typed op naming the revision they saw.

    The person is a client of the same task and executor as the agent, so the
    pick lands in the one task both read. A refusal keeps the spec's names:
    ``conflict``, ``not_found`` or ``op_failed``.
    """
    try:
        outcome = task_service(deps).pick(
            card_id,
            actor="human",
            expected_rev=expected_rev,
        )
    except TaskConflict as err:
        raise CodedHTTPException(409, str(err), code="conflict") from err
    except TaskNotFound as err:
        raise CodedHTTPException(404, str(err), code="not_found") from err
    except TaskOpFailed as err:
        raise CodedHTTPException(502, str(err), code="op_failed") from err
    body: dict[str, object] = {
        "opId": outcome.op_id,
        "status": outcome.status,
        "rev": outcome.rev,
    }
    if outcome.material:
        body["material"] = dict(outcome.material)
    return body


def frame_task_material(
    deps: AppDeps, doc_id: str, blocks: list[str], expected_rev: int
) -> dict[str, object]:
    """A person's frame, as a pure-state op naming the revision they saw.

    It lands in the one task the chat reads, through the same service, writer
    lock and revision as the agent's frames. A refusal keeps the spec's names:
    ``conflict``, ``not_found`` or ``bad_spec``.
    """
    try:
        outcome = task_service(deps).frame(
            doc_id,
            blocks,
            actor="human",
            expected_rev=expected_rev,
        )
    except TaskConflict as err:
        raise CodedHTTPException(409, str(err), code="conflict") from err
    except TaskNotFound as err:
        raise CodedHTTPException(404, str(err), code="not_found") from err
    except TaskBadSpec as err:
        raise CodedHTTPException(422, str(err), code="bad_spec") from err
    return {
        "opId": outcome.op_id,
        "status": outcome.status,
        "rev": outcome.rev,
        "approvalCleared": outcome.approval_cleared,
        "frame": frame_to_dict(outcome.frame),
    }


def default_search(layout: ProjectLayout) -> SearchFn:
    """The project's configured web search, as a pure card-returning node."""
    logger = RunLogger.for_layout(layout)

    def search(query: str) -> list[SearchCard]:
        run = run_web_search(
            query, top_k=8, run_logger=logger, caller="task"
        )
        return [
            SearchCard(
                title=card.title,
                url=card.url,
                snippet=card.snippet,
                site=card.site or card.site_name,
            )
            for card in run.cards
        ]

    return search


def default_fetch(layout: ProjectLayout) -> FetchFn:
    """The project's public-page fetch, as a pure capture-returning node."""
    logger = RunLogger.for_layout(layout)

    def fetch(url: str) -> Capture:
        return run_web_fetch(url, run_logger=logger, caller="task")

    return fetch


def default_stage(layout: ProjectLayout, clock: Clock) -> StageFn:
    """The project's staging area, as a pure capture-to-material node.

    The node writes the staging directory and the material log; it never reads
    or writes the task. The block index comes from the manifest the staging
    wrote, so no full block text enters the task.
    """
    materials = Materials(layout, clock=clock)

    def stage(capture: Capture, run_id: str) -> StagedMaterial:
        state = materials.stage(capture, run_id=run_id)
        manifest = materials.manifest(state.material, run_id=run_id) or {}
        blocks: list[MaterialBlock] = []
        raw_blocks = manifest.get("blocks")
        if isinstance(raw_blocks, list):
            for item in raw_blocks:
                if not isinstance(item, Mapping):
                    continue
                blocks.append(
                    MaterialBlock(
                        id=str(item.get("id") or ""),
                        tag=str(item.get("tag") or ""),
                        preview=str(item.get("preview") or ""),
                    )
                )
        count = manifest.get("blockCount")
        return StagedMaterial(
            material=state.material,
            title=str(manifest.get("title") or state.title),
            url=str(manifest.get("sourceUrl") or capture.url),
            kind=str(manifest.get("kind") or ""),
            fidelity=str(manifest.get("fidelity") or capture.fidelity),
            state=state.state,
            block_count=count if isinstance(count, int) else len(blocks),
            blocks=tuple(blocks),
        )

    return stage


__all__ = [
    "default_fetch",
    "default_search",
    "default_stage",
    "frame_task_material",
    "pick_task_card",
    "task_service",
    "task_service_for",
    "task_view_payload",
]
