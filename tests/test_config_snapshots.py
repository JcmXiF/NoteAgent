"""A running job keeps the connection it started with.

Switching the active profile changes the next job, never one already in
flight. These cases run each entry point with a loader that would refuse a
second read, so a job that re-read the mutable configuration cannot pass.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.ingest import run_ingest_job
from noteagent.api.topic_gate import run_ingest_confirmed_job, run_topic_map_job
from noteagent.chat.query import run_query
from noteagent.chat.store import create_conversation
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    save_clip_store,
)
from noteagent.ingest.topic_map import map_clips
from noteagent.llm.client import LlmTurn, ToolCall
from noteagent.llm.config import LlmConfig
from noteagent.project.init import init_project
from noteagent.wiki.deep_search import act_session


CONFIG_A = LlmConfig(
    base_url="https://a.example/v1",
    api_key="key-a",
    model="model-a",
    temperature=0.2,
    max_tokens=1024,
    timeout_seconds=30,
)
CONFIG_B = LlmConfig(
    base_url="https://b.example/v1",
    api_key="key-b",
    model="model-b",
    temperature=0.8,
    max_tokens=2048,
    timeout_seconds=60,
)


class _LoadOnce:
    """Returns one configuration, and fails if the job asks for another."""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.calls = 0

    def __call__(self) -> LlmConfig:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("the job re-read the mutable configuration")
        return self.config


class _ChatClient:
    def __init__(self, script: list[LlmTurn]) -> None:
        self.script = list(script)

    def complete(self, _messages, **_kwargs) -> LlmTurn:
        return self.script.pop(0)


_TOOL_CALL = ToolCall(
    id="call-1",
    name="wiki_read",
    arguments='{"targets": ["concepts/entropy"]}',
)


class ChatSnapshotTests(unittest.TestCase):
    def test_a_turn_keeps_its_connection_through_tool_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            loader = _LoadOnce(CONFIG_A)
            seen: list[LlmConfig] = []
            client = _ChatClient(
                [
                    LlmTurn(content="", tool_calls=(_TOOL_CALL,)),
                    LlmTurn(content="answer"),
                ]
            )

            with (
                patch("noteagent.chat.query.load_llm_config", loader),
                patch(
                    "noteagent.chat.query.LlmClient",
                    lambda config: seen.append(config) or client,
                ),
                patch("noteagent.chat.query.observe_tool", lambda *a, **k: ()),
            ):
                run_query(layout, created.id, "什么是熵")

            self.assertEqual(loader.calls, 1)
            self.assertEqual(seen, [CONFIG_A])

    def test_the_next_turn_uses_the_connection_active_then(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            configs = [CONFIG_A, CONFIG_B]
            loads: list[LlmConfig] = []
            seen: list[LlmConfig] = []

            def load() -> LlmConfig:
                config = configs[len(loads)]
                loads.append(config)
                return config

            def make_client(config: LlmConfig) -> _ChatClient:
                seen.append(config)
                return _ChatClient([LlmTurn(content="answer")])

            with (
                patch("noteagent.chat.query.load_llm_config", load),
                patch("noteagent.chat.query.LlmClient", make_client),
            ):
                run_query(layout, created.id, "first")
                run_query(layout, created.id, "second")

            self.assertEqual(loads, [CONFIG_A, CONFIG_B])
            self.assertEqual(seen, [CONFIG_A, CONFIG_B])


class IngestSnapshotTests(unittest.TestCase):
    def test_every_source_in_one_job_uses_the_same_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            for name in ("a", "b"):
                (layout.raw / f"{name}.md").write_text(
                    f"# {name}\n\ncontent\n", encoding="utf-8"
                )
            loader = _LoadOnce(CONFIG_A)
            seen: list[LlmConfig] = []

            def fake_auto(*_args, **kwargs):
                seen.append(kwargs["llm_config"])
                return object()

            with (
                patch("noteagent.llm.config.load_llm_config", loader),
                patch("noteagent.api.ingest.auto_ingest", fake_auto),
                patch(
                    "noteagent.api.ingest.ingest_result_payload",
                    lambda *_args, **_kwargs: {"ok": True},
                ),
            ):
                payload = run_ingest_job(
                    layout, ["raw/a.md", "raw/b.md"]
                )

            self.assertEqual(payload["errors"], [])
            self.assertEqual(loader.calls, 1)
            self.assertEqual(seen, [CONFIG_A, CONFIG_A])


TOPIC_MAP_JSON = json.dumps(
    {
        "primary": {
            "name": "KL",
            "slug": "kl",
            "wikiPath": "wiki/concepts/kl.md",
            "collectionIds": ["c1"],
            "blockIds": ["b1"],
            "evidence": "[b1] KL 是相对熵。",
            "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
        }
    }
)


class TopicMapSnapshotTests(unittest.TestCase):
    def test_a_planning_pass_reads_the_connection_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = layout.raw / "web" / "kl.html"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("<p>KL</p>\n", encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="KL",
                    collections=(
                        ClipCollection(
                            id="c1",
                            source_key="raw/web/kl.html",
                            source_label="KL",
                            block_ids=("b1",),
                            blocks=(
                                DerivedBlock(
                                    id="b1",
                                    tag="p",
                                    text="KL 是相对熵。",
                                    preview="KL",
                                ),
                            ),
                        ),
                    ),
                ),
            )
            loader = _LoadOnce(CONFIG_A)
            seen: list[LlmConfig] = []

            def fake_map(layout, *, llm_config, map_text=None, **kwargs):
                seen.append(llm_config)
                return map_clips(
                    layout,
                    llm_config=llm_config,
                    map_text=TOPIC_MAP_JSON,
                    **kwargs,
                )

            with (
                patch("noteagent.llm.config.load_llm_config", loader),
                patch("noteagent.api.topic_gate.map_clips", fake_map),
            ):
                run_topic_map_job(layout)

            self.assertEqual(loader.calls, 1)
            self.assertEqual(seen, [CONFIG_A])


class ConfirmedIngestSnapshotTests(unittest.TestCase):
    def test_a_confirmed_ingest_reads_the_connection_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            loader = _LoadOnce(CONFIG_A)
            seen: list[LlmConfig] = []

            class Session:
                round_id = ""
                step = 0

            def fake_ingest(*_args, **kwargs):
                seen.append(kwargs["llm_config"])
                return object()

            with (
                patch("noteagent.llm.config.load_llm_config", loader),
                patch("noteagent.api.topic_gate.ingest_confirmed", fake_ingest),
                patch(
                    "noteagent.api.topic_gate.ingest_result_payload",
                    lambda *_args, **_kwargs: {"ok": True},
                ),
                patch(
                    "noteagent.api.topic_gate.load_session",
                    lambda _layout: Session(),
                ),
            ):
                payload = run_ingest_confirmed_job(layout, [])

            self.assertEqual(payload["errors"], [])
            self.assertEqual(loader.calls, 1)
            self.assertEqual(seen, [CONFIG_A])


class DeepSearchSnapshotTests(unittest.TestCase):
    def test_a_broken_connection_falls_back_instead_of_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            act_session(layout, {"action": "start", "mode": "explore", "intent": "熵"})

            def broken() -> LlmConfig:
                raise ValueError("NOTEAGENT_LLM_TEMPERATURE is not a number")

            with patch("noteagent.wiki.deep_search.load_llm_config", broken):
                session = act_session(layout, {"action": "proposeWhitelist"})

            self.assertIn("model unavailable", session["notice"])

    def test_proposing_pages_reads_the_connection_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            act_session(layout, {"action": "start", "mode": "explore", "intent": "熵"})
            loader = _LoadOnce(CONFIG_A)
            seen: list[LlmConfig] = []

            class Client:
                def chat(self, _messages, **_kwargs) -> str:
                    return json.dumps(
                        {
                            "pages": [
                                {
                                    "title": "熵",
                                    "slug": "entropy",
                                    "action": "add",
                                    "plan": "补定义",
                                    "query": "熵",
                                }
                            ]
                        }
                    )

            with (
                patch("noteagent.wiki.deep_search.load_llm_config", loader),
                patch(
                    "noteagent.wiki.deep_search.LlmClient",
                    lambda config: seen.append(config) or Client(),
                ),
            ):
                session = act_session(layout, {"action": "proposeWhitelist"})

            self.assertEqual(loader.calls, 1)
            self.assertEqual(seen, [CONFIG_A])
            self.assertIn(
                "entropy",
                {item["slug"] for item in session["whitelist"]},
            )


if __name__ == "__main__":
    unittest.main()
