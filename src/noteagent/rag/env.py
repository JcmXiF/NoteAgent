"""Read RAG settings from environment strings, and join endpoint paths.

The embedding and rerank settings loaders share one grammar: a missing name
means the default, a malformed value names the variable, and a base endpoint
gets one path appended unless it already ends with it. Keeping it here means
the two loaders cannot drift in how they read the same kind of value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

__all__ = [
    "ErrorFactory",
    "endpoint_path",
    "parse_boolean",
    "read_boolean",
    "read_integer",
    "read_number",
]

ErrorFactory = Callable[[str], Exception]

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def parse_boolean(text: str) -> bool | None:
    """A boolean as an environment value spells it, or ``None`` when it is not one."""
    raw = text.strip().casefold()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def read_boolean(
    source: Mapping[str, str],
    name: str,
    default: bool,
    *,
    error: ErrorFactory,
) -> bool:
    raw = source.get(name, "").strip().casefold()
    if not raw:
        return default
    value = parse_boolean(raw)
    if value is None:
        raise error(f"{name} must be a boolean, got {raw!r}")
    return value


def read_integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int = 1,
    error: ErrorFactory,
) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as err:
        raise error(f"{name} must be an integer, got {raw!r}") from err
    if value < minimum:
        raise error(f"{name} must be at least {minimum}, got {value}")
    return value


def read_number(
    source: Mapping[str, str],
    name: str,
    default: float,
    *,
    error: ErrorFactory,
) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as err:
        raise error(f"{name} must be a number, got {raw!r}") from err
    if value <= 0:
        raise error(f"{name} must be positive, got {value}")
    return value


def endpoint_path(endpoint: str, path: str) -> str:
    """A base endpoint with *path* appended once."""
    url = endpoint.rstrip("/")
    if url.endswith(f"/{path}"):
        return url
    return f"{url}/{path}"
