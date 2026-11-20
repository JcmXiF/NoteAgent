"""The production compose node: staged frame -> one source-marked FILE block.

This is the pure node the executor calls; it is exercised here with the real
staging area and the real ``write_confirmed_page`` so the test proves the page
is built from the frame's blocks and that every collection's marker reaches a
heading. The model is scripted: no network and no client.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.acquire.types import Capture
from noteagent.effects.journal import COMMITTED, WIKI_PAGE_WRITE, EffectJournal
from noteagent.pipeline.materials import Materials
from noteagent.project.init import init_project
from noteagent.task.writing import (
    ComposePageError,
    ComposeRequest,
    SaveRequest,
    TaskPageCompose,
    TaskPageSave,
    WriteCancelled,
    locators_for_frame,
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


class TaskPageComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        self.materials = Materials(self.layout, clock=FixedClock())
        self.staged = self.materials.stage(
            CAPTURE, run_id="task-7", captured_at="2026-11-10T09:00:00Z"
        )
        rows = self.materials.blocks(
            self.staged.material, run_id="task-7"
        ) or []
        self.block_ids = tuple(str(row.get("id")) for row in rows)
        self.spec = {
            "slug": "entropy",
            "title": "Entropy",
            "action": "create",
            "sections": [
                {"title": "What it is", "blocks": [self.block_ids[0]]},
                {"title": "Why it matters", "blocks": [self.block_ids[1]]},
            ],
        }

    def request(self, **overrides) -> ComposeRequest:
        values: dict[str, object] = {
            "task_id": "task-7",
            "intent": "entropy",
            "slug": "entropy",
            "page_name": "Entropy",
            "action": "create",
            "wiki_path": "wiki/entropy.md",
            "material": self.staged.material,
            "blocks": self.block_ids,
            "locators": locators_for_frame(
                self.staged.material, self.block_ids
            ),
            "spec": self.spec,
        }
        values.update(overrides)
        return ComposeRequest(**values)  # type: ignore[arg-type]

    def script(self) -> dict[str, object]:
        sources = ", ".join(
            f'"{item}"'
            for item in locators_for_frame(
                self.staged.material, self.block_ids
            )
        )
        return {
            "blueprint": {
                "title": "Entropy",
                "wikiPath": "wiki/entropy.md",
                "contributions": [
                    {
                        "collectionId": self.staged.material,
                        "role": "other",
                        "sectionTitle": "What it is",
                        "blockIds": list(self.block_ids),
                    }
                ],
            },
            "drafts": {
                self.staged.material: (
                    "## What it is\n\n熵衡量不确定性。"
                )
            },
            "compose": (
                "---FILE: wiki/entropy.md---\n"
                "---\n"
                "title: Entropy\n"
                "created: 2026-11-10\n"
                "updated: 2026-11-10\n"
                f"sources: [{sources}]\n"
                "---\n\n"
                "# Entropy\n\n"
                "## What it is\n\n"
                "熵衡量不确定性。\n\n"
                "## Why it matters\n\n"
                "信息是不确定性的减少。\n"
                "---END FILE---\n"
            ),
        }

    def test_the_composed_page_is_one_marked_file_block(self) -> None:
        compose = TaskPageCompose(script=self.script())

        block = compose(self.layout, self.request())

        self.assertEqual(block.path, "wiki/entropy.md")
        # The writer stamped the collection's marker above the heading it owns:
        # the page is source-grounded, not a model paragraph with no trace.
        self.assertIn(
            f"<!-- na-clip:{self.staged.material} -->", block.content
        )
        self.assertIn("熵衡量不确定性", block.content)
        self.assertIn("信息是不确定性的减少", block.content)
        first = block.content.splitlines().index(
            f"<!-- na-clip:{self.staged.material} -->"
        )
        self.assertTrue(
            block.content.splitlines()[first + 1].startswith("## ")
        )
        for locator in locators_for_frame(
            self.staged.material, self.block_ids
        ):
            self.assertIn(locator, block.content)

    def test_a_frame_whose_staging_is_gone_is_a_compose_error(self) -> None:
        compose = TaskPageCompose(script=self.script())

        with self.assertRaises(ComposePageError):
            compose(
                self.layout,
                self.request(
                    material="raw/web/missing.html",
                    blocks=("b1",),
                    locators=("raw/web/missing.html#b1",),
                ),
            )


class TaskPageSaveTests(unittest.TestCase):
    """The save node is the one place a page lands, and it lands once."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        self.materials = Materials(self.layout, clock=FixedClock())
        self.staged = self.materials.stage(
            CAPTURE, run_id="task-7", captured_at="2026-11-10T09:00:00Z"
        )
        rows = self.materials.blocks(self.staged.material, run_id="task-7") or []
        self.block_ids = tuple(str(row.get("id")) for row in rows)
        self.request = SaveRequest(
            task_id="task-7",
            op_id="op-9",
            session_id="s-1",
            wiki_path="wiki/entropy.md",
            action="create",
            content=(
                "---\n"
                "title: Entropy\n"
                "created: 2026-11-10\n"
                "updated: 2026-11-10\n"
                "sources: [\"raw/web/entropy-a1.html#b1\"]\n"
                "---\n\n"
                "# Entropy\n\n"
                f"<!-- na-clip:{self.staged.material} -->\n"
                "## What it is\n\n熵衡量不确定性。\n"
            ),
            material=self.staged.material,
            locators=locators_for_frame(
                self.staged.material, self.block_ids
            ),
            expected_rev=3,
        )

    def save(self):
        return TaskPageSave()(self.layout, self.request)

    def test_saving_the_same_op_twice_writes_the_page_once(self) -> None:
        first = self.save()
        self.assertEqual(first.status, "written")
        page = self.layout.wiki / "entropy.md"
        self.assertTrue(page.is_file())

        # A person edits the page after the write. Re-running the same op (its
        # executor died after the save, say) must not write it again.
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\nperson text\n",
            encoding="utf-8",
        )
        second = self.save()

        self.assertEqual(second.status, "written")
        self.assertIn("already wrote", second.summary)
        self.assertIn("person text", page.read_text(encoding="utf-8"))
        effects = EffectJournal(self.layout).query(
            run_id="op-9", kind=WIKI_PAGE_WRITE, status=COMMITTED
        )
        self.assertEqual(len(effects), 1)

    def test_a_cancelled_save_writes_no_page(self) -> None:
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(WriteCancelled):
            TaskPageSave()(self.layout, self.request, cancel)

        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        self.assertEqual(
            list(
                EffectJournal(self.layout).query(
                    run_id="op-9", kind=WIKI_PAGE_WRITE, status=COMMITTED
                )
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
