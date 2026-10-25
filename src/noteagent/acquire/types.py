from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noteagent.acquire.budget import (
    MAX_CANDIDATES,
    MAX_FETCHES,
    MAX_WEB_SEARCHES,
    MAX_WIKIPEDIA_LOOKUPS,
)
from noteagent.util.refusal import CodedError


class ProviderNotConfiguredError(CodedError, RuntimeError):
    """Raised when a search/lookup tool has no backend configured."""

    code = "provider.notConfigured"
    status = 503


@dataclass(frozen=True, slots=True)
class SearchBudget:
    """Caps for one acquire run. Adjust the constants in budget.py."""

    max_web_searches: int = MAX_WEB_SEARCHES
    max_wikipedia_lookups: int = MAX_WIKIPEDIA_LOOKUPS
    max_candidates: int = MAX_CANDIDATES
    max_fetches: int = MAX_FETCHES


@dataclass(frozen=True, slots=True)
class SerpHit:
    """One search-engine result. Metadata only — no fetched body."""

    title: str
    url: str
    snippet: str
    site: str = ""
    site_name: str = ""
    favicon: str = ""


@dataclass(frozen=True, slots=True)
class WikiLookup:
    """One encyclopedia entry. Metadata / short extract only."""

    title: str
    url: str
    extract: str
    lang: str = ""


@dataclass(frozen=True, slots=True)
class Capture:
    """One fetched original after extract + quality gate. Not a wiki page."""

    url: str
    title: str
    markdown: str
    capture_method: str
    fidelity: str
    reasons: tuple[str, ...]
    http_status: int | None = None
    final_url: str = ""
    char_count: int = 0
    html: str = ""
    image_count: int = 0
    images_failed: int = 0
    kind: str = "html"
    pdf_bytes: bytes = b""
    office_bytes: bytes = b""
    image_bytes: bytes = b""
    page_count: int = 0


@dataclass(frozen=True, slots=True)
class CandidateCard:
    """A search hit the human may select for fetch and save to raw/."""

    title: str
    url: str
    site: str
    snippet: str
    why: str
    via: str
    site_name: str = ""
    favicon: str = ""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Catalog row shown to the loop without loading a provider."""

    name: str
    description: str
    configured: bool = False


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Tier-1 skill disclosure: name + when-to-use only."""

    name: str
    description: str
    filename: str


@dataclass(frozen=True, slots=True)
class Skill:
    """Tier-2 skill disclosure: full SKILL.md body."""

    name: str
    description: str
    body: str
    path: Path
