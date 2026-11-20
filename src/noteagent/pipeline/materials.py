"""Materials: a captured original, its temporary home, and its history.

A capture becomes a material the moment it is staged: `.noteagent/staging/`
holds the original, a manifest of what it is, and its block texts, under the
run that collected it. The material log records every transition; the current
state is read back from it rather than written down a second time.

Identity is the path the material will have in `raw/`, even while it is only
staged: that is where every later step (framing, planning, compiling) refers to
it, and staging is a place, not a different object.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from noteagent.acquire.adapters.image_source import ImageSourceAdapter
from noteagent.acquire.adapters.office_source import OfficeSourceAdapter
from noteagent.acquire.adapters.pdf_source import (
    PdfSourceAdapter,
    blocks_from_payload,
    save_derived_blocks,
)
from noteagent.acquire.derive import blocks_payload
from noteagent.acquire.quality import FIDELITY_FAILED
from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import (
    capture_kind,
    raw_relative_for_capture,
    render_capture_source,
    resolve_raw_write_path,
)
from noteagent.agent.clock import Clock, iso_timestamp
from noteagent.ingest.clips import ClipCollection, block_from_dict
from noteagent.io.atomic import write_bytes_atomic, write_text_atomic
from noteagent.pipeline.run import PipelineRun, is_run_id
from noteagent.project.identity import source_identity
from noteagent.project.layout import ProjectLayout
from noteagent.project.raw_kinds import raw_kind
from noteagent.task.store import TaskStore, TaskUnreadableError

#: How many runs of un-promoted staging are kept. Older ones are removed.
DEFAULT_KEEP_RUNS = 3

#: How much of a block the first disclosure shows.
PREVIEW_CHARS = 20

MATERIAL_STATES = (
    "staged",
    "framed",
    "planned",
    "promoted",
    "compiled",
    "changed",
    "dropped",
)

#: States from which a raw file is already accounted for by the log.
_SETTLED = frozenset({"promoted", "compiled", "changed", "dropped"})

#: One vocabulary for a material's kind: the capture kinds, not the folder names,
#: so a staged and a backfilled material of the same thing read alike.
_KIND_FROM_RAW = {
    "web": "html",
    "pdf": "pdf",
    "office": "office",
    "media": "image",
}

#: Kinds whose blocks raw/ cannot enumerate without a derived sidecar.
_SIDECAR_KINDS = frozenset({"pdf", "office", "image"})

class PromotionError(RuntimeError):
    """A material cannot be made durable: nothing staged and nothing in raw/."""


logger = logging.getLogger(__name__)

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve()) if path.exists() else str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def preview_text(text: str, limit: int = PREVIEW_CHARS) -> str:
    """The opening of a block, short enough to scan a long document by."""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def hydrate_collection(
    collection: ClipCollection,
    blocks: Sequence[Mapping[str, object]],
) -> ClipCollection | None:
    """A selection carrying the staged texts of the blocks it names.

    ``None`` when none of its block ids are in the staged blocks: the selection
    is about text this staging no longer has.
    """
    by_id = {
        str(block.get("id")): block for block in blocks if block.get("id")
    }
    hydrated = tuple(
        block_from_dict(by_id[block_id])
        for block_id in collection.block_ids
        if block_id in by_id
    )
    return replace(collection, blocks=hydrated) if hydrated else None


@dataclass(frozen=True, slots=True)
class MaterialTransition:
    """One thing that happened to one material."""

    at: str
    material: str
    state: str
    run_id: str = ""
    source: str = ""
    detail: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaterialState:
    """Where a material stands now, derived from its last transition."""

    material: str
    state: str
    run_id: str
    updated_at: str
    detail: Mapping[str, object] = field(default_factory=dict)
    #: Who made the last change: ``system`` for a stage, ``human`` for a person.
    source: str = ""
    #: Whether a person ever drew its frames. Re-staging writes a later
    #: ``system`` transition, so the latest source alone would forget the edit.
    human_edited: bool = False

    @property
    def title(self) -> str:
        value = self.detail.get("title")
        return value.strip() if isinstance(value, str) and value.strip() else _stem(self.material)

    def payload(self) -> dict[str, object]:
        """The material as the interface reads it."""
        kind = self.detail.get("kind")
        fidelity = self.detail.get("fidelity")
        return {
            "material": self.material,
            "state": self.state,
            "runId": self.run_id,
            "updatedAt": self.updated_at,
            "title": self.title,
            "kind": kind if isinstance(kind, str) else "",
            "fidelity": fidelity if isinstance(fidelity, str) else "",
            "source": self.source,
            "humanEdited": self.human_edited,
        }


class MaterialLog:
    """The append-only history of a project's materials."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def append(self, transition: MaterialTransition) -> None:
        if transition.state not in MATERIAL_STATES:
            raise ValueError(f"unknown material state: {transition.state!r}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(_row(transition), ensure_ascii=False) + "\n"
        with _lock_for(self._path):
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())

    def transitions(self) -> list[MaterialTransition]:
        if not self._path.is_file():
            return []
        found: list[MaterialTransition] = []
        try:
            with self._path.open(encoding="utf-8") as stream:
                for line in stream:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        raw = json.loads(text)
                    except json.JSONDecodeError:
                        # One bad line stays where it is; the rest of an
                        # append-only log is not lost with it.
                        continue
                    entry = _entry(raw)
                    if entry is not None:
                        found.append(entry)
        except OSError:
            return []
        return found

    def states(self) -> dict[str, MaterialState]:
        latest: dict[str, MaterialState] = {}
        human: set[str] = set()
        for entry in self.transitions():
            if entry.source == "human":
                human.add(entry.material)
            latest[entry.material] = MaterialState(
                material=entry.material,
                state=entry.state,
                run_id=entry.run_id,
                updated_at=entry.at,
                detail=dict(entry.detail),
                source=entry.source,
                human_edited=entry.material in human,
            )
        return latest

    def state_for(self, material: str) -> MaterialState | None:
        return self.states().get(material.strip())

    def for_run(self, run_id: str) -> list[MaterialState]:
        """The materials one run collected, with the state they are in now.

        A material belongs to the run that staged it, not to whichever run wrote
        its last line: re-staging the same identity later, or promoting it, must
        not take it out of the list of the run that collected it.
        """
        wanted = (run_id or "").strip()
        if not wanted:
            return []
        grouped: dict[str, list[MaterialTransition]] = {}
        for entry in self.transitions():
            grouped.setdefault(entry.material, []).append(entry)
        items: list[MaterialState] = []
        for material, entries in grouped.items():
            owner = next(
                (
                    entry.run_id
                    for entry in reversed(entries)
                    if entry.state == "staged" and entry.run_id
                ),
                "",
            )
            if not owner:
                owner = next(
                    (entry.run_id for entry in entries if entry.run_id), ""
                )
            if owner != wanted:
                continue
            last = entries[-1]
            detail = dict(last.detail)
            staged = next(
                (
                    entry
                    for entry in entries
                    if entry.state == "staged" and entry.detail.get("title")
                ),
                None,
            )
            if staged is not None:
                # What the material is does not change with what happened to it.
                for key in ("title", "kind", "fidelity"):
                    if key not in detail and staged.detail.get(key) is not None:
                        detail[key] = staged.detail[key]
            items.append(
                MaterialState(
                    material=material,
                    state=last.state,
                    run_id=owner,
                    updated_at=last.at,
                    detail=detail,
                    source=last.source,
                    human_edited=any(
                        entry.source == "human" for entry in entries
                    ),
                )
            )
        items.sort(key=lambda state: state.material)
        return items


@dataclass(frozen=True, slots=True)
class StagedMaterial:
    """One capture written into a run's staging directory."""

    material: str
    run_id: str
    title: str
    kind: str
    fidelity: str
    block_count: int
    directory: Path


class StagingArea:
    """Where captures wait before they are promoted into `raw/`."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def stage(
        self,
        capture: Capture,
        *,
        run_id: str,
        captured_at: str,
    ) -> StagedMaterial:
        if not is_run_id(run_id):
            raise ValueError(f"not a pipeline run id: {run_id!r}")
        if capture.fidelity == FIDELITY_FAILED:
            raise ValueError("a capture that failed the quality gate is not material")
        kind = capture_kind(capture)
        material = raw_relative_for_capture(capture)
        blocks = _blocks_for(capture, kind)
        data = _source_bytes(capture, kind, captured_at)

        directory = self._material_directory(material, run_id=run_id)
        suffix = Path(material).suffix or ".bin"
        write_bytes_atomic(directory / f"source{suffix}", data)
        write_text_atomic(
            directory / "blocks.json",
            json.dumps(blocks, ensure_ascii=False, indent=2) + "\n",
        )
        manifest = {
            "version": 1,
            "runId": run_id,
            "material": material,
            "sourceUrl": capture.url,
            "finalUrl": capture.final_url or "",
            "title": capture.title,
            "kind": kind,
            "fidelity": capture.fidelity,
            "reasons": list(capture.reasons),
            "capturedAt": captured_at,
            # The block cache is derived from the source; these say whether the
            # pair is still the pair that was staged.
            "sourceDigest": hashlib.sha256(data).hexdigest(),
            "blocksDigest": hashlib.sha256(
                json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "blockCount": len(blocks),
            "blocks": [
                {
                    "id": block["id"],
                    "tag": block["tag"],
                    "preview": preview_text(block["text"]),
                }
                for block in blocks
            ],
        }
        # The manifest is the commit marker: a directory without one is a staging
        # that did not finish, and nothing reads it as a material.
        write_text_atomic(
            directory / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        return StagedMaterial(
            material=material,
            run_id=run_id,
            title=capture.title,
            kind=kind,
            fidelity=capture.fidelity,
            block_count=len(blocks),
            directory=directory,
        )

    def manifest_path(self, material: str, *, run_id: str) -> Path:
        """Where one material's manifest is, once its run id is checked."""
        return self._material_directory(material, run_id=run_id) / "manifest.json"

    def blocks_path(self, material: str, *, run_id: str) -> Path:
        """Where one material's full block texts are."""
        return self._material_directory(material, run_id=run_id) / "blocks.json"

    def source_path(self, material: str, *, run_id: str) -> Path | None:
        """The staged original, or ``None`` when this run no longer has one."""
        directory = self._material_directory(material, run_id=run_id)
        found = sorted(directory.glob("source.*"))
        return found[0] if found else None

    def _material_directory(self, material: str, *, run_id: str) -> Path:
        if not is_run_id(run_id):
            raise ValueError(f"not a pipeline run id: {run_id!r}")
        key = (material or "").replace("\\", "/").strip()
        parts = key.split("/")
        if not key.startswith("raw/") or any(
            part in {"", ".", ".."} for part in parts
        ):
            # A material is an original under raw/ and nothing else; a path that
            # climbs out of the run's folder is not one.
            raise ValueError(f"not a material path: {material!r}")
        return self._root / run_id / key.replace("/", "__")

    def expired(self, keep: int, started_at: Mapping[str, str]) -> list[tuple[str, Path]]:
        """The run folders older than the newest *keep*, newest first."""
        if not self._root.is_dir():
            return []
        entries: list[tuple[str, str, Path]] = []
        for path in self._root.iterdir():
            if not path.is_dir():
                continue
            started = started_at.get(path.name) or _mtime_iso(path)
            entries.append((started, path.name, path))
        entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [(name, path) for _, name, path in entries[max(0, keep) :]]

    def remove(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)


class Materials:
    """The project's materials: staging, the log, backfill and retention."""

    def __init__(self, layout: ProjectLayout, *, clock: Clock) -> None:
        self._layout = layout
        self._clock = clock
        self._log = MaterialLog(layout.materials_log)
        self._staging = StagingArea(layout.staging)

    def stage(
        self,
        capture: Capture,
        *,
        run_id: str,
        captured_at: str = "",
    ) -> MaterialState:
        at = captured_at or iso_timestamp(self._clock)
        staged = self._staging.stage(
            capture, run_id=run_id, captured_at=at
        )
        self._log.append(
            MaterialTransition(
                at=at,
                material=staged.material,
                state="staged",
                run_id=run_id,
                source="system",
                detail={
                    "title": staged.title,
                    "kind": staged.kind,
                    "fidelity": staged.fidelity,
                    "blocks": staged.block_count,
                },
            )
        )
        state = self._log.state_for(staged.material)
        if state is None:  # pragma: no cover - the append just wrote it
            raise RuntimeError("the staged material was not logged")
        return state

    def states(self) -> dict[str, MaterialState]:
        return self._log.states()

    def for_run(self, run_id: str) -> list[MaterialState]:
        return self._log.for_run(run_id)

    def backfill(self) -> int:
        """Give every original already in `raw/` a promoted record, once."""
        if not self._layout.raw.is_dir():
            return 0
        states = self._log.states()
        added = 0
        at = iso_timestamp(self._clock)
        for path in sorted(self._layout.raw.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self._layout.raw)
            if any(part.startswith(".") for part in relative.parts):
                continue
            material = source_identity(path, project_root=self._layout.root)
            current = states.get(material)
            if current is not None and current.state in _SETTLED:
                continue
            self._log.append(
                MaterialTransition(
                    at=at,
                    material=material,
                    state="promoted",
                    run_id="",
                    source="system",
                    detail={
                        "title": _stem(material),
                        "kind": _KIND_FROM_RAW.get(raw_kind(material), ""),
                        "backfilled": True,
                    },
                )
            )
            added += 1
        return added

    def manifest(self, material: str, *, run_id: str) -> dict[str, object] | None:
        """Read back what was staged, or ``None`` when the folder is gone."""
        try:
            path = self._staging.manifest_path(material, run_id=run_id)
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return dict(raw) if isinstance(raw, Mapping) else None

    def blocks(self, material: str, *, run_id: str) -> list[dict[str, object]] | None:
        """The material's full block texts, in document order."""
        try:
            path = self._staging.blocks_path(material, run_id=run_id)
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(raw, list):
            return None
        return [dict(item) for item in raw if isinstance(item, Mapping)]

    def promote(self, material: str, *, run_id: str) -> MaterialState | None:
        """Make one staged original durable under raw/, once.

        A material already settled in the log and present under raw/ is left
        alone, so promoting the same material twice writes neither a second file
        nor a second row.
        """
        key = (material or "").replace("\\", "/").strip()
        raw_path = _raw_path_for(self._layout, key)
        state = self._log.state_for(key)
        if raw_path.is_file() and state is not None and state.state in _SETTLED:
            return state
        staged = self._staging.source_path(key, run_id=run_id)
        if staged is None or not staged.is_file():
            if raw_path.is_file():
                return self.mark(
                    key,
                    "promoted",
                    run_id=run_id,
                    detail={"existing": True},
                )
            raise PromotionError(f"the staged source is gone: {key}")
        data = staged.read_bytes()
        manifest = self.manifest(key, run_id=run_id) or {}
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(raw_path, data)
        self._write_derived(key, run_id=run_id)
        return self.mark(
            key,
            "promoted",
            run_id=run_id,
            detail={
                "digest": hashlib.sha256(data).hexdigest(),
                "title": str(manifest.get("title") or _stem(key)),
                "kind": str(manifest.get("kind") or ""),
            },
        )

    def _write_derived(self, material: str, *, run_id: str) -> None:
        """Keep the block cache for a binary original, which raw/ cannot enumerate."""
        manifest = self.manifest(material, run_id=run_id)
        blocks = self.blocks(material, run_id=run_id)
        if manifest is None or blocks is None:
            return
        kind = str(manifest.get("kind") or "")
        if kind not in _SIDECAR_KINDS:
            return
        derived = blocks_from_payload({"blocks": blocks})
        if derived is None:
            return
        try:
            save_derived_blocks(
                self._layout,
                material,
                derived,
                source_url=str(manifest.get("sourceUrl") or ""),
                captured_at=str(manifest.get("capturedAt") or ""),
                kind=kind,
            )
        except (OSError, ValueError):
            # The original is what matters; the cache can be derived again, but
            # the failure is worth knowing about.
            logger.warning(
                "a derived block cache could not be written: %s", material
            )

    def mark(
        self,
        material: str,
        state: str,
        *,
        run_id: str,
        source: str = "system",
        detail: Mapping[str, object] | None = None,
    ) -> MaterialState | None:
        """Record one transition, the same shape every other stage uses."""
        self._log.append(
            MaterialTransition(
                at=iso_timestamp(self._clock),
                material=material,
                state=state,
                run_id=run_id,
                source=source,
                detail=dict(detail or {}),
            )
        )
        return self._log.state_for(material)

    def clean(
        self,
        runs: Sequence[PipelineRun],
        keep: int = DEFAULT_KEEP_RUNS,
    ) -> int:
        """Remove old staging, recording what was dropped with it.

        Retention is about a pipeline run's staging, not about every folder
        under ``staging/``: the active writing task stages its materials under
        its own id, and a later pipeline run must not expire the material the
        task and the agent are still reading. The active task is protected
        here, so no pipeline caller has to know about the task to keep it safe.
        """
        started = {run.id: run.started_at for run in runs}
        protected = self._active_task_ids()
        doomed = [
            (run_id, path)
            for run_id, path in self._staging.expired(keep, started)
            if run_id not in protected
        ]
        at = iso_timestamp(self._clock)
        for run_id, path in doomed:
            for state in self.for_run(run_id):
                if state.state in _SETTLED:
                    # Promoted and compiled materials live in raw/; only staging
                    # is going away.
                    continue
                self._log.append(
                    MaterialTransition(
                        at=at,
                        material=state.material,
                        state="dropped",
                        run_id=run_id,
                        source="system",
                        detail={
                            "reason": "staging expired",
                            "title": state.title,
                            "kind": state.detail.get("kind", ""),
                        },
                    )
                )
            self._staging.remove(path)
        return len(doomed)

    def _active_task_ids(self) -> set[str]:
        """The task whose staging this project must keep, if it has one.

        An unreadable task file is not a reason to fail a pipeline clean: the
        retention pass keeps the runs it knows and leaves the task alone. The
        task surfaces its own error when something reads it.
        """
        try:
            task = TaskStore(self._layout).load()
        except TaskUnreadableError:
            return set()
        return {task.id} if task is not None else set()


def _blocks_for(capture: Capture, kind: str) -> list[dict[str, str]]:
    if kind == "pdf":
        return [
            block.to_dict()
            for block in PdfSourceAdapter().derive_bytes(capture.pdf_bytes)
        ]
    if kind == "office":
        return [
            block.to_dict()
            for block in OfficeSourceAdapter().derive_bytes(capture.office_bytes).blocks
        ]
    if kind == "image":
        derived = ImageSourceAdapter().derive_bytes(capture.image_bytes)
        return [block.to_dict() for block in derived.blocks]
    return blocks_payload(capture.html)


def _source_bytes(capture: Capture, kind: str, captured_at: str) -> bytes:
    if kind == "pdf":
        if not capture.pdf_bytes:
            raise ValueError("a pdf capture has no original bytes")
        return capture.pdf_bytes
    if kind == "office":
        if not capture.office_bytes:
            raise ValueError("an office capture has no original bytes")
        return capture.office_bytes
    if kind == "image":
        if not capture.image_bytes:
            raise ValueError("an image capture has no original bytes")
        return capture.image_bytes
    if not (capture.html or "").strip():
        raise ValueError("a page capture has no html original")
    return render_capture_source(capture, captured_at=captured_at).encode("utf-8")


def _row(transition: MaterialTransition) -> dict[str, object]:
    row: dict[str, object] = {
        "at": transition.at,
        "material": transition.material,
        "state": transition.state,
    }
    if transition.run_id:
        row["runId"] = transition.run_id
    if transition.source:
        row["source"] = transition.source
    if transition.detail:
        row["detail"] = dict(transition.detail)
    return row


def _entry(raw: object) -> MaterialTransition | None:
    if not isinstance(raw, Mapping):
        return None
    at = str(raw.get("at") or "").strip()
    material = str(raw.get("material") or "").strip()
    state = str(raw.get("state") or "").strip()
    if not at or not material or state not in MATERIAL_STATES:
        return None
    detail = raw.get("detail")
    return MaterialTransition(
        at=at,
        material=material,
        state=state,
        run_id=str(raw.get("runId") or "").strip(),
        source=str(raw.get("source") or "").strip(),
        detail=dict(detail) if isinstance(detail, Mapping) else {},
    )


def _mtime_iso(path: Path) -> str:
    try:
        moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return ""
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw_path_for(layout: ProjectLayout, material: str) -> Path:
    try:
        return resolve_raw_write_path(layout, material)
    except ValueError as err:
        raise PromotionError(str(err)) from err


def _stem(material: str) -> str:
    name = material.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


__all__ = [
    "DEFAULT_KEEP_RUNS",
    "MATERIAL_STATES",
    "MaterialLog",
    "MaterialState",
    "MaterialTransition",
    "Materials",
    "PREVIEW_CHARS",
    "PromotionError",
    "StagedMaterial",
    "StagingArea",
    "hydrate_collection",
    "preview_text",
]
