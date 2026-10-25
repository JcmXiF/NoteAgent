"""Where the model endpoint and its settings come from.

Settings are environment variables, optionally seeded from a ``.env`` file found
beside the working directory or above it. The file never overrides a variable
that is already set, so a shell can point at one endpoint while a checkout's
``.env`` names another without the two fighting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


# Names already in the process when the settings file is first read. The file
# never overrides those, and the settings screen must not pretend it can either.
# Recorded once: a value the screen writes for this run is not an outside override.
_process_env_keys: set[str] | None = None


def reset_process_env_keys() -> None:
    """Forget which names were already set. Tests call this between cases."""
    global _process_env_keys
    _process_env_keys = None


def process_env_keys() -> frozenset[str]:
    """``NOTEAGENT_*`` names set before the settings file was applied."""
    if _process_env_keys is None:
        load_dotenv()
    return frozenset(_process_env_keys or ())


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read a local ``.env`` into a dict, skipping keys already in the environment."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            values[key] = value
    return values


def load_dotenv(start: Path | str | None = None, *, walk: bool = True) -> None:
    """Load a ``.env`` into ``os.environ`` without overwriting existing vars.

    With ``walk``, the nearest file at or above *start* wins and a nested file
    does not merge with its parent. Without it, only *start* itself is read,
    which is how a settings write targets one file without adopting another.
    """
    global _process_env_keys
    if _process_env_keys is None:
        _process_env_keys = {key for key in os.environ if key.startswith("NOTEAGENT_")}

    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    candidates = [current, *current.parents] if walk else [current]
    for candidate in candidates:
        dotenv = candidate / ".env"
        if dotenv.is_file():
            for key, value in _read_dotenv(dotenv).items():
                os.environ.setdefault(key, value)
            return


def load_llm_config(*, require_api_key: bool = True) -> LlmConfig:
    """Load LLM config from the environment, after an optional ``.env``.

    Environment variables:
      NOTEAGENT_LLM_BASE_URL   e.g. https://api.openai.com/v1
      NOTEAGENT_LLM_API_KEY
      NOTEAGENT_LLM_MODEL      e.g. gpt-4o-mini
      NOTEAGENT_LLM_TEMPERATURE (optional, default 0.1)
      NOTEAGENT_LLM_MAX_TOKENS  (optional, default 4096)
      NOTEAGENT_LLM_TIMEOUT     (optional, seconds, default 120)
    """
    load_dotenv()

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
            + ". Set them in the environment or a project-root .env file."
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
