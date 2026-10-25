from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.chat import iter_chat_query_sse
from noteagent.chat.query import run_query
from noteagent.chat.store import ChatConversation, create_conversation, load_conversation
from noteagent.llm.client import LlmTurn
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled


def _sse_events(blob: bytes) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in blob.decode("utf-8").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        kind = ""
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if kind and data_lines:
            events.append((kind, json.loads("\n".join(data_lines))))
    return events


class _AnswerClient:
    def __init__(self, config=None) -> None:
        pass

    def complete(self, messages, **kwargs):
        from noteagent.util.cancel import check_cancelled

        check_cancelled(kwargs.get("cancel"))
        return LlmTurn(content="最终回答", finish_reason="stop")


class ChatQueryCancelTests(unittest.TestCase):
    def test_cancelling_before_the_answer_keeps_only_the_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                run_query(layout, created.id, "hello", cancel=cancel)
            loaded = load_conversation(layout, created.id)
            self.assertEqual(len(loaded.messages), 1)
            self.assertEqual(loaded.messages[0].role, "user")
            self.assertEqual(loaded.messages[0].content, "hello")

    def test_status_emits_think_then_answer(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def status(message: str, **kwargs) -> None:
            seen.append((message, kwargs.get("step_id")))

        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            with patch("noteagent.chat.query.load_llm_config", return_value=object()):
                with patch("noteagent.chat.query.LlmClient", _AnswerClient):
                    saved = run_query(
                        layout,
                        created.id,
                        "hello",
                        on_status=status,
                    )
        self.assertEqual(saved.messages[-1].content, "最终回答")
        ids = [item[1] for item in seen]
        self.assertIn("think", ids)
        self.assertIn("answer", ids)

    def test_chat_sse_close_sets_cancel(self) -> None:
        started = threading.Event()
        saw_cancel = threading.Event()

        def fake_run_query(layout, conversation_id, content, **kwargs):
            started.set()
            kwargs["on_status"](
                "正在思考是否需要打开、搜索、检查或规划笔记", step_id="think"
            )
            cancel = kwargs.get("cancel")
            if cancel is None or not cancel.wait(timeout=2):
                return {"id": conversation_id}
            saw_cancel.set()
            raise JobCancelled()

        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with patch("noteagent.api.chat.run_query", fake_run_query):
                gen = iter_chat_query_sse(layout, "c-test", "hi")
                try:
                    first = next(gen)
                    self.assertIn(b"think", first)
                    self.assertTrue(started.is_set())
                finally:
                    gen.close()
                self.assertTrue(saw_cancel.wait(2))

    def test_a_completed_answer_ends_the_stream_with_done(self) -> None:
        def fake_run_query(layout, conversation_id, content, **kwargs):
            kwargs["on_status"]("正在保存回答", step_id="answer")
            return ChatConversation(
                id=conversation_id,
                title="t",
                updated_at="",
                messages=(),
            )

        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with patch("noteagent.api.chat.run_query", fake_run_query):
                events = _sse_events(
                    b"".join(iter_chat_query_sse(layout, "c-test", "hi"))
                )
        self.assertTrue(any(kind == "done" for kind, _ in events))
        self.assertFalse(any(kind == "error" for kind, _ in events))
        status = [item for item in events if item[0] == "status"]
        self.assertEqual(status[0][1]["stepId"], "answer")

    def test_a_retraction_goes_out_as_a_token_event_without_text(self) -> None:
        def fake_run_query(layout, conversation_id, content, **kwargs):
            # A round that looked like prose and then turned into a tool call.
            kwargs["on_token"]("我先查一下")
            kwargs["on_reset"]()
            kwargs["on_token"]("熵衡量不确定性")
            return ChatConversation(
                id=conversation_id,
                title="t",
                updated_at="",
                messages=(),
            )

        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with patch("noteagent.api.chat.run_query", fake_run_query):
                events = _sse_events(b"".join(iter_chat_query_sse(layout, "c-test", "hi")))

        tokens = [item[1] for item in events if item[0] == "token"]
        self.assertEqual(
            tokens,
            [
                {"text": "我先查一下"},
                {"text": "", "reset": True},
                {"text": "熵衡量不确定性"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
