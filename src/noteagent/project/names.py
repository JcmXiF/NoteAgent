"""Validate wiki project directory names (single path segment under projects/)."""

from __future__ import annotations

import re

from noteagent.util.refusal import Refusal

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Avoid colliding with the product route POST /api/projects/use.
_RESERVED_PRODUCT = frozenset({"use"})

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


class ProjectNameError(Refusal):
    """Raised when a project name is not a safe directory segment."""


def validate_project_name(name: str) -> str:
    """Return a stripped project name, or raise ProjectNameError.

    Names are a single directory segment under ``projects/``.
    """
    if not isinstance(name, str):
        raise ProjectNameError("project.name.empty", "project name must be a string")
    raw = name.strip()
    if not raw:
        raise ProjectNameError("project.name.empty", "project name is empty")
    if len(raw) > 64:
        raise ProjectNameError("project.name.tooLong", "project name is too long (max 64)")
    if raw in {".", ".."}:
        raise ProjectNameError("project.name.notAllowed", "project name is not allowed")
    if raw.startswith("."):
        # A hidden folder is the program's own bookkeeping (the trash, for one),
        # and a project named like one would be invisible in a listing.
        raise ProjectNameError(
            "project.name.notAllowed",
            "project name cannot start with a dot",
            params={"name": raw},
        )
    if raw.casefold() in _RESERVED_PRODUCT:
        raise ProjectNameError(
            "project.name.notAllowed",
            f"project name is reserved: {raw}",
            params={"name": raw},
        )
    if any(ord(ch) < 32 for ch in raw):
        raise ProjectNameError(
            "project.name.badChars", "project name contains control characters"
        )
    if _UNSAFE_CHARS.search(raw):
        raise ProjectNameError(
            "project.name.badChars",
            "project name contains path or reserved characters",
        )
    if re.search(r"[ .]$", raw):
        raise ProjectNameError(
            "project.name.badChars", "project name cannot end with a space or dot"
        )
    stem = raw.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise ProjectNameError(
            "project.name.device",
            f"project name is a reserved device name: {raw}",
            params={"name": raw},
        )
    return raw
