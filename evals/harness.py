"""The eval harness: a temporary project, a scripted model, and the HTTP API.

A case is a small user story driven through the endpoints the interface calls.
The model is scripted, so the case is deterministic and free; the project is a
temporary directory, so the case reads and writes real files without touching a
checkout. Nothing here reaches a provider or an external service.

The harness keeps what a failed case needs to be understood: the project it
wrote (including each session's transcript and event log), every exchange it
made, and the checks that failed. A passing case leaves nothing behind.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from evals.report import UNCATEGORIZED, CaseResult, Check
from evals.sse import parse_sse
from evals.support import FixedClock, SequentialIds, remove_tree
from evals.trace import TraceEvidence, TraceSpec, evidence_from_entries, first_error

from noteagent.agent.model import ModelClient
from noteagent.agent.tools import ToolRegistry
from noteagent.api import projects as projects_api
from noteagent.api.app import create_app
from noteagent.api.deps import AppDeps
from noteagent.project.init import init_project
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.writing.tools import WebFetchFn, WebSearchFn

#: The pages every case starts with. They are ordinary wiki pages: the tools
#: find them the same way they find a reader's pages.
_PAGES = (
    (
        "concepts/entropy",
        "Entropy",
        "熵衡量不确定性。信息是不确定性的减少。",
    ),
    (
        "concepts/information",
        "Information",
        "信息是不确定性的减少。\n\n熵是信息的度量。",
    ),
)


@dataclass(frozen=True, slots=True)
class Exchange:
    """One request the case made, with the events it streamed back."""

    method: str
    path: str
    status_code: int
    text: str
    events: tuple[tuple[str, dict[str, Any]], ...] = ()

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as err:
            raise AssertionError(
                f"{self.method} {self.path} did not answer JSON: {self.text[:300]}"
            ) from err

    def events_of(self, kind: str) -> list[dict[str, Any]]:
        return [data for event, data in self.events if event == kind]


@dataclass(frozen=True, slots=True)
class Case:
    """One scripted user story, named so a failure says what broke."""

    name: str
    title: str
    run: Callable[["CaseRun"], None]
    #: The tool path this story expects; ``None`` keeps the trace as evidence only.
    trace: TraceSpec | None = None


class Api:
    """The case's view of the running app: requests, reads and waiting."""

    def __init__(self, case: "CaseRun", client: TestClient) -> None:
        self.case = case
        self.client = client
        self.exchanges: list[Exchange] = []
        #: The chat session the trace checks; the last query wins unless a case sets it.
        self.main_session: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        client: TestClient | None = None,
        **kwargs: object,
    ) -> Exchange:
        response = (client or self.client).request(method, path, **kwargs)
        media_type = response.headers.get("content-type", "")
        events = (
            parse_sse(response.text)
            if "text/event-stream" in media_type
            else ()
        )
        exchange = Exchange(
            method.upper(),
            path,
            response.status_code,
            response.text,
            events,
        )
        self.exchanges.append(exchange)
        self.case.note(f"{exchange.method} {path} -> {response.status_code}")
        return exchange

    def create_session(self, title: str = "") -> str:
        reply = self.request(
            "POST", "/api/v1/sessions", json={"title": title}
        )
        if reply.status_code != 201:
            raise AssertionError(
                f"the session could not be created: {reply.text[:300]}"
            )
        return str(reply.json()["id"])

    def query(self, session_id: str, content: str) -> Exchange:
        self.main_session = session_id
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/query",
            json={"content": content},
        )

    def get_session(self, session_id: str) -> dict[str, Any]:
        reply = self.request("GET", f"/api/v1/sessions/{session_id}")
        if reply.status_code != 200:
            raise AssertionError(
                f"the session could not be read: {reply.text[:300]}"
            )
        return reply.json()

    def cancel(self, session_id: str) -> Exchange:
        """Stop a run through a second client: the first one is streaming it."""
        app = self.case.app
        assert app is not None
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/cancel",
            client=TestClient(app),
        )

    def entries(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.case.store.load(session_id).entries)

    def trace(self, session_id: str) -> list[dict[str, Any]]:
        path = self.case.layout.session_events_path(session_id)
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def wait_for(
        self,
        predicate: Callable[[], bool],
        what: str,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out: {what}")


class CaseRun:
    """One case's project, checks and log.

    The active project is pointed at a temporary folder under the repository
    layout the API reads, so a request that resolves the project finds this
    case's wiki and nobody else's.
    """

    def __init__(
        self,
        workdir: Path,
        *,
        pages: Iterable[tuple[str, str, str]] | None = None,
    ) -> None:
        self.repo = workdir / "repo"
        self.repo.mkdir(parents=True)
        self.layout = init_project(self.repo / "projects" / "demo")
        self.checks: list[Check] = []
        self.log: list[str] = []
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.app: FastAPI | None = None
        self.api: Api | None = None
        self._store: JsonlSessionStore | None = None
        self._deps: AppDeps | None = None
        self._patchers: list[Any] = []
        try:
            self._activate()
            for slug, title, body in (_PAGES if pages is None else pages):
                self.write_page(slug, title, body)
        except Exception:
            # The patches point at this case's temporary folder. A case that
            # cannot finish starting must not leave the next one looking there.
            self.close()
            raise

    def _activate(self) -> None:
        root = patch.object(projects_api, "REPO_ROOT", self.repo)
        root.start()
        self._patchers.append(root)
        env = patch.dict(
            os.environ,
            {
                "NOTEAGENT_PROJECT": str(self.layout.root),
                "NOTEAGENT_HOME": "",
            },
        )
        env.start()
        self._patchers.append(env)
        projects_api.use_project("demo")

    @property
    def store(self) -> JsonlSessionStore:
        if self._store is None:
            self._store = JsonlSessionStore(
                self.layout, clock=self.clock, ids=self.ids
            )
        return self._store

    def start(
        self,
        model: ModelClient,
        *,
        #: The name the kernel records and sends on the model request. Scripted
        #: cases are faux; a live arm must pass the configured model's name.
        model_name: str = "faux",
        tools: ToolRegistry | None = None,
        seed_system_sections: bool = False,
        max_turns: int = 50,
        web_search: WebSearchFn | None = None,
        web_fetch: WebFetchFn | None = None,
    ) -> Api:
        if self.api is not None:
            raise RuntimeError("a case starts one app")
        deps = AppDeps(
            clock=self.clock,
            ids=self.ids,
            model=model,
            tools=tools,
            settings={"model": model_name},
            seed_system_sections=seed_system_sections,
            max_turns=max_turns,
            web_search=web_search,
            web_fetch=web_fetch,
        )
        self._deps = deps
        self.app = create_app(deps)
        self.api = Api(self, TestClient(self.app))
        return self.api

    def restart(self) -> Api:
        """Close the app and open it again on the same temporary project.

        This is an app restart, not a process restart: the temporary project
        (wiki, state, session store, event logs) persists, while the in-memory
        app is rebuilt from the same dependencies.
        """
        if self.api is None or self._deps is None:
            raise RuntimeError("a case must start before it can restart")
        exchanges = list(self.api.exchanges)
        main_session = self.api.main_session
        try:
            self.api.client.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        self.app = create_app(self._deps)
        self.api = Api(self, TestClient(self.app))
        self.api.exchanges.extend(exchanges)
        self.api.main_session = main_session
        return self.api

    def evidence(self, session_id: str) -> TraceEvidence:
        """Read the trace evidence from one finished session."""
        return evidence_from_entries(self.store.load(session_id).entries)

    def write_page(self, slug: str, title: str, body: str) -> None:
        path = self.layout.wiki / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'---\ntitle: "{title}"\n---\n\n{body}\n',
            encoding="utf-8",
        )

    def check(
        self,
        name: str,
        condition: object,
        detail: str = "",
        category: str = UNCATEGORIZED,
    ) -> bool:
        passed = bool(condition)
        self.checks.append(
            Check(name, passed, "" if passed else detail, category)
        )
        return passed

    def equal(
        self,
        name: str,
        actual: object,
        expected: object,
        category: str = UNCATEGORIZED,
    ) -> bool:
        return self.check(
            name,
            actual == expected,
            f"expected {expected!r}, got {actual!r}",
            category,
        )

    def note(self, text: str) -> None:
        self.log.append(text)

    def close(self) -> None:
        if self.api is not None:
            try:
                self.api.client.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()


def run_case(case: Case, artifacts_root: Path) -> CaseResult:
    """Run one case in its own temporary project, keeping a failure's evidence."""
    started = time.monotonic()
    tmp = Path(
        tempfile.mkdtemp(prefix=f"noteagent-eval-{case.name}-")
    )
    run: CaseRun | None = None
    error = ""
    try:
        try:
            run = CaseRun(tmp)
            case.run(run)
        except Exception:
            error = traceback.format_exc()
        checks = list(run.checks) if run is not None else []
        evidence: TraceEvidence | None = None
        if (
            run is not None
            and case.trace is not None
            and run.api is not None
            and run.api.main_session
        ):
            evidence = run.evidence(run.api.main_session)
            checks.extend(case.trace.checks(evidence))
        first_failure = first_error(checks, evidence, case.trace, error)
        result = CaseResult(
            case.name,
            case.title,
            tuple(checks),
            error,
            time.monotonic() - started,
            tuple(run.api.exchanges) if run is not None and run.api else (),
            tuple(run.log) if run is not None else (),
            first_failure,
        )
        if not result.passed:
            try:
                _preserve(run, result, artifacts_root)
            except Exception as err:  # noqa: BLE001 - evidence is best effort
                result = replace(
                    result,
                    error=(
                        result.error
                        + f"\nthe failure artifacts could not be kept: {err}"
                    ).strip(),
                )
        return result
    finally:
        if run is not None:
            run.close()
        remove_tree(tmp)


def _preserve(
    run: CaseRun | None,
    result: CaseResult,
    artifacts_root: Path,
) -> None:
    """Write the project, its event logs and the exchange list for one failure."""
    target = artifacts_root / result.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if run is not None:
        shutil.copytree(run.layout.root, target / "project")
        sessions = run.layout.sessions_dir
        if sessions.is_dir():
            event_target = target / "events"
            event_target.mkdir()
            for path in sorted(sessions.glob("*.events.jsonl")):
                shutil.copyfile(path, event_target / path.name)
    payload: dict[str, object] = {
        "case": result.name,
        "title": result.title,
        "passed": result.passed,
        "durationSeconds": round(result.duration_s, 3),
        "checks": [
            {"name": item.name, "passed": item.passed, "detail": item.detail}
            for item in result.checks
        ],
        "error": result.error,
        "log": list(result.log),
        "exchanges": [
            {
                "method": exchange.method,
                "path": exchange.path,
                "status": exchange.status_code,
                "events": [
                    {"event": kind, "data": data}
                    for kind, data in exchange.events
                ],
                "body": (
                    "" if exchange.events else exchange.text[:4_000]
                ),
            }
            for exchange in result.exchanges
        ],
    }
    (target / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "case.log").write_text(
        "\n".join(result.log) + ("\n" if result.log else ""),
        encoding="utf-8",
    )


__all__ = [
    "Api",
    "Case",
    "CaseRun",
    "Exchange",
    "run_case",
]
