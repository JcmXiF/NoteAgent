"""Remove secrets from anything that may be written down or shown.

A key belongs in the credential store and in the process environment, and
nowhere else. The one way it leaks is through a string that was built around it:
a provider's error body, a log line, a trace record. This module is the single
place that scrubs such a string, so every writer treats a secret the same way.

Discovery in the environment ignores short values: a placeholder such as
``EMPTY`` (a local endpoint that asks for no key) is not a secret. Redaction
itself replaces whatever it is handed, because a caller that names a configured
key knows it is a secret even when it is short. Replacing longest first matters
when one configured value contains another.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[redacted]"

#: An environment value shorter than this is a placeholder, not a key.
MIN_SECRET_CHARS = 8

#: A name carrying one of these words is a secret the app must not log.
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")


def environment_secrets(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """The secret-looking values in the environment, longest first."""
    source = os.environ if environ is None else environ
    return _usable(
        (
            value
            for name, value in source.items()
            if any(marker in name.upper() for marker in SECRET_NAME_MARKERS)
        ),
        min_chars=MIN_SECRET_CHARS,
    )


def _usable(secrets: Iterable[str], *, min_chars: int) -> tuple[str, ...]:
    """Distinct stripped values long enough to be worth replacing."""
    return tuple(
        sorted(
            {
                secret.strip()
                for secret in secrets
                if secret and len(secret.strip()) >= min_chars
            },
            key=len,
            reverse=True,
        )
    )


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of a named secret in *text*, longest first.

    *secrets* are values the caller already knows are secret — a configured key,
    not environment noise — so length is not a filter here.
    """
    usable = _usable(secrets, min_chars=1)
    cleaned = text
    for secret in usable:
        cleaned = cleaned.replace(secret, REDACTED)
    return cleaned


def redact_value(value: Any, secrets: Iterable[str]) -> Any:
    """``redact`` through lists, tuples and mappings, leaving other values alone."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        return {
            key: redact_value(item, secrets) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, secrets) for item in value)
    return value


__all__ = [
    "MIN_SECRET_CHARS",
    "REDACTED",
    "SECRET_NAME_MARKERS",
    "environment_secrets",
    "redact",
    "redact_value",
]
