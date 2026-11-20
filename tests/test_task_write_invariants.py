"""The task's write invariants: one seam, content revs, and lock order.

Every mutation of the writing task goes through one writer seam. A content
write (cards, materials, frames, gate, result) moves ``rev`` exactly once and
invalidates the person's approval and pending request. A log write (op status,
resume markers, cancel) moves neither. The lock order is page then task, so a
person's save and the executor's page write cannot deadlock across the two
locks.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import Capture
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.project.init import init_project
from noteagent.task.model import (
    Approval,
    Card,
    Frame,
    MaterialBlock,
    Op,
    SearchCard,
    StagedMaterial,
    Task,
    WriteRequest,
    canonical_page_spec,
)
from noteagent.task.service import TaskOpFailed, TaskService, TaskRegistry
from noteagent.task.store import TaskStore
from noteagent.task.writing import PageLocks, SaveOutcome

PAGE_SPEC = {
    "slug": "entropy",
    "title": "Entropy",
    "action": "create",
    "sections": [
        {"title": "What it is", "blocks": ["b1"]},
        {"title": "Why it matters", "blocks": ["b2"]},
    ],
}

#: A valid spec that is not the one a seeded approval covers.
OTHER_SPEC = {**PAGE_SPEC, "title": "Entropy, again"}

CARDS = (
    SearchCard(
        title="Entropy",
        url="https://example.com/entropy",
        snippet="熵衡量不确定性。",
        site="example.com",
    ),
)

CAPTURE = Capture(
    url="https://example.com/entropy",
    title="Entropy",
    markdown="熵衡量不确定性。",
    html=(
        "<html><head><title>Entropy</title></head><body><article>"
        '<p data-na-block="b1">熵衡量不确定性。</p>'
        '<p data-na-block="b2">信息是不确定性的减少。</p>'
        "</article></body></html>"
    ),
    capture_method="test",
    fidelity="ok",
    reasons=(),
)

STAGED = StagedMaterial(
    material="raw/web/entropy-a1.html",
    title="Entropy",
    url="https://example.com/entropy",
    kind="html",
    fidelity="ok",
    state="staged",
    block_count=2,
    blocks=(
        MaterialBlock(id="b1", tag="p", preview="熵衡量不确定性。"),
        MaterialBlock(id="b2", tag="p", preview="信息是不确定性的减少。"),
    ),
)


class FakeSearch:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def __call__(self, query: str):
        if self.error is not None:
            raise self.error
        return list(CARDS)


class FakeFetch:
    def __call__(self, url: str) -> Capture:
        return CAPTURE


class FakeStage:
    def __call__(self, capture: Capture, run_id: str) -> StagedMaterial:
        return STAGED


class FakeCompose:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def __call__(self, layout, request, cancel=None) -> FileBlock:
        if self.error is not None:
            raise self.error
        return FileBlock(path=request.wiki_path, content="# Entropy\n\nnew\n")


class FakeSave:
    def __call__(self, layout, request, cancel=None) -> SaveOutcome:
        return SaveOutcome(
            status="written",
            written=(request.wiki_path,),
            summary=f"wrote {request.wiki_path}",
        )


def _seed_task(
    *,
    rev: int = 5,
    approval_spec: dict | None = None,
    request_spec: dict | None = None,
    ops: tuple[Op, ...] = (),
    status: str = "needs_approval",
) -> Task:
    """One task with the person's gate set, so a write can clear or keep it."""
    approved = (
        None
        if approval_spec is None
        else Approval(
            frame_id="frame-1",
            spec=canonical_page_spec(approval_spec),
            rev=rev,
            by="human",
            at="2026-11-10T09:05:00Z",
        )
    )
    requested = (
        None
        if request_spec is None
        else WriteRequest(
            frame_id="frame-1",
            spec=canonical_page_spec(request_spec),
            rev=rev,
            at="2026-11-10T09:05:00Z",
        )
    )
    return Task(
        id="task-7",
        created_at="2026-11-10T09:00:00Z",
        updated_at="2026-11-10T09:05:00Z",
        rev=rev,
        status=status,  # type: ignore[arg-type]
        cards=(
            Card(
                id="card-1",
                title="Entropy",
                url="https://example.com/entropy",
                snippet="熵衡量不确定性。",
                site="example.com",
            ),
        ),
        materials=(
            {
                "id": "raw/web/entropy-a1.html",
                "title": "Entropy",
                "url": "https://example.com/entropy",
                "kind": "html",
                "fidelity": "ok",
                "state": "staged",
                "runId": "task-7",
                "blockCount": 2,
                "blockIds": ["b1", "b2"],
                "blocks": [
                    {"id": "b1", "tag": "p", "preview": "熵衡量不确定性。"},
                    {"id": "b2", "tag": "p", "preview": "信息是不确定性的减少。"},
                ],
            },
        ),
        frames=(
            Frame(
                id="frame-1",
                doc_id="raw/web/entropy-a1.html",
                blocks=("b1", "b2"),
                source="agent",
            ),
        ),
        approval=approved,
        request=requested,
        ops=ops,
    )


def _active_op(op_id: str = "op-1", *, kind: str = "search") -> Op:
    return Op(
        id=op_id,
        kind=kind,  # type: ignore[arg-type]
        actor="agent",
        status="running",
        input={"query": "entropy", "sessionId": "s-1"},
        expected_rev=0,
        created_at="2026-11-10T09:00:00Z",
        updated_at="2026-11-10T09:00:00Z",
    )


def _terminal_op(op_id: str = "op-1") -> Op:
    return replace(
        _active_op(op_id),
        status="done",
        resume_requested=True,
        resume_rev=4,
    )


class TaskWriteSeamTests(unittest.TestCase):
    """Every mutation path, enumerated: content bumps and clears the gate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        self.store = TaskStore(self.layout)

    def service(
        self,
        *,
        search=None,
        fetch=None,
        stage=None,
        compose=None,
        save=None,
        spawn=None,
        page_locks=None,
        registry=None,
    ) -> TaskService:
        return TaskService(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
            search=search or FakeSearch(),
            fetch=fetch or FakeFetch(),
            stage=stage or FakeStage(),
            compose=compose or FakeCompose(),
            save=save or FakeSave(),
            page_locks=page_locks,
            registry=registry,
            spawn=spawn if spawn is not None else (lambda work: work()),
            wait_s=0.01,
            pick_wait_s=0.01,
            compose_wait_s=0.01,
            poll_s=0.001,
        )

    def seed(self, task: Task) -> Task:
        self.store.save(task)
        return task

    def load(self) -> Task:
        stored = self.store.load()
        self.assertIsNotNone(stored)
        return stored

    def assert_log_only(self, before: Task, after: Task) -> None:
        self.assertEqual(after.rev, before.rev, "a log write moved the rev")
        self.assertEqual(after.approval, before.approval)
        self.assertEqual(after.request, before.request)

    def assert_content_once(self, before: Task, after: Task) -> None:
        self.assertEqual(after.rev, before.rev + 1, "a content write must bump once")
        # A content write always invalidates the gate unless it is the approval
        # itself; every scenario asserting this seeds both halves.
        self.assertIsNone(after.approval)
        self.assertIsNone(after.request)

    def test_a_search_request_and_its_settle_are_log_only(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )
        service = self.service(spawn=lambda work: None)

        outcome = service.search("entropy")

        self.assertEqual(outcome.status, "requested")
        after = self.load()
        self.assert_log_only(before, after)
        self.assertTrue(after.ops[-1].resume_requested)
        self.assertEqual(after.ops[-1].status, "requested")

    def test_a_finished_search_is_one_content_write(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )

        outcome = self.service().search("entropy")

        self.assertEqual(outcome.status, "done")
        after = self.load()
        self.assert_content_once(before, after)
        self.assertEqual(after.cards[-1].title, "Entropy")

    def test_a_failed_search_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )

        with self.assertRaises(TaskOpFailed):
            self.service(search=FakeSearch(error=RuntimeError("down"))).search(
                "entropy"
            )

        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].status, "failed")

    def test_a_pick_request_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )
        service = self.service(spawn=lambda work: None)

        outcome = service.pick("card-1")

        self.assertEqual(outcome.status, "requested")
        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].status, "requested")

    def test_a_finished_pick_is_one_content_write(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )

        outcome = self.service().pick("card-1")

        self.assertEqual(outcome.status, "done")
        after = self.load()
        self.assert_content_once(before, after)
        self.assertEqual(after.materials[0]["id"], STAGED.material)

    def test_a_failed_pick_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC)
        )

        def fail_fetch(url: str) -> Capture:
            raise RuntimeError("network down")

        with self.assertRaises(TaskOpFailed):
            self.service(fetch=fail_fetch).pick("card-1")

        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].status, "failed")

    def test_a_frame_is_one_content_write_that_clears_the_gate(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=PAGE_SPEC, request_spec=PAGE_SPEC)
        )

        outcome = self.service().frame("raw/web/entropy-a1.html", ["b1"])

        after = self.load()
        self.assert_content_once(before, after)
        self.assertEqual(outcome.rev, after.rev)
        self.assertTrue(outcome.approval_cleared)
        self.assertEqual(after.frames[0].blocks, ("b1",))

    def test_a_compose_without_a_matching_approval_is_one_content_write(
        self,
    ) -> None:
        before = self.seed(_seed_task(approval_spec=OTHER_SPEC))

        outcome = self.service().compose("frame-1", PAGE_SPEC)

        self.assertEqual(outcome.status, "needs_approval")
        after = self.load()
        self.assertEqual(after.rev, before.rev + 1)
        self.assertIsNone(after.approval)
        self.assertIsNotNone(after.request)

    def test_a_write_request_is_log_only_and_keeps_the_approval(self) -> None:
        before = self.seed(_seed_task(approval_spec=PAGE_SPEC))
        service = self.service(spawn=lambda work: None)

        outcome = service.compose("frame-1", PAGE_SPEC)

        self.assertEqual(outcome.status, "requested")
        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].kind, "write")
        self.assertEqual(after.ops[-1].status, "requested")

    def test_an_approval_is_one_content_write_that_replaces_the_gate(self) -> None:
        before = self.seed(_seed_task(request_spec=PAGE_SPEC))

        outcome = self.service().approve(
            "task-7", before.rev, "frame-1", PAGE_SPEC, "approve"
        )

        self.assertEqual(outcome.decision, "approve")
        after = self.load()
        self.assertEqual(after.rev, before.rev + 1)
        self.assertIsNotNone(after.approval)
        self.assertEqual(after.approval.rev, after.rev)
        self.assertIsNone(after.request)
        self.assertEqual(after.status, "idle")

    def test_a_rejection_is_one_content_write_that_clears_the_gate(self) -> None:
        before = self.seed(_seed_task(request_spec=PAGE_SPEC))

        self.service().approve(
            "task-7", before.rev, "frame-1", PAGE_SPEC, "reject"
        )

        after = self.load()
        self.assert_content_once(before, after)

    def test_a_page_edit_is_one_content_write_that_clears_the_gate(self) -> None:
        before = self.seed(
            _seed_task(approval_spec=PAGE_SPEC, request_spec=PAGE_SPEC)
        )

        outcome = self.service().record_page_edit("wiki/entropy.md", "digest")

        after = self.load()
        self.assert_content_once(before, after)
        self.assertTrue(outcome.approval_cleared)
        self.assertTrue(outcome.request_cleared)
        self.assertEqual(after.ops[-1].kind, "edit")

    def test_a_finished_write_is_one_content_write_that_clears_the_gate(
        self,
    ) -> None:
        before = self.seed(
            _seed_task(approval_spec=PAGE_SPEC, request_spec=PAGE_SPEC)
        )

        outcome = self.service().compose("frame-1", PAGE_SPEC)

        self.assertEqual(outcome.status, "written")
        after = self.load()
        self.assert_content_once(before, after)
        self.assertEqual(after.result["status"], "written")
        self.assertEqual(after.status, "done")

    def test_a_failed_write_is_one_content_write_that_clears_the_gate(
        self,
    ) -> None:
        before = self.seed(
            _seed_task(approval_spec=PAGE_SPEC, request_spec=PAGE_SPEC)
        )

        with self.assertRaises(TaskOpFailed):
            self.service(compose=FakeCompose(error=RuntimeError("model"))).compose(
                "frame-1", PAGE_SPEC
            )

        after = self.load()
        self.assert_content_once(before, after)
        self.assertEqual(after.result["status"], "failed")
        self.assertEqual(after.status, "failed")
        self.assertEqual(after.ops[-1].status, "failed")

    def test_a_cancel_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC,
                request_spec=PAGE_SPEC,
                ops=(_active_op(),),
            )
        )

        outcome = self.service().cancel_op("op-1")

        self.assertTrue(outcome.changed)
        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].status, "cancelled")

    def test_a_resume_op_is_log_only(self) -> None:
        op = replace(_active_op(), interrupted=True)
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC, request_spec=PAGE_SPEC, ops=(op,)
            )
        )

        self.service(spawn=lambda work: None).resume_op("op-1")

        after = self.load()
        self.assert_log_only(before, after)
        self.assertTrue(after.ops[-1].resume_requested)
        self.assertFalse(after.ops[-1].interrupted)

    def test_claiming_a_resume_attempt_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC,
                request_spec=PAGE_SPEC,
                ops=(_terminal_op(),),
            )
        )

        notice = self.service().claim_resume("op-1")

        self.assertIsNotNone(notice)
        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].resume_attempt, 1)
        self.assertFalse(after.ops[-1].resume_started)

    def test_marking_a_resume_delivered_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC,
                request_spec=PAGE_SPEC,
                ops=(_terminal_op(),),
            )
        )

        self.service().mark_resume_delivered("op-1")

        after = self.load()
        self.assert_log_only(before, after)
        self.assertTrue(after.ops[-1].resume_started)

    def test_recording_a_resume_error_is_log_only(self) -> None:
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC,
                request_spec=PAGE_SPEC,
                ops=(_terminal_op(),),
            )
        )

        self.service().record_resume_error("op-1", "the session is gone")

        after = self.load()
        self.assert_log_only(before, after)
        self.assertEqual(after.ops[-1].resume_error, "the session is gone")

    def test_recovery_marks_active_ops_interrupted_without_a_rev(self) -> None:
        before = self.seed(
            _seed_task(
                approval_spec=OTHER_SPEC,
                request_spec=PAGE_SPEC,
                ops=(_active_op(),),
            )
        )

        self.service().recover_ops()

        after = self.load()
        self.assert_log_only(before, after)
        self.assertTrue(after.ops[-1].interrupted)


class _Recorder:
    """A shared log of lock acquisitions and releases."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, str]] = []

    def add(self, kind: str, action: str) -> None:
        with self._lock:
            self.events.append((kind, action))


class _RecordingLock:
    def __init__(self, recorder: _Recorder) -> None:
        self._inner = threading.Lock()
        self._recorder = recorder

    def __enter__(self) -> "_RecordingLock":
        self._inner.acquire()
        self._recorder.add("task", "acquired")
        return self

    def __exit__(self, *args: object) -> bool:
        self._recorder.add("task", "released")
        self._inner.release()
        return False

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        acquired = self._inner.acquire(blocking, timeout)
        if acquired:
            self._recorder.add("task", "acquired")
        return acquired

    def release(self) -> None:
        self._recorder.add("task", "released")
        self._inner.release()


class RecordingRegistry(TaskRegistry):
    def __init__(self, recorder: _Recorder) -> None:
        super().__init__()
        self._recorder = recorder

    def writer(self, project_key: str) -> object:
        with self._lock:
            return self._writers.setdefault(
                project_key, _RecordingLock(self._recorder)
            )


class RecordingPageLocks(PageLocks):
    """Page locks that record their interval and can prove they were held."""

    def __init__(self, recorder: _Recorder) -> None:
        super().__init__()
        self._recorder = recorder
        self.held: set[tuple[str, str]] = set()

    @contextmanager
    def holding(self, project_key: str, page: str) -> Iterator[None]:
        with super().holding(project_key, page):
            key = (project_key, page.replace("\\", "/").lstrip("/"))
            self.held.add(key)
            self._recorder.add("page", "acquired")
            try:
                yield
            finally:
                self._recorder.add("page", "released")
                self.held.discard(key)

    def is_held(self, project_key: str, page: str) -> bool:
        return (project_key, page.replace("\\", "/").lstrip("/")) in self.held


class TaskLockOrderTests(unittest.TestCase):
    """Page lock first, task lock second, released in reverse."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        self.store = TaskStore(self.layout)
        self.recorder = _Recorder()
        self.page_locks = RecordingPageLocks(self.recorder)
        self.registry = RecordingRegistry(self.recorder)

    def service(self, *, compose=None, save=None, spawn=None) -> TaskService:
        return TaskService(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
            search=FakeSearch(),
            fetch=FakeFetch(),
            stage=FakeStage(),
            compose=compose or FakeCompose(),
            save=save or FakeSave(),
            page_locks=self.page_locks,
            registry=self.registry,
            spawn=spawn if spawn is not None else (lambda work: work()),
            wait_s=0.01,
            pick_wait_s=0.01,
            compose_wait_s=5.0,
            poll_s=0.001,
        )

    def seed(self) -> Task:
        task = _seed_task(approval_spec=PAGE_SPEC, status="idle")
        self.store.save(task)
        return task

    def test_the_executor_takes_the_page_lock_before_the_task_lock(self) -> None:
        self.seed()

        outcome = self.service().compose("frame-1", PAGE_SPEC)

        self.assertEqual(outcome.status, "written")
        page_at = self.recorder.events.index(("page", "acquired"))
        task_after = next(
            index
            for index, event in enumerate(self.recorder.events)
            if event == ("task", "acquired") and index > page_at
        )
        task_release = next(
            index
            for index, event in enumerate(self.recorder.events)
            if event == ("task", "released") and index > task_after
        )
        page_release = next(
            index
            for index, event in enumerate(self.recorder.events)
            if event == ("page", "released") and index > page_at
        )
        # The task lock is nested inside the page lock: acquired after it and
        # released before it, never the other way round.
        self.assertLess(task_after, task_release)
        self.assertLess(task_release, page_release)

    def test_the_human_save_records_the_edit_while_holding_the_page_lock(
        self,
    ) -> None:
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\nold\n",
            encoding="utf-8",
        )
        self.seed()
        seen: list[bool] = []

        def record(path: str, revision: str) -> None:
            seen.append(
                self.page_locks.is_held(
                    str(self.layout.root.resolve()), "wiki/entropy.md"
                )
            )
            self.service().record_page_edit(path, revision)

        from noteagent.api.pages import page_save_payload

        payload = page_save_payload(
            self.layout,
            {
                "path": "wiki/entropy.md",
                "body": "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\nnew\n",
            },
            page_locks=self.page_locks,
            on_saved=record,
        )

        self.assertTrue(payload["changed"])
        self.assertEqual(seen, [True])
        task = self.store.load()
        self.assertIsNotNone(task)
        self.assertEqual(task.ops[-1].kind, "edit")
        self.assertEqual(task.ops[-1].actor, "human")

    def test_a_save_and_an_edit_on_one_page_do_not_deadlock(self) -> None:
        self.seed()
        gate = threading.Event()
        self.addCleanup(gate.set)

        class _GatedSave(FakeSave):
            def __call__(self, layout, request, cancel=None):
                gate.wait(5)
                return super().__call__(layout, request, cancel)

        service = self.service(
            save=_GatedSave(),
            spawn=lambda work: threading.Thread(target=work).start(),
        )
        errors: list[BaseException] = []

        def write() -> None:
            try:
                service.compose("frame-1", PAGE_SPEC)
            except BaseException as err:  # noqa: BLE001 - re-raised below
                errors.append(err)

        writer = threading.Thread(target=write)
        writer.start()
        try:
            # The executor holds the page and the task lock inside its save.
            deadline = threading.Event()
            for _ in range(500):
                if self.page_locks.held:
                    break
                deadline.wait(0.01)
            self.assertTrue(self.page_locks.held)

            # The person's save must be refused by the page lock, promptly:
            # it never takes the task lock first and waits for the page.
            from noteagent.api.pages import page_save_payload
            from noteagent.util.refusal import Refusal

            started = threading.Event()
            result: list[str] = []

            def save() -> None:
                started.set()
                try:
                    page_save_payload(
                        self.layout,
                        {
                            "path": "wiki/entropy.md",
                            "body": "a person's edit\n",
                        },
                        page_locks=self.page_locks,
                    )
                except Refusal as err:
                    result.append(err.code)

            person = threading.Thread(target=save)
            person.start()
            self.assertTrue(started.wait(2))
            person.join(2)
            self.assertFalse(person.is_alive(), "the human save deadlocked")
            self.assertEqual(result, ["page.busy"])
        finally:
            gate.set()
            writer.join(5)
        self.assertFalse(writer.is_alive(), "the executor deadlocked")
        self.assertEqual(errors, [])
        task = self.store.load()
        self.assertIsNotNone(task)
        self.assertEqual(task.ops[-1].status, "done")


if __name__ == "__main__":
    unittest.main()
