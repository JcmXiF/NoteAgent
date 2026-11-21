"""The acquisition stage, driven through the HTTP API with scripted pages."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent_fakes import (
    FixedClock,
    OLD_PIPELINE_RETIRED,
    SequentialIds,
)
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.pipeline.acquire import AcquireStage
from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]

_LONG = "熵是不确定性的度量，它衡量一个随机变量的平均信息量。"


def _card(url: str) -> CandidateCard:
    return CandidateCard(
        title=url.rsplit("/", 1)[-1],
        url=url,
        site="example.com",
        snippet="",
        why="search",
        via="web_search",
    )


def _cards(count: int) -> list[CandidateCard]:
    return [_card(f"https://example.com/page-{index}") for index in range(count)]


def _capture(
    url: str,
    *,
    fidelity: str = "ok",
    reasons: tuple[str, ...] = (),
    status: int | None = None,
) -> Capture:
    html = (
        "<html><body><article>"
        f'<p data-na-block="b1">{_LONG}</p>'
        "</article></body></html>"
    )
    return Capture(
        url=url,
        title=url.rsplit("/", 1)[-1],
        markdown=_LONG,
        html=html,
        capture_method="test",
        fidelity=fidelity,
        reasons=reasons,
        http_status=status,
    )


class _Fetches:
    """Pages by URL, or an exception; records every call."""

    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, query: str) -> Capture:
        self.calls.append((url, query))
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is not None:
            return page  # type: ignore[return-value]
        return _capture(url)


@contextmanager
def _app(stage: AcquireStage):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_project(repo / "projects" / "demo")
        with patch.object(projects_mod, "REPO_ROOT", repo):
            projects_mod.use_project("demo")
            client = TestClient(
                create_app(
                    AppDeps(
                        clock=FixedClock(),
                        ids=SequentialIds(),
                        pipeline_stages=(stage,),
                        pipeline_spawn=lambda work: work(),
                    )
                )
            )
            yield client


def _start(
    client,
    *,
    query_mode: str = "precise",
    material_mode: str = "auto",
    intent: str = "熵",
):
    return client.post(
        "/api/v1/pipeline/runs",
        json={
            "intent": intent,
            "queryMode": query_mode,
            "materialMode": material_mode,
        },
    )


def _run(client, run_id: str) -> dict:
    return client.get(f"/api/v1/pipeline/runs/{run_id}").json()


def _materials(client, run_id: str) -> list[dict]:
    return client.get(
        f"/api/v1/pipeline/runs/{run_id}/materials"
    ).json()["materials"]


@unittest.skip(OLD_PIPELINE_RETIRED)
class AcquireStageTests(unittest.TestCase):
    def test_precise_mode_uses_the_intent_and_stages_four_materials(self) -> None:
        queries: list[str] = []
        fetch = _Fetches({})

        def search(query: str):
            queries.append(query)
            return _cards(6)

        with _app(AcquireStage(search=search, fetch=fetch)) as client:
            started = _start(client)
            self.assertEqual(started.status_code, 201, started.text)
            run_id = started.json()["id"]
            run = _run(client, run_id)
            materials = _materials(client, run_id)

        self.assertEqual(queries, ["熵"])
        self.assertEqual(len(fetch.calls), 4)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["detail"]["query"], "熵")
        self.assertEqual(run["detail"]["querySource"], "intent")
        self.assertEqual(run["detail"]["acquired"], 4)
        self.assertEqual(len(materials), 4)

    def test_model_mode_searches_the_rewritten_query(self) -> None:
        queries: list[str] = []

        def search(query: str):
            queries.append(query)
            return _cards(4)

        stage = AcquireStage(
            search=search,
            fetch=_Fetches({}),
            rewrite=lambda intent: "entropy information theory",
        )
        with _app(stage) as client:
            started = _start(client, query_mode="model")
            run = _run(client, started.json()["id"])

        self.assertEqual(queries, ["entropy information theory"])
        self.assertEqual(run["detail"]["query"], "entropy information theory")
        self.assertEqual(run["detail"]["querySource"], "model")

    def test_a_failed_rewrite_falls_back_to_the_intent(self) -> None:
        queries: list[str] = []

        def search(query: str):
            queries.append(query)
            return _cards(4)

        def broken(intent: str) -> str:
            raise RuntimeError("the model is not configured")

        stage = AcquireStage(
            search=search,
            fetch=_Fetches({}),
            rewrite=broken,
        )
        with _app(stage) as client:
            started = _start(client, query_mode="model")
            run = _run(client, started.json()["id"])

        self.assertEqual(queries, ["熵"])
        self.assertEqual(run["detail"]["querySource"], "intent-fallback")
        self.assertEqual(run["status"], "succeeded")

    def test_a_failed_quality_gate_is_skipped_and_backfilled(self) -> None:
        cards = _cards(6)
        fetch = _Fetches(
            {
                cards[0].url: _capture(
                    cards[0].url, fidelity="failed", reasons=("too_short",)
                )
            }
        )
        with _app(AcquireStage(search=lambda query: cards, fetch=fetch)) as client:
            started = _start(client)
            run = _run(client, started.json()["id"])

        self.assertEqual(len(fetch.calls), 5)
        self.assertEqual(run["detail"]["acquired"], 4)
        self.assertEqual(
            run["detail"]["skipped"][0]["reason"], "too_short"
        )
        self.assertEqual(run["detail"]["skipped"][0]["url"], cards[0].url)

    def test_a_fetch_exception_is_skipped_and_backfilled(self) -> None:
        cards = _cards(6)
        fetch = _Fetches({cards[0].url: RuntimeError("timed out")})
        with _app(AcquireStage(search=lambda query: cards, fetch=fetch)) as client:
            started = _start(client)
            run = _run(client, started.json()["id"])

        self.assertEqual(run["detail"]["acquired"], 4)
        self.assertEqual(run["detail"]["skipped"][0]["reason"], "fetch_failed")

    def test_a_sign_in_wall_waits_in_the_attention_panel(self) -> None:
        cards = _cards(6)
        fetch = _Fetches(
            {
                cards[0].url: _capture(
                    cards[0].url,
                    fidelity="failed",
                    reasons=("http_forbidden",),
                    status=403,
                )
            }
        )
        with _app(AcquireStage(search=lambda query: cards, fetch=fetch)) as client:
            started = _start(client)
            run_id = started.json()["id"]
            run = _run(client, run_id)
            attention = client.get(
                "/api/projects/demo/attention?check=false"
            ).json()

        self.assertEqual(run["detail"]["loginWalls"], 1)
        self.assertEqual(run["detail"]["acquired"], 4)
        self.assertEqual(attention["capture"]["openCount"], 1)
        self.assertEqual(attention["capture"]["items"][0]["url"], cards[0].url)
        self.assertEqual(attention["counts"]["capture"], 1)

    def test_no_material_fails_the_run(self) -> None:
        cards = _cards(4)
        fetch = _Fetches(
            {
                card.url: _capture(
                    card.url, fidelity="failed", reasons=("empty",)
                )
                for card in cards
            }
        )
        with _app(AcquireStage(search=lambda query: cards, fetch=fetch)) as client:
            started = _start(client)
            run = _run(client, started.json()["id"])

        self.assertEqual(run["status"], "failed")
        self.assertIn("no material could be collected", run["error"])
        self.assertEqual(run["detail"]["acquired"], 0)

    def test_at_most_eight_pages_are_fetched(self) -> None:
        cards = _cards(10)
        fetch = _Fetches(
            {card.url: RuntimeError("offline") for card in cards}
        )
        with _app(AcquireStage(search=lambda query: cards, fetch=fetch)) as client:
            started = _start(client)
            run = _run(client, started.json()["id"])

        self.assertEqual(len(fetch.calls), 8)
        self.assertEqual(run["status"], "failed")

    def test_manual_material_mode_is_refused(self) -> None:
        stage = AcquireStage(search=lambda query: _cards(4), fetch=_Fetches({}))
        with _app(stage) as client:
            response = _start(client, material_mode="manual")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["code"], "pipeline.invalid")


if __name__ == "__main__":
    unittest.main()
