"""The configuration document is the only settings source.

A file named `.env` beside the working directory must be inert, and the source
tree must not name one as a live configuration path. These cases fail if the
old mechanism is reintroduced, rather than only when somebody notices it.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.acquire.search_config import load_search_config
from noteagent.llm.config import LlmConfigError, load_llm_config
from fakes import FakeCredentials

ROOT = Path(__file__).resolve().parent.parent

#: Live configuration references a source tree may not hold. Vite's
#: ``import.meta.env`` and Node's ``process.env`` are not file references.
LIVE = re.compile(
    r"""load_dotenv|dotenv_path|update_env_text|write_env_file|envFilePath|"""
    r"""env-template|[/\\]\.env\b|["'`]\.env["'`]"""
)

#: Source trees that describe the product rather than test the old mechanism.
_SCANNED_ROOTS = ("src", "desktop", "scripts", "web/src", "web/scripts")

#: Current guides. Historical design records are not instructions.
_GUIDES = (
    "README.md",
    "docs/architecture.md",
    "docs/development.md",
    "docs/dev-project.md",
)


def source_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"node_modules", "__pycache__"} for part in path.parts):
            continue
        if path.name == "package-lock.json":
            continue
        # The interface copied into the package is build output, not source.
        if ROOT / "src" / "noteagent" / "web" in path.parents:
            continue
        if path.suffix.lower() in {
            ".py",
            ".cjs",
            ".js",
            ".mjs",
            ".ts",
            ".tsx",
            ".yml",
            ".yaml",
            ".md",
            ".txt",
            ".json",
        }:
            found.append(path)
    return found


class DotenvIsInertTests(unittest.TestCase):
    def test_a_dotenv_beside_the_working_directory_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                "NOTEAGENT_LLM_BASE_URL=https://example.com/v1\n"
                "NOTEAGENT_LLM_API_KEY=sk-test\n"
                "NOTEAGENT_LLM_MODEL=demo\n"
                "NOTEAGENT_SEARCH_PROVIDER=brave\n",
                encoding="utf-8",
            )
            previous = Path.cwd()
            os.chdir(tmp)
            try:
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch(
                        "noteagent.acquire.search_config.default_config_path",
                        lambda: Path(tmp) / "config.json",
                    ),
                    patch(
                        "noteagent.acquire.search_config.keyring_store",
                        lambda: FakeCredentials(),
                    ),
                ):
                    with self.assertRaises(LlmConfigError):
                        load_llm_config()
                    self.assertEqual(load_search_config().provider, "none")
            finally:
                os.chdir(previous)

    def test_no_live_dotenv_reference_remains(self) -> None:
        found: list[str] = []
        for name in _SCANNED_ROOTS:
            for path in source_files(ROOT / name):
                text = path.read_text(encoding="utf-8", errors="replace")
                for match in LIVE.finditer(text):
                    found.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        for name in _GUIDES:
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for match in LIVE.finditer(text):
                found.append(f"{name}: {match.group(0)}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
