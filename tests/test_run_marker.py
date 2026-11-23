"""The run marker: what it records, and what the transcript says without one."""

from __future__ import annotations

import unittest

from noteagent.agent.run_marker import (
    CANCELLED,
    FAILED,
    FINISHED,
    INTERRUPTED,
    RUNNING,
    RunFingerprint,
    RunMarker,
    infer_terminal,
    marker_entry,
    markers_by_run,
    parse_marker,
)


def _user(entry_id: str = "e-u", content: str = "什么是熵？") -> dict[str, object]:
    return {
        "type": "message",
        "id": entry_id,
        "role": "user",
        "content": content,
    }


def _assistant(
    entry_id: str = "e-a",
    *,
    content: str = "熵衡量不确定性。",
    tool_calls: list[dict[str, object]] | None = None,
    stop_reason: str = "stop",
) -> dict[str, object]:
    entry: dict[str, object] = {
        "type": "message",
        "id": entry_id,
        "role": "assistant",
        "content": content,
        "stopReason": stop_reason,
    }
    if tool_calls is not None:
        entry["toolCalls"] = tool_calls
    return entry


def _tool_result(entry_id: str = "e-t") -> dict[str, object]:
    return {
        "type": "message",
        "id": entry_id,
        "role": "toolResult",
        "toolCallId": "c1",
        "name": "wiki_read",
        "content": "[[concepts/entropy]] — 熵",
    }


class MarkerEntryTests(unittest.TestCase):
    def test_a_marker_round_trips_and_keeps_no_content(self) -> None:
        marker = RunMarker(
            run_id="run-1",
            state=RUNNING,
            last_entry_id="e-u",
            fingerprint=RunFingerprint(
                model="faux",
                toolset="tools:abc",
                config_hash="cfg",
                project_root="/project",
                wiki_generation="wiki-gen",
                source_fingerprint="src-gen",
            ),
            resumes="run-0",
            note="resume from the previous run",
            reason="tool call without result",
            created_at="2026-11-18T09:00:00Z",
        )

        entry = marker_entry(marker)

        self.assertEqual(entry["type"], "run_marker")
        self.assertEqual(entry["runId"], "run-1")
        self.assertEqual(entry["state"], RUNNING)
        self.assertEqual(entry["lastEntryId"], "e-u")
        self.assertNotIn("content", entry)

        parsed = parse_marker({**entry, "id": "e-m", "ts": "..."})

        assert parsed is not None
        self.assertEqual(parsed.run_id, "run-1")
        self.assertEqual(parsed.state, RUNNING)
        self.assertEqual(parsed.last_entry_id, "e-u")
        self.assertEqual(parsed.fingerprint.model, "faux")
        self.assertEqual(parsed.fingerprint.toolset, "tools:abc")
        self.assertEqual(parsed.fingerprint.config_hash, "cfg")
        self.assertEqual(parsed.fingerprint.project_root, "/project")
        self.assertEqual(parsed.fingerprint.wiki_generation, "wiki-gen")
        self.assertEqual(parsed.fingerprint.source_fingerprint, "src-gen")
        self.assertEqual(parsed.resumes, "run-0")
        self.assertEqual(parsed.note, "resume from the previous run")
        self.assertEqual(parsed.reason, "tool call without result")
        self.assertEqual(parsed.entry_id, "e-m")

    def test_a_malformed_marker_is_ignored(self) -> None:
        self.assertIsNone(parse_marker({"type": "run_marker"}))
        self.assertIsNone(parse_marker({"type": "run_marker", "runId": "run-1"}))
        self.assertIsNone(parse_marker({"type": "message", "runId": "run-1"}))

    def test_the_latest_marker_per_run_wins(self) -> None:
        entries = [
            marker_entry(RunMarker("run-1", RUNNING)),
            marker_entry(RunMarker("run-2", RUNNING)),
            marker_entry(RunMarker("run-1", FINISHED)),
        ]

        markers = markers_by_run(entries)

        self.assertEqual(markers["run-1"].state, FINISHED)
        self.assertEqual(markers["run-2"].state, RUNNING)


class TerminalInferenceTests(unittest.TestCase):
    def test_a_final_answer_is_finished(self) -> None:
        inferred = infer_terminal([_user(), _assistant()])

        self.assertEqual(inferred.state, FINISHED)
        self.assertEqual(inferred.last_entry_id, "e-a")

    def test_a_tool_call_without_its_result_is_interrupted(self) -> None:
        inferred = infer_terminal(
            [_user(), _assistant(tool_calls=[{"id": "c1", "name": "wiki_read"}])]
        )

        self.assertEqual(inferred.state, INTERRUPTED)
        self.assertEqual(inferred.last_entry_id, "e-a")

    def test_a_tool_result_is_interrupted_until_the_next_turn(self) -> None:
        inferred = infer_terminal(
            [
                _user(),
                _assistant(tool_calls=[{"id": "c1", "name": "wiki_read"}]),
                _tool_result(),
            ]
        )

        self.assertEqual(inferred.state, INTERRUPTED)
        self.assertEqual(inferred.last_entry_id, "e-t")

    def test_an_aborted_answer_is_cancelled(self) -> None:
        inferred = infer_terminal([_user(), _assistant(stop_reason="aborted")])

        self.assertEqual(inferred.state, CANCELLED)

    def test_an_incomplete_answer_is_failed(self) -> None:
        inferred = infer_terminal(
            [_user(), _assistant(stop_reason="incomplete")]
        )

        self.assertEqual(inferred.state, FAILED)

    def test_a_terminal_marker_is_trusted(self) -> None:
        inferred = infer_terminal(
            [
                _user(),
                _assistant(),
                marker_entry(RunMarker("run-1", FINISHED, last_entry_id="e-a")),
            ]
        )

        self.assertEqual(inferred.state, FINISHED)
        self.assertEqual(inferred.last_entry_id, "e-a")

    def test_a_question_without_an_answer_is_interrupted(self) -> None:
        inferred = infer_terminal([_user()])

        self.assertEqual(inferred.state, INTERRUPTED)

    def test_a_new_question_after_a_finished_marker_is_interrupted(self) -> None:
        inferred = infer_terminal(
            [
                _user("e-u1"),
                _assistant("e-a1"),
                marker_entry(RunMarker("run-1", FINISHED, last_entry_id="e-a1")),
                _user("e-u2", "第二个问题"),
            ]
        )

        self.assertEqual(inferred.state, INTERRUPTED)
        self.assertEqual(inferred.last_entry_id, "e-u2")

    def test_an_old_session_without_markers_is_read_from_its_shape(self) -> None:
        self.assertEqual(markers_by_run([_user(), _assistant()]), {})
        self.assertEqual(
            infer_terminal([_user(), _assistant()]).state, FINISHED
        )

    def test_an_empty_session_is_interrupted(self) -> None:
        inferred = infer_terminal([])

        self.assertEqual(inferred.state, INTERRUPTED)


class FingerprintComparisonTests(unittest.TestCase):
    def test_a_changed_model_is_refused(self) -> None:
        saved = RunFingerprint(model="faux")
        current = RunFingerprint(model="other")

        self.assertEqual(
            saved.refusal(current),
            "the model changed since this run paused",
        )

    def test_a_legacy_sections_only_config_is_accepted(self) -> None:
        saved = RunFingerprint(config_hash="sections-digest")
        current = RunFingerprint(config_hash="settings-and-sections")

        self.assertEqual(
            saved.refusal(current),
            "the config changed since this run paused",
        )
        self.assertEqual(
            saved.refusal(current, legacy_config="sections-digest"), ""
        )

    def test_a_changed_wiki_is_stale_not_refused(self) -> None:
        saved = RunFingerprint(
            wiki_generation="wiki-old",
            source_fingerprint="sources-old",
        )
        current = RunFingerprint(
            wiki_generation="wiki-new",
            source_fingerprint="sources-new",
        )

        self.assertEqual(saved.refusal(current), "")
        self.assertEqual(saved.stale(current), ("wiki", "sources"))


if __name__ == "__main__":
    unittest.main()
