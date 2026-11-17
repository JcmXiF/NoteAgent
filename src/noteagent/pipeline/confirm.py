"""What a run proposes to write, and the compile that follows approval.

The plan is the model's proposal; approval is the person's. This module turns the
stored plan and the answers a person gave into the pages a run would write, and
reads the compile's outcome back as three bounded lists: written, skipped and
failed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from noteagent.ingest.auto_ingest import IngestResult
from noteagent.ingest.clips import (
    GATE_ACTIONS,
    ClipStore,
    GateDecision,
    decision_to_dict,
    load_clip_store,
)
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.topic_map import (
    TopicMap,
    TopicNode,
    carry_over_choices,
    parse_topic_map,
)
from noteagent.ingest.write_file_blocks import HARD_WRITE_CODES
from noteagent.project.layout import ProjectLayout
from noteagent.util.run_log import RunLogger


class PlanMissing(RuntimeError):
    """The plan the run refers to is no longer stored."""


class NothingToWrite(ValueError):
    """Every page is mention or skip; there is no page to write."""


@dataclass(frozen=True, slots=True)
class PageDecision:
    """One page of a confirmation: what happens to it, and which parts."""

    slug: str
    name: str
    wiki_path: str
    action: str
    section_ids: tuple[str, ...] = ()

    def confirmed(self) -> dict[str, object]:
        """The shape the compile reads for one confirmed page."""
        return {
            "name": self.name,
            "slug": self.slug,
            "wikiPath": self.wiki_path,
            "action": self.action,
            "sectionIds": list(self.section_ids),
        }


@dataclass(frozen=True, slots=True)
class Confirmation:
    """The pages a run proposes, and the answers that produced them.

    ``choices`` are the person's own answers, kept for the next plan;
    ``decisions`` are this pass's answers, the gate that says it was confirmed.
    """

    run_id: str
    intent: str
    revision: int
    topic_map: TopicMap
    pages: tuple[PageDecision, ...]
    choices: tuple[GateDecision, ...] = ()

    @property
    def writes(self) -> tuple[PageDecision, ...]:
        return tuple(page for page in self.pages if page.action == "write")

    @property
    def decisions(self) -> tuple[GateDecision, ...]:
        """The confirmed pass: one answer for every page the plan names."""
        return tuple(
            GateDecision(page.slug, page.action, page.section_ids)
            for page in self.pages
        )


@dataclass(frozen=True, slots=True)
class PageProblem:
    """One page the compile could not write, and why."""

    path: str
    reason: str

    def payload(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CompileResult:
    """What a compile did, bounded by the pages it was given."""

    written: tuple[str, ...] = ()
    skipped: tuple[PageProblem, ...] = ()
    failed: tuple[PageProblem, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "written": list(self.written),
            "skipped": [item.payload() for item in self.skipped],
            "failed": [item.payload() for item in self.failed],
        }


#: What a run calls to compile a confirmation; a test scripts it.
CompileFn = Callable[[ProjectLayout, Confirmation, threading.Event | None], CompileResult]


def derive_confirmation(
    layout: ProjectLayout,
    *,
    run_id: str,
    intent: str,
    revision: int,
    store: ClipStore,
    overrides: Sequence[Mapping[str, object]] = (),
) -> Confirmation:
    """The pages the stored plan and the person's answers say to write.

    A person's earlier answer wins over the plan's suggestion; an override at
    approval wins over both. The action is the person's, the sections are names
    for parts of the outline the plan still has.
    """
    if store.topic_map is None:
        raise PlanMissing("the run has no plan to confirm")
    plan = parse_topic_map(
        store.topic_map,
        intent=intent,
        collections=store.collections,
        layout=layout,
    )
    nodes = {node.slug: node for node in plan.nodes()}
    answers = {choice.slug: choice for choice in carry_over_choices(store.choices, plan)}
    changes = _overrides(overrides, nodes)
    choices: list[GateDecision] = []
    pages: list[PageDecision] = []
    for node in plan.nodes():
        answer = changes.get(node.slug) or answers.get(node.slug)
        if answer is not None:
            action = answer.action
            section_ids = tuple(answer.section_ids)
        else:
            action = _action_for(node)
            section_ids = tuple(section.id for section in node.outline)
        pages.append(
            PageDecision(
                slug=node.slug,
                name=(node.title or node.slug).strip(),
                wiki_path=node.wiki_path.strip(),
                action=action,
                section_ids=section_ids,
            )
        )
        if answer is not None:
            choices.append(
                GateDecision(node.slug, answer.action, tuple(answer.section_ids))
            )
    return Confirmation(
        run_id=run_id,
        intent=intent,
        revision=revision,
        topic_map=plan,
        pages=tuple(pages),
        choices=tuple(choices),
    )


def confirmation_digest(store: ClipStore) -> str:
    """A stable name for the plan and answers a confirmation would derive from.

    The run keeps this at plan time. A board that moved on without a new plan
    revision makes the digest differ, so an approval of the old revision cannot
    silently write pages the approver never saw.
    """
    payload = {
        "intent": store.intent,
        "topicMap": store.topic_map,
        "choices": sorted(
            (decision_to_dict(item) for item in store.choices),
            key=lambda item: str(item.get("slug") or ""),
        ),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def confirmation_payload(confirmation: Confirmation) -> dict[str, object]:
    """The confirmation as the interface reads it."""
    nodes = {node.slug: node for node in confirmation.topic_map.nodes()}
    return {
        "runId": confirmation.run_id,
        "intent": confirmation.intent,
        "planRevision": confirmation.revision,
        "pages": [
            {
                **page.confirmed(),
                "sections": [
                    {"id": section.id, "title": section.title}
                    for section in nodes[page.slug].outline
                ]
                if page.slug in nodes
                else [],
            }
            for page in confirmation.pages
        ],
    }


def default_compile(
    layout: ProjectLayout,
    confirmation: Confirmation,
    cancel: threading.Event | None = None,
) -> CompileResult:
    """Compile the confirmed pages through the existing write path."""
    result = ingest_confirmed(
        layout,
        confirmed=[page.confirmed() for page in confirmation.pages],
        intent=confirmation.intent,
        topic_map=confirmation.topic_map,
        store=load_clip_store(layout),
        run_logger=RunLogger.for_layout(layout),
        cancel=cancel,
    )
    return compile_result_from(result)


def compile_result_from(result: IngestResult) -> CompileResult:
    """Read the write outcome back as the three lists the run records."""
    warnings = result.write.warnings
    return CompileResult(
        written=tuple(item.relative_path for item in result.write.written),
        skipped=tuple(
            PageProblem(warning.path, warning.message)
            for warning in warnings
            if warning.code == "skipped"
        ),
        failed=tuple(
            PageProblem(warning.path, warning.message)
            for warning in warnings
            if warning.code in HARD_WRITE_CODES and warning.code != "skipped"
        ),
    )


def compile_outcome(
    confirmation: Confirmation, result: CompileResult
) -> dict[str, object]:
    """The run's record of a compile: every page's outcome, bounded by the plan.

    A page the person left as ``mention`` or ``skip`` was not attempted, so it is
    a skipped page with its action as the reason; the compile's own skipped pages
    follow.
    """
    skipped = tuple(
        PageProblem(page.wiki_path or page.slug, page.action)
        for page in confirmation.pages
        if page.action != "write"
    ) + result.skipped
    return {
        "planRevision": confirmation.revision,
        "written": list(result.written),
        "skipped": [item.payload() for item in skipped],
        "failed": [item.payload() for item in result.failed],
    }


def _action_for(node: TopicNode) -> str:
    return node.coverage if node.coverage in GATE_ACTIONS else "skip"


def _overrides(
    overrides: Sequence[Mapping[str, object]],
    nodes: Mapping[str, TopicNode],
) -> dict[str, GateDecision]:
    """The answers given at approval, checked against the plan they name."""
    out: dict[str, GateDecision] = {}
    for raw in overrides:
        if not isinstance(raw, Mapping):
            raise ValueError("each page override must be an object")
        slug = str(raw.get("slug") or "").strip()
        if not slug:
            raise ValueError("a page override needs a slug")
        node = nodes.get(slug)
        if node is None:
            raise ValueError(f"no page {slug!r} in this plan")
        action = str(raw.get("action") or "").strip()
        if action not in GATE_ACTIONS:
            raise ValueError(f"unknown page action: {action!r}")
        raw_sections = raw.get("sectionIds") or raw.get("section_ids") or []
        if not isinstance(raw_sections, (list, tuple)):
            raise ValueError("sectionIds must be a list")
        supplied = [
            str(item).strip()
            for item in raw_sections
            if isinstance(item, str) and item.strip()
        ]
        allowed = {section.id for section in node.outline}
        section_ids = [item for item in supplied if item in allowed]
        if supplied and not section_ids:
            raise ValueError(f"no section of {slug!r} was named")
        out[slug] = GateDecision(slug, action, tuple(section_ids))
    return out


__all__ = [
    "CompileFn",
    "CompileResult",
    "Confirmation",
    "NothingToWrite",
    "PageDecision",
    "PageProblem",
    "PlanMissing",
    "compile_outcome",
    "compile_result_from",
    "confirmation_digest",
    "confirmation_payload",
    "default_compile",
    "derive_confirmation",
]
