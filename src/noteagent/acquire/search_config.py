"""Where a search comes from: the external provider, or the model service itself.

The external side is one provider and one key. The native side belongs to the
active model connection, so the same resolved value carries the provider id it
belongs to and, when the connection is complete, the exact endpoint, key and
model a native adapter needs. Which of the two a run uses is the global mode's
decision, made in ``run_search.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from noteagent.llm.config import load_dotenv
from noteagent.settings.credentials import keyring_store
from noteagent.settings.store import SEARCH_MODES, ConfigStore, default_config_path
from noteagent.util.refusal import CodedError

KNOWN_PROVIDERS = frozenset({"none", "brave", "serper"})

#: Providers whose model service can search natively. The Anthropic adapter
#: adds itself here when it lands.
NATIVE_SEARCH_PROVIDERS: frozenset[str] = frozenset({"openai"})

#: Why native search can or cannot run, in the order the resolver asks.
NativeSupport = Literal["ready", "config-missing", "no-adapter"]


def provider_for_host(base_url: str) -> str:
    """The native provider a bare endpoint belongs to, when one can be named."""
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return ""
    if host == "api.openai.com":
        return "openai"
    return ""


def native_search_available(*, provider: str, base_url: str) -> bool:
    """Whether the active model service has a native adapter in this build."""
    resolved = provider or provider_for_host(base_url)
    return resolved in NATIVE_SEARCH_PROVIDERS


class SearchConfigError(CodedError, ValueError):
    """Raised when search provider settings are invalid."""

    code = "provider.misconfigured"


class SearchError(CodedError, RuntimeError):
    """Raised when a search HTTP call fails."""

    code = "provider.failed"
    status = 502


class SearchModeUnavailable(CodedError, RuntimeError):
    """Native search was selected, but this build has no adapter for it."""

    code = "provider.nativeUnavailable"
    status = 503


class NativeSearchError(CodedError, RuntimeError):
    """The provider's own search answered, but not with a usable result."""

    code = "provider.nativeFailed"
    status = 502


class NativeSearchRejected(NativeSearchError):
    """The provider refused the key or the request."""

    code = "provider.nativeRejected"


class NativeSearchUnreachable(NativeSearchError):
    """The provider's search endpoint could not be reached."""

    code = "provider.nativeUnreachable"


class NativeSearchConfigMissing(NativeSearchError):
    """The profile supports native search but its model connection is incomplete."""

    code = "provider.nativeConfigMissing"
    status = 503


@dataclass(frozen=True, slots=True)
class NativeSearchConfig:
    """What a provider's own search endpoint needs, and nothing more."""

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """One resolved search context.

    The external fields say who searches and for which locale. ``llm_provider``
    names the active model service; ``native`` is filled only when that service
    has an adapter and the connection has everything the adapter needs. Ranking,
    pagination, snippet decoration and extra result types stay in the adapters.
    """

    provider: str
    api_key: str
    timeout_seconds: float = 30.0
    country: str = ""
    search_lang: str = ""
    ui_lang: str = ""
    #: ``auto`` prefers a native adapter and falls back to the external one;
    #: ``external`` never tries native; ``native`` refuses when no adapter exists.
    mode: str = "auto"
    #: Which model service the profile belongs to, even when its connection is
    #: incomplete; that is what distinguishes "no adapter" from "not set up yet".
    llm_provider: str = ""
    native: NativeSearchConfig | None = None

    @property
    def configured(self) -> bool:
        return self.provider not in ("", "none") and bool(self.api_key)


def build_native_config(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = 120.0,
) -> NativeSearchConfig | None:
    """The native connection, or None when the profile cannot make one."""
    if provider not in NATIVE_SEARCH_PROVIDERS:
        return None
    if not (base_url and api_key and model):
        return None
    return NativeSearchConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=min(timeout_seconds, 60.0),
    )


def native_support(config: SearchConfig) -> NativeSupport:
    """Why native search can run, is not set up, or has no adapter."""
    if config.native is not None:
        return "ready"
    if config.llm_provider in NATIVE_SEARCH_PROVIDERS:
        return "config-missing"
    return "no-adapter"


def active_llm_provider() -> str:
    """The provider of the active profile, without touching the credential store."""
    try:
        config = ConfigStore(default_config_path(), keyring_store()).read()
    except Exception:
        return ""
    profile = config.active_profile
    return profile.provider if profile else ""


def load_search_config() -> SearchConfig:
    """
    Load search settings from the environment (after optional ``.env``).

        NOTEAGENT_SEARCH_PROVIDER   none | brave | serper
        NOTEAGENT_SEARCH_API_KEY
        NOTEAGENT_SEARCH_TIMEOUT    optional, default 30
        NOTEAGENT_SEARCH_COUNTRY    optional, 2-letter (Brave ``country``, Serper ``gl``)
        NOTEAGENT_SEARCH_LANG       optional (Brave ``search_lang`` / Serper ``hl``).
                                    Brave Chinese: ``zh-hans`` or ``zh-hant``.
                                    ``zh`` is accepted and sent as ``zh-hans``.
        NOTEAGENT_SEARCH_UI_LANG    optional, Brave ``ui_lang`` (e.g. ``zh-CN``)
        NOTEAGENT_SEARCH_MODE       auto | external | native, default auto

    The active model connection is read too: its provider decides whether a
    native adapter exists, and its endpoint, key and model feed that adapter.
    """
    load_dotenv()
    provider = os.environ.get("NOTEAGENT_SEARCH_PROVIDER", "none").strip().lower() or "none"
    if provider not in KNOWN_PROVIDERS:
        raise SearchConfigError(
            f"Unknown NOTEAGENT_SEARCH_PROVIDER={provider!r}. "
            f"Use one of: {', '.join(sorted(KNOWN_PROVIDERS))}."
        )
    mode = os.environ.get("NOTEAGENT_SEARCH_MODE", "auto").strip().lower() or "auto"
    if mode not in SEARCH_MODES:
        raise SearchConfigError(
            f"Unknown NOTEAGENT_SEARCH_MODE={mode!r}. "
            f"Use one of: {', '.join(sorted(SEARCH_MODES))}."
        )
    api_key = os.environ.get("NOTEAGENT_SEARCH_API_KEY", "").strip()
    timeout_seconds = float(os.environ.get("NOTEAGENT_SEARCH_TIMEOUT", "30"))
    country = os.environ.get("NOTEAGENT_SEARCH_COUNTRY", "").strip()
    search_lang = os.environ.get("NOTEAGENT_SEARCH_LANG", "").strip()
    ui_lang = os.environ.get("NOTEAGENT_SEARCH_UI_LANG", "").strip()
    llm_base_url = os.environ.get("NOTEAGENT_LLM_BASE_URL", "").strip()
    try:
        llm_timeout_seconds = float(os.environ.get("NOTEAGENT_LLM_TIMEOUT", "120"))
    except ValueError:
        llm_timeout_seconds = 120.0
    llm_provider = active_llm_provider() or provider_for_host(llm_base_url)
    native = build_native_config(
        llm_provider,
        llm_base_url,
        os.environ.get("NOTEAGENT_LLM_API_KEY", "").strip(),
        os.environ.get("NOTEAGENT_LLM_MODEL", "").strip(),
        llm_timeout_seconds,
    )
    return SearchConfig(
        provider=provider,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
        mode=mode,
        llm_provider=llm_provider,
        native=native,
    )
