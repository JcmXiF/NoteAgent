"""Automatic planning: a run's frames become the plan a human confirms.

The plan board is a review surface, not a stop: the stage plans from the frames
the run already made, carrying the answers a person gave to earlier plans. The
frames' blocks live in staging, so they are read from there and handed to the
existing planning job as ordinary selections; the plan that is stored keeps only
the block ids the frames already named.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from noteagent.ingest.clips import (
    MAX_INTENT_CHARS,
    ClipCollection,
    ClipStore,
    block_from_dict,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.topic_map import TopicMap, carry_over_choices, map_clips
from noteagent.pipeline.confirm import confirmation_digest
from noteagent.pipeline.materials import Materials
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.stage import PlanSummary, StageContext
from noteagent.project.layout import ProjectLayout

#: What a plan stage calls: the hydrated store in, the plan out.
PlanFn = Callable[[ProjectLayout, ClipStore, threading.Event | None], TopicMap]


class PlanError(RuntimeError):
    """The run has nothing planable, or no planner is wired."""


def default_planner(
    layout: ProjectLayout,
    store: ClipStore,
    cancel: threading.Event | None = None,
) -> TopicMap:
    """The product's planner: the existing planning job, on staged material."""
    return map_clips(
        layout,
        store=store,
        verify_sources=False,
        cancel=cancel,
    )


class Planning:
    """Plans one run from the frames it has, preserving a person's answers."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        materials: Materials,
        planner: PlanFn | None,
    ) -> None:
        self._layout = layout
        self._materials = materials
        self._planner = planner

    def plan_run(
        self, run: PipelineRun, cancel: threading.Event | None = None
    ) -> PlanSummary:
        if self._planner is None:
            raise PlanError("no planner is configured for this pipeline")
        store = load_clip_store(self._layout)
        collections, used = self._collections_for(store, run)
        if not collections:
            raise PlanError("no frames to plan for this run")
        intent = run.intent.strip()[:MAX_INTENT_CHARS]
        hydrated = replace(store, intent=intent, collections=collections)
        plan = self._planner(self._layout, hydrated, cancel)
        # The person's earlier answers outlive the plan they were given against;
        # a page the new plan no longer has drops its answer.
        carried = carry_over_choices(store.choices, plan)
        # The new plan is a proposal: ``choices`` are what a person said about
        # earlier plans, ``decisions`` are reserved for the pass they confirmed.
        # Carrying them into ``decisions`` would make this revision look already
        # confirmed before anyone has seen it.
        updated = replace(
            store,
            intent=intent,
            topic_map=plan.to_dict(),
            choices=carried,
            decisions=(),
        )
        save_clip_store(self._layout, updated)
        pages = len(plan.nodes())
        for material, frames in used:
            self._materials.mark(
                material,
                "planned",
                run_id=run.id,
                detail={"frames": frames, "pages": pages},
            )
        return PlanSummary(
            pages=pages,
            carried=len(carried),
            digest=confirmation_digest(updated),
        )

    def _collections_for(
        self, store: ClipStore, run: PipelineRun
    ) -> tuple[tuple[ClipCollection, ...], list[tuple[str, int]]]:
        """The run's frames with their block texts, and which material each is."""
        chosen: list[ClipCollection] = []
        used: list[tuple[str, int]] = []
        for state in self._materials.for_run(run.id):
            material_collections = [
                collection
                for collection in store.collections
                if collection.source_key == state.material
            ]
            if not material_collections:
                continue
            blocks = self._materials.blocks(state.material, run_id=run.id)
            if blocks is None:
                # Collected material whose staging is gone is not a plan with one
                # material silently missing.
                raise PlanError(
                    f"the staged blocks for {state.material} are gone"
                )
            by_id = {
                str(block.get("id")): block
                for block in blocks
                if block.get("id")
            }
            frames = 0
            for collection in material_collections:
                hydrated = tuple(
                    block_from_dict(by_id[block_id])
                    for block_id in collection.block_ids
                    if block_id in by_id
                )
                if hydrated:
                    chosen.append(replace(collection, blocks=hydrated))
                    frames += 1
            if frames:
                used.append((state.material, frames))
        return tuple(chosen), used


@dataclass(slots=True)
class PlanStage:
    """The stage that plans whatever the framing produced."""

    name: str = "plan"

    def __call__(self, context: StageContext) -> None:
        summary = context.plan_materials()
        context.add_detail(**summary.detail())
        context.update(plan_revision=context.run.plan_revision + 1)


__all__ = [
    "PlanError",
    "PlanFn",
    "PlanStage",
    "Planning",
    "default_planner",
]
