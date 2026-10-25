from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from noteagent.api.sse import iter_job_sse, sse_status_payload
from noteagent.project.doctor import doctor_after_write
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


class SseStatusTests(unittest.TestCase):
    def test_payload_omits_missing_step(self) -> None:
        self.assertEqual(sse_status_payload("hi"), {"message": "hi"})
        self.assertEqual(
            sse_status_payload("hi", "plan"),
            {"message": "hi", "stepId": "plan"},
        )

    def test_job_sse_default_step_id(self) -> None:
        def worker(on_status, cancel, emit):
            on_status("mapping clipped passages")
            return {"ok": True}

        events = _sse_events(b"".join(iter_job_sse(worker, default_step_id="plan")))
        status = [item for item in events if item[0] == "status"]
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0][1]["message"], "mapping clipped passages")
        self.assertEqual(status[0][1]["stepId"], "plan")
        self.assertTrue(any(kind == "done" for kind, _ in events))

    def test_job_sse_override_step_id(self) -> None:
        def worker(on_status, cancel, emit):
            on_status("Checking wiki (doctor)", step_id="doctor")
            return {"ok": True}

        events = _sse_events(
            b"".join(iter_job_sse(worker, default_step_id="compose"))
        )
        status = [item for item in events if item[0] == "status"]
        self.assertEqual(status[0][1]["stepId"], "doctor")
        self.assertNotIn("compose", json.dumps(status[0][1]))

    def test_job_sse_carries_a_jobs_own_events(self) -> None:
        # The one thing the shared shape does not already cover: a chat answer
        # sends its tokens through the emitter.
        def worker(on_status, cancel, emit):
            emit("token", {"text": "he"})
            emit("token", {"text": "llo"})
            return {"ok": True}

        events = _sse_events(b"".join(iter_job_sse(worker)))
        tokens = [item[1]["text"] for item in events if item[0] == "token"]
        self.assertEqual(tokens, ["he", "llo"])
        self.assertTrue(any(kind == "done" for kind, _ in events))

    def test_doctor_after_write_emits_step_id(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def status(message: str, **kwargs) -> None:
            seen.append((message, kwargs.get("step_id")))

        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            doctor_after_write(layout, dry_run=False, status=status)
        self.assertEqual(seen[0][0], "Checking wiki (doctor)")
        self.assertEqual(seen[0][1], "doctor")

    def test_job_sse_cancelled_is_error_not_done(self) -> None:
        def worker(on_status, cancel, emit):
            raise JobCancelled()

        events = _sse_events(b"".join(iter_job_sse(worker)))
        errors = [item for item in events if item[0] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][1]["detail"], "job cancelled")
        self.assertFalse(any(kind == "done" for kind, _ in events))

    def test_job_sse_close_sets_cancel(self) -> None:
        started = threading.Event()
        saw_cancel = threading.Event()

        def worker(on_status, cancel, emit):
            started.set()
            on_status("working")
            if cancel is None or not cancel.wait(timeout=2):
                return {"ok": False}
            saw_cancel.set()
            raise JobCancelled()

        gen = iter_job_sse(worker, default_step_id="plan")
        try:
            first = next(gen)
            self.assertIn(b"working", first)
            self.assertTrue(started.is_set())
        finally:
            gen.close()
        self.assertTrue(saw_cancel.wait(2))

    def test_a_finished_job_ends_with_done(self) -> None:
        def worker(on_status, cancel, emit):
            on_status("ok")
            return {"ok": True}

        events = _sse_events(b"".join(iter_job_sse(worker)))
        self.assertTrue(any(kind == "done" for kind, _ in events))
        self.assertFalse(any(kind == "error" for kind, _ in events))


if __name__ == "__main__":
    unittest.main()
