"""What one run assumes about its project, recorded for a later resume.

A resume may happen minutes or days later. The pages, the raw originals, the
model and the settings can all have moved. This module turns those into four
short, non-secret digests the run marker can carry: the project root, the wiki,
the raw sources, and the configuration. A resume compares them and decides
whether it may continue, may continue with a warning, or must refuse.

Only paths, mtimes and sizes of files go into the tree digests; the bytes stay
where they are. The configuration is hashed after secret-looking keys are
dropped, so a key never travels even as a hash input.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from noteagent.project.layout import ProjectLayout

__all__ = ["project_fingerprint"]

_SECRET_WORDS = ("key", "token", "secret", "password", "credential")


def project_fingerprint(
    layout: ProjectLayout,
    *,
    config: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """The four digests a run marker records for *layout* and *config*."""
    return {
        "projectRoot": str(layout.root.resolve()),
        "wikiGeneration": _tree_digest(
            layout.wiki, suffixes=(".md",), content=True
        ),
        "sourceFingerprint": _tree_digest(layout.raw),
        "configHash": _config_digest(config or {}),
    }


def _tree_digest(
    root: Path,
    *,
    suffixes: Sequence[str] | None = None,
    content: bool = False,
) -> str:
    """A digest of the tree's names, mtimes and sizes; empty when it is absent."""
    if not root.is_dir():
        return ""
    allowed = (
        tuple(item.lower() for item in suffixes)
        if suffixes is not None
        else None
    )
    digest = hashlib.sha256()
    try:
        paths = sorted(
            root.rglob("*"),
            key=lambda item: item.as_posix().casefold(),
        )
    except OSError:
        return ""
    for path in paths:
        if not path.is_file():
            continue
        if allowed is not None and path.suffix.lower() not in allowed:
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        if content:
            try:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                continue
            digest.update(b"\0")
    return digest.hexdigest()


def _config_digest(config: Mapping[str, object]) -> str:
    """A stable digest of the non-secret part of a settings mapping."""
    public = _public_value(config)
    try:
        text = json.dumps(
            public,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        text = repr(public)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public_value(value: Any) -> Any:
    """The same shape with secret-looking keys dropped, recursively."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if any(word in key.lower() for word in _SECRET_WORDS):
                continue
            out[key] = _public_value(raw_value)
        return out
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
