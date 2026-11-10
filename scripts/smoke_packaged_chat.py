"""One chat + wait flow through the packaged core, with a scripted model.

``scripts/smoke-packaged.mjs`` runs this with the interpreter and the source
that travel in the installer. The model is a short script, so nothing here
reaches a provider; what the flow proves is the part a packaged app cannot lose
— a streamed answer, a proposed plan wait, the wait stopping from the chat, and
the conversation continuing afterwards, all through the versioned HTTP API and
the real JSONL store.

The composition root is the same one production uses: ``create_app(deps)`` with
a different ``ModelClient``. That is the test seam the harness was built around,
not a branch in the shipped code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noteagent.agent.faux import FauxModelClient

HELLO = "你好，我可以帮你整理这些材料。"
ACK = "好的，随时继续。"


def parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    """The ``event:``/``data:`` blocks of one finished stream."""
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        kind = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                kind = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].lstrip())
        if kind:
            events.append((kind, json.loads("\n".join(data))))
    return events


def _model() -> "FauxModelClient":
    """Two turns: a proposal and a plain answer, both scripted."""
    from noteagent.agent.faux import FauxModelClient

    proposal = FauxModelClient.tool_call(
        "call-smoke",
        "propose",
        json.dumps(
            {"job": "plan", "interface": "source", "reason": "packaged smoke"},
            ensure_ascii=False,
        ),
        text=HELLO,
    )
    return FauxModelClient(proposal.script, FauxModelClient.text(ACK).script)


def _prepare_home(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prepare_project(root: Path) -> Path:
    from noteagent.project.init import init_project

    return init_project(root).root


def _expect(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _ask(client, session_id: str, content: str) -> list[tuple[str, dict[str, object]]]:
    response = client.post(
        f"/api/v1/sessions/{session_id}/query",
        json={"content": content},
    )
    _expect(response.status_code == 200, f"query: {response.text}")
    events = parse_sse(response.text)
    _expect(
        "error" not in [kind for kind, _payload in events],
        f"the run failed: {response.text}",
    )
    return events


def _tokens(events: list[tuple[str, dict[str, object]]]) -> str:
    return "".join(
        str(payload.get("text", ""))
        for kind, payload in events
        if kind == "token"
    )


def run(home: Path, project: Path) -> None:
    os.environ["NOTEAGENT_HOME"] = str(_prepare_home(home))
    os.environ["NOTEAGENT_PROJECT"] = str(_prepare_project(project))

    import threading
    import time

    import httpx
    import uvicorn

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
    from noteagent.api.serve_control import bind_listener

    app = create_app(AppDeps(model=_model()))
    sock = bind_listener("127.0.0.1", 0)
    port = sock.getsockname()[1]
    # The app is served for real, over a socket the packaged interpreter owns.
    # Its lifespan stays off: startup migration and saved settings belong to the
    # core process the smoke already started, not to this scripted flow.
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            _expect(thread.is_alive(), "the server stopped before it started")
            _expect(time.monotonic() < deadline, "the server did not start in time")
            time.sleep(0.05)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=30
        ) as client:
            _flow(client, project)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def _flow(client, project: Path) -> None:
    created = client.post("/api/v1/sessions", json={"title": "Packaged smoke"})
    _expect(created.status_code == 201, f"create session: {created.text}")
    session_id = created.json()["id"]
    print(f"session {session_id}")

    events = _ask(client, session_id, "请帮我计划一下这些材料。")
    _expect(HELLO in _tokens(events), "streamed answer missing")
    waits = [payload for kind, payload in events if kind == "wait"]
    _expect(len(waits) == 1, f"expected one wait, saw {len(waits)}")
    wait = waits[0]
    _expect(wait.get("job") == "plan", f"not a plan wait: {wait}")
    _expect(wait.get("interface") == "source", f"wrong interface: {wait}")
    _expect(wait.get("status") == "proposed", f"wait not proposed: {wait}")
    print(f"wait {wait['waitId']} plan/source proposed")

    opened = client.get(f"/api/v1/sessions/{session_id}")
    _expect(opened.status_code == 200, f"session: {opened.text}")
    session = opened.json()
    _expect(
        session.get("wait", {}).get("status") == "proposed",
        f"the session does not show the wait: {session}",
    )
    roles = [item["role"] for item in session["messages"]]
    _expect(roles == ["user", "assistant"], f"unexpected transcript: {roles}")
    _expect(
        session["messages"][1].get("stopReason") == "propose",
        f"the proposal was not the end of the turn: {session['messages']}",
    )

    stopped = client.post(
        f"/api/v1/sessions/{session_id}/wait/{wait['waitId']}/cancel"
    )
    _expect(stopped.status_code == 200, f"stop wait: {stopped.text}")
    _expect(stopped.json().get("cancelled") is True, f"wait not stopped: {stopped.text}")
    print("wait cancelled")

    second = _ask(client, session_id, "继续。")
    second_kinds = [kind for kind, _payload in second]
    _expect("wait" not in second_kinds, "the stopped wait came back")
    _expect(ACK in _tokens(second), "second answer missing")

    final = client.get(f"/api/v1/sessions/{session_id}").json()
    _expect(
        final.get("wait", {}).get("status") == "cancelled",
        f"the stopped wait came back: {final.get('wait')}",
    )
    _expect(
        [item["role"] for item in final["messages"]]
        == ["user", "assistant", "user", "assistant"],
        f"unexpected final transcript: {final['messages']}",
    )
    store = project / ".noteagent" / "sessions"
    _expect((store / f"{session_id}.jsonl").is_file(), "the session was not written")
    _expect(
        (store / f"{session_id}.events.jsonl").is_file(),
        "the run trace was not written",
    )
    print("chat + wait flow ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_packaged_chat")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        run(args.home.resolve(), args.project.resolve())
    except Exception:
        import traceback

        traceback.print_exc()
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
