"""The skill tools and the ``<available_skills>`` index."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from noteagent.agent.tools import ToolContext
from noteagent.skills import (
    MAX_REFERENCE_BYTES,
    SkillRegistry,
    SkillRoot,
    SkillRootKind,
)
from noteagent.skills.index import render_index
from noteagent.skills.tools import skill_tools


def _skill(
    folder: Path,
    name: str,
    *,
    body: str = "Skill body.",
    description: str = "Use when needed.",
) -> Path:
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _link_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise unittest.SkipTest("symlinks are not available")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise unittest.SkipTest("directory links are not available")


class SkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base = self.root / "bundled"
        self.manifest = _skill(
            self.base, "writing", body="Write the page."
        )
        self.skill_folder = self.manifest.parent
        self.registry = SkillRegistry(
            (SkillRoot(SkillRootKind.BUNDLED, self.base),)
        )
        self.tools = {
            spec.name: spec for spec in skill_tools(self.registry)
        }
        self.context = ToolContext("s")

    def _call(self, tool: str, arguments: dict[str, object]):
        return self.tools[tool].handler(arguments, self.context)

    def test_skill_returns_the_body_and_the_base_directory(self) -> None:
        result = self._call("skill", {"name": "writing"})

        self.assertIn("Write the page.", result.content)
        self.assertIn(str(self.skill_folder), result.content)

    def test_an_unknown_skill_says_what_is_available(self) -> None:
        result = self._call("skill", {"name": "missing"})

        self.assertIn("missing", result.content)
        self.assertIn("writing", result.content)
        self.assertTrue(result.meta["missing"])

    def test_skill_read_file_reads_a_reference(self) -> None:
        reference = self.skill_folder / "references" / "example.md"
        reference.parent.mkdir()
        reference.write_text("Example reference.", encoding="utf-8")

        result = self._call(
            "skill_read_file",
            {"name": "writing", "path": "references/example.md"},
        )

        self.assertEqual(result.content, "Example reference.")

    def test_a_parent_path_is_refused(self) -> None:
        secret = self.root / "secret.md"
        secret.write_text("TOP SECRET BODY", encoding="utf-8")

        result = self._call(
            "skill_read_file", {"name": "writing", "path": "../secret.md"}
        )

        self.assertIn("cannot read", result.content)
        self.assertNotIn("TOP SECRET BODY", result.content)

    def test_an_absolute_path_is_refused(self) -> None:
        secret = self.root / "secret.md"
        secret.write_text("TOP SECRET BODY", encoding="utf-8")

        result = self._call(
            "skill_read_file",
            {"name": "writing", "path": str(secret)},
        )

        self.assertIn("cannot read", result.content)
        self.assertNotIn("TOP SECRET BODY", result.content)

    def test_a_linked_reference_is_refused(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "leak.md").write_text("LEAK", encoding="utf-8")
        references = self.skill_folder / "references"
        references.mkdir()
        _link_dir(references / "leak.md", outside)

        result = self._call(
            "skill_read_file",
            {"name": "writing", "path": "references/leak.md"},
        )

        self.assertIn("cannot read", result.content)
        self.assertNotIn("LEAK", result.content)

    def test_an_oversized_reference_is_refused(self) -> None:
        reference = self.skill_folder / "references" / "big.md"
        reference.parent.mkdir()
        reference.write_text(
            "x" * (MAX_REFERENCE_BYTES + 1), encoding="utf-8"
        )

        result = self._call(
            "skill_read_file",
            {"name": "writing", "path": "references/big.md"},
        )

        self.assertIn("cannot read", result.content)
        self.assertIn("exceeds", result.content)

    def test_a_missing_reference_names_the_skill(self) -> None:
        result = self._call(
            "skill_read_file",
            {"name": "writing", "path": "references/none.md"},
        )

        self.assertIn("cannot read", result.content)
        self.assertIn("none.md", result.content)

    def test_a_flat_skill_has_no_referenced_files(self) -> None:
        flat = self.root / "flat"
        flat.mkdir()
        (flat / "note.md").write_text(
            "---\nname: note\n---\n\nFlat body.\n", encoding="utf-8"
        )
        (flat / "other.txt").write_text(
            "OTHER BODY", encoding="utf-8"
        )
        registry = SkillRegistry(
            (SkillRoot(SkillRootKind.USER, flat),)
        )
        tools = {spec.name: spec for spec in skill_tools(registry)}

        result = tools["skill_read_file"].handler(
            {"name": "note", "path": "other.txt"}, self.context
        )

        self.assertIn("cannot read", result.content)
        self.assertNotIn("OTHER BODY", result.content)


class IndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_the_index_lists_name_kind_and_description(self) -> None:
        project = self.root / "project"
        _skill(project, "writing", description="Use for writing.")

        text = render_index(
            SkillRegistry(
                (SkillRoot(SkillRootKind.PROJECT, project),)
            )
        )

        self.assertIn("<available_skills>", text)
        self.assertIn("- writing [project]: Use for writing.", text)
        self.assertNotIn("skill_read_file", text)
        self.assertIn("</available_skills>", text)

    def test_an_empty_registry_has_no_index(self) -> None:
        registry = SkillRegistry(
            (SkillRoot(SkillRootKind.BUNDLED, self.root / "missing"),)
        )

        self.assertEqual(render_index(registry), "")

    def test_a_project_skill_shadows_a_bundled_one(self) -> None:
        project = self.root / "project"
        bundled = self.root / "bundled"
        _skill(project, "writing", description="Project copy.")
        _skill(bundled, "writing", description="Bundled copy.")

        text = render_index(
            SkillRegistry(
                (
                    SkillRoot(SkillRootKind.PROJECT, project),
                    SkillRoot(SkillRootKind.BUNDLED, bundled),
                )
            )
        )

        self.assertIn("- writing [project]: Project copy.", text)
        self.assertNotIn("Bundled copy.", text)

    def test_a_small_budget_truncates_descriptions(self) -> None:
        _skill(
            self.root,
            "writing",
            description="A very long description that must be shortened "
            "because the index has a character budget.",
        )

        text = render_index(
            SkillRegistry(
                (SkillRoot(SkillRootKind.BUNDLED, self.root),)
            ),
            max_chars=120,
        )

        self.assertLessEqual(len(text), 120)
        self.assertIn("writing", text)
        self.assertIn("…", text)

    def test_a_tiny_budget_stays_bounded(self) -> None:
        for name in ("alpha", "beta", "gamma"):
            _skill(self.root, name, description="A description.")
        registry = SkillRegistry(
            (SkillRoot(SkillRootKind.BUNDLED, self.root),)
        )

        for budget in (0, 40, 80, 160):
            with self.subTest(budget=budget):
                text = render_index(registry, max_chars=budget)
                self.assertLessEqual(len(text), budget)


if __name__ == "__main__":
    unittest.main()
