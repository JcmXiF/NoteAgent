from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api import projects as projects_mod
from noteagent.api.projects import (
    create_project,
    data_home,
    list_projects,
    resolve_repo_root,
    use_project,
)
from noteagent.project.config import (
    ENV_HOME,
    ProjectNotConfiguredError,
    home_dir,
    resolve_active_project,
)
from noteagent.project.lifecycle import create_named_project, projects_dir
from noteagent.project.names import ProjectNameError


class CheckoutTests(unittest.TestCase):
    """Without a home configured, nothing changes for a checkout."""

    def test_the_repository_is_named_and_projects_keep_their_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.dict(os.environ, {ENV_HOME: ""}), patch.object(
                projects_mod, "REPO_ROOT", repo
            ):
                self.assertIsNone(home_dir())
                self.assertEqual(resolve_repo_root(), repo)
                self.assertEqual(data_home(), repo / "projects")
                self.assertEqual(projects_dir(repo), repo / "projects")


class HomeTests(unittest.TestCase):
    """A packaged install names its own home, which is the container itself."""

    def test_the_home_is_the_container_without_an_extra_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                self.assertEqual(resolve_repo_root(), home)
                self.assertEqual(data_home(), home)
                self.assertEqual(projects_dir(home), home)

    def test_a_project_is_created_in_the_home_and_pointed_at_from_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                created = create_project("notes")

                self.assertTrue((home / "notes" / "raw").is_dir())
                self.assertFalse((home / "projects").exists())
                pointer = json.loads(
                    (home / ".noteagent-project").read_text(encoding="utf-8")
                )
                # The pointer sits beside the projects, so its path is the name.
                self.assertEqual(pointer["path"], "notes")
                self.assertEqual(created["path"], "notes")

    def test_listing_speaks_in_names_relative_to_the_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                create_project("notes")
                create_project("papers")

                names = {item["name"] for item in list_projects()}
                paths = {item["path"] for item in list_projects()}

                self.assertEqual(names, {"notes", "papers"})
                self.assertEqual(paths, {"notes", "papers"})

    def test_using_a_project_moves_the_pointer_inside_the_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                create_project("first")
                create_project("second")

                use_project("first")

                pointer = json.loads(
                    (home / ".noteagent-project").read_text(encoding="utf-8")
                )
                self.assertEqual(pointer["path"], "first")

    def test_the_active_project_is_found_through_the_home_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                create_project("notes")

                items = list_projects()

                self.assertEqual(
                    [item["name"] for item in items if item["active"]], ["notes"]
                )

    def test_an_explicit_folder_still_wins_over_the_home(self) -> None:
        # Tests and callers that name a folder mean it; the home is only the
        # answer when nobody said otherwise.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            other = Path(tmp) / "elsewhere"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                self.assertEqual(resolve_repo_root(other), other)
                self.assertEqual(projects_dir(other), other / "projects")

    def test_a_name_that_climbs_out_of_the_home_is_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                with self.assertRaises(ProjectNameError):
                    create_named_project("../escape", repo_root=home)


class EmptyHomeTests(unittest.TestCase):
    def test_an_install_with_no_projects_says_it_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            with patch.dict(os.environ, {ENV_HOME: str(home)}):
                self.assertEqual(list_projects(), [])

    def test_reading_the_active_project_without_a_pointer_raises_the_usual_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NoteAgent"
            home.mkdir()
            with patch.dict(os.environ, {ENV_HOME: str(home), "NOTEAGENT_PROJECT": ""}):
                with self.assertRaises(ProjectNotConfiguredError):
                    resolve_active_project(start=home)


if __name__ == "__main__":
    unittest.main()
