"""Acquire tools: each module is one capability. Providers are configured in `.env`."""

from noteagent.acquire.search_config import SearchConfig, load_search_config
from noteagent.acquire.tools.web_fetch import (
    TOOL_DESCRIPTION as WEB_FETCH_DESCRIPTION,
    TOOL_NAME as WEB_FETCH_NAME,
)
from noteagent.acquire.tools.web_search import (
    TOOL_DESCRIPTION as WEB_SEARCH_DESCRIPTION,
    TOOL_NAME as WEB_SEARCH_NAME,
)
from noteagent.acquire.tools.wikipedia import (
    TOOL_DESCRIPTION as WIKIPEDIA_DESCRIPTION,
    TOOL_NAME as WIKIPEDIA_NAME,
)
from noteagent.acquire.types import ToolSpec


def tool_catalog(*, search_config: SearchConfig | None = None) -> tuple[ToolSpec, ...]:
    """
    Names and one-line descriptions only. Does not call a search engine.
    """
    cfg = search_config if search_config is not None else load_search_config()
    return (
        ToolSpec(
            name=WEB_SEARCH_NAME,
            description=WEB_SEARCH_DESCRIPTION,
            configured=cfg.configured,
        ),
        ToolSpec(
            name=WEB_FETCH_NAME,
            description=WEB_FETCH_DESCRIPTION,
            configured=True,
        ),
        ToolSpec(
            name=WIKIPEDIA_NAME,
            description=WIKIPEDIA_DESCRIPTION,
            configured=False,
        ),
    )
