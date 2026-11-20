from __future__ import annotations

import os
import tempfile
import threading
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

    def test_a_transient_replace_error_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")
            real_replace = os.replace
            calls: list[str] = []

            def replace(source, target) -> None:
                calls.append(str(target))
                if len(calls) < 3:
                    # The shape Windows uses when a reader holds the target.
                    raise PermissionError(13, "the file is in use")
                real_replace(source, target)

            with mock.patch(
                "noteagent.io.atomic.os.replace", side_effect=replace
            ):
                write_text_atomic(path, "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(len(calls), 3)
            self.assertEqual(
                [item.name for item in path.parent.iterdir()], ["page.md"]
            )

    def test_a_persistent_permission_error_is_raised_after_the_retries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")
            calls: list[str] = []

            def replace(source, target) -> None:
                calls.append(str(target))
                raise PermissionError(13, "the file is in use")

            with mock.patch(
                "noteagent.io.atomic.os.replace", side_effect=replace
            ):
                with self.assertRaises(PermissionError):
                    write_text_atomic(path, "new\n")

            # It retried, then gave up without touching the old file or
            # leaving a temporary beside it.
            self.assertGreater(len(calls), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(
                [item.name for item in path.parent.iterdir()], ["page.md"]
            )

    @unittest.skipUnless(
        os.name == "nt",
        "only Windows denies a replace while a reader holds the file",
    )
    def test_a_reader_holding_the_target_is_waited_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.md"
            path.write_text("old\n", encoding="utf-8")
            holder = open(path, "rb")
            released = threading.Event()

            def release() -> None:
                threading.Event().wait(0.03)
                holder.close()
                released.set()

            thread = threading.Thread(target=release, daemon=True)
            thread.start()
            try:
                # Without the retry this raises PermissionError at once: the
                # reader's open handle denies the replace.
                write_text_atomic(path, "new\n")
            finally:
                holder.close()
                thread.join(2)

            self.assertTrue(released.is_set())
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

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
