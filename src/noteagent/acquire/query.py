"""One search query, rewritten from a note intent by the model.

The model is asked for the query alone; the caller bounds and falls back to the
intent, so a long or empty answer still leaves a run with something to search.
"""

from __future__ import annotations

REWRITE_PROMPT = (
    "Rewrite the note intent below as one web search query. "
    "Reply with the query only, in the intent's language, at most 200 characters."
    "\n\nIntent: {intent}"
)


def rewrite_query(intent: str) -> str:
    """The model's one query for *intent*; the caller decides what to do with it."""
    # Imported here: a precise-keyword run should not load the model stack.
    from noteagent.llm.client import ChatMessage, LlmClient
    from noteagent.llm.config import load_llm_config

    client = LlmClient(load_llm_config())
    return client.chat(
        [
            ChatMessage(
                role="user",
                content=REWRITE_PROMPT.format(intent=intent),
            )
        ]
    )


__all__ = ["REWRITE_PROMPT", "rewrite_query"]
