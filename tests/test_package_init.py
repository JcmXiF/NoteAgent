"""A module's namespace is its surface, and a package root is only a name.

Re-exporting a package's contents from its ``__init__`` costs everyone who
imports any leaf module: the root's imports run first, so a helper that needs
nothing from the network still waits for the extractor stack to load. The same
gesture inside a leaf module makes a second import path for a name, which is how
``api.search`` came to look like the home of two errors it only passed along.

Three checks hold the line: no root declares ``__all__``, no module imports a
name it does not use itself, and a leaf module imported in a clean interpreter
does not load the network stack.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "noteagent"

#: Leaf modules that need nothing from the network or the extractors.
LIGHT = ("noteagent.acquire.types", "noteagent.ingest.frontmatter")
#: What those imports must not drag in behind them.
NETWORK = ("httpx", "trafilatura", "lxml")


def init_files() -> list[Path]:
    return sorted(PACKAGE.rglob("__init__.py"))


def source_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def bound_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    return [alias.asname or alias.name for alias in node.names]


def used_names(tree: ast.Module) -> set[str]:
    """Every name the module reads, ignoring its own import statements.

    A forward reference is a string, not a ``Name`` node, so string constants
    count as mentions too.
    """
    found: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        for node in ast.walk(statement):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.update(re.findall(r"[A-Za-z_]\w*", node.value))
    return found


def imports_of(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        for name in bound_names(node):
            found.append((node.lineno, name.split(".")[0]))
    return found


class InitFileTests(unittest.TestCase):
    def test_no_init_declares_an_all_list(self) -> None:
        offenders = []
        for path in init_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else ()
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual(offenders, [], "the module's namespace is its public surface")

    def test_no_init_imports_a_name_it_does_not_use(self) -> None:
        """Such an import is a re-export: it exists only to be handed to someone else."""
        offenders = []
        for path in init_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            mentioned = used_names(tree)
            for line, name in imports_of(tree):
                if name not in mentioned:
                    where = path.relative_to(ROOT).as_posix()
                    offenders.append(f"{where}:{line} {name}")
        self.assertEqual(offenders, [], "import it where it is used instead")

    def test_no_module_imports_a_name_it_does_not_use(self) -> None:
        """A leaf module that forwards a name gives it a second home."""
        offenders = []
        for path in source_files():
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            mentioned = used_names(tree)
            for line, name in imports_of(tree):
                if name not in mentioned:
                    where = path.relative_to(ROOT).as_posix()
                    offenders.append(f"{where}:{line} {name}")
        self.assertEqual(offenders, [], "import it where it is used instead")

    def test_the_version_the_package_reports_is_the_packaged_one(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version = "([^"]+)"$', metadata, re.MULTILINE)
        assert declared is not None, "pyproject.toml declares no version"
        from noteagent import __version__

        self.assertEqual(__version__, declared.group(1))


class ImportWeightTests(unittest.TestCase):
    def test_a_leaf_module_does_not_load_the_network_stack(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "src"), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        for module in LIGHT:
            with self.subTest(module=module):
                code = (
                    "import sys\n"
                    f"import {module}\n"
                    f"print(','.join(n for n in {NETWORK!r} if n in sys.modules))\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )
                self.assertEqual(
                    result.stdout.strip(), "", f"{module} pulled in {result.stdout.strip()}"
                )


if __name__ == "__main__":
    unittest.main()
