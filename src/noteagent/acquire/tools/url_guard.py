"""Which addresses the fetch layer may reach: the public internet, and nothing else.

A local app is not a browser: the pages it fetches are chosen by a model, a
search result, or a page that was itself fetched, so the address in hand is not
trusted. Reaching ``127.0.0.1`` would hand the app's own API to whatever asked;
reaching a home router or a cloud metadata address is the same mistake with a
worse reward. Only a public address is a webpage.

Two checks make that decision. The literal check reads the URL itself: an
``http(s)`` scheme, a name, no credentials, and none of the names or addresses
that mean "this machine or the network around it". The resolution check asks DNS
where a name goes and refuses a name that answers with a private address. It
cannot stop a name whose answer changes between the check and the connection
(DNS rebinding) — closing that needs connecting to the checked address itself —
so the residual risk is named here rather than left implicit.

A name that does not resolve at all is not refused: the request cannot reach
anything either, and treating "offline" as "unsafe" would turn every DNS hiccup
into a security error.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

#: Suffixes reserved for local networks (RFC 6761/6762/8375). A name under one
#: of these never means a public page.
_LOCAL_SUFFIXES = (".localhost", ".local", ".home.arpa")

#: Statuses that ask the client to fetch somewhere else.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: How many hops a page may take before it is not one page any more.
MAX_REDIRECTS = 5

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Iterable[tuple[Any, ...]]]


class UnsafeURL(ValueError):
    """A URL the fetch layer refuses to reach, and the reason it does."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"{url} is not a public page ({reason})")


def check_public_url(url: str) -> str:
    """Refuse a URL that names this machine or a local network, by inspection.

    Nothing is resolved here, so this is the check that also works offline and
    in tests. It still catches the common attack: the address was written down
    as a literal, or as one of the names that only ever point inward.
    """
    cleaned = url.strip()
    try:
        parsed = urlsplit(cleaned)
    except ValueError as err:
        raise UnsafeURL(cleaned, "invalid_url") from err
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURL(cleaned, "blocked_scheme")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL(cleaned, "credentials")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL(cleaned, "blocked_host")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise UnsafeURL(cleaned, "blocked_host")

    address = _literal_address(host)
    if address is not None:
        if not _is_public(address):
            raise UnsafeURL(cleaned, "blocked_address")
        return cleaned
    # A public name has a dot: a single label is an intranet name resolved by a
    # search domain, never a page on the internet.
    if "." not in host:
        raise UnsafeURL(cleaned, "blocked_host")
    return cleaned


def resolve_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> str:
    """``check_public_url`` plus where the name actually goes.

    *resolver* is the seam a test uses; production leaves it to the system. A
    name that cannot be resolved is returned: the request that follows will
    fail on its own, and that failure is a network error rather than a refusal.
    """
    cleaned = check_public_url(url)
    parsed = urlsplit(cleaned)
    host = (parsed.hostname or "").rstrip(".").lower()
    if _literal_address(host) is not None:
        return cleaned
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as err:
        raise UnsafeURL(cleaned, "invalid_url") from err
    resolve = resolver or _system_resolver
    try:
        answers = list(resolve(host, port))
    except (OSError, UnicodeError):
        return cleaned
    addresses = {
        text
        for answer in answers
        if len(answer) > 4 and isinstance((text := answer[4][0]), str)
    }
    if not addresses:
        return cleaned
    for text in addresses:
        address = _literal_address(text)
        if address is not None and not _is_public(address):
            raise UnsafeURL(cleaned, "blocked_address")
    return cleaned


def guarded_get(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    max_redirects: int = MAX_REDIRECTS,
    resolve_dns: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """GET *url* while re-checking every address the page sends it to.

    httpx's own redirect following would fetch the target before the guard ever
    sees it, so redirects are walked here. ``resolve_dns`` is off for a client a
    caller injected (a test transport): that caller owns the network, while the
    literal check still runs on every hop.
    """
    check = resolve_public_url if resolve_dns else check_public_url
    current = url
    for _ in range(max_redirects + 1):
        check(current)
        response = client.get(
            current,
            headers=headers,
            follow_redirects=False,
            **kwargs,
        )
        location = response.headers.get("location")
        if response.status_code not in REDIRECT_STATUSES or not location:
            return response
        current = urljoin(str(response.url), location)
    raise UnsafeURL(url, "too_many_redirects")


def _system_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _literal_address(host: str) -> Address | None:
    """The address *host* spells, or ``None`` when it is a name."""
    text = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    # ``::ffff:127.0.0.1`` is another way to write the loopback address.
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _is_public(address: Address) -> bool:
    """Whether *address* is somewhere on the public internet.

    ``is_global`` excludes every range reserved for private use, loopback,
    link-local, multicast, the unspecified address, and shared space such as
    ``100.64.0.0/10`` — one rule instead of a list that goes stale.
    """
    return address.is_global


__all__ = [
    "MAX_REDIRECTS",
    "REDIRECT_STATUSES",
    "UnsafeURL",
    "check_public_url",
    "guarded_get",
    "resolve_public_url",
]
