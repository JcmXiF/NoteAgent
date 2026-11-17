"""The retrieval variants an eval can run, and what each one returns.

A variant is a function from a project and a query to two ranked id lists. The
Stage 0 baseline is ``lexical``: the search the project already runs, so the
number it produces is the number a reader gets today, not a reimplementation
that might drift from it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from evals.rag.dataset import note_ref, page_ref

from noteagent.project.layout import ProjectLayout
from noteagent.wiki.local_search import search_project

__all__ = [
    "DEPTH",
    "Retrieval",
    "UnknownVariant",
    "VARIANTS",
    "Variant",
    "get_variant",
]

#: How many results a variant may hand back, mirroring the ``search`` tool's
#: own cap. Metrics cut at 5 and 10; MRR looks through this whole list.
DEPTH = 20


@dataclass(frozen=True, slots=True)
class Retrieval:
    """One variant's answer: ranked page slugs and ranked note ids."""

    pages: tuple[str, ...]
    notes: tuple[str, ...]

    def for_type(self, query_type: str) -> tuple[str, ...]:
        """The side of the result a query is scored on, as namespaced ids."""
        if query_type == "notes":
            return tuple(note_ref(note_id) for note_id in self.notes)
        return tuple(page_ref(slug) for slug in self.pages)


@dataclass(frozen=True, slots=True)
class Variant:
    """A named retrieval strategy the harness can compare."""

    name: str
    description: str
    retrieve: Callable[[ProjectLayout, str], Retrieval]


class UnknownVariant(ValueError):
    """No variant has the requested name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"unknown variant {name!r}; try: {', '.join(VARIANTS)}"
        )


def _lexical(layout: ProjectLayout, query: str) -> Retrieval:
    found = search_project(layout, query, per_kind=DEPTH)
    pages = cast(list[dict[str, object]], found["pages"])
    notes = cast(list[dict[str, object]], found["notes"])
    return Retrieval(
        pages=tuple(str(item["slug"]) for item in pages),
        notes=tuple(str(item["id"]) for item in notes),
    )


VARIANTS: dict[str, Variant] = {
    "lexical": Variant(
        name="lexical",
        description=(
            "the project's local search over wiki pages and notes: "
            "rank_text scores slug/title exact 4, contained 3, all words 2, "
            "body 1; ties by slug or note id"
        ),
        retrieve=_lexical,
    ),
}


def get_variant(name: str) -> Variant:
    """The variant called *name*, or a refusal naming the ones that exist."""
    try:
        return VARIANTS[name]
    except KeyError:
        raise UnknownVariant(name) from None
