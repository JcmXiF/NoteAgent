from __future__ import annotations

import unittest

from noteagent.ingest.page_merge import (
    RULES,
    annotation_key,
    is_owned_only_by_material,
    merge_page_content,
    merged_frontmatter,
    structure_breaks,
)
from noteagent.prompts.merge import (
    build_page_merge_system_prompt,
    build_page_merge_user_message,
)


def _page(
    *,
    title: str = "Demo",
    created: str = "2026-01-01",
    updated: str = "2026-01-01",
    tags: str = "[a]",
    related: str = "[x]",
    sources: str = '["raw/one.md"]',
    body: str = "# Old Body\n",
    typ: str = "concept",
) -> str:
    return (
        "---\n"
        f"type: {typ}\n"
        f"title: {title}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"tags: {tags}\n"
        f"related: {related}\n"
        f"sources: {sources}\n"
        "---\n"
        f"\n{body}"
    )


class PageMergeTests(unittest.TestCase):
    def test_create_when_no_existing(self) -> None:
        incoming = _page(body="# New\n")
        result = merge_page_content(
            incoming, None, None, source_identity="raw/one.md"
        )
        self.assertEqual(result.mode, "create")
        self.assertEqual(result.content, incoming)

    def test_unchanged_when_byte_identical(self) -> None:
        page = _page()
        result = merge_page_content(
            page, page, lambda *_: "should-not-run", source_identity="raw/one.md"
        )
        self.assertEqual(result.mode, "unchanged")
        self.assertIs(result.content, page)

    def test_union_frontmatter_keeps_incoming_body(self) -> None:
        existing = _page(body="# Old Body\n")
        incoming = _page(
            title="New Title",
            created="2026-07-21",
            updated="2026-07-21",
            tags="[b]",
            related="[y]",
            sources='["raw/two.md"]',
            body="# New Body\n",
        )
        merged = merged_frontmatter(existing, incoming)
        self.assertIn("title: Demo", merged)
        self.assertIn("created: 2026-01-01", merged)
        self.assertIn("updated: 2026-07-21", merged)
        # Retired fields are carried as the incoming page wrote them.
        self.assertIn("tags: [b]", merged)
        self.assertIn("related: [y]", merged)
        self.assertIn("raw/one.md", merged)
        self.assertIn("raw/two.md", merged)
        self.assertIn("# New Body", merged)
        self.assertNotIn("# Old Body", merged)

    def test_type_is_no_longer_locked(self) -> None:
        existing = _page(typ="concept", body="# Old\n")
        incoming = _page(typ="note", body="# New\n")
        merged = merged_frontmatter(existing, incoming)
        self.assertIn("type: note", merged)
        self.assertNotIn("type: concept", merged)

    def test_replace_when_owned_only_by_same_source(self) -> None:
        existing = _page(body="# Obsolete wording\n" + ("x" * 80) + "\n")
        incoming = _page(
            title="Changed",
            created="2026-08-01",
            updated="2026-08-10",
            tags="[b]",
            sources='["raw/one.md"]',
            body="# Corrected wording\n",
        )
        calls: list[str] = []

        def merger(*_args: str) -> str:
            calls.append("called")
            return "---\ntype: concept\n---\n\n# merged\n"

        result = merge_page_content(
            incoming,
            existing,
            merger,
            source_identity="raw/one.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "replace")
        self.assertEqual(calls, [])
        self.assertIn("# Corrected wording", result.content)
        self.assertNotIn("Obsolete", result.content)
        self.assertIn("title: Demo", result.content)
        self.assertIn("created: 2026-01-01", result.content)
        self.assertIn("updated: 2026-08-10", result.content)
        self.assertIn("tags: [b]", result.content)

    def test_multi_source_calls_merger(self) -> None:
        existing = _page(
            sources='["raw/one.md"]',
            body="# From one\n\nDetail A.\n",
        )
        incoming = _page(
            title="Renamed",
            sources='["raw/two.md"]',
            body="# From two\n\nDetail B.\n",
            updated="2026-08-10",
        )
        merged_body = (
            "# Combined\n\nDetail A.\n\nDetail B.\n\n"
            + ("kept " * 40)
        )
        llm_page = _page(
            title="LLM Title",
            created="2099-01-01",
            sources='["raw/two.md"]',
            body=merged_body,
        )

        def merger(existing_content: str, incoming_content: str, identity: str) -> str:
            self.assertIn("From one", existing_content)
            self.assertIn("From two", incoming_content)
            self.assertEqual(identity, "raw/two.md")
            return llm_page

        result = merge_page_content(
            incoming,
            existing,
            merger,
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge")
        self.assertIn("Detail A", result.content)
        self.assertIn("Detail B", result.content)
        self.assertIn("title: Demo", result.content)
        self.assertIn("created: 2026-01-01", result.content)
        self.assertIn("raw/one.md", result.content)
        self.assertIn("raw/two.md", result.content)
        self.assertIn("updated: 2026-08-10", result.content)
        self.assertNotIn("LLM Title", result.content)

    def test_llm_cannot_drop_chinese_path_sources(self) -> None:
        src6 = "raw/6. 正交向量 Orthogonal vectors.md"
        src7 = "raw/7. 子空间投影 Projections onto subspaces.md"
        src8 = "raw/8. 最小二乘法 Least squares.md"
        existing = _page(
            sources=f'["{src6}", "{src7}"]',
            body="# Old LS\n\n" + ("a" * 80) + "\n",
        )
        incoming = _page(
            sources=f'["{src8}"]',
            body="# New LS\n\n" + ("b" * 80) + "\n",
        )
        llm_only_latest = _page(
            sources=f'["{src8}"]',
            body="# Merged LS\n\n" + ("c" * 120) + "\n",
        )
        result = merge_page_content(
            incoming,
            existing,
            lambda *_: llm_only_latest,
            source_identity=src8,
            today=lambda: "2026-08-12",
        )
        self.assertEqual(result.mode, "merge")
        self.assertIn(src6, result.content)
        self.assertIn(src7, result.content)
        self.assertIn(src8, result.content)
        self.assertIn("Merged LS", result.content)

    def test_union_keeps_existing_fm_when_incoming_lacks_fence(self) -> None:
        existing = _page(
            sources='["raw/one.md"]',
            body="# Old\n",
        )
        incoming = (
            "type: concept\n"
            "title: Broken\n"
            "sources: [\"raw/two.md\"]\n"
            "---\n\n# Incoming body\n"
        )
        merged = merged_frontmatter(existing, incoming)
        self.assertIn("raw/one.md", merged)
        self.assertIn("raw/two.md", merged)
        self.assertIn("# Incoming body", merged)
        self.assertTrue(merged.startswith("---\n"))

    def test_shrink_keeps_old_body(self) -> None:
        existing = _page(body="# Old\n\n" + ("old " * 100) + "\n")
        incoming = _page(
            sources='["raw/two.md"]',
            body="# New\n\n" + ("new " * 100) + "\n",
        )
        tiny = _page(
            sources='["raw/two.md"]',
            body="# tiny\n",
        )
        result = merge_page_content(
            incoming,
            existing,
            lambda *_: tiny,
            source_identity="raw/two.md",
            page_path="wiki/concepts/demo.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIsNotNone(result.warning)
        self.assertIn("Old", result.content)
        self.assertIn("old ", result.content)
        self.assertNotIn("tiny", result.content)
        # The prose was not absorbed, so the page must not claim the new locator.
        self.assertIn("raw/one.md", result.content)
        self.assertNotIn("raw/two.md", result.content)
        self.assertIn("updated: 2026-01-01", result.content)

    def test_no_frontmatter_keeps_old_body(self) -> None:
        existing = _page(body="# Keep me\n" + ("k" * 50) + "\n")
        incoming = _page(
            sources='["raw/two.md"]',
            body="# Incoming\n" + ("n" * 50) + "\n",
        )
        result = merge_page_content(
            incoming,
            existing,
            lambda *_: "not a wiki page",
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("Keep me", result.content)

    def test_merger_exception_keeps_old_body(self) -> None:
        existing = _page(body="# Keep\n" + ("k" * 50) + "\n")
        incoming = _page(
            sources='["raw/two.md"]',
            body="# New\n" + ("n" * 50) + "\n",
        )

        def boom(*_args: str) -> str:
            raise RuntimeError("network down")

        result = merge_page_content(
            incoming,
            existing,
            boom,
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("network down", result.warning or "")
        self.assertIn("Keep", result.content)

    def test_missing_merger_keeps_old_body_and_old_sources(self) -> None:
        existing = _page(body="# Keep\n" + ("k" * 50) + "\n")
        incoming = _page(
            sources='["raw/two.md"]',
            tags="[b]",
            related="[y]",
            body="# New\n" + ("n" * 50) + "\n",
        )
        result = merge_page_content(
            incoming,
            existing,
            None,
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("Keep", result.content)
        self.assertNotIn("raw/two.md", result.content)
        self.assertIn("raw/one.md", result.content)
        # A rejected body leaves every field as it was on disk.
        self.assertIn("tags: [a]", result.content)
        self.assertIn("related: [x]", result.content)
        self.assertIn("updated: 2026-01-01", result.content)

    def test_same_body_skips_merger(self) -> None:
        body = "# Same body\n"
        existing = _page(sources='["raw/one.md"]', body=body)
        incoming = _page(
            sources='["raw/two.md"]',
            tags="[b]",
            body=body,
            updated="2026-08-10",
        )
        calls: list[int] = []

        def merger(*_args: str) -> str:
            calls.append(1)
            return incoming

        result = merge_page_content(
            incoming,
            existing,
            merger,
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge")
        self.assertEqual(calls, [])
        self.assertIn("tags: [b]", result.content)
        self.assertIn("raw/two.md", result.content)

    def test_is_owned_only_by_material(self) -> None:
        page = _page(sources='["raw/one.md"]')
        self.assertTrue(is_owned_only_by_material(page, {"raw/one.md"}))
        self.assertTrue(is_owned_only_by_material(page, {r"raw\one.md"}))
        self.assertFalse(is_owned_only_by_material(page, {"raw/two.md"}))
        multi = _page(sources='["raw/one.md", "raw/two.md"]')
        self.assertFalse(is_owned_only_by_material(multi, {"raw/one.md"}))
        self.assertTrue(
            is_owned_only_by_material(
                multi, {"raw/one.md", "raw/two.md"}
            )
        )

    def test_locator_page_is_owned_only_when_every_block_is_carried(self) -> None:
        page = _page(
            sources='["raw/x.html#b1", "raw/x.html#b2"]'
        )
        self.assertTrue(
            is_owned_only_by_material(
                page,
                {
                    "raw/x.html#b1",
                    "raw/x.html#b2",
                    "raw/x.html#b3",
                },
            )
        )
        self.assertFalse(
            is_owned_only_by_material(
                page, {"raw/x.html#b1", "raw/x.html#b3"}
            )
        )

    def test_a_whole_file_does_not_own_a_locator_it_never_named(self) -> None:
        page = _page(sources='["raw/x.html#b1"]')
        self.assertFalse(
            is_owned_only_by_material(page, {"raw/x.html"})
        )

    def test_page_without_sources_is_not_owned(self) -> None:
        page = _page(sources="[]")
        self.assertFalse(
            is_owned_only_by_material(page, {"raw/one.md"})
        )

    def test_shrink_floor_is_part_of_the_rules(self) -> None:
        self.assertEqual(RULES.smallest_body, 0.7)
        self.assertEqual(RULES.kept, ("title", "created"))
        self.assertEqual(RULES.unioned, ("sources",))

    def test_merge_prompt_requires_frontmatter_opener(self) -> None:
        text = build_page_merge_system_prompt()
        self.assertIn("opens with frontmatter", text)
        self.assertIn("keep the substance of both texts", text)


class MergeStructureGuardTests(unittest.TestCase):
    """A merge may not lose the page's markers or its wikilink targets."""

    MARKED_BODY = (
        "<!-- na-clip:coll-a -->\n"
        "## Definition\n\n"
        "See [[Related Page|related]] for the other half.\n\n" + ("paragraph text. " * 10) + "\n"
    )

    def _merge(self, existing_body: str, incoming_body: str, merged_body: str):
        existing = _page(sources='["raw/one.md"]', body=existing_body)
        incoming = _page(sources='["raw/two.md"]', body=incoming_body)
        llm_page = _page(sources='["raw/two.md"]', body=merged_body)
        return merge_page_content(
            incoming,
            existing,
            lambda *_: llm_page,
            source_identity="raw/two.md",
            page_path="wiki/concepts/demo.md",
            today=lambda: "2026-08-10",
        )

    def test_kept_marker_and_link_merge_normally(self) -> None:
        result = self._merge(
            self.MARKED_BODY,
            "# From two\n\nMore material.\n",
            self.MARKED_BODY + "Merged closing line.\n",
        )
        self.assertEqual(result.mode, "merge")
        self.assertIn("<!-- na-clip:coll-a -->", result.content)
        self.assertIn("[[Related Page|related]]", result.content)

    def test_dropped_marker_falls_back(self) -> None:
        result = self._merge(
            self.MARKED_BODY,
            "# From two\n\nMore material.\n",
            self.MARKED_BODY.replace("<!-- na-clip:coll-a -->\n", ""),
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("dropped marker na-clip:coll-a", result.warning or "")
        self.assertIn("<!-- na-clip:coll-a -->", result.content)

    def test_invented_marker_falls_back(self) -> None:
        result = self._merge(
            self.MARKED_BODY,
            "# From two\n\nMore material.\n",
            self.MARKED_BODY + "<!-- na-clip:coll-z -->\n## Invented\n\nText.\n",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("invented marker na-clip:coll-z", result.warning or "")
        self.assertNotIn("coll-z", result.content)

    def test_duplicate_marker_collapsing_to_one_is_allowed(self) -> None:
        result = self._merge(
            self.MARKED_BODY,
            self.MARKED_BODY,
            self.MARKED_BODY,
        )
        self.assertEqual(result.mode, "merge")

    def test_marker_detached_from_its_heading_falls_back(self) -> None:
        detached = self.MARKED_BODY.replace(
            "<!-- na-clip:coll-a -->\n## Definition",
            "<!-- na-clip:coll-a -->\nIntro paragraph.",
        )
        result = self._merge(
            self.MARKED_BODY,
            "# From two\n\nMore material.\n",
            detached,
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("no longer sit above a heading", result.warning or "")

    def test_marker_from_the_incoming_side_is_kept(self) -> None:
        incoming_body = "<!-- na-ecg -->\n## Related\n\n- [[Other Page]]\n"
        result = self._merge(
            "# Only on disk\n\n" + ("disk text. " * 10) + "\n",
            incoming_body,
            "# Merged\n\n" + ("disk text. " * 10) + "\n" + incoming_body,
        )
        self.assertEqual(result.mode, "merge")

    def test_dropped_marker_from_the_incoming_side_falls_back(self) -> None:
        result = self._merge(
            "# Only on disk\n\n" + ("disk text. " * 10) + "\n",
            "<!-- na-ecg -->\n## Related\n\n- [[Other Page]]\n",
            "# Merged\n\n" + ("disk text. " * 10) + "\n",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("dropped marker na-ecg", result.warning or "")

    def test_dropped_wikilink_target_falls_back(self) -> None:
        result = self._merge(
            self.MARKED_BODY,
            "# From two\n\nMore material.\n",
            self.MARKED_BODY.replace("[[Related Page|related]]", "a related note"),
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("dropped wikilink target(s) Related Page", result.warning or "")

    def test_annotation_key_reads_the_shared_patterns(self) -> None:
        self.assertEqual(annotation_key("<!-- na-clip:abc -->"), "na-clip:abc")
        self.assertEqual(annotation_key("  <!-- na-clip:abc b4, B3 -->  "), "na-clip:abc")
        self.assertEqual(annotation_key("<!-- na-ecg -->"), "na-ecg")
        self.assertIsNone(annotation_key("text <!-- na-clip:abc -->"))
        self.assertIsNone(annotation_key("## Heading"))

    def test_structure_breaks_is_empty_for_a_faithful_merge(self) -> None:
        self.assertEqual(
            structure_breaks(self.MARKED_BODY, self.MARKED_BODY, ""),
            [],
        )


class ReplaceOwnershipTests(unittest.TestCase):
    """A page is rewritten only while it still owns everything it claims."""

    def _replace(self, existing: str, incoming: str, material: set[str], merger=None):
        return merge_page_content(
            incoming,
            existing,
            merger,
            source_identity="raw/x.html",
            material=material,
            page_path="wiki/concepts/demo.md",
            today=lambda: "2026-08-10",
        )

    def test_rewrite_when_this_round_carried_every_claimed_block(self) -> None:
        existing = _page(
            sources='["raw/x.html#b1"]', body="# Old\n"
        )
        incoming = _page(
            sources='["raw/x.html#b1", "raw/x.html#b2"]',
            body="# New\n",
        )
        result = self._replace(
            existing,
            incoming,
            {"raw/x.html#b1", "raw/x.html#b2"},
        )
        self.assertEqual(result.mode, "replace")
        self.assertIn("# New", result.content)
        self.assertIn("raw/x.html#b1", result.content)
        self.assertIn("raw/x.html#b2", result.content)

    def test_partial_material_goes_through_the_merger(self) -> None:
        existing = _page(
            sources='["raw/x.html#b1"]', body="# Old\n"
        )
        incoming = _page(
            sources='["raw/x.html#b3"]', body="# New\n"
        )
        calls: list[int] = []

        def merger(*_args: str) -> str:
            calls.append(1)
            return incoming

        result = self._replace(
            existing, incoming, {"raw/x.html#b3"}, merger
        )
        self.assertEqual(result.mode, "merge")
        self.assertEqual(calls, [1])

    def test_rewrite_that_drops_a_marker_falls_back(self) -> None:
        existing = _page(
            sources='["raw/x.html#b1"]',
            body="<!-- na-clip:coll-a -->\n## Definition\n\nOld text.\n",
        )
        incoming = _page(
            sources='["raw/x.html#b1"]',
            body="## Definition\n\nNew text.\n",
        )
        result = self._replace(
            existing, incoming, {"raw/x.html#b1"}
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("dropped marker na-clip:coll-a", result.warning or "")
        self.assertIn("<!-- na-clip:coll-a -->", result.content)
        self.assertIn("Old text.", result.content)

    def test_rewrite_may_repoint_its_wikilinks(self) -> None:
        existing = _page(
            sources='["raw/x.html#b1"]', body="See [[Old Target]].\n"
        )
        incoming = _page(
            sources='["raw/x.html#b1"]', body="See [[New Target]].\n"
        )
        result = self._replace(
            existing, incoming, {"raw/x.html#b1"}
        )
        self.assertEqual(result.mode, "replace")
        self.assertIn("[[New Target]]", result.content)
        self.assertNotIn("[[Old Target]]", result.content)

    def test_empty_material_never_rewrites(self) -> None:
        existing = _page(
            sources='["raw/x.html#b1"]', body="# Old\n"
        )
        incoming = _page(
            sources='["raw/x.html#b1"]', body="# New\n"
        )
        result = self._replace(existing, incoming, set())
        self.assertEqual(result.mode, "merge-fallback")


class MergeRuleOrderTests(unittest.TestCase):
    """R1-R7 are applied in order: the first rule that decides, decides."""

    def test_r2_same_text_beats_r4_ownership(self) -> None:
        page = _page(sources='["raw/one.md"]', body="# Same\n")
        calls: list[int] = []

        def merger(*_args: str) -> str:  # pragma: no cover - must not run
            calls.append(1)
            return page

        result = merge_page_content(
            page,
            page,
            merger,
            material={"raw/one.md"},
            source_identity="raw/one.md",
        )
        self.assertEqual(result.mode, "unchanged")
        self.assertEqual(calls, [])

    def test_r4_ownership_beats_r6_the_model(self) -> None:
        existing = _page(sources='["raw/one.md"]', body="# Old\n")
        incoming = _page(sources='["raw/one.md"]', body="# New\n")
        calls: list[int] = []

        def merger(*_args: str) -> str:  # pragma: no cover - must not run
            calls.append(1)
            return incoming

        result = merge_page_content(
            incoming,
            existing,
            merger,
            material={"raw/one.md"},
            source_identity="raw/one.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "replace")
        self.assertEqual(calls, [])

    def test_r5_same_body_after_the_union_beats_r6(self) -> None:
        body = "# Shared\n"
        existing = _page(sources='["raw/one.md"]', body=body)
        incoming = _page(sources='["raw/two.md"]', body=body)
        calls: list[int] = []

        def merger(*_args: str) -> str:  # pragma: no cover - must not run
            calls.append(1)
            return incoming

        result = merge_page_content(
            incoming,
            existing,
            merger,
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(result.mode, "merge")
        self.assertEqual(calls, [])

    def test_r7_measures_the_answer_against_the_longer_input(self) -> None:
        from noteagent.ingest.frontmatter import parse_page

        existing = _page(sources='["raw/one.md"]', body="o" * 100)
        incoming = _page(sources='["raw/two.md"]', body="n" * 100)
        longest = max(
            len(parse_page(existing).body.strip()),
            len(parse_page(incoming).body.strip()),
        )
        at_the_floor = "a" * int(longest * RULES.smallest_body)

        kept = merge_page_content(
            incoming,
            existing,
            lambda *_: _page(sources='["raw/two.md"]', body=at_the_floor),
            source_identity="raw/two.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(kept.mode, "merge")
        self.assertIn(at_the_floor, kept.content)

        below = at_the_floor[:-1]
        rejected = merge_page_content(
            incoming,
            existing,
            lambda *_: _page(sources='["raw/two.md"]', body=below),
            source_identity="raw/two.md",
            page_path="wiki/demo.md",
            today=lambda: "2026-08-10",
        )
        self.assertEqual(rejected.mode, "merge-fallback")
        self.assertIn("wiki/demo.md", rejected.warning or "")
        self.assertIn("floor", rejected.warning or "")


class MergePromptTests(unittest.TestCase):
    """The merge prompt must not put source-version attribution in the page body."""

    def test_system_prompt_never_labels_the_inputs_as_versions(self) -> None:
        lowered = build_page_merge_system_prompt().lower()
        for forbidden in (
            "existing version",
            "newly generated version",
            "source version",
            "previous version",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_system_prompt_forbids_attribution_prose(self) -> None:
        lowered = build_page_merge_system_prompt().lower()
        self.assertIn("never says that two texts were", lowered)
        self.assertIn("never names a source or a file in its prose", lowered)

    def test_system_prompt_keeps_facts_over_a_single_conclusion(self) -> None:
        lowered = build_page_merge_system_prompt().lower()
        self.assertIn("do not average the disagreement away", lowered)
        self.assertIn("what each account holds, and what it holds under", lowered)
        self.assertIn("a page that resolves a conflict the sources", lowered)

    def test_system_prompt_declares_the_tool_managed_structures(self) -> None:
        text = build_page_merge_system_prompt()
        self.assertIn("`[[link]]`", text)
        self.assertIn("`<!-- na-clip:ID -->`", text)
        self.assertIn("`<!-- na-ecg -->`", text)
        self.assertIn("`---FILE:`", text)

    def test_system_prompt_states_the_checks_the_answer_must_pass(self) -> None:
        # A refused answer keeps the old page, so the prompt says what is checked.
        text = build_page_merge_system_prompt()
        self.assertIn("What the program checks", text)
        self.assertIn("no annotation is missing, invented", text)
        self.assertIn("no link target either text carried has disappeared", text)

    def test_system_prompt_gives_the_frontmatter_to_the_caller(self) -> None:
        text = build_page_merge_system_prompt()
        self.assertIn("The frontmatter is the program's to settle", text)
        self.assertIn("keeps the page's title", text)
        self.assertIn("unions the `sources` list", text)

    def test_user_message_delimiters_are_not_version_labelled(self) -> None:
        message = build_page_merge_user_message("OLD BODY", "NEW BODY", "raw/x.md")
        self.assertIn("## The page as it stands", message)
        self.assertIn(
            "## The material this write brought (from `raw/x.md`; "
            "not to be named in the page)",
            message,
        )
        self.assertIn("OLD BODY", message)
        self.assertIn("NEW BODY", message)
        self.assertNotIn("version", message.lower())

    def test_user_message_drops_the_label_when_there_is_no_identity(self) -> None:
        message = build_page_merge_user_message("OLD BODY", "NEW BODY", "")
        self.assertIn("## The material this write brought", message)
        self.assertNotIn("(from", message)
        self.assertNotIn("not to be named", message)

    def test_user_message_without_a_refusal_asks_for_the_page(self) -> None:
        message = build_page_merge_user_message("OLD BODY", "NEW BODY", "raw/x.md")
        self.assertTrue(message.endswith("Write the page."))
        self.assertNotIn("Why the last answer", message)

    def test_user_message_with_a_refusal_names_what_to_fix(self) -> None:
        message = build_page_merge_user_message(
            "OLD BODY",
            "NEW BODY",
            "raw/x.md",
            refused="the answer had no frontmatter",
        )
        self.assertIn("## Why the last answer was not written", message)
        self.assertIn("the answer had no frontmatter", message)
        self.assertIn("A shorter page is not the fix.", message)
        self.assertTrue(message.endswith("Write the page again."))


class RewriteRuleTests(unittest.TestCase):
    """R4b: the body was written with this page in front of it."""

    old = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        'sources: ["raw/one.md"]\n'
        "---\n"
        "\n"
        "Entropy measures how uncertain a distribution is.\n\n"
        "See [[kl-divergence]].\n"
    )
    rewritten = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-02-02\n"
        'sources: ["raw/one.md", "raw/two.md"]\n'
        "---\n"
        "\n"
        "Entropy measures how uncertain a distribution is, and is the average "
        "information: a lower bound on code length.\n\n"
        "<!-- na-clip:two -->\n"
        "## Joint entropy\n"
        "\n"
        "The entropy of a pair is at most the sum of the two.\n\n"
        "See [[kl-divergence]] and [[joint-entropy]].\n"
    )
    # What this write would have said under the page's name, with the one thing
    # only the new source brings: a passage marker and a link of its own.
    draft = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-02-02\n"
        'sources: ["raw/one.md", "raw/two.md"]\n'
        "---\n"
        "\n"
        "Entropy is the average information a distribution carries.\n\n"
        "<!-- na-clip:two -->\n"
        "## Joint entropy\n"
        "\n"
        "See [[joint-entropy]].\n"
    )

    def _decide(self, answer: str, **kwargs):
        return merge_page_content(
            answer,
            self.old,
            lambda *_: self.fail("a rewrite must not call the merger"),
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
            today=lambda: "2026-02-02",
            rewrite_draft=self.draft,
            **kwargs,
        )

    def test_a_rewrite_is_written_as_it_came(self) -> None:
        result = self._decide(self.rewritten)
        self.assertEqual(result.mode, "rewrite")
        self.assertIsNone(result.warning)
        self.assertIn("average information", result.content)

    def test_a_rewrite_keeps_the_marker_and_the_link_the_draft_brought(self) -> None:
        # The check has to accept what only the new source earned; holding the
        # answer to the page alone would call this marker an invention.
        result = self._decide(self.rewritten)
        self.assertEqual(result.mode, "rewrite")
        self.assertIn("<!-- na-clip:two -->", result.content)
        self.assertIn("[[joint-entropy]]", result.content)

    def test_a_rewrite_unions_sources_and_keeps_the_page_s_identity(self) -> None:
        result = self._decide(self.rewritten)
        self.assertIn("title: Entropy", result.content)
        self.assertIn("created: 2026-01-01", result.content)
        self.assertIn("updated: 2026-02-02", result.content)
        self.assertIn("raw/one.md", result.content)
        self.assertIn("raw/two.md", result.content)

    def test_a_rewrite_that_drops_a_link_keeps_the_page(self) -> None:
        answer = self.rewritten.replace("[[kl-divergence]] and ", "")
        result = self._decide(answer)
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("kl-divergence", result.warning or "")

    def test_a_rewrite_that_drops_the_draft_s_link_keeps_the_page(self) -> None:
        answer = self.rewritten.replace(" and [[joint-entropy]]", "")
        result = self._decide(answer)
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("joint-entropy", result.warning or "")

    def test_a_rewrite_that_drops_the_draft_s_marker_keeps_the_page(self) -> None:
        answer = self.rewritten.replace("<!-- na-clip:two -->\n", "")
        result = self._decide(answer)
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("na-clip:two", result.warning or "")

    def test_a_rewrite_that_invents_a_marker_keeps_the_page(self) -> None:
        answer = self.rewritten.replace(
            "<!-- na-clip:two -->", "<!-- na-clip:three -->"
        )
        result = self._decide(answer)
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("na-clip:three", result.warning or "")

    def test_a_rewrite_far_shorter_than_the_page_keeps_the_page(self) -> None:
        # The floor is the page's, not the draft's: a short draft is no reason to
        # accept a page that lost most of what it held.
        long_old = (
            "---\ntitle: Entropy\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            'sources: ["raw/one.md"]\n---\n\n'
            "Entropy measures how uncertain a distribution is. " * 30
            + "\n\nSee [[kl-divergence]].\n"
        )
        answer = (
            "---\ntitle: Entropy\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/one.md"]\n---\n\n'
            "<!-- na-clip:two -->\n"
            "## Joint entropy\n\n"
            "See [[kl-divergence]] and [[joint-entropy]].\n"
        )
        result = merge_page_content(
            answer,
            long_old,
            lambda *_: self.fail("a rewrite must not call the merger"),
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
            today=lambda: "2026-02-02",
            rewrite_draft=self.draft,
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, long_old)
        self.assertIn("floor", result.warning or "")

    def test_a_rewrite_may_keep_every_link_and_add_one(self) -> None:
        answer = self.rewritten.replace(
            "[[joint-entropy]]", "[[joint-entropy]] and [[cross-entropy]]"
        )
        result = self._decide(answer)
        self.assertEqual(result.mode, "rewrite")
        self.assertIn("[[cross-entropy]]", result.content)

    def test_without_a_draft_a_second_source_still_merges(self) -> None:
        # The same text without `rewrite_draft` is two accounts of one subject,
        # and no merger is supplied, so the page keeps its body.
        result = merge_page_content(
            self.rewritten,
            self.old,
            None,
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)


class RepairRuleTests(unittest.TestCase):
    """R7b: one second attempt, told what the last answer was refused for."""

    old = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        'sources: ["raw/one.md"]\n'
        "---\n"
        "\n"
        "Entropy measures how uncertain a distribution is.\n\n"
        "See [[kl-divergence]] and [[joint-entropy]].\n"
    )
    incoming = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-02-02\n"
        'sources: ["raw/one.md", "raw/two.md"]\n'
        "---\n"
        "\n"
        "Entropy is the average information, a lower bound on code length.\n\n"
        "See [[kl-divergence]] and [[joint-entropy]].\n"
    )
    # A second source landed, so R4 cannot replace and R6 asks for a page.
    bad_answer = "Body only, no frontmatter.\n"
    good_answer = (
        "---\n"
        "title: Entropy\n"
        "created: 2026-01-01\n"
        "updated: 2026-02-02\n"
        'sources: ["raw/one.md", "raw/two.md"]\n'
        "---\n"
        "\n"
        "Entropy measures how uncertain a distribution is, and is the average "
        "information: a lower bound on code length.\n\n"
        "See [[kl-divergence]] and [[joint-entropy]].\n"
    )

    def _decide(self, repair, *, merger=None):
        return merge_page_content(
            self.incoming,
            self.old,
            merger if merger is not None else (lambda *_: self.bad_answer),
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
            today=lambda: "2026-02-02",
            repair=repair,
        )

    def test_a_refused_page_is_asked_once_more_and_written(self) -> None:
        seen: list[tuple[str, str, str, str]] = []

        def repair(existing, incoming, identity, reason):
            seen.append((existing, incoming, identity, reason))
            return self.good_answer

        result = self._decide(repair)
        self.assertEqual(result.mode, "repair")
        self.assertIn("lower bound on code length", result.content)
        self.assertEqual(len(seen), 1)
        existing, incoming, identity, reason = seen[0]
        self.assertEqual(existing, self.old)
        self.assertEqual(identity, "raw/two.md")
        self.assertIn("no frontmatter", reason)
        self.assertIn("raw/two.md", incoming)

    def test_a_repair_that_is_refused_too_leaves_the_page(self) -> None:
        result = self._decide(lambda *_: "Still no frontmatter.\n")
        self.assertEqual(result.mode, "repair-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("no frontmatter", result.warning or "")
        self.assertIn("repair reply", result.warning or "")

    def test_a_repair_is_not_asked_twice(self) -> None:
        calls: list[int] = []

        def repair(*_):
            calls.append(1)
            return self.bad_answer

        self._decide(repair)
        self.assertEqual(len(calls), 1)

    def test_a_successful_merge_is_not_repaired(self) -> None:
        result = self._decide(
            lambda *_: self.fail("no repair for a page that merged"),
            merger=lambda *_: self.good_answer,
        )
        self.assertEqual(result.mode, "merge")

    def test_a_repair_that_loses_a_link_is_refused(self) -> None:
        answer = self.good_answer.replace(" and [[joint-entropy]]", "")
        result = self._decide(lambda *_: answer)
        self.assertEqual(result.mode, "repair-fallback")
        self.assertIn("joint-entropy", result.warning or "")

    def test_a_repair_that_is_far_shorter_is_refused(self) -> None:
        # No links either side, so the only check a shorter page can fail is the
        # floor: this test is about the floor, not about a dropped link.
        old = (
            "---\ntitle: Entropy\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            'sources: ["raw/one.md"]\n---\n\n'
            + "Entropy measures how uncertain a distribution is. " * 4
            + "\n"
        )
        incoming = (
            old.replace(
                'sources: ["raw/one.md"]', 'sources: ["raw/one.md", "raw/two.md"]'
            )
            .replace("updated: 2026-01-01", "updated: 2026-02-02")
            .replace(
                "Entropy measures how uncertain a distribution is. ",
                "Entropy is also a lower bound on code length. ",
                1,
            )
        )
        answer = (
            "---\ntitle: Entropy\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/one.md", "raw/two.md"]\n---\n\nEntropy.\n'
        )
        result = merge_page_content(
            incoming,
            old,
            lambda *_: "Body only, no frontmatter.\n",
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
            today=lambda: "2026-02-02",
            repair=lambda *_: answer,
        )
        self.assertEqual(result.mode, "repair-fallback")
        self.assertIn("floor", result.warning or "")

    def test_a_failed_repair_call_keeps_the_page(self) -> None:
        def repair(*_):
            raise RuntimeError("upstream said no")

        result = self._decide(repair)
        self.assertEqual(result.mode, "repair-fallback")
        self.assertEqual(result.content, self.old)
        self.assertIn("upstream said no", result.warning or "")

    def test_an_empty_repair_keeps_the_page(self) -> None:
        result = self._decide(lambda *_: "   \n")
        self.assertEqual(result.mode, "repair-fallback")
        self.assertIn("empty", result.warning or "")

    def test_without_a_repair_the_page_is_left_alone_as_before(self) -> None:
        result = merge_page_content(
            self.incoming,
            self.old,
            lambda *_: self.bad_answer,
            source_identity="raw/two.md",
            page_path="wiki/entropy.md",
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertEqual(result.content, self.old)

    def test_a_failed_merge_call_is_not_repaired(self) -> None:
        # The answer was never seen, so there is nothing the model got wrong.
        def merger(*_):
            raise RuntimeError("network")

        result = self._decide(
            lambda *_: self.fail("no repair for a call that never answered"),
            merger=merger,
        )
        self.assertEqual(result.mode, "merge-fallback")
        self.assertIn("merge call failed", result.warning or "")


if __name__ == "__main__":
    unittest.main()
