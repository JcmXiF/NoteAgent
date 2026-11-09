"""The kernel is reusable only while it stays ignorant of the product.

The wiki, ingestion, acquisition and the old chat are adapters at the kernel's
seams. Importing one from inside the kernel would turn the seam into a direct
dependency and make the kernel impossible to test or reuse on its own.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "src" / "noteagent" / "agent"

#: Packages that belong to the product, not the kernel.
FORBIDDEN = (
    "noteagent.acquire",
    "noteagent.api",
    "noteagent.chat",
    "noteagent.ingest",
    "noteagent.llm",
    "noteagent.project",
    "noteagent.settings",
    "noteagent.wiki",
)

#: Every module a clean interpreter should be able to import on its own.
KERNEL_MODULES = (
    "noteagent.agent",
    "noteagent.agent.clock",
    "noteagent.agent.context",
    "noteagent.agent.events",
    "noteagent.agent.faux",
    "noteagent.agent.jobs",
    "noteagent.agent.kernel",
    "noteagent.agent.model",
    "noteagent.agent.projection",
    "noteagent.agent.runtime",
    "noteagent.agent.store",
    "noteagent.agent.tools",
    "noteagent.agent.wait",
)


def _imported_modules(path: Path):
    """Every module a file names, with relative imports resolved to absolute."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = list(path.relative_to(ROOT / "src").with_suffix("").parts)[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Level 1 is the file's own package; each extra level walks out.
                base = package[: len(package) - (node.level - 1)]
                head = ".".join(base + ([node.module] if node.module else []))
                yield head
                for alias in node.names:
                    yield f"{head}.{alias.name}"
            elif node.module == "noteagent":
                for alias in node.names:
                    yield f"noteagent.{alias.name}"
            elif node.module:
                yield node.module


class KernelBoundaryTests(unittest.TestCase):
    def test_the_kernel_does_not_import_a_business_module(self) -> None:
        offenders = []
        for path in sorted(KERNEL.rglob("*.py")):
            for module in _imported_modules(path):
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN
                ):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}: {module}")
        self.assertEqual(offenders, [], "the adapter belongs outside the kernel")

    def test_a_clean_interpreter_imports_every_kernel_module(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "src"), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        imports = "\n".join(f"import {name}" for name in KERNEL_MODULES)
        code = (
            "import sys\n"
            f"{imports}\n"
            f"print(','.join(n for n in {FORBIDDEN!r} if n in sys.modules))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "", "the kernel pulled in the product")


if __name__ == "__main__":
    unittest.main()
