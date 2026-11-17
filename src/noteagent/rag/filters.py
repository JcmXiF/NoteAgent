"""SQL literals for the filters LanceDB accepts as strings.

The values here are slugs, note ids and kinds, but one apostrophe in a value
would end the literal early; every caller goes through this one helper so the
escaping rule cannot drift between the lifecycle and the retrievers.
"""

from __future__ import annotations

__all__ = ["sql_string"]


def sql_string(value: str) -> str:
    """One SQL string literal, with embedded quotes doubled."""
    return "'" + value.replace("'", "''") + "'"
