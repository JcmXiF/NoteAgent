"""HTML original: derive uses the existing leaf extractor."""

from __future__ import annotations

from pathlib import Path

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.derive import DerivedBlock, extract_derived_blocks


class HtmlSourceAdapter:
    """Reading-mode HTML already in raw/web."""

    kind = SourceKind.HTML

    def derive(self, path: Path) -> list[DerivedBlock]:
        html = path.read_text(encoding="utf-8")
        return self.derive_html(html)

    def derive_html(self, html: str) -> list[DerivedBlock]:
        return extract_derived_blocks(html)

    def preview(self, path: Path) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "renderer": "html-sandbox",
            "path": path.as_posix(),
        }
