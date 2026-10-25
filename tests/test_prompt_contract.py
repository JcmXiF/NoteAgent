"""What every prompt must carry, and what no prompt may say any more.

Prompt text has no type checker. A heading left over from the old taxonomy, a
folder name that no longer exists, a sentence that describes the pipeline to the
model: each one still produces a well-formed request, and only a live round would
show it. So the built prompts are held two ways here — a contract test for the
things they must and must not contain, and a snapshot for everything else, so a
wording change has to be seen before it lands.

Regenerate the snapshots after an intended change:

    NOTEAGENT_UPDATE_PROMPTS=1 python -m unittest tests.test_prompt_contract
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from noteagent.ingest.topic_map import PlanTarget
from noteagent.io.read_source import SourceDocument
from noteagent.project.init import init_project
from noteagent.prompts.analysis import build_analysis_prompt
from noteagent.prompts.deep_search import build_blocks_prompt, build_whitelist_prompt
from noteagent.prompts.generation import build_generation_prompt
from noteagent.prompts.merge import build_page_merge_prompt
from noteagent.prompts.page_agents import (
    build_blueprint_prompt,
    build_compose_prompt,
    build_draft_prompt,
)
from noteagent.prompts.topic_map import build_topic_map_prompt

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "prompts"
UPDATE = os.environ.get("NOTEAGENT_UPDATE_PROMPTS") == "1"

BRIEF = """\
# House style

- Short paragraphs. A claim names what carries it.
- Keep the material's notation.
"""

INDEX = """\
# Wiki Index

## Sources

- [[sources/entropy-clips]] — Entropy clips

## Notes

- [[entropy]] — Entropy
"""

TODAY = date(2026, 10, 20)
IDENTITY = "raw/web/intro-1a2b3c.html"
CATALOG = "- Entropy defined (`c1`): raw/web/intro-1a2b3c.html (b1, b4)"
CLIPS = "### Entropy defined\n\nEntropy measures uncertainty. [b1]\n"
LOCATORS = [f"{IDENTITY}#b1", f"{IDENTITY}#b4"]

#: Sentences that belonged to the retired six-type layout or to the old
#: navigation file. They must not survive in any prompt.
RETIRED = (
    "Stage 1",
    "aggregate navigation",
    "wiki/concepts/",
    "wiki/entities/",
    "wiki/overview.md",
    "wiki/queries/",
    "## Concepts",
    "## Entities",
    "## Queries",
    "## Comparisons",
    "## Synthesis",
    "Schema",
    "Page type",
)

#: Sentences the reference project's merge prompt carried word for word, kept
#: here so a later edit cannot copy them back. The merge prompt is written from
#: what its answer has to satisfy; none of the reference's instruction list is.
COPIED_MERGE_SENTENCES = (
    "- Preserve every factual claim from both descriptions (do not drop content)",
    "- Eliminate redundancy when both descriptions state the same fact",
    "- When in doubt whether two similar-looking claims describe the same fact, "
    "prefer keeping them separate",
    "not just a concatenation of the two inputs",
    "- Use consistent markdown structure (headings, tables, lists, callouts)",
    "- The FIRST character of your response MUST be `-` (the opening of `---`)",
    "- Output the COMPLETE file: YAML frontmatter + body",
)


def _format(prompt: object) -> str:
    system = getattr(prompt, "system")
    user = getattr(prompt, "user")
    return f"=== system ===\n{system}\n=== user ===\n{user}\n"


class _Prompts(unittest.TestCase):
    """One project, one source, every prompt the core builds from them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.layout = init_project(Path(cls._tmp.name) / "proj")
        cls.layout.config.mkdir()
        cls.layout.brief.write_text(BRIEF, encoding="utf-8")
        cls.layout.wiki_index.write_text(INDEX, encoding="utf-8")
        cls.doc = SourceDocument(
            project_root=cls.layout.root,
            identity=IDENTITY,
            path=cls.layout.raw / "web" / "intro-1a2b3c.html",
            content=(
                "<html><body>"
                "<p id='na-b1'>Entropy measures uncertainty.</p>"
                "<p id='na-b4'>It is additive for independent systems.</p>"
                "</body></html>\n"
            ),
        )

    def analysis(self):
        return build_analysis_prompt(self.doc, self.layout)

    def generation(self, analysis: str = "## Subjects worth a page\n- Entropy\n"):
        return build_generation_prompt(self.doc, self.layout, analysis, today=TODAY)

    def topic_map(self, targets: tuple[PlanTarget, ...] = ()):
        return build_topic_map_prompt(
            self.doc,
            self.layout,
            intent="Entropy",
            collection_catalog=CATALOG,
            targets=targets,
        )

    def blueprint(self):
        return build_blueprint_prompt(
            intent="Entropy",
            page_name="Entropy",
            wiki_path="wiki/entropy.md",
            section_plan="- Definition",
            mention_names=["Shannon"],
            skip_names=[],
            collection_catalog=CATALOG,
            clips_text=CLIPS,
            brief=BRIEF,
        )

    def draft(self):
        return build_draft_prompt(
            page_name="Entropy",
            headline="Entropy defined",
            role="definition",
            section_title="Definition",
            instruction="Keep the uncertainty framing.",
            collection_text=CLIPS,
            collection_id="c1",
            brief=BRIEF,
        )

    def compose(self):
        return build_compose_prompt(
            intent="Entropy",
            page_name="Entropy",
            wiki_path="wiki/entropy.md",
            wiki_date=TODAY.isoformat(),
            mention_names=["Shannon"],
            locators=LOCATORS,
            blueprint_text='{"contributions": []}',
            drafts_text="### Definition\n\nEntropy measures uncertainty.",
            brief=BRIEF,
        )

    def merge(self):
        return build_page_merge_prompt(
            "---\ntitle: Entropy\n---\n\nOld body.\n",
            "---\ntitle: Entropy\n---\n\nNew body.\n",
            IDENTITY,
        )

    def whitelist(self):
        return build_whitelist_prompt(
            mode="chew",
            intent="Entropy",
            seed_line="wiki/entropy.md",
            graph_summary="- entropy -> kl-divergence (prerequisite)",
            index_text=INDEX,
            remainder_skim="b7: entropy of a joint distribution",
        )

    def blocks(self):
        return build_blocks_prompt(
            intent="Entropy",
            wiki_titles=INDEX,
            remainder_previews=f"- {IDENTITY}#b7: joint entropy",
        )


class PromptContractTests(_Prompts):
    def test_every_prompt_carries_the_brief(self) -> None:
        expected = "Keep the material's notation."
        for name, prompt in {
            "analysis": self.analysis(),
            "generation": self.generation(),
            "topic-map": self.topic_map(),
            "blueprint": self.blueprint(),
            "draft": self.draft(),
            "compose": self.compose(),
        }.items():
            with self.subTest(name):
                self.assertIn(expected, prompt.system)

    def test_no_prompt_keeps_the_retired_vocabulary(self) -> None:
        for name, prompt in {
            "analysis": self.analysis(),
            "generation": self.generation(),
            "topic-map": self.topic_map(),
            "blueprint": self.blueprint(),
            "draft": self.draft(),
            "compose": self.compose(),
            "merge": self.merge(),
            "whitelist": self.whitelist(),
            "blocks": self.blocks(),
        }.items():
            text = prompt.system + "\n" + prompt.user
            for phrase in RETIRED:
                with self.subTest(name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_no_prompt_copies_the_reference_merge_prompt(self) -> None:
        for name, prompt in {
            "analysis": self.analysis(),
            "generation": self.generation(),
            "topic-map": self.topic_map(),
            "blueprint": self.blueprint(),
            "draft": self.draft(),
            "compose": self.compose(),
            "merge": self.merge(),
            "whitelist": self.whitelist(),
            "blocks": self.blocks(),
        }.items():
            text = prompt.system + "\n" + prompt.user
            for sentence in COPIED_MERGE_SENTENCES:
                with self.subTest(name, sentence=sentence[:40]):
                    self.assertNotIn(sentence, text)

    def test_generation_names_the_source_page_and_a_flat_locator(self) -> None:
        prompt = self.generation()
        self.assertIn("wiki/sources/intro-1a2b3c.md", prompt.system)
        self.assertEqual(prompt.source_summary_path, "wiki/sources/intro-1a2b3c.md")
        # The example has to be one this layout writes: raw/<kind>/..., with a
        # block id, and a page at wiki/<slug>.md.
        self.assertIn(f"{IDENTITY}#b4", prompt.system)
        self.assertIn("---FILE: wiki/<path>.md---", prompt.system)
        self.assertIn("wiki/<slug>.md", prompt.system)

    def test_analysis_does_not_route_subjects_into_page_types(self) -> None:
        prompt = self.analysis()
        for heading in (
            "## Key Entities",
            "## Key Concepts",
            "## Main Arguments & Findings",
        ):
            self.assertNotIn(heading, prompt.system)
        # The reading still asks for the subjects themselves, without a type
        # decision the code has already made.
        self.assertIn("## Subjects worth a page", prompt.system)

    def test_the_index_is_the_program_s_to_write(self) -> None:
        prompt = self.generation()
        self.assertIn("The application writes `wiki/index.md`", prompt.system)

    def test_a_whole_file_source_is_cited_as_its_identity(self) -> None:
        prompt = self.analysis()
        self.assertIn(IDENTITY, prompt.user)

    def test_merge_carries_the_page_s_own_passages_when_they_resolve(self) -> None:
        evidence = (
            "## The passages the page was built from\n\n"
            f"### `{IDENTITY}#b4`\n\n熵是平均信息量。\n"
        )
        prompt = build_page_merge_prompt(
            "---\ntitle: Entropy\n---\n\nOld body.\n",
            "---\ntitle: Entropy\n---\n\nNew body.\n",
            IDENTITY,
            evidence=evidence,
        )
        self.assertIn("## The passages the page was built from", prompt.user)
        self.assertIn(f"`{IDENTITY}#b4`", prompt.user)
        # The order is the argument: the page, then what it was built from, then
        # the material this write brought.
        self.assertLess(prompt.user.index("Old body."), prompt.user.index("#b4"))
        self.assertLess(prompt.user.index("#b4"), prompt.user.index("New body."))

    def test_merge_without_evidence_is_the_two_text_prompt(self) -> None:
        prompt = self.merge()
        self.assertNotIn("## The passages the page was built from", prompt.user)
        self.assertNotIn("passages the page says it was built from", prompt.user)



class PromptSnapshotTests(_Prompts):
    def assert_snapshot(self, name: str, prompt: object) -> None:
        text = _format(prompt)
        path = SNAPSHOT_DIR / f"{name}.txt"
        if UPDATE:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            return
        if not path.is_file():
            self.fail(
                f"missing prompt snapshot {path}; run with "
                "NOTEAGENT_UPDATE_PROMPTS=1"
            )
        self.maxDiff = None
        self.assertEqual(text, path.read_text(encoding="utf-8"), name)

    def test_analysis(self) -> None:
        self.assert_snapshot("analysis", self.analysis())

    def test_generation(self) -> None:
        self.assert_snapshot("generation", self.generation())

    def test_topic_map(self) -> None:
        self.assert_snapshot("topic-map", self.topic_map())

    def test_topic_map_with_targets(self) -> None:
        self.assert_snapshot(
            "topic-map-targets",
            self.topic_map(
                (PlanTarget(slug="entropy", title="Entropy", action="revise"),)
            ),
        )

    def test_blueprint(self) -> None:
        self.assert_snapshot("blueprint", self.blueprint())

    def test_draft(self) -> None:
        self.assert_snapshot("draft", self.draft())

    def test_compose(self) -> None:
        self.assert_snapshot("compose", self.compose())

    def test_merge(self) -> None:
        self.assert_snapshot("merge", self.merge())

    def test_deep_search_whitelist(self) -> None:
        self.assert_snapshot("deep-search-whitelist", self.whitelist())

    def test_deep_search_blocks(self) -> None:
        self.assert_snapshot("deep-search-blocks", self.blocks())


if __name__ == "__main__":
    unittest.main()
