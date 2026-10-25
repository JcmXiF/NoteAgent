"""Edit a ``.env`` file without throwing away the lines we do not own.

Comments, blank lines and keys this program does not manage stay where they
are. A value is quoted only when the line would otherwise be ambiguous, and
the reader in ``llm.config`` only strips one pair of surrounding quotes, so a
value that itself contains a quote is refused before it gets here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def dotenv_path(start: Path | str | None = None) -> Path:
    """The settings file in use: the nearest ``.env``, or one to create at *start*."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        found = candidate / ".env"
        if found.is_file():
            return found
    return current / ".env"


def format_env_value(value: str) -> str:
    """One value as it should appear after ``=``.

    Quoting is for spaces and comment markers. A quote or a newline cannot be
    round-tripped by the reader, so those are a programmer error by the time
    they reach this function.
    """
    if any(ch in value for ch in "\r\n\""):
        raise ValueError("env value cannot contain a newline or a double quote")
    if value == "" or any(ch.isspace() for ch in value) or "#" in value:
        return f'"{value}"'
    return value


def update_env_text(text: str, changes: Mapping[str, str | None]) -> str:
    """Return *text* with *changes* applied.

    ``None`` deletes the key. A key that is not already on its own line is
    appended. Comment lines are never treated as assignments.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, _value = stripped.partition("=")
        key = key.strip()
        if key not in changes:
            out.append(line)
            continue
        seen.add(key)
        new = changes[key]
        if new is None:
            continue
        out.append(f"{key}={format_env_value(new)}")
    missing = [key for key, value in changes.items() if key not in seen and value is not None]
    if missing and out and out[-1] != "":
        out.append("")
    for key in missing:
        value = changes[key]
        assert value is not None
        out.append(f"{key}={format_env_value(value)}")
    if not out:
        return ""
    return "\n".join(out) + "\n"


def write_env_file(path: Path, text: str) -> None:
    """Replace *path* atomically so a crash cannot leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
