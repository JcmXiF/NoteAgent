from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.page_rewrite import REWRITE_THRESHOLD, rewrite_existing_pages
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.project.layout import default_layout
from noteagent.prompts.page_rewrite import build_page_rewrite_prompt

PAGE_PATH = "wiki/entropy.md"
OLD_BODY = "熵是不确定性的度量。" * 12
OLD_PAGE = (
    "---\n"
    "title: 熵\n"
    "created: 2026-10-01\n"
    "updated: 2026-10-01\n"
    'sources: ["raw/web/entropy.html#b1"]\n'
    "---\n"
    "\n"
    f"{OLD_BODY}\n\n"
    "见 [[kl-divergence]]。\n"
)
DRAFT = (
    "---\n"
    "title: 熵\n"
    "created: 2026-10-01\n"
    "updated: 2026-10-20\n"
    'sources: ["raw/web/entropy.html#b1", "raw/web/entropy.html#b2"]\n'
    "---\n"
    "\n"
    f"{OLD_BODY}\n\n"
    "联合熵不超过各自熵之和。\n"
)
REWRITTEN = (
    "---\n"
    "title: 熵\n"
    "created: 2026-10-01\n"
    "updated: 2026-10-20\n"
    'sources: ["raw/web/entropy.html#b1", "raw/web/entropy.html#b2"]\n'
    "---\n"
    "\n"
    f"{OLD_BODY}\n\n"
    "联合熵不超过各自熵之和，见 [[kl-divergence]]。\n"
)


class RewriteExistingPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = default_layout(Path(self._tmp.name))
        page = self.layout.root / PAGE_PATH
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(OLD_PAGE, encoding="utf-8")

    def _blocks(self, *paths: str) -> list[FileBlock]:
        return [FileBlock(path=path, content=DRAFT) for path in paths]

    def _rewrite(self, answer: str = REWRITTEN, *, threshold: int = 10):
        calls: list[str] = []

        def rewrite(page_path: str, existing: str, draft: str) -> str:
            calls.append(page_path)
            self.assertIn("title: 熵", existing)
            self.assertEqual(draft, DRAFT)
            return answer

        result = rewrite_existing_pages(
            self.layout, self._blocks(PAGE_PATH), rewrite, threshold=threshold
        )
        return result, calls

    def test_a_long_existing_page_is_written_again(self) -> None:
        result, calls = self._rewrite()
        self.assertEqual(calls, [PAGE_PATH])
        self.assertEqual(result.rewritten, (PAGE_PATH,))
        self.assertEqual(result.blocks[0].content, REWRITTEN)
        self.assertEqual(result.warnings, ())

    def test_a_short_page_is_not_worth_a_call(self) -> None:
        result, calls = self._rewrite(threshold=REWRITE_THRESHOLD)
        self.assertEqual(calls, [])
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, DRAFT)

    def test_a_page_that_does_not_exist_is_left_to_the_write_path(self) -> None:
        result = rewrite_existing_pages(
            self.layout,
            self._blocks("wiki/new-subject.md"),
            lambda *_: self.fail("no call for a page that is not there"),
            threshold=10,
        )
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, DRAFT)

    def test_the_log_and_source_pages_are_records_not_subjects(self) -> None:
        log = self.layout.root / "wiki" / "log.md"
        log.write_text("## [2026-10-01] ingest | 熵\n", encoding="utf-8")
        source = self.layout.root / "wiki" / "sources" / "entropy.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(OLD_PAGE, encoding="utf-8")
        result = rewrite_existing_pages(
            self.layout,
            self._blocks("wiki/log.md", "wiki/sources/entropy.md"),
            lambda *_: self.fail("no call for a record page"),
            threshold=10,
        )
        self.assertEqual(result.rewritten, ())

    def test_a_reply_without_frontmatter_keeps_the_draft(self) -> None:
        result, _ = self._rewrite("只是正文。")
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, DRAFT)
        self.assertIn("no frontmatter", result.warnings[0])

    def test_a_reply_that_drops_a_link_keeps_the_draft(self) -> None:
        answer = REWRITTEN.replace("，见 [[kl-divergence]]", "")
        result, _ = self._rewrite(answer)
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, DRAFT)
        self.assertIn("kl-divergence", result.warnings[0])

    def test_a_reply_that_is_far_shorter_keeps_the_draft(self) -> None:
        answer = (
            "---\ntitle: 熵\ncreated: 2026-10-01\nupdated: 2026-10-20\n"
            'sources: ["raw/web/entropy.html#b1"]\n---\n\n熵。\n'
        )
        result, _ = self._rewrite(answer)
        self.assertEqual(result.rewritten, ())
        self.assertIn("floor", result.warnings[0])

    def test_a_reply_that_drops_a_link_the_draft_brought_keeps_the_draft(
        self,
    ) -> None:
        draft = DRAFT + "\n另见 [[joint-entropy]]。\n"
        answer = REWRITTEN
        result = rewrite_existing_pages(
            self.layout,
            [FileBlock(path=PAGE_PATH, content=draft)],
            lambda *_: answer,
            threshold=10,
        )
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, draft)
        self.assertIn("joint-entropy", result.warnings[0])

    def test_a_reply_that_drops_a_marker_the_draft_brought_keeps_the_draft(
        self,
    ) -> None:
        draft = (
            DRAFT
            + "\n<!-- na-clip:two -->\n## 联合熵\n\nH(X,Y) 不超过 H(X)+H(Y)。\n"
        )
        result = rewrite_existing_pages(
            self.layout,
            [FileBlock(path=PAGE_PATH, content=draft)],
            lambda *_: REWRITTEN,
            threshold=10,
        )
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, draft)
        self.assertIn("na-clip:two", result.warnings[0])

    def test_a_reply_may_carry_the_marker_and_link_the_draft_brought(self) -> None:
        # Held to the page alone, this marker would look invented. It is the
        # marker the new source earned, so it has to be accepted.
        draft = (
            DRAFT
            + "\n<!-- na-clip:two -->\n## 联合熵\n\nH(X,Y) 不超过 H(X)+H(Y)。\n"
            "\n另见 [[joint-entropy]]。\n"
        )
        answer = (
            REWRITTEN
            + "\n<!-- na-clip:two -->\n## 联合熵\n\nH(X,Y) 不超过 H(X)+H(Y)。\n"
            "\n另见 [[joint-entropy]]。\n"
        )
        result = rewrite_existing_pages(
            self.layout,
            [FileBlock(path=PAGE_PATH, content=draft)],
            lambda *_: answer,
            threshold=10,
        )
        self.assertEqual(result.rewritten, (PAGE_PATH,))
        self.assertEqual(result.drafts, {PAGE_PATH: draft})
        self.assertEqual(result.blocks[0].content, answer)

    def test_a_failed_call_keeps_the_draft(self) -> None:
        def rewrite(*_: str) -> str:
            raise RuntimeError("upstream said no")

        result = rewrite_existing_pages(
            self.layout, self._blocks(PAGE_PATH), rewrite, threshold=10
        )
        self.assertEqual(result.rewritten, ())
        self.assertEqual(result.blocks[0].content, DRAFT)
        self.assertIn("upstream said no", result.warnings[0])

    def test_an_empty_reply_keeps_the_draft(self) -> None:
        result, _ = self._rewrite("   \n")
        self.assertEqual(result.rewritten, ())
        self.assertIn("empty", result.warnings[0])

    def test_each_page_is_announced_before_it_is_written(self) -> None:
        seen: list[str] = []
        rewrite_existing_pages(
            self.layout,
            self._blocks(PAGE_PATH),
            lambda *_: REWRITTEN,
            threshold=10,
            on_rewrite=seen.append,
        )
        self.assertEqual(seen, [PAGE_PATH])

    def test_the_threshold_is_a_constant_with_a_reason(self) -> None:
        self.assertEqual(REWRITE_THRESHOLD, 4_000)
        self.assertGreater(len(OLD_PAGE), 100)


class PageRewritePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = default_layout(Path(self._tmp.name))
        (self.layout.root / "config").mkdir(parents=True, exist_ok=True)
        self.layout.brief.write_text("Keep the source's notation.", encoding="utf-8")
        self.layout.wiki_index.parent.mkdir(parents=True, exist_ok=True)
        self.layout.wiki_index.write_text(
            "- [[kl-divergence]] — 相对熵\n", encoding="utf-8"
        )

    def _prompt(self):
        return build_page_rewrite_prompt(
            page_path=PAGE_PATH,
            existing_content=OLD_PAGE,
            draft_content=DRAFT,
            analysis="## Subjects worth a page\n- 熵\n",
            layout=self.layout,
        )

    def test_the_page_and_the_draft_are_both_carried(self) -> None:
        prompt = self._prompt()
        self.assertIn("## The page as it stands", prompt.user)
        self.assertIn("## The draft this source produced", prompt.user)
        self.assertIn("不确定性的度量", prompt.user)
        self.assertIn("联合熵不超过", prompt.user)
        self.assertIn(PAGE_PATH, prompt.user)

    def test_the_draft_is_named_as_one_source_s_account(self) -> None:
        prompt = self._prompt()
        self.assertIn("one source's account", prompt.system)
        self.assertIn("not a second page to append", prompt.system)

    def test_the_brief_and_the_index_are_carried(self) -> None:
        prompt = self._prompt()
        self.assertIn("Keep the source's notation.", prompt.system)
        self.assertIn("[[kl-divergence]]", prompt.system)

    def test_the_reply_is_the_page_without_a_file_wrapper(self) -> None:
        prompt = self._prompt()
        self.assertIn("`---FILE:` wrapper, no preamble", prompt.system)
        self.assertIn("Your reply starts with `---`", prompt.user)

    def test_the_program_keeps_title_and_created(self) -> None:
        prompt = self._prompt()
        self.assertIn("keeps its `title` and", prompt.system)
        self.assertIn("`created`", prompt.system)


if __name__ == "__main__":
    unittest.main()
