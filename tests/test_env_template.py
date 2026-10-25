"""The one settings template, and the settings it has to name.

There is a single one: ``desktop/env-template.txt``. First run copies it to
``%APPDATA%\\NoteAgent\\.env``, and a checkout copies the same file to a ``.env``
at the repository root. The settings screen edits that file too, so the two lists
have to agree: a setting the screen can change but the template omits is one
nobody filling the file in by hand can find, and a key the template still names
after it was removed is one that reads as supported and does nothing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from noteagent.settings.install import MANAGED_ENV_NAMES

ROOT = Path(__file__).resolve().parent.parent

#: What an installed app copies to its settings file on first run.
TEMPLATE = ROOT / "desktop" / "env-template.txt"

#: Keys that are read but have no field on the settings screen: they describe
#: where an installation keeps things rather than how it behaves.
UNMANAGED = frozenset({"NOTEAGENT_PROJECT", "NOTEAGENT_UPDATE_URL"})

#: A key line, commented out or not — the template keeps every key commented so
#: copying it never seeds a value that looks deliberate.
_KEY = re.compile(r"^#?\s*(NOTEAGENT_[A-Z0-9_]+)\s*=", re.MULTILINE)
#: A key line that would take effect, which a template must not have.
_ACTIVE = re.compile(r"^\s*(NOTEAGENT_[A-Z0-9_]+)\s*=", re.MULTILINE)

#: Without these the core cannot make a single call.
REQUIRED = frozenset({"NOTEAGENT_LLM_BASE_URL", "NOTEAGENT_LLM_API_KEY"})


def keys_in(path: Path) -> set[str]:
    """The keys a template names, in any order."""
    return set(_KEY.findall(path.read_text(encoding="utf-8")))


class EnvTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        # A missing file fails every test below with the path that is missing,
        # rather than a traceback from whichever test ran first.
        if not TEMPLATE.is_file():
            self.fail(f"missing settings template: {TEMPLATE}")
        self.named = keys_in(TEMPLATE)

    def test_every_setting_the_screen_edits_is_named(self) -> None:
        # Reported as a set difference, so a failure names the setting to add.
        self.assertEqual(
            sorted(MANAGED_ENV_NAMES - self.named),
            [],
            "these settings are editable in the app but missing from the template",
        )

    def test_the_template_names_nothing_that_was_removed(self) -> None:
        # A key nobody reads any more still looks supported when it is listed.
        self.assertEqual(
            sorted(self.named - MANAGED_ENV_NAMES - UNMANAGED),
            [],
            "these keys are in the template but nothing reads them",
        )

    def test_the_keys_a_first_run_needs_are_named(self) -> None:
        # Also catches a template that lost its keys but stayed "in agreement"
        # with a list that lost them the same way.
        self.assertEqual(sorted(REQUIRED - self.named), [], TEMPLATE.name)

    def test_a_template_never_sets_a_value(self) -> None:
        # A live line in a template would be copied into someone's settings and
        # read as a decision they made.
        active = _ACTIVE.findall(TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(set(active)), [], TEMPLATE.name)


if __name__ == "__main__":
    unittest.main()
