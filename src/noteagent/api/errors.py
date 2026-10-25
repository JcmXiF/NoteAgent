"""How a refusal becomes an HTTP response.

A response keeps ``detail`` a plain sentence — the log line, and what a client
that knows nothing about codes still shows — and carries the code and its figures
as siblings. The interface reads the code; everything else reads the sentence.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from noteagent.util.refusal import CodedError


class CodedHTTPException(HTTPException):
    """An HTTP error that names its situation beside the sentence it carries."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.params = dict(params or {})


def _body(message: str, code: str, params: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"detail": message, "code": code}
    if params:
        body["params"] = dict(params)
    return body


def http_error(err: Exception, status: int) -> HTTPException:
    """The response for a raised error, carrying its code when it named one."""
    code = getattr(err, "code", "")
    if not code:
        return HTTPException(status_code=status, detail=str(err))
    return CodedHTTPException(
        getattr(err, "status", status),
        str(err),
        code=code,
        params=getattr(err, "params", None),
    )


async def coded_error_handler(_request: Any, exc: CodedHTTPException) -> JSONResponse:
    """Answers a code-carrying error raised where no route caught it."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(str(exc.detail), exc.code, exc.params),
        headers=exc.headers,
    )


async def refusal_handler(_request: Any, exc: CodedError) -> JSONResponse:
    """Answers a refusal raised where no route caught it."""
    return JSONResponse(
        status_code=exc.status,
        content=_body(str(exc), exc.code, exc.params),
    )


__all__ = [
    "CodedHTTPException",
    "coded_error_handler",
    "http_error",
    "refusal_handler",
]
