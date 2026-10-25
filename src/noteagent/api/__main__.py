"""Run the local wiki API: ``python -m noteagent.api``.

The process binds its own socket before the server starts, so the port it reports
(and writes down for the desktop shell) is the port it is really listening on.
Without ``--fallback-port`` a taken port is an error worth telling a person about;
with it — the way the desktop shell starts the core — a taken port means "serve
on a free one instead", and the address is read back from the record.
"""

from __future__ import annotations

import argparse
import os
import sys

from noteagent.api.serve_control import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    AlreadyServing,
    PortBusy,
    bind_listener,
    found_servers,
    record_server,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="noteagent.api",
        description="NOTEAGENT local HTTP API for the web UI",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--fallback-port",
        action="store_true",
        help="When this port is taken, serve on a free one instead of stopping",
    )
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError as err:
        raise SystemExit(
            "Missing API dependencies. Install with:\n"
            "  pip install 'noteagent[api]'\n"
            "or: pip install fastapi uvicorn"
        ) from err

    from noteagent.api.app import create_app

    try:
        sock = bind_listener(args.host, args.port, fallback=args.fallback_port)
    except AlreadyServing as err:
        # Asking twice for a server that is already up is not a failure: the
        # address the caller wanted is serving. Say where, so a caller that needs
        # the address does not have to go looking for it.
        existing = found_servers(args.host, args.port)
        where = f" at {existing[0].url}" if existing else ""
        print(f"{err}{where}", flush=True)
        return 0
    except PortBusy as err:
        print(
            f"{err}. Try another port with --port, or: noteagent serve stop --port {args.port}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    port = sock.getsockname()[1]
    record = record_server(host=args.host, port=port, requested=args.port)
    print(
        f"Serving on {record.url} (pid {os.getpid()})"
        + ("" if port == args.port else f" — port {args.port} was taken"),
        flush=True,
    )

    config = uvicorn.Config(
        create_app(),
        host=args.host,
        port=port,
        log_level="info",
    )
    uvicorn.Server(config).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
