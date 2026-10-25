"""The one place a reader is told what needs them.

Three queues used to speak separately: sources that changed after they were
compiled, a compile that was confirmed and then interrupted, and the wiki's own
check. They are one question to the person reading them — "what is waiting on
me" — so they are counted together, and the panel shows one number. Nothing here
acts: each item still carries the action that resolves it.
"""

from __future__ import annotations

from typing import Any

from noteagent.ingest.ingest_recovery import recovery_payload
from noteagent.ingest.revision_inbox import revision_inbox_payload
from noteagent.project.doctor import doctor_payload, doctor_project
from noteagent.project.layout import ProjectLayout

__all__ = ["attention_payload"]

#: How many findings the panel is handed. The rest are counted, not listed: a
#: page with a hundred broken links is one problem to look at, not a hundred.
DOCTOR_LIMIT = 50


def attention_payload(layout: ProjectLayout, *, check: bool = True) -> dict[str, Any]:
    """Everything waiting on the reader, and how much of it there is."""
    revision = revision_inbox_payload(layout)
    recovery = recovery_payload(layout)
    doctor: dict[str, Any] | None = None
    if check:
        doctor = doctor_payload(doctor_project(layout), limit=DOCTOR_LIMIT)

    revision_count = int(revision.get("openCount") or 0)
    recovery_count = int(recovery.get("openCount") or 0)
    # An orphan page and a page from an older project layout are what a healthy
    # project looks like, so they are not things to do.
    doctor_count = 0
    if doctor is not None:
        doctor_count = int(doctor.get("errors") or 0) + int(doctor.get("warnings") or 0)

    return {
        "revision": revision,
        "recovery": recovery,
        "doctor": doctor,
        "counts": {
            "revision": revision_count,
            "recovery": recovery_count,
            "doctor": doctor_count,
        },
        "total": revision_count + recovery_count + doctor_count,
    }
