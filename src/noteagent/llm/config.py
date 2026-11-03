"""Where the model endpoint and its settings come from.

Settings are process environment variables. The settings layer reads the saved
document and the credential store, then applies them to this process; anything
already set when the process started wins. No file beside the working directory
is read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from noteagent.util.refusal import CodedError


class LlmConfigError(CodedError, ValueError):
    """Raised when LLM settings are missing or invalid."""

    code = "llm.notConfigured"
    status = 503


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """OpenAI-compatible chat endpoint settings."""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout_seconds: float = 120.0

    @property
    def chat_completions_url(self) -> str:
        """The standard ``/chat/completions`` path under the configured base URL."""
        return f"{self.base_url.rstrip('/')}/chat/completions"


# Names already in the process when the settings layer first ran. The document
# never overrides those, and the settings screen must not pretend it can either.
# Recorded once: a value the screen applies for this run is not an outside override.
_process_env_keys: set[str] | None = None


def reset_process_env_keys() -> None:
    """Forget which names were already set. Tests call this between cases."""
    global _process_env_keys
    _process_env_keys = None


def capture_process_env() -> frozenset[str]:
    """Record the ``NOTEAGENT_*`` names the process was started with.

    The settings layer must not treat a value it applies for this run as an
    outside override, so the snapshot is taken before it writes anything.
    """
    global _process_env_keys
    if _process_env_keys is None:
        _process_env_keys = {key for key in os.environ if key.startswith("NOTEAGENT_")}
    return frozenset(_process_env_keys)


def process_env_keys() -> frozenset[str]:
    """``NOTEAGENT_*`` names set before the settings document was applied."""
    if _process_env_keys is None:
        capture_process_env()
    return frozenset(_process_env_keys or ())


def load_llm_config(*, require_api_key: bool = True) -> LlmConfig:
    """Load LLM config from the applied environment.

    Environment variables:
      NOTEAGENT_LLM_BASE_URL   e.g. https://api.openai.com/v1
      NOTEAGENT_LLM_API_KEY
      NOTEAGENT_LLM_MODEL      e.g. gpt-4o-mini
      NOTEAGENT_LLM_TEMPERATURE (optional, default 0.1)
      NOTEAGENT_LLM_MAX_TOKENS  (optional, default 4096)
      NOTEAGENT_LLM_TIMEOUT     (optional, seconds, default 120)

    The settings layer applies the saved document to these names; this function
    never reads a file of its own.
    """
    base_url = os.environ.get("NOTEAGENT_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("NOTEAGENT_LLM_API_KEY", "").strip()
    model = os.environ.get("NOTEAGENT_LLM_MODEL", "").strip()

    missing: list[str] = []
    if not base_url:
        missing.append("NOTEAGENT_LLM_BASE_URL")
    if require_api_key and not api_key:
        missing.append("NOTEAGENT_LLM_API_KEY")
    if not model:
        missing.append("NOTEAGENT_LLM_MODEL")
    if missing:
        raise LlmConfigError(
            "Missing LLM config: "
            + ", ".join(missing)
            + ". Set them in the app's settings, or in the process environment."
        )

    temperature = float(os.environ.get("NOTEAGENT_LLM_TEMPERATURE", "0.1"))
    max_tokens = int(os.environ.get("NOTEAGENT_LLM_MAX_TOKENS", "4096"))
    timeout_seconds = float(os.environ.get("NOTEAGENT_LLM_TIMEOUT", "120"))

    return LlmConfig(
        base_url=base_url,
        api_key=api_key or "EMPTY",  # A local server may not ask for one.
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )


def snapshot_llm_config(
    config: LlmConfig | None = None,
    *,
    needed: bool = True,
) -> LlmConfig | None:
    """The connection a job keeps for its whole run.

    A caller that already holds a snapshot keeps it. ``needed=False`` is for a
    run whose model stages are injected: it never builds a client, so reading
    the configuration would only be a way to fail for no reason.
    """
    if config is not None or not needed:
        return config
    return load_llm_config()


# Taken at import, before the settings layer applies the document: a value it
# applies for this run must never be mistaken for one the process was started with.
capture_process_env()
