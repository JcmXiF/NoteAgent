"""The skill registry: three roots, folder and file skills, and its guards."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.skills import (
    MAX_SKILL_BYTES,
    SkillNotFoundError,
    SkillRegistry,
    SkillRoot,
    SkillRootKind,
    catalog_in,
    default_roots,
    problems_in,
    skill_in,
)


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


def _flat(
    folder: Path,
    filename: str,
    *,
    name: str | None = None,
    body: str = "Flat body.",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    front = (
        f"---\nname: {name}\ndescription: Use when needed.\n---\n\n"
        if name is not None
        else ""
    )
    path.write_text(front + f"{body}\n", encoding="utf-8")
    return path


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a symlink, or a Windows junction without privilege."""
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


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_folder_skill_carries_name_description_and_body(self) -> None:
        _skill(
            self.root,
            "writing",
            body="Write the page.",
            description="Use for writing requests.",
        )

        entries = catalog_in(self.root)

        self.assertEqual([entry.name for entry in entries], ["writing"])
        self.assertEqual(
            entries[0].description, "Use for writing requests."
        )
        skill = skill_in(self.root, "writing")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.body, "Write the page.")
        self.assertEqual(skill.path.name, "SKILL.md")

    def test_a_flat_markdown_file_still_loads(self) -> None:
        _flat(self.root, "search.md", name="search", body="Search body.")

        self.assertEqual(
            [entry.name for entry in catalog_in(self.root)], ["search"]
        )
        self.assertEqual(skill_in(self.root, "search").body, "Search body.")

    def test_a_flat_file_without_frontmatter_uses_its_stem(self) -> None:
        _flat(self.root, "plain.md", body="Plain body.")

        self.assertEqual(
            [entry.name for entry in catalog_in(self.root)], ["plain"]
        )

    def test_a_missing_root_is_empty(self) -> None:
        missing = self.root / "missing"

        self.assertEqual(catalog_in(missing), ())
        self.assertEqual(problems_in(missing), ())
        self.assertIsNone(skill_in(missing, "writing"))

    def test_an_invalid_name_is_refused(self) -> None:
        for name in ("Upper", "under_score", "two spaces", "a" * 65, "-lead"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    _flat(folder, "x.md", name=name)

                    self.assertEqual(catalog_in(folder), ())
                    problems = problems_in(folder)
                    self.assertEqual(len(problems), 1)
                    self.assertIn("invalid skill name", problems[0].reason)

    def test_a_duplicate_keeps_the_first_and_refuses_the_rest(self) -> None:
        _flat(self.root, "one.md", name="dupe", body="First.")
        _flat(self.root, "two.md", name="dupe", body="Second.")

        entries = catalog_in(self.root)
        problems = problems_in(self.root)

        self.assertEqual([entry.name for entry in entries], ["dupe"])
        self.assertEqual(skill_in(self.root, "dupe").body, "First.")
        self.assertEqual(len(problems), 1)
        self.assertIn("duplicate skill 'dupe'", problems[0].reason)

    def test_an_oversized_skill_is_refused(self) -> None:
        path = self.root / "big" / "SKILL.md"
        path.parent.mkdir()
        path.write_text("x" * (MAX_SKILL_BYTES + 1), encoding="utf-8")

        self.assertEqual(catalog_in(self.root), ())
        problems = problems_in(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("exceeds", problems[0].reason)

    def test_a_symlinked_skill_file_is_refused(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text(
            "---\nname: peek\n---\n\nBody.\n", encoding="utf-8"
        )
        folder = self.root / "skills"
        folder.mkdir()
        link = folder / "peek.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("file symlinks are not available")

        self.assertEqual(catalog_in(folder), ())
        self.assertEqual(len(problems_in(folder)), 1)

    def test_a_linked_flat_entry_is_refused(self) -> None:
        outside = self.root / "outside"
        _skill(outside, "peek")
        folder = self.root / "skills"
        folder.mkdir()
        _link_dir(folder / "peek.md", outside)

        self.assertEqual(catalog_in(folder), ())
        self.assertEqual(len(problems_in(folder)), 1)

    def test_a_skill_folder_escaping_its_root_is_refused(self) -> None:
        outside = self.root / "outside"
        _skill(outside, "peek")
        folder = self.root / "skills"
        folder.mkdir()
        _link_dir(folder / "peek", outside / "peek")

        self.assertEqual(catalog_in(folder), ())
        self.assertEqual(len(problems_in(folder)), 1)

    def test_a_path_is_not_a_skill_name(self) -> None:
        _skill(self.root, "writing")
        secret = self.root / "secret"
        secret.mkdir()

        self.assertIsNone(skill_in(self.root, "../secret"))

    def test_the_skill_filename_is_not_a_skill_name(self) -> None:
        _skill(self.root, "writing")

        self.assertIsNone(skill_in(self.root, "SKILL"))


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _registry(self) -> tuple[SkillRegistry, Path, Path, Path]:
        bundled = self.base / "bundled"
        user = self.base / "user"
        project = self.base / "project"
        return (
            SkillRegistry(
                (
                    SkillRoot(SkillRootKind.PROJECT, project),
                    SkillRoot(SkillRootKind.USER, user),
                    SkillRoot(SkillRootKind.BUNDLED, bundled),
                )
            ),
            project,
            user,
            bundled,
        )

    def test_project_overrides_user_overrides_bundled(self) -> None:
        registry, project, user, bundled = self._registry()
        _skill(project, "writing", body="project body")
        _skill(user, "writing", body="user body")
        _skill(bundled, "writing", body="bundled body")

        self.assertEqual(registry.load("writing").body, "project body")
        self.assertEqual(len(registry.catalog()), 1)

        (project / "writing" / "SKILL.md").unlink()
        (project / "writing").rmdir()
        self.assertEqual(registry.load("writing").body, "user body")

        (user / "writing" / "SKILL.md").unlink()
        (user / "writing").rmdir()
        self.assertEqual(registry.load("writing").body, "bundled body")

    def test_a_lower_root_fills_the_gap(self) -> None:
        registry, _project, user, bundled = self._registry()
        _skill(bundled, "writing")
        _skill(user, "search")

        self.assertEqual(
            [entry.name for entry in registry.catalog()],
            ["search", "writing"],
        )

    def test_an_unknown_skill_names_what_was_asked(self) -> None:
        registry, *_ = self._registry()

        with self.assertRaises(SkillNotFoundError) as err:
            registry.load("missing")

        self.assertIn("missing", str(err.exception))

    def test_a_duplicate_within_one_root_keeps_the_first(self) -> None:
        registry, _project, user, _bundled = self._registry()
        _flat(user, "one.md", name="dupe", body="First.")
        _flat(user, "two.md", name="dupe", body="Second.")

        self.assertEqual(registry.load("dupe").body, "First.")
        self.assertEqual(len(registry.problems()), 1)


class DefaultRootsTests(unittest.TestCase):
    def test_the_roots_are_project_user_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            user = base / "user"
            bundled = base / "bundled"

            roots = default_roots(
                project, config_home=user, bundled=bundled
            )

        self.assertEqual(
            [root.kind for root in roots],
            [
                SkillRootKind.PROJECT,
                SkillRootKind.USER,
                SkillRootKind.BUNDLED,
            ],
        )
        self.assertEqual(
            roots[0].folder, project / ".noteagent" / "skills"
        )
        self.assertEqual(roots[1].folder, user / "skills")
        self.assertEqual(roots[2].folder, bundled)

    def test_without_a_project_the_user_and_bundled_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user"
            bundled = Path(tmp) / "bundled"

            roots = default_roots(
                None, config_home=user, bundled=bundled
            )

        self.assertEqual(
            [root.kind for root in roots],
            [SkillRootKind.USER, SkillRootKind.BUNDLED],
        )

    def test_the_default_user_root_follows_the_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ, {"NOTEAGENT_CONFIG_DIR": tmp}, clear=False
            ):
                roots = default_roots(None)

        self.assertEqual(roots[0].folder, Path(tmp) / "skills")


if __name__ == "__main__":
    unittest.main()
