"""Who may reach this server, and who is only pretending to be it.

The server has no password: it listens on the loopback interface, and that is the
whole access rule. Two things break that assumption, and both arrive through a
browser the user was told they could use:

* **A name that resolves here.** An attacker's domain may stop pointing at the
  attacker and start pointing at ``127.0.0.1``. The browser then treats the
  attacker's page and this server as one origin, so it asks no permission and the
  page can read and write freely. The page still sends its own name in ``Host``,
  which is the only thing that tells the two apart.
* **A page that was never ours.** A page anywhere on the web may send a
  cross-origin request to this address. It cannot read the answer, but a write
  that lands is a write. Such a request carries the page's address in ``Origin``.

So both headers are checked against this machine. The port comes from the request
itself (the address the server is listening on) rather than from a setting: the
server may have had to take a free port, and a whitelist written for one port would
then refuse the interface it just served.

A request with no ``Origin`` at all is let through. A browser marks every request
a page makes with one — including same-origin writes and cross-origin reads — so a
request without it did not come from a page. Local tools and the test client are
in that group, and a program running on this machine does not need this door: it
can write the files directly.

The refusal carries a code, but no sentence is written for it in the interface:
whoever reaches one is not looking at our interface.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: Names that mean "this machine". A literal address cannot be taken over by a
#: domain that later changes where it points.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Ports a development server may serve our interface from. In development the
#: page arrives from Vite and the API from another process, so the page's origin
#: is not the bound port; a page from here is still ours, because reaching the
#: port would already mean running a server on this machine.
DEV_INTERFACE_PORTS = frozenset({5174})

UNTRUSTED_HOST = "request.untrustedHost"
UNTRUSTED_ORIGIN = "request.untrustedOrigin"


def _host_and_port(value: str) -> tuple[str, int | None] | None:
    """Split a ``Host`` header or an origin into its name and port, or ``None``."""
    parsed = urlsplit(value if "//" in value else f"//{value}")
    try:
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None
    return host, port


def _names_this_machine(host: str, server_host: str) -> bool:
    """Whether *host* names the machine this server is running on.

    The address the server reports for itself counts, so a server told to bind a
    particular name is reachable by that name. A name that resolves here by
    accident (the rebinding case) is not reported, which is the point.
    """
    return host in LOOPBACK_HOSTS or host == server_host.lower()


def _refusal(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


class LocalOnly:
    """Refuses requests whose ``Host`` or ``Origin`` is not this machine."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        # A websocket handshake carries the same two headers and reaches the same
        # endpoints, so letting it past unchecked would leave the door open for
        # whichever protocol comes second.
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        verdict = self._verdict(scope)
        if verdict is None:
            await self.app(scope, receive, send)
            return

        status, code, detail = verdict
        if kind == "websocket":
            # Refused before it was accepted; 1008 is "policy violation".
            await send({"type": "websocket.close", "code": 1008})
            return
        await _refusal(status, code, detail)(scope, receive, send)

    @staticmethod
    def _verdict(scope: Scope) -> tuple[int, str, str] | None:
        """Why this request is not for us, or ``None`` when it is."""
        bound_host, bound_port = scope.get("server") or ("", 0)
        bound_host = str(bound_host or "")
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or ()
        }

        host = headers.get("host", "")
        parsed_host = _host_and_port(host) if host else None
        if parsed_host is None or not _names_this_machine(parsed_host[0], bound_host):
            return (
                403,
                UNTRUSTED_HOST,
                f"request came for {host or 'no host'}, which is not this server",
            )

        origin = headers.get("origin")
        if origin is None:
            return None

        parsed_origin = _host_and_port(origin)
        reasons = []
        if parsed_origin is None:
            reasons.append("not an address")
        else:
            name, port = parsed_origin
            if not _names_this_machine(name, bound_host):
                reasons.append("another machine")
            elif port != bound_port and port not in DEV_INTERFACE_PORTS:
                reasons.append("another port")
        if reasons:
            return (
                403,
                UNTRUSTED_ORIGIN,
                f"request came from {origin} ({', '.join(reasons)})",
            )
        return None


__all__ = [
    "DEV_INTERFACE_PORTS",
    "LOOPBACK_HOSTS",
    "LocalOnly",
    "UNTRUSTED_HOST",
    "UNTRUSTED_ORIGIN",
]
