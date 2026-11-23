"""The writing state as the interface reads it."""

from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingStateStore


def writing_state_payload(layout: ProjectLayout) -> dict[str, object]:
    """The project's state, or the empty state before the first write."""
    return WritingStateStore(layout).load().to_dict()


__all__ = ["writing_state_payload"]
