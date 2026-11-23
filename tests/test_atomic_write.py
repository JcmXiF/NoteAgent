from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noteagent.io.atomic import write_text_atomic
from noteagent.io.text_file import write_text


class AtomicWriteTests(unittest.TestCase):
    def test_replaces_what_was_there_and_leaves_no_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")

            written = write_text(path, "new\r\n")

            self.assertEqual(written, len(b"new\n"))
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual([item.name for item in path.parent.iterdir()], ["page.md"])

    def test_a_new_file_is_created_with_its_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "er" / "page.md"

            write_text_atomic(path, "body\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "body\n")

    def test_a_failed_move_leaves_the_old_file_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")

            with mock.patch("noteagent.io.atomic.os.replace", side_effect=OSError("busy")):
                with self.assertRaises(OSError):
                    write_text_atomic(path, "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            # Nothing half-written is left beside it for the next run to read.
            self.assertEqual([item.name for item in path.parent.iterdir()], ["page.md"])

    @unittest.skipIf(os.name == "nt", "POSIX permissions are not what Windows checks")
    def test_keeps_the_permissions_the_file_had(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")
            path.chmod(0o644)

            write_text_atomic(path, "new\n")

            self.assertEqual(path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
