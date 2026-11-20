"""The temporary-tree cleanup: retry a closing handle, not a real failure."""

from __future__ import annotations

import errno
import unittest

from noteagent.util.tempdir import remove_tree, temporary_tree


class RemoveTreeTests(unittest.TestCase):
    def test_retries_a_sharing_error_twice_then_succeeds(self) -> None:
        calls: list[object] = []
        sleeps: list[float] = []

        def remover(path) -> None:
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError(errno.EACCES, "the file is in use")

        remove_tree(
            "tree",
            attempts=5,
            delay=0.01,
            sleep=sleeps.append,
            remover=remover,
        )

        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.01, 0.01])

    def test_a_persistent_sharing_error_is_raised_after_the_last_attempt(self) -> None:
        calls: list[object] = []

        def remover(path) -> None:
            calls.append(path)
            raise PermissionError(errno.EACCES, "still open")

        with self.assertRaises(PermissionError):
            remove_tree(
                "tree",
                attempts=3,
                delay=0,
                sleep=lambda _seconds: None,
                remover=remover,
            )

        self.assertEqual(len(calls), 3)

    def test_a_non_transient_error_is_not_retried(self) -> None:
        calls: list[object] = []
        sleeps: list[float] = []

        def remover(path) -> None:
            calls.append(path)
            raise OSError(errno.ENOTDIR, "not a directory")

        with self.assertRaises(OSError):
            remove_tree(
                "tree",
                attempts=5,
                delay=0.01,
                sleep=sleeps.append,
                remover=remover,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_a_missing_tree_is_already_removed(self) -> None:
        calls: list[object] = []

        def remover(path) -> None:
            calls.append(path)
            raise FileNotFoundError(errno.ENOENT, "gone")

        remove_tree("tree", remover=remover)

        self.assertEqual(len(calls), 1)

    def test_the_context_manager_removes_a_real_tree(self) -> None:
        with temporary_tree() as root:
            nested = root / "sessions"
            nested.mkdir()
            (nested / "s.events.jsonl").write_text("{}\n", encoding="utf-8")
            path = root

        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
