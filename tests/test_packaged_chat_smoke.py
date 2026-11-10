"""The scripted flow the packaging smoke runs inside the packaged core.

It runs here against the source checkout, which is the same application the
runtime carries. The nightly smoke repeats it with the interpreter that ships,
so a missing dependency or a stale copy is caught there; this keeps the flow
itself from rotting between releases.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover - CI installs the API extra
    fastapi = None

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_packaged_chat.py"


@unittest.skipIf(fastapi is None, "fastapi not installed")
class PackagedChatSmokeTests(unittest.TestCase):
    def test_the_flow_answers_proposes_and_stops_its_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **os.environ,
                "NOTEAGENT_CONFIG_DIR": str(root / "config"),
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--home",
                    str(root / "home"),
                    "--project",
                    str(root / "project"),
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )

        self.assertEqual(
            result.returncode,
            0,
            (result.stdout or "") + (result.stderr or ""),
        )
        self.assertIn("wait cancelled", result.stdout)
        self.assertIn("SMOKE OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
