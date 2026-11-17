"""What an index is, without loading the index engine.

The status vocabulary is read by the settings screen even when RAG is off, so
it lives here rather than beside the LanceDB code. ``rag.index`` re-exports
these names for the callers that already use them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "STATUS_COLD",
    "STATUS_DISABLED",
    "STATUS_FAILED",
    "STATUS_READY",
    "STATUS_STALE",
    "STATUS_UNAVAILABLE",
    "IndexStatus",
]

STATUS_READY = "ready"
STATUS_COLD = "cold"
STATUS_STALE = "stale"
STATUS_DISABLED = "disabled"
#: No backend could be built, so there is no index to report.
STATUS_UNAVAILABLE = "unavailable"
#: A page row's own state when its vectors could not be produced.
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """What the index currently is, for a caller deciding whether to search."""

    state: str
    units: int = 0
    chunks: int = 0
    failed: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "units": self.units,
            "chunks": self.chunks,
            "failed": self.failed,
            "reason": self.reason,
        }
