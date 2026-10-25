"""Run web_fetch with run-log events. Does not write raw/."""

from __future__ import annotations

from time import perf_counter

import httpx

from noteagent.acquire.budget import MAX_FETCHES
from noteagent.acquire.tools.web_fetch import capture_rendered_html, web_fetch
from noteagent.acquire.types import Capture
from noteagent.util.run_log import RunLogger

_PREVIEW_CHARS = 2000


def format_capture(capture: Capture) -> str:
    reasons = ", ".join(capture.reasons) or "(none)"
    preview = capture.markdown[:_PREVIEW_CHARS]
    if len(capture.markdown) > _PREVIEW_CHARS:
        preview += "\n…\n"
    lines = [
        f"url: {capture.url}",
        f"final_url: {capture.final_url or capture.url}",
        f"title: {capture.title or '(none)'}",
        f"method: {capture.capture_method}",
        f"fidelity: {capture.fidelity}",
        f"reasons: {reasons}",
        f"http_status: {capture.http_status if capture.http_status is not None else '(none)'}",
        f"chars: {capture.char_count}",
        f"images: {capture.image_count}",
        f"images_failed: {capture.images_failed}",
        "",
        preview.rstrip(),
        "",
    ]
    return "\n".join(lines)


def run_web_fetch(
    url: str,
    *,
    query: str = "",
    client: httpx.Client | None = None,
    run_logger: RunLogger | None = None,
    caller: str | None = None,
) -> Capture:
    logger = run_logger or RunLogger.disabled()
    cleaned = url.strip()
    logger.append(
        "fetch.start",
        component="acquire",
        url=cleaned,
        query=" ".join(query.split()) or None,
        caller=caller,
    )
    started = perf_counter()
    try:
        capture = web_fetch(cleaned, client=client)
    except Exception as err:
        logger.append(
            "fetch.error",
            component="acquire",
            url=cleaned,
            caller=caller,
            error=type(err).__name__,
            message=str(err)[:300],
            duration_ms=int((perf_counter() - started) * 1000),
        )
        raise
    logger.append(
        "fetch.end",
        component="acquire",
        url=cleaned,
        final_url=capture.final_url or None,
        method=capture.capture_method,
        fidelity=capture.fidelity,
        reasons=list(capture.reasons) or None,
        chars=capture.char_count,
        images=capture.image_count or None,
        images_failed=capture.images_failed or None,
        http_status=capture.http_status,
        caller=caller,
        duration_ms=int((perf_counter() - started) * 1000),
    )
    return capture


def run_rendered_html(
    url: str,
    html: str,
    *,
    client: httpx.Client | None = None,
    run_logger: RunLogger | None = None,
    caller: str | None = None,
) -> Capture:
    """Reader-mode capture from a rendered DOM. Does not write raw/."""
    logger = run_logger or RunLogger.disabled()
    cleaned = url.strip()
    logger.append(
        "fetch.start",
        component="acquire",
        url=cleaned,
        caller=caller,
        method="rendered-html",
    )
    started = perf_counter()
    try:
        capture = capture_rendered_html(cleaned, html, client=client)
    except Exception as err:
        logger.append(
            "fetch.error",
            component="acquire",
            url=cleaned,
            caller=caller,
            error=type(err).__name__,
            message=str(err)[:300],
            duration_ms=int((perf_counter() - started) * 1000),
        )
        raise
    logger.append(
        "fetch.end",
        component="acquire",
        url=cleaned,
        final_url=capture.final_url or None,
        method=capture.capture_method,
        fidelity=capture.fidelity,
        reasons=list(capture.reasons) or None,
        chars=capture.char_count,
        images=capture.image_count or None,
        images_failed=capture.images_failed or None,
        http_status=capture.http_status,
        caller=caller,
        duration_ms=int((perf_counter() - started) * 1000),
    )
    return capture


def run_web_fetch_many(
    urls: list[str],
    *,
    query: str = "",
    client: httpx.Client | None = None,
    run_logger: RunLogger | None = None,
    caller: str | None = None,
) -> list[Capture]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in urls:
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
        if len(ordered) >= MAX_FETCHES:
            break
    return [
        run_web_fetch(
            item,
            query=query,
            client=client,
            run_logger=run_logger,
            caller=caller,
        )
        for item in ordered
    ]
