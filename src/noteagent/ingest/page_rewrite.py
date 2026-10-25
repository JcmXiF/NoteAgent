"""Write an existing page once more, with that page in front of the model.

The generation call writes every page this source earns from the source and the
index; it cannot see a page that already exists, because it does not know which
pages those are until it has written them. That is why a merge exists at all.

This pass closes the gap for the pages where it matters: a page already on disk,
longer than :data:`REWRITE_THRESHOLD`, gets one call that shows the model the page
and the draft this source produced for it. The reply replaces that page's block,
and the write path writes it as a rewrite (R4b) rather than asking a third model
to fuse two finished texts.

What it must never do is make things worse. A reply that drops a link or an
annotation either text carried, invents one, or is far shorter than the page it
replaces is discarded and the draft is kept — which is exactly what would have
been written without this pass. The check is
:func:`noteagent.ingest.page_merge.rewrite_breaks`, the same one the write path
applies, and both texts are what it holds the reply to.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from noteagent.ingest.frontmatter import parse_page
from noteagent.ingest.page_merge import rewrite_breaks
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.wiki_paths import resolve_wiki_write_path
from noteagent.project.layout import WIKI_DIR, WIKI_LOG, ProjectLayout

#: A page shorter than this is cheap to merge and cheap to read; one longer than
#: it is the kind whose blind rewrite the merge then has to repair.
REWRITE_THRESHOLD = 4_000

#: Source pages and the log are records of what a source contributed, not
#: subjects; a rewrite of one would rewrite history rather than the subject.
_SKIP_PREFIX = f"{WIKI_DIR}/sources/"

#: ``rewrite(page_path, page_on_disk, draft) -> whole page``.
RewriteFn = Callable[[str, str, str], str]


@dataclass(frozen=True, slots=True)
class RewriteResult:
    """The blocks after the pass, and what was replaced and refused.

    ``drafts`` maps each rewritten page to the draft the reply replaced: the
    write path needs it, because the rewrite was held to the page *and* that
    draft, and R4b cannot re-run the check without both.
    """

    blocks: tuple[FileBlock, ...]
    drafts: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def rewritten(self) -> tuple[str, ...]:
        """The pages replaced, in the order they were written."""
        return tuple(self.drafts)


def _is_skipped(relative: str) -> bool:
    normalized = relative.replace("\\", "/").lower()
    return normalized == WIKI_LOG or normalized.startswith(_SKIP_PREFIX)


def rewrite_existing_pages(
    layout: ProjectLayout,
    blocks: Sequence[FileBlock],
    rewrite: RewriteFn,
    *,
    threshold: int = REWRITE_THRESHOLD,
    on_rewrite: Callable[[str], None] | None = None,
) -> RewriteResult:
    """Rewrite each existing page above *threshold* that this reply touches."""
    out = list(blocks)
    drafts: dict[str, str] = {}
    warnings: list[str] = []

    for index, block in enumerate(blocks):
        relative = block.path.replace("\\", "/")
        if _is_skipped(relative):
            continue
        try:
            absolute = resolve_wiki_write_path(layout, block.path)
        except ValueError:
            # The write path reports an unsafe path; rewriting it would pre-empt
            # that report with a second one.
            continue
        if not absolute.is_file():
            continue
        try:
            existing = absolute.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(existing) < threshold:
            continue

        if on_rewrite is not None:
            on_rewrite(relative)
        try:
            reply = rewrite(relative, existing, block.content)
        except Exception as err:  # noqa: BLE001 — the draft is written instead
            warnings.append(f'[page-rewrite] {relative}: the call failed ({err})')
            continue
        if not reply.strip():
            warnings.append(f"[page-rewrite] {relative}: the reply was empty")
            continue
        answer = parse_page(reply)
        if not answer.has_frontmatter:
            warnings.append(
                f"[page-rewrite] {relative}: the reply had no frontmatter"
            )
            continue
        breaks = rewrite_breaks(
            answer.body, parse_page(existing).body, parse_page(block.content).body
        )
        if breaks:
            warnings.append(
                f"[page-rewrite] {relative}: the reply would lose part of it "
                f"({'; '.join(breaks)}); the draft is written instead"
            )
            continue
        out[index] = FileBlock(path=block.path, content=reply)
        drafts[relative] = block.content

    return RewriteResult(blocks=tuple(out), drafts=drafts, warnings=tuple(warnings))
