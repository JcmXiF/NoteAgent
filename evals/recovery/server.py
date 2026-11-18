"""Run the real app for one eval scenario, with a fixed tool delay.

The eval kills this process with SIGKILL, so it has to be a real ASGI server in
its own process, composed as the production app is composed. The only
difference is the tool registry: each handler sleeps for a fixed time before it
does its work, which widens the window in which the harness can kill the server
between a committed tool call and its result. Production code is untouched;
``AppDeps.tools`` is the seam the app already exposes.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import uvicorn

from noteagent.agent.tools import ToolHandler, ToolRegistry, ToolSpec
from noteagent.api.app import create_app
from noteagent.api.deps import AppDeps
from noteagent.project.layout import default_layout
from noteagent.wiki.agent_tools import wiki_tools

__all__ = ["build_app", "main", "slow_registry", "wrap_tool"]


def wrap_tool(
    spec: ToolSpec,
    delay_s: float,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> ToolSpec:
    """The same tool, slower by *delay_s* before its handler runs."""
    if delay_s < 0:
        raise ValueError("the tool delay must be non-negative")
    handler = spec.handler

    def delayed(
        arguments: object, context: object
    ) -> object:  # pragma: no cover - shape mirrors ToolHandler
        sleeper(delay_s)
        return handler(arguments, context)  # type: ignore[arg-type]

    return replace(spec, handler=delayed)  # type: ignore[arg-type]


def slow_registry(
    registry: ToolRegistry,
    delay_s: float,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> ToolRegistry:
    """A registry whose enabled specs all wait *delay_s* before running."""
    return ToolRegistry(
        [
            wrap_tool(spec, delay_s, sleeper=sleeper)
            for spec in registry.enabled()
        ]
    )


def build_app(project: Path, delay_s: float):  # noqa: ANN201 - FastAPI
    """The real app on *project*, with every wiki tool delayed.

    The product seeds its default system sections here on purpose: this eval
    exercises the chat path a reader actually gets. The deterministic harness
    in ``evals/harness.py`` is the one that turns that seeding off and supplies
    its own sections.
    """
    layout = default_layout(project)
    tools = slow_registry(wiki_tools(layout), delay_s)
    return create_app(AppDeps(tools=tools, seed_system_sections=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.recovery.server",
        description=(
            "Serve one eval project on loopback with delayed wiki tools. "
            "Started by the eval, not by a person."
        ),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--tool-delay-ms", type=float, default=300.0)
    args = parser.parse_args(argv)

    app = build_app(Path(args.project), args.tool_delay_ms / 1000.0)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            lifespan="on",
        )
    )
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
