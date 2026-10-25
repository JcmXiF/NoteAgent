"""Every path a wiki project knows, resolved from its root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RAW_DIR = "raw"
WIKI_DIR = "wiki"
NOTES_DIR = "notes"
META_DIR = ".noteagent"
CONFIG_DIR = "config"

BRIEF_FILE = "config/brief.md"
#: Read when a project has no brief yet, so an old project keeps its context.
LEGACY_PURPOSE_FILE = "purpose.md"
#: The retired second half of the brief; dropped by migration.
LEGACY_SCHEMA_FILE = "schema.md"

WIKI_INDEX = "wiki/index.md"
WIKI_LOG = "wiki/log.md"
#: No longer maintained; kept so a leftover file is still recognised and reported.
WIKI_OVERVIEW = "wiki/overview.md"
#: What each source compiled to (digest, outputs, fingerprints), by identity.
COMPILE_CACHE = ".noteagent/compile-cache.json"
#: The cache's name before it was renamed. Migration drops it: its rows describe
#: a project shape that no longer exists, so nothing in it can match again.
LEGACY_COMPILE_CACHE = ".noteagent/ingest-cache.json"


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    """Resolved paths for a NOTEAGENT wiki project."""

    root: Path

    @property
    def raw(self) -> Path:
        """Every original the project kept. A kind folder is made when first used."""
        return self.root / RAW_DIR

    @property
    def wiki(self) -> Path:
        return self.root / WIKI_DIR

    @property
    def notes(self) -> Path:
        """Where a person's notes live. Made on the first note, not before."""
        return self.root / NOTES_DIR

    @property
    def meta(self) -> Path:
        return self.root / META_DIR

    @property
    def config(self) -> Path:
        """Settings a person may change. Made on the first setting, not before."""
        return self.root / CONFIG_DIR

    @property
    def brief(self) -> Path:
        """What this wiki is for, in the author's words. Optional."""
        return self.root / BRIEF_FILE

    @property
    def legacy_purpose(self) -> Path:
        """The pre-`config/` location of the brief, read when the new one is absent."""
        return self.root / LEGACY_PURPOSE_FILE

    @property
    def legacy_schema(self) -> Path:
        """The retired schema file, present only in an unmigrated project."""
        return self.root / LEGACY_SCHEMA_FILE

    @property
    def wiki_index(self) -> Path:
        return self.root / WIKI_INDEX

    @property
    def wiki_log(self) -> Path:
        return self.root / WIKI_LOG

    @property
    def legacy_overview(self) -> Path:
        """The retired catalog file, present only in an unmigrated project."""
        return self.root / WIKI_OVERVIEW

    @property
    def compile_cache(self) -> Path:
        """What each source compiled to, keyed by identity. Safe to delete."""
        return self.root / COMPILE_CACHE

    @property
    def legacy_compile_cache(self) -> Path:
        """The cache's pre-rename location, present only in an old project."""
        return self.root / LEGACY_COMPILE_CACHE

    @property
    def clips(self) -> Path:
        return self.meta / "clips.json"

    @property
    def revision_inbox(self) -> Path:
        return self.meta / "revision-inbox.json"

    @property
    def ingest_recovery(self) -> Path:
        return self.meta / "ingest-recovery.json"

    @property
    def deep_search(self) -> Path:
        return self.meta / "deep-search.json"

    @property
    def snapshots(self) -> Path:
        return self.meta / "snapshots"

    @property
    def snapshot_log(self) -> Path:
        return self.meta / "snapshots.jsonl"

    @property
    def derived(self) -> Path:
        return self.meta / "derived"

    @property
    def media(self) -> Path:
        return self.meta / "media"

    @property
    def media_captions(self) -> Path:
        return self.meta / "media-captions.json"

    @property
    def chats_dir(self) -> Path:
        return self.meta / "chats"

    @property
    def chat_limits(self) -> Path:
        return self.config / "chat-limits.json"

    @property
    def legacy_chat_limits(self) -> Path:
        """Where limits lived before they moved out of program state."""
        return self.meta / "chat-limits.json"

    def chat_path(self, conversation_id: str) -> Path:
        """The file one chat conversation is stored in."""
        return self.chats_dir / f"{conversation_id}.json"


def default_layout(project_root: Path | str) -> ProjectLayout:
    """The layout of the project at *project_root*."""
    root = Path(project_root).expanduser().resolve()
    return ProjectLayout(root=root)
