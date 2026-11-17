"""The settings screen's retrieval status, and its rebuild action.

The answer describes the active project, resolved on every call so switching
projects changes it without a restart. A missing project or an unusable
service is an answer with a reason, not a failure: the screen can say why it
cannot report an index. Rebuild runs the same synchronous sync as
``noteagent rag rebuild``, and never writes the settings document.
"""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.api.projects import get_active_layout
from noteagent.api.session_access import rag_backend
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.rag.status import (
    STATUS_DISABLED,
    STATUS_UNAVAILABLE,
    IndexStatus,
)
from noteagent.rag.settings import load_rag_settings
from noteagent.util.refusal import Refusal

__all__ = [
    "rag_rebuild_payload",
    "rag_status_payload",
]


@dataclass(frozen=True, slots=True)
class _Target:
    """The backend the screen should ask, or why there is none."""

    backend: object | None = None
    disabled: bool = False
    reason: str = ""
    #: The code a rebuild refusal carries when no backend was reached.
    code: str = "settings.ragUnavailable"


def _target() -> _Target:
    """Resolve the switch, the active project and the backend, once."""
    try:
        settings = load_rag_settings()
    except Exception as err:  # noqa: BLE001 - a status reports, never fails
        return _Target(reason=f"rag settings: {err}")
    if not settings.enabled:
        return _Target(disabled=True)
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError as err:
        return _Target(reason=str(err), code="project.notConfigured")
    except (OSError, ValueError) as err:
        return _Target(reason=f"the active project cannot be read: {err}")
    return _Target(backend=rag_backend(layout, settings))


def _unavailable(reason: str) -> dict[str, object]:
    return IndexStatus(STATUS_UNAVAILABLE, reason=reason).as_dict()


def _disabled() -> dict[str, object]:
    return IndexStatus(
        STATUS_DISABLED, reason="retrieval is disabled"
    ).as_dict()


def rag_status_payload() -> dict[str, object]:
    """The active project's index state, or why there is none to report."""
    target = _target()
    if target.disabled:
        return _disabled()
    if target.backend is None:
        return _unavailable(target.reason or "rag unavailable")
    try:
        return target.backend.status().as_dict()
    except Exception as err:  # noqa: BLE001 - a status reports, never fails
        return _unavailable(f"the index cannot be read: {err}")


def rag_rebuild_payload() -> dict[str, object]:
    """Rebuild the active project's index now, and answer with what changed."""
    target = _target()
    if target.disabled:
        raise Refusal(
            "settings.ragDisabled",
            "retrieval is off, so there is no index to rebuild",
            status=409,
        )
    if target.backend is None:
        raise Refusal(
            target.code, target.reason or "rag unavailable", status=409
        )
    return target.backend.rebuild().as_dict()
