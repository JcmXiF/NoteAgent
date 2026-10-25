"""Why a round wants a page: figures the interface can say in the reader's words."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from noteagent.ingest.proposal import (
    PageProposal,
    proposal_from_wire,
    proposal_to_wire,
)
from noteagent.llm.client import language_name
from noteagent.prompts.deep_search import build_whitelist_prompt
from noteagent.prompts.topic_map import build_topic_map_prompt
from noteagent.project.init import init_project


class ReasonCodeTests(unittest.TestCase):
    def test_a_reason_travels_with_its_figures(self) -> None:
        proposal = PageProposal(
            slug="concepts/entropy",
            title="熵",
            reason="thin-page",
            reason_args=(("page", "concepts/entropy"), ("chars", "120"), ("floor", "400")),
        )
        raw = proposal_to_wire(proposal)
        self.assertEqual(raw["reason"], "thin-page")
        self.assertEqual(
            raw["reasonArgs"],
            {"page": "concepts/entropy", "chars": "120", "floor": "400"},
        )

        back = proposal_from_wire(raw)
        self.assertEqual(back.reason, "thin-page")
        self.assertEqual(dict(back.reason_args)["chars"], "120")

    def test_a_proposal_with_no_reason_says_nothing(self) -> None:
        raw = proposal_to_wire(PageProposal(slug="a", title="甲"))
        self.assertEqual(raw["reason"], "")
        self.assertEqual(raw["reasonArgs"], {})
        self.assertEqual(proposal_from_wire(raw).reason_args, ())

    def test_an_unreadable_reason_is_dropped_rather_than_trusted(self) -> None:
        # The value is on disk and user-editable, so a shape it cannot read means no
        # reason rather than a crash when the round is read back.
        for args in ("not a dict", {"a": None}, {"": "x"}):
            parsed = proposal_from_wire({"slug": "a", "title": "甲", "reasonArgs": args})
            self.assertEqual(dict(parsed.reason_args), {})

    def test_an_older_round_reads_back_without_one(self) -> None:
        parsed = proposal_from_wire({"slug": "a", "title": "甲", "plan": "图里缺这一页"})
        self.assertEqual(parsed.reason, "")
        self.assertEqual(parsed.plan, "图里缺这一页")


class LanguageNameTests(unittest.TestCase):
    def test_it_names_the_languages_the_interface_offers(self) -> None:
        self.assertEqual(language_name("zh"), "Chinese (简体中文)")
        self.assertEqual(language_name("en"), "English")

    def test_it_accepts_the_shapes_a_browser_reports(self) -> None:
        self.assertEqual(language_name("zh-CN"), "Chinese (简体中文)")
        self.assertEqual(language_name("ZH"), "Chinese (简体中文)")

    def test_it_says_nothing_for_a_language_it_does_not_know(self) -> None:
        # Better silent than wrong: the prompt then leaves the question alone.
        for code in ("", "  ", "xx", "zz-ZZ"):
            self.assertEqual(language_name(code), "")


class PromptLanguageTests(unittest.TestCase):
    def _topic_prompt(self, language: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            doc = SimpleNamespace(
                content="材料",
                project_root=layout.root,
                identity="x",
                path=layout.root / "x",
            )
            prompt = build_topic_map_prompt(
                doc,
                layout,
                intent="整理熵",
                collection_catalog="",
                language=language,
            )
            return prompt.system + "\n" + prompt.user

    def test_the_names_follow_the_material_and_the_explanation_the_reader(self) -> None:
        text = self._topic_prompt("Chinese (简体中文)")
        self.assertIn("outline titles follows the clips", text)
        self.assertIn("The reader is using Chinese (简体中文)", text)
        self.assertIn("Write every evidence sentence in", text)

    def test_it_says_nothing_about_a_language_it_was_not_given(self) -> None:
        text = self._topic_prompt("")
        # The split rule stays; the language line does not.
        self.assertIn("outline titles follows the clips", text)
        self.assertNotIn("The reader is using", text)

    def test_the_round_s_page_list_asks_for_plans_in_that_language(self) -> None:
        prompt = build_whitelist_prompt(
            mode="explore",
            intent="找料",
            seed_line="",
            graph_summary="",
            index_text="",
            remainder_skim="",
            language="English",
        )
        text = prompt.system + "\n" + prompt.user
        self.assertIn("name follows the material", text)
        # Said twice: once as a rule, and once where the field is written, and once
        # more at the end — an instruction about output language is easy to miss.
        self.assertIn("The reader is using English", text)
        self.assertIn('"plan" (English)', text)
        self.assertIn("every plan field must be written in English", text)

    def test_a_round_without_a_language_leaves_the_plans_alone(self) -> None:
        prompt = build_whitelist_prompt(
            mode="explore",
            intent="找料",
            seed_line="",
            graph_summary="",
            index_text="",
            remainder_skim="",
        )
        text = prompt.system + "\n" + prompt.user
        self.assertIn("name follows the material", text)
        self.assertNotIn("The reader is using", text)
        self.assertNotIn('"plan" (', text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
