"""The MVP security boundary, at the places it can be observed.

The rest of the baseline has its own home: the local-only guard in
``test_api_guard``, the public-URL guard in ``test_url_guard`` and
``test_web_fetch``, the credential store in ``test_config_store`` and
``test_settings``. This file covers the last promise — a configured key never
reaches the session, the trace, or the error the interface shows.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from unittest.mock import patch

from agent_fakes import FakeWaitStore, FixedClock, SequentialIds
from noteagent.agent.events import ErrorEvent
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.runtime import UserInput
from noteagent.agent.wait import WaitState
from noteagent.api.waited_jobs import iter_waited_job_sse
from noteagent.llm.client import ChatMessage, LlmClient, LlmError
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.sessions.trace import SessionTrace


class KeyBoundaryTests(unittest.TestCase):
    def test_a_provider_error_cannot_carry_the_key_into_the_session(self) -> None:
        key = "sk-live-1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(Path(tmp))
            store = JsonlSessionStore(
                layout, clock=FixedClock(), ids=SequentialIds()
            )
            store.create("s1")

            def handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    401,
                    text=json.dumps({"error": f"bad api key {key}"}),
                )

            http = httpx.Client(transport=httpx.MockTransport(handler))
            self.addCleanup(http.close)
            model = OpenAIModelClient(
                LlmConfig(
                    base_url="https://model.test/v1",
                    api_key=key,
                    model="demo",
                ),
                client=http,
            )
            runtime = KernelRuntime(
                model=model,
                sessions=store,
                trace=SessionTrace(layout),
                clock=FixedClock(),
                ids=SequentialIds(),
            )
            events = []

            result = runtime.execute(
                UserInput("s1", "a question"), events.append
            )

            self.assertEqual(result.status, "failed")
            error = next(
                event for event in events if isinstance(event, ErrorEvent)
            )
            self.assertNotIn(key, error.message)
            self.assertIn("[redacted]", error.message)
            self.assertNotIn(
                key, layout.session_path("s1").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                key,
                layout.session_events_path("s1").read_text(encoding="utf-8"),
            )

    def test_the_older_chat_client_also_redacts_provider_bodies(self) -> None:
        key = "sk-live-1234567890"

        class FakeHTTP:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def post(self, *_args, **_kwargs):
                return httpx.Response(401, text=f"bad api key {key}")

        client = LlmClient(
            LlmConfig(
                base_url="https://model.test/v1",
                api_key=key,
                model="demo",
            )
        )
        with patch("noteagent.llm.client.httpx.Client", FakeHTTP):
            with self.assertRaises(LlmError) as caught:
                client.chat([ChatMessage("user", "hi")])

        self.assertNotIn(key, str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))

    def test_a_job_error_cannot_carry_the_key_into_the_wait(self) -> None:
        key = "sk-live-1234567890"
        waits = FakeWaitStore()
        waits.propose(
            "s1",
            WaitState(
                wait_id="w1",
                job="plan",
                interface="source",
                status="entered",
                proposed_at="2026-11-10T09:00:00Z",
                job_id="j1",
            ),
        )

        def worker(_on_status, _cancel, _emit):
            raise LlmError(f"provider said {key}")

        with patch.dict(
            "os.environ", {"NOTEAGENT_LLM_API_KEY": key}, clear=False
        ):
            list(
                iter_waited_job_sse(
                    worker,
                    waits=waits,
                    session_id="s1",
                    wait_id="w1",
                    job="plan",
                    result_from_payload=lambda _payload: None,  # type: ignore[arg-type,return-value]
                )
            )

        wait = waits.current("s1")
        self.assertIsNotNone(wait)
        self.assertNotIn(key, wait.error)
        self.assertIn("[redacted]", wait.error)


if __name__ == "__main__":
    unittest.main()
