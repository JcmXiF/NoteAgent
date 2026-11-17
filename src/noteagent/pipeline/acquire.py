"""The acquisition stage: from one intent to a few staged materials.

The stage is the automatic half of the material switch. It asks for one query
(literal or model-rewritten), takes the top cards, fetches them until it has a
few usable materials, and leaves what it could not take on the run: a page that
failed the quality gate, a fetch that never answered, or a sign-in wall that only
the browser can pass.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from noteagent.acquire.budget import MAX_FETCHES
from noteagent.acquire.quality import FIDELITY_FAILED
from noteagent.acquire.tools.web_fetch import LOGIN_WALL_REASONS, LOGIN_WALL_STATUSES
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.pipeline.captures import LoginWall
from noteagent.pipeline.materials import MaterialState
from noteagent.pipeline.service import StageContext

#: How many materials one acquisition aims for.
TARGET_MATERIALS = 4

_REWRITE_LIMIT = 200


class AcquireError(RuntimeError):
    """The run could not collect any material."""


@dataclass(slots=True)
class AcquireStage:
    """One run's material acquisition, with its collaborators injected."""

    search: Callable[[str], Sequence[CandidateCard]]
    fetch: Callable[[str, str], Capture]
    rewrite: Callable[[str], str] | None = None
    name: str = "acquire"

    def __call__(self, context: StageContext) -> None:
        query, query_source = self._query(context)
        context.check_cancelled()
        cards = list(self.search(query))
        urls = _unique_urls(cards)
        titles = {card.url: card.title for card in cards}

        staged: list[MaterialState] = []
        skipped: list[dict[str, object]] = []
        walls: list[LoginWall] = []
        fetches = 0
        for url in urls:
            if len(staged) >= TARGET_MATERIALS or fetches >= MAX_FETCHES:
                break
            context.check_cancelled()
            fetches += 1
            try:
                capture = self.fetch(url, query)
            except Exception as err:  # noqa: BLE001 - one page, not the run
                skipped.append(
                    {"url": url, "reason": "fetch_failed", "detail": str(err)[:200]}
                )
                continue
            if capture.fidelity == FIDELITY_FAILED:
                reason = capture.reasons[0] if capture.reasons else "failed"
                if _is_login_wall(capture, reason):
                    walls.append(
                        LoginWall(
                            url=capture.url,
                            title=capture.title or titles.get(url, ""),
                            reason=reason,
                            http_status=capture.http_status,
                            run_id=context.run.id,
                        )
                    )
                else:
                    skipped.append({"url": url, "reason": reason})
                continue
            try:
                staged.append(context.stage_material(capture))
            except Exception as err:  # noqa: BLE001 - a bad capture, not the run
                skipped.append(
                    {"url": url, "reason": "stage_failed", "detail": str(err)[:200]}
                )

        detail = {
            "query": query,
            # "intent" / "model" / "intent-fallback": model mode that could not
            # ask the model searched the intent, and says so.
            "querySource": query_source,
            "cards": len(cards),
            "fetches": fetches,
            "acquired": len(staged),
            "skipped": skipped,
            # The service writes these to the sign-in queue; the stage only says
            # which pages they are.
            "loginWallItems": [asdict(wall) for wall in walls],
            "loginWalls": len(walls),
        }
        context.update(detail=detail)
        if not staged:
            raise AcquireError(
                "no material could be collected "
                f"({fetches} fetched, {len(skipped)} skipped, {len(walls)} need sign-in)"
            )

    def _query(self, context: StageContext) -> tuple[str, str]:
        intent = context.run.intent
        if context.run.query_mode != "model" or self.rewrite is None:
            return intent, "intent"
        try:
            rewritten = " ".join(self.rewrite(intent).split())
        except Exception:  # noqa: BLE001 - a rewrite is an optimization
            return intent, "intent-fallback"
        bounded = rewritten[:_REWRITE_LIMIT]
        if not bounded:
            return intent, "intent-fallback"
        return bounded, "model"


def _unique_urls(cards: Sequence[CandidateCard]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for card in cards:
        url = (card.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _is_login_wall(capture: Capture, reason: str) -> bool:
    if capture.http_status in LOGIN_WALL_STATUSES:
        return True
    return reason in LOGIN_WALL_REASONS


__all__ = ["AcquireError", "AcquireStage", "TARGET_MATERIALS"]
