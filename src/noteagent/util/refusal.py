"""Refusals the interface can say in the reader's own language.

A refusal is raised where the core knows which situation it is in. Its ``code``
names that situation and ``params`` carries the figures a sentence needs, so the
interface writes the sentence itself, in the reader's language, instead of
showing the core's own wording. The message stays for the log and the command
line.

A refusal raised without a code is one only a log can read: the interface falls
back to a sentence of its own and keeps the message in the job's log.
"""

from __future__ import annotations

from collections.abc import Mapping


class CodedError(Exception):
    """A failure that names its situation.

    Subclasses set ``code`` and ``status`` as class attributes; per-instance
    figures go in ``params``.
    """

    #: Stable name of the situation; the interface has a sentence for it.
    code: str = ""
    #: Figures the interface's sentence needs, as text. Never written to.
    params: Mapping[str, str] = {}
    #: The status a request carrying this refusal answers with.
    status: int = 400


class Refusal(CodedError, ValueError):
    """A refusal raised where the situation is known.

    Also a ``ValueError``: a refusal answers the request rather than reporting a
    fault in the service, and callers already treat bad requests that way.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        params: Mapping[str, str] | None = None,
        status: int | None = None,
    ) -> None:
        self.code = code
        self.params = dict(params) if params else {}
        if status is not None:
            self.status = status
        super().__init__(message)


__all__ = ["CodedError", "Refusal"]
