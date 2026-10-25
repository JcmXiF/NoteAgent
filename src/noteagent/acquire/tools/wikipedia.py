from __future__ import annotations

from noteagent.acquire.types import ProviderNotConfiguredError, WikiLookup

TOOL_NAME = "wikipedia_lookup"
TOOL_DESCRIPTION = (
    "Look up an encyclopedia entry by title and return a short extract plus URL. "
    "Use only when the SERP list lacks a reliable definitional source."
)


def wikipedia_lookup(title: str, *, lang: str = "zh") -> WikiLookup:
    """Encyclopedia lookup. This build has no encyclopedia provider."""
    del title, lang
    raise ProviderNotConfiguredError(
        "wikipedia_lookup has no encyclopedia provider configured. "
        "Use web_search for definitional sources instead."
    )
