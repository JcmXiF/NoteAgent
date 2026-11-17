"""Confirmation: the pages a run proposes, and what approval means for them."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from noteagent.ingest.auto_ingest import IngestResult
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    GateDecision,
    save_clip_store,
)
from noteagent.ingest.parse_file_blocks import ParseFileBlocksResult
from noteagent.ingest.write_file_blocks import (
    WriteFileBlocksResult,
    WriteWarning,
    WrittenFile,
)
from noteagent.pipeline.confirm import (
    CompileResult,
    PageProblem,
    PlanMissing,
    compile_outcome,
    compile_result_from,
    confirmation_digest,
    confirmation_payload,
    derive_confirmation,
    store_for_compile,
)
from noteagent.project.init import init_project

INTENT = "熵"


def _collection(
    collection_id: str = "frame-1",
    source_key: str = "raw/web/entropy.html",
) -> ClipCollection:
    return ClipCollection(
        id=collection_id,
        source_key=source_key,
        source_label="熵",
        block_ids=("b1", "b2", "b3"),
        blocks=(),
    )


def _node(
    slug: str,
    title: str,
    coverage: str,
    *,
    wiki_path: str = "",
    sections: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    collection_id: str = "frame-1",
) -> dict[str, object]:
    return {
        "slug": slug,
        "title": title,
        "coverage": coverage,
        "wikiPath": wiki_path,
        "collectionIds": [collection_id],
        "outline": [
            {
                "id": section_id,
                "title": section_title,
                "collectionId": collection_id,
                "blockIds": list(block_ids),
            }
            for section_id, section_title, block_ids in sections
        ],
    }


def _plan(
    *, entropy_path: str = "wiki/entropy.md", measurement: str = "mention"
) -> dict[str, object]:
    return {
        "intent": INTENT,
        "candidates": [
            _node(
                "entropy",
                "熵",
                "write",
                wiki_path=entropy_path,
                sections=(
                    ("s1", "定义", ("b1",)),
                    ("s2", "度量", ("b2",)),
                ),
            ),
            _node(
                "measurement",
                "测量",
                measurement,
                collection_id="frame-2",
            ),
            _node("noise", "噪声", "skip"),
        ],
    }


def _confirm(
    tmp: str,
    *,
    choices: tuple[GateDecision, ...] = (),
    overrides: list[dict[str, object]] | None = None,
    plan: dict[str, object] | None = None,
):
    layout = init_project(Path(tmp) / "projects" / "demo")
    store = ClipStore(
        intent=INTENT,
        collections=(
            _collection(),
            _collection("frame-2", "raw/web/measurement.html"),
        ),
        topic_map=_plan() if plan is None else plan,
        choices=choices,
    )
    return derive_confirmation(
        layout,
        run_id="pr-1",
        intent=INTENT,
        revision=1,
        store=store,
        overrides=overrides or [],
    )


class ConfirmationTests(unittest.TestCase):
    def test_a_page_nobody_answered_keeps_the_plans_action_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(tmp)

        self.assertEqual(
            [(page.slug, page.action) for page in confirmation.pages],
            [("entropy", "write"), ("measurement", "mention"), ("noise", "skip")],
        )
        entropy = confirmation.pages[0]
        self.assertEqual(entropy.section_ids, ("s1", "s2"))
        self.assertEqual([page.slug for page in confirmation.writes], ["entropy"])
        payload = confirmation_payload(confirmation)
        self.assertEqual(payload["planRevision"], 1)
        self.assertEqual(
            payload["pages"][0]["sections"],
            [{"id": "s1", "title": "定义"}, {"id": "s2", "title": "度量"}],
        )

    def test_a_person_answer_wins_over_the_plans_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(
                tmp,
                choices=(GateDecision("entropy", "skip", ("s1",)),),
                plan=_plan(measurement="write"),
            )

        entropy = confirmation.pages[0]
        self.assertEqual(entropy.action, "skip")
        self.assertEqual(entropy.section_ids, ("s1",))
        self.assertEqual(
            [(page.slug, page.action) for page in confirmation.writes],
            [("measurement", "write")],
        )
        # The answer is kept as the person's, and the gate records the final pass.
        self.assertEqual(
            confirmation.choices, (GateDecision("entropy", "skip", ("s1",)),)
        )
        self.assertEqual(
            confirmation.decisions,
            (
                GateDecision("entropy", "skip", ("s1",)),
                GateDecision("measurement", "write", ()),
                GateDecision("noise", "skip", ()),
            ),
        )

    def test_an_override_wins_over_the_person_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(
                tmp,
                choices=(GateDecision("entropy", "skip", ("s1",)),),
                overrides=[
                    {"slug": "entropy", "action": "write", "sectionIds": ["s2"]}
                ],
            )

        entropy = confirmation.pages[0]
        self.assertEqual(entropy.action, "write")
        self.assertEqual(entropy.section_ids, ("s2",))
        self.assertIn(
            GateDecision("entropy", "write", ("s2",)), confirmation.choices
        )

    def test_only_the_write_pages_selections_need_durable_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(tmp)

        self.assertEqual(
            [collection.id for collection in confirmation.selections],
            ["frame-1"],
        )

    def test_an_overridden_write_page_brings_its_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(
                tmp,
                overrides=[
                    {
                        "slug": "measurement",
                        "action": "write",
                        "sectionIds": [],
                    }
                ],
            )

        self.assertEqual(
            [collection.id for collection in confirmation.selections],
            ["frame-1", "frame-2"],
        )

    def test_a_page_cites_only_the_collections_of_its_chosen_sections(self) -> None:
        plan = {
            "intent": INTENT,
            "candidates": [
                {
                    "slug": "entropy",
                    "title": "熵",
                    "coverage": "write",
                    "wikiPath": "wiki/entropy.md",
                    "collectionIds": ["frame-1", "frame-2"],
                    "outline": [
                        {
                            "id": "s1",
                            "title": "定义",
                            "collectionId": "frame-1",
                            "blockIds": ["b1"],
                        },
                        {
                            "id": "s2",
                            "title": "测量",
                            "collectionId": "frame-2",
                            "blockIds": ["b3"],
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(
                tmp,
                plan=plan,
                overrides=[
                    {
                        "slug": "entropy",
                        "action": "write",
                        "sectionIds": ["s2"],
                    }
                ],
            )

        self.assertEqual(
            [collection.id for collection in confirmation.selections],
            ["frame-2"],
        )

    def test_a_write_page_without_a_path_gets_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(tmp, plan=_plan(entropy_path=""))

        self.assertEqual(confirmation.pages[0].wiki_path, "wiki/entropy.md")

    def test_a_plan_that_is_gone_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = ClipStore(
                intent=INTENT, collections=(_collection(),), topic_map=None
            )

            with self.assertRaises(PlanMissing):
                derive_confirmation(
                    layout,
                    run_id="pr-1",
                    intent=INTENT,
                    revision=1,
                    store=store,
                )

    def test_an_override_for_a_page_the_plan_does_not_have_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _confirm(
                    tmp,
                    overrides=[{"slug": "elsewhere", "action": "write"}],
                )

    def test_an_override_naming_no_section_of_the_page_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _confirm(
                    tmp,
                    overrides=[
                        {"slug": "entropy", "action": "write", "sectionIds": ["s9"]}
                    ],
                )

    def test_a_confirmation_with_no_write_page_still_shows_its_pages(self) -> None:
        # The read is a view of the plan; refusing an unwritable plan belongs to
        # the approval, not to looking at it.
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(
                tmp,
                overrides=[
                    {"slug": "entropy", "action": "skip", "sectionIds": []},
                    {"slug": "measurement", "action": "mention", "sectionIds": []},
                ],
            )

        self.assertEqual(
            [(page.slug, page.action) for page in confirmation.pages],
            [("entropy", "skip"), ("measurement", "mention"), ("noise", "skip")],
        )
        self.assertEqual(confirmation.writes, ())

    def test_the_digest_changes_when_a_person_answer_changes(self) -> None:
        store = ClipStore(
            intent=INTENT,
            collections=(_collection(),),
            topic_map=_plan(),
            choices=(),
        )

        self.assertEqual(confirmation_digest(store), confirmation_digest(store))
        answered = replace(
            store, choices=(GateDecision("entropy", "skip", ()),)
        )
        self.assertNotEqual(confirmation_digest(store), confirmation_digest(answered))

    def test_the_outcome_names_the_pages_that_were_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            confirmation = _confirm(tmp)

        outcome = compile_outcome(
            confirmation,
            CompileResult(
                written=("wiki/entropy.md", "wiki/log.md"),
                skipped=(PageProblem("wiki/other.md", "unsafe path"),),
            ),
        )

        self.assertEqual(outcome["planRevision"], 1)
        self.assertEqual(outcome["written"], ["wiki/entropy.md", "wiki/log.md"])
        self.assertEqual(
            outcome["skipped"],
            [
                {"path": "wiki/measurement.md", "reason": "mention"},
                {"path": "wiki/noise.md", "reason": "skip"},
                {"path": "wiki/other.md", "reason": "unsafe path"},
            ],
        )
        self.assertEqual(outcome["failed"], [])

    def test_the_compile_store_has_only_the_write_pages_selections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = ClipStore(
                intent=INTENT,
                collections=(
                    _collection(),
                    _collection("frame-2", "raw/web/measurement.html"),
                ),
                topic_map=_plan(),
            )
            save_clip_store(layout, store)
            confirmation = derive_confirmation(
                layout,
                run_id="pr-1",
                intent=INTENT,
                revision=1,
                store=store,
            )

            handed = store_for_compile(store, confirmation)

        self.assertEqual(
            [collection.id for collection in handed.collections],
            ["frame-1"],
        )

    def test_the_compile_outcome_is_written_skipped_and_failed(self) -> None:
        result = IngestResult(
            source_identity="raw/web/entropy.html",
            analysis="",
            generation="",
            parse=ParseFileBlocksResult(blocks=(), warnings=()),
            write=WriteFileBlocksResult(
                written=(
                    WrittenFile(
                        "wiki/entropy.md",
                        Path("/wiki/entropy.md"),
                        10,
                        "create",
                    ),
                ),
                warnings=(
                    WriteWarning("skipped", "unsafe path", "wiki/bad.md"),
                    WriteWarning(
                        "repair_failed", "repair did not finish", "wiki/broken.md"
                    ),
                    # A soft warning is not a failed page.
                    WriteWarning("merged", "merged", "wiki/entropy.md"),
                ),
            ),
            source_summary_path="wiki/sources/entropy-clips.md",
        )

        converted = compile_result_from(result)

        self.assertEqual(converted.written, ("wiki/entropy.md",))
        self.assertEqual(
            [(item.path, item.reason) for item in converted.skipped],
            [("wiki/bad.md", "unsafe path")],
        )
        self.assertEqual(
            [(item.path, item.reason) for item in converted.failed],
            [("wiki/broken.md", "repair did not finish")],
        )


if __name__ == "__main__":
    unittest.main()
