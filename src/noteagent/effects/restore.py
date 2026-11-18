"""Preview and undo the pages one run wrote, without overwriting what changed.

The effect journal says which pages a run wrote and what each page said before
and after. Undoing the run means putting the ``before`` version back — but only
for pages nobody else has touched since. The preview classifies every page from
the page history, and the execute step refuses a changed page unless the person
explicitly forces it. A successful restore writes its own ``undo`` effect and a
page-history row, so a second restore is a no-op.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from noteagent.effects.journal import (
    COMMITTED,
    PROVIDER_PAGE_HISTORY,
    REVERSIBLE_SNAPSHOT,
    UNDO,
    UNDO_PAGE_HISTORY,
    WIKI_PAGE_WRITE,
    Effect,
    EffectJournal,
)
from noteagent.effects.provider import PageHistoryProvider, SnapshotProvider
from noteagent.ingest.page_history import (
    MODE_RESTORE,
    WRITTEN_BY_AGENT,
    SnapshotEntry,
    history_entries,
)
from noteagent.project.layout import ProjectLayout
from noteagent.util.refusal import Refusal
from noteagent.wiki.editing import WRITTEN_BY_PERSON

UNCHANGED = "unchanged"
CHANGED_BY_PERSON = "changed_by_person"
CHANGED_BY_RUN = "changed_by_run"
CHANGED_BY_RESTORE = "changed_by_restore"
UNKNOWN_EXTERNAL = "unknown_external"
MISSING = "missing"
RENAMED = "renamed"

RESTORE = "restore"
SKIP = "skip"


@dataclass(frozen=True, slots=True)
class _PageState:
    """What the page says now, and how that compares with the run's write."""

    current: str
    renamed: str
    classification: str


def preview_restore(
    layout: ProjectLayout,
    run_id: str,
    *,
    journal: EffectJournal | None = None,
    provider: SnapshotProvider | None = None,
) -> dict[str, object]:
    """Every page the run wrote, with how it stands now and what to do."""
    journal = journal or EffectJournal(layout)
    provider = provider or PageHistoryProvider(layout)
    entries = history_entries(layout)
    pages: list[dict[str, object]] = []
    for effect in _run_effects(journal, run_id):
        pages.append(_page_preview(effect, entries, provider))
    return {"runId": run_id, "pages": pages}


def restore_run(
    layout: ProjectLayout,
    run_id: str,
    pages: Sequence[Mapping[str, object]],
    *,
    journal: EffectJournal | None = None,
    provider: SnapshotProvider | None = None,
) -> dict[str, object]:
    """Restore the selected pages, reporting every page in one of four lists."""
    journal = journal or EffectJournal(layout)
    provider = provider or PageHistoryProvider(layout)
    entries = history_entries(layout)
    effects = {effect.target: effect for effect in _run_effects(journal, run_id)}
    restored: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    conflicted: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    for request in pages:
        path = str(request.get("path") or "").strip()
        action = str(request.get("action") or RESTORE).strip()
        force = bool(request.get("force"))
        effect = effects.get(path)
        if effect is None:
            skipped.append({"path": path, "reason": "not in run"})
            continue
        if action == SKIP:
            skipped.append({"path": path, "reason": "chosen"})
            continue
        if action != RESTORE:
            failed.append({"path": path, "error": f"unknown action: {action}"})
            continue

        state = _page_state(effect, entries, provider)
        current = state.current
        renamed = state.renamed
        classification = state.classification
        if effect.before and current == effect.before:
            skipped.append({"path": path, "reason": "already restored"})
            continue
        if classification != UNCHANGED and not force:
            conflicted.append(
                {
                    "path": path,
                    "classification": classification,
                    "currentPath": renamed,
                }
            )
            continue
        if not effect.before:
            failed.append(
                {"path": path, "error": "page was created; no earlier version"}
            )
            continue
        if classification == MISSING:
            failed.append({"path": path, "error": "page is missing"})
            continue
        if classification == RENAMED:
            failed.append(
                {
                    "path": path,
                    "error": f"page moved to {renamed}" if renamed else "page moved",
                    "currentPath": renamed,
                }
            )
            continue

        try:
            restored_ref = provider.restore(
                path,
                effect.before,
                base="" if force else current,
                force=force,
            )
        except Refusal as err:
            target = conflicted if err.code == "page.changed" else failed
            target.append(
                {
                    "path": path,
                    "classification": classification,
                    "error": str(err),
                    "code": err.code,
                }
            )
            continue
        except OSError as err:
            failed.append({"path": path, "error": str(err)})
            continue

        try:
            undo = journal.append(
                Effect(
                    kind=UNDO,
                    target=path,
                    run_id=run_id,
                    actor=WRITTEN_BY_PERSON,
                    before=current,
                    after=restored_ref,
                    reversible=REVERSIBLE_SNAPSHOT,
                    undo=UNDO_PAGE_HISTORY,
                    provider=PROVIDER_PAGE_HISTORY,
                    status=COMMITTED,
                )
            )
        except OSError as err:
            failed.append(
                {
                    "path": path,
                    "error": f"restored, but undo was not recorded: {err}",
                }
            )
            continue
        restored.append(
            {
                "path": path,
                "classification": classification,
                "effectId": undo.ident,
                "before": current,
                "after": restored_ref,
            }
        )

    return {
        "runId": run_id,
        "restored": restored,
        "skipped": skipped,
        "conflicted": conflicted,
        "failed": failed,
    }


def _run_effects(
    journal: EffectJournal, run_id: str
) -> tuple[Effect, ...]:
    return journal.query(
        run_id=run_id,
        kind=WIKI_PAGE_WRITE,
        status=COMMITTED,
    )


def _page_preview(
    effect: Effect,
    entries: Sequence[SnapshotEntry],
    provider: SnapshotProvider,
) -> dict[str, object]:
    state = _page_state(effect, entries, provider)
    return {
        "path": effect.target,
        "kind": effect.kind,
        "runId": effect.run_id,
        "at": effect.at,
        "classification": state.classification,
        "defaultAction": (
            RESTORE if state.classification == UNCHANGED else SKIP
        ),
        "effectId": effect.ident,
        "before": effect.before,
        "after": effect.after,
        "currentPath": state.renamed,
    }


def _page_state(
    effect: Effect,
    entries: Sequence[SnapshotEntry],
    provider: SnapshotProvider,
) -> _PageState:
    current = provider.current(effect.target)
    renamed = "" if current else _renamed_path(entries, effect)
    return _PageState(
        current=current,
        renamed=renamed,
        classification=_classification(effect, entries, current, renamed),
    )


def _classification(
    effect: Effect,
    entries: Sequence[SnapshotEntry],
    current: str,
    renamed: str,
) -> str:
    if renamed:
        return RENAMED
    if not current:
        return MISSING
    if current == effect.after:
        return UNCHANGED
    later = [
        entry
        for entry in entries
        if entry.path == effect.target and _later(entry, effect)
    ]
    if any(entry.mode == MODE_RESTORE for entry in later):
        return CHANGED_BY_RESTORE
    if any(entry.by == WRITTEN_BY_PERSON for entry in later):
        return CHANGED_BY_PERSON
    if any(
        entry.by == WRITTEN_BY_AGENT and entry.run_id != effect.run_id
        for entry in later
    ):
        return CHANGED_BY_RUN
    return UNKNOWN_EXTERNAL


def _later(entry: SnapshotEntry, effect: Effect) -> bool:
    if _moment(entry.at) < _moment(effect.at):
        return False
    if effect.ident and entry.effect_id == effect.ident:
        return False
    return True


def _moment(value: str) -> datetime:
    """Read a history timestamp, treating a legacy naive one as UTC."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _renamed_path(
    entries: Sequence[SnapshotEntry], effect: Effect
) -> str:
    """The page history row for this effect, if a rename moved it."""
    for entry in entries:
        if entry.path != effect.target and entry.effect_id == effect.ident:
            return entry.path
    return ""


__all__ = [
    "CHANGED_BY_PERSON",
    "CHANGED_BY_RESTORE",
    "CHANGED_BY_RUN",
    "MISSING",
    "RENAMED",
    "RESTORE",
    "SKIP",
    "UNCHANGED",
    "UNKNOWN_EXTERNAL",
    "preview_restore",
    "restore_run",
]
