"""What each model call is given, and what is done with what it answers.

A rule this project learned the hard way: a check applied to a model's answer may
only depend on information that went into the request. Break it and only two things
can happen — the answer is refused for a reason the model could not have known, or
it is quietly altered by something nobody saw. Both are worse than a model that gets
an instruction wrong, because neither is visible where the work happens.

So each call declares what it is given and what checks its answer, and the checker
below enforces the rule mechanically. The declarations are hand-written — nothing can
derive what a check depends on — but from then on a violation is a test failure
rather than a surprise in production, and a new model call cannot be added without
declaring its contract.

Kinds of post-check, because they are not the same thing:

- ``gate`` refuses or drops part of an answer.
- ``clamp`` alters an answer (caps, defaults, markers).
- ``report`` only tells the reader afterwards (the doctor). It decides nothing, so it
  is outside the rule: a report may need the whole project, and no request can carry
  that.

Counted by the rule: gates and clamps. A default that only fills a field the model
left empty is a clamp, since a hidden input can still change the outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where the calls live, relative to the package root.
_PACKAGE = Path(__file__).resolve().parent.parent

_CALL_SITE = re.compile(r"\.(?:chat|complete)\(")


@dataclass(frozen=True, slots=True)
class PostCheck:
    """Something done to a model's answer before it is used."""

    name: str
    kind: str  # gate | clamp | report
    #: Information the check reads. Constants a check compares against are not
    #: information and need not be listed.
    needs: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One place the model is asked something."""

    name: str
    #: The file that makes the call, relative to the package root.
    source: str
    #: What went into the request: the prompt's content, and the call's settings.
    given: frozenset[str]
    #: What happens to the answer before it is used.
    checks: tuple[PostCheck, ...] = ()


#: Checks that break the rule, with the reason each one is allowed.
#:
#: A violation is not a bug to be tolerated silently. Each one here is a decision the
#: project has made deliberately, and the checker holds the list exact: adding a new
#: violation fails, and so does fixing one without removing its entry. It is empty at
#: the moment, which is the point of keeping it.
KNOWN_EXCEPTIONS: dict[str, str] = {}

MODEL_CALLS: tuple[ModelCall, ...] = (
    ModelCall(
        name="cli-chat",
        source="__main__.py",
        given=frozenset({"question"}),
    ),
    ModelCall(
        name="page-merge",
        source="prompts/merge.py",
        given=frozenset({"page-body", "incoming-body", "source-identity"}),
    ),
    ModelCall(
        # R7b: the same two texts and the refusal, asked once more. The check that
        # refuses it is the merge's own, so it may read what this request carried.
        name="page-merge-repair",
        source="prompts/merge.py",
        given=frozenset(
            {"page-body", "incoming-body", "source-identity", "refusal-reason"}
        ),
        checks=(PostCheck("merge-structure", "gate", frozenset({"page-body", "incoming-body"})),),
    ),
    ModelCall(
        name="round-pages",
        source="wiki/deep_search.py",
        given=frozenset(
            {"intent", "seed", "graph-summary", "wiki-index", "leftover-skim", "mode"}
        ),
        checks=(
            PostCheck("cap-whitelist", "clamp"),
            # Both of these weigh the answer against the graph, and the graph they may
            # read is the one the prompt carried: it is capped by rows and, for a
            # seeded round, is one neighbourhood of the project. Reading the project
            # instead would let a page the model never saw decide the outcome.
            PostCheck("default-page-action", "clamp", frozenset({"graph-summary"})),
            PostCheck("drawable-overlay-edges", "gate", frozenset({"graph-summary"})),
        ),
    ),
    ModelCall(
        name="round-blocks",
        source="wiki/deep_search.py",
        given=frozenset({"intent", "wiki-titles", "leftover-previews"}),
        checks=(
            PostCheck("cap-blocks", "clamp"),
            # A proposed passage has to be one that was on offer, or the round would
            # be holding a passage nobody can open.
            PostCheck("restrict-to-leftover", "gate", frozenset({"leftover-previews"})),
        ),
    ),
    ModelCall(
        name="topic-map",
        source="ingest/topic_map.py",
        given=frozenset(
            {"clips", "catalog", "wiki-index", "brief", "targets"}
        ),
        checks=(
            PostCheck("known-collection-ids", "gate", frozenset({"clips", "catalog"})),
            PostCheck("known-block-ids", "gate", frozenset({"clips"})),
            # Reads the same list the request carries.
            PostCheck("within-targets", "gate", frozenset({"targets"})),
            PostCheck("write-atomic", "gate", frozenset({"clips"})),
            PostCheck("cap-candidates", "clamp"),
            PostCheck("infer-coverage", "clamp", frozenset({"clips"})),
        ),
    ),
    ModelCall(
        name="page-blueprint",
        source="ingest/page_agents.py",
        given=frozenset(
            {"page-name", "wiki-path", "collection-catalog", "clips", "logic-links", "brief"}
        ),
        checks=(
            PostCheck("known-contributions", "gate", frozenset({"clips", "collection-catalog"})),
        ),
    ),
    ModelCall(
        name="page-draft",
        source="ingest/page_agents.py",
        given=frozenset(
            {
                "page-name",
                "role",
                "section-title",
                "instruction",
                "collection-text",
                "collection-id",
                "brief",
            }
        ),
        checks=(PostCheck("clip-marker", "clamp", frozenset({"collection-id"})),),
    ),
    ModelCall(
        name="page-compose",
        source="ingest/page_agents.py",
        given=frozenset(
            {
                "intent",
                "page-name",
                "wiki-path",
                "wiki-date",
                "mentions",
                "locators",
                "blueprint",
                "drafts",
                "logic-links",
                "brief",
            }
        ),
        checks=(
            PostCheck("file-path", "gate", frozenset({"wiki-path"})),
            PostCheck("locators-known", "gate", frozenset({"locators"})),
            PostCheck("clip-markers", "clamp", frozenset({"blueprint"})),
            PostCheck("logic-wikilinks", "clamp", frozenset({"logic-links"})),
        ),
    ),
    ModelCall(
        name="ingest-analysis",
        source="ingest/auto_ingest.py",
        given=frozenset({"source-text", "brief", "wiki-index"}),
    ),
    ModelCall(
        name="ingest-generation",
        source="ingest/auto_ingest.py",
        given=frozenset({"source-text", "brief", "analysis", "wiki-index"}),
        checks=(PostCheck("file-path", "gate"),),
    ),
    ModelCall(
        name="ingest-rewrite",
        source="ingest/auto_ingest.py",
        given=frozenset(
            {"page-body", "draft-body", "analysis", "brief", "wiki-index"}
        ),
        # R4b holds the answer to both texts it was shown: the page on disk and
        # the draft, so a marker or link either one carried has to survive.
        checks=(
            PostCheck(
                "rewrite-structure", "gate", frozenset({"page-body", "draft-body"})
            ),
        ),
    ),
    ModelCall(
        name="chat-turn",
        source="chat/query.py",
        given=frozenset({"question", "history", "wiki-context", "tool-catalog", "limits"}),
        checks=(
            PostCheck("known-tool", "gate", frozenset({"tool-catalog"})),
            PostCheck("tool-arguments", "gate", frozenset({"tool-catalog"})),
            PostCheck("round-limits", "clamp", frozenset({"limits"})),
        ),
    ),
    ModelCall(
        # The same turn again, without tools: a model that does not support them is
        # asked to answer directly rather than failing the whole question.
        name="chat-turn-plain",
        source="chat/query.py",
        given=frozenset({"question", "history", "wiki-context", "limits"}),
        checks=(PostCheck("round-limits", "clamp", frozenset({"limits"})),),
    ),
)


def check_id(call: ModelCall, check: PostCheck) -> str:
    """How a violation is named, so the list of exceptions can hold it exactly."""
    return f"{call.name}/{check.name}"


def violations(calls: tuple[ModelCall, ...] = MODEL_CALLS) -> list[str]:
    """Checks that read something the request did not carry, named.

    Reports decide nothing, so they are not counted: a report may need the whole
    project, and no request can carry that.
    """
    out: list[str] = []
    for call in calls:
        for check in call.checks:
            if check.kind == "report":
                continue
            missing = check.needs - call.given
            if missing:
                out.append(check_id(call, check))
    return sorted(out)


def declared_call_sites(
    calls: tuple[ModelCall, ...] = MODEL_CALLS,
) -> dict[str, int]:
    """How many calls each declared file is expected to make."""
    counts: dict[str, int] = {}
    for call in calls:
        counts[call.source] = counts.get(call.source, 0) + 1
    return counts


def call_sites_on_disk(root: Path | None = None) -> dict[str, int]:
    """How many calls each file actually makes.

    Counts the places the model is asked something, so a new one cannot be added
    without a contract.
    """
    base = root or _PACKAGE
    counts: dict[str, int] = {}
    for path in sorted(base.rglob("*.py")):
        # The client module is the transport, not a place a feature asks
        # something: its two methods are the call itself.
        if path.name == "client.py" and path.parent.name == "llm":
            continue
        found = len(_CALL_SITE.findall(path.read_text(encoding="utf-8")))
        if found:
            counts[str(path.relative_to(base)).replace("\\", "/")] = found
    return counts


def undeclared_sources(
    root: Path | None = None,
    calls: tuple[ModelCall, ...] = MODEL_CALLS,
) -> list[str]:
    """Files whose calls and declarations do not add up, either way."""
    on_disk = call_sites_on_disk(root)
    declared = declared_call_sites(calls)
    out: list[str] = []
    for source in sorted(set(on_disk) | set(declared)):
        actual = on_disk.get(source, 0)
        expected = declared.get(source, 0)
        if actual != expected:
            out.append(f"{source}: {actual} call(s) in the source, {expected} declared")
    return out


__all__ = [
    "KNOWN_EXCEPTIONS",
    "MODEL_CALLS",
    "ModelCall",
    "PostCheck",
    "call_sites_on_disk",
    "check_id",
    "declared_call_sites",
    "undeclared_sources",
    "violations",
]
