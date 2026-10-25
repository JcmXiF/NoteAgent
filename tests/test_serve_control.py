from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from noteagent.api import serve_control
from noteagent.api.serve_control import (
    AlreadyServing,
    PortBusy,
    ServerRecord,
    bind_listener,
    clear_record,
    parse_listening_pids,
    prune_records,
    read_record,
    read_records,
    record_path,
    stop_server,
    write_record,
)

REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _take_port(port: int) -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen(8)
    return sock


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = serve_control.records_dir
        self.dir = Path(tempfile.mkdtemp(prefix="noteagent-records-"))
        serve_control.records_dir = lambda: self.dir

    def tearDown(self) -> None:
        serve_control.records_dir = self.previous

    def test_a_record_says_which_port_was_taken_and_which_was_asked_for(self) -> None:
        record = ServerRecord(pid=4242, host="127.0.0.1", port=8791, requested_port=8765)
        write_record(record)

        read = read_record(4242)

        self.assertEqual(read, record)
        self.assertEqual(read.url, "http://127.0.0.1:8791")
        self.assertEqual(json.loads(record_path(4242).read_text(encoding="utf-8"))["port"], 8791)

    def test_a_missing_record_is_not_an_error(self) -> None:
        self.assertIsNone(read_record(999999))
        self.assertEqual(read_records(), [])

    def test_a_damaged_record_is_ignored(self) -> None:
        # A half-written file must not be mistaken for a running server.
        path = record_path(4243)
        path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(read_record(4243))
        self.assertEqual(read_records(), [])

    def test_a_record_that_names_no_port_is_ignored(self) -> None:
        record_path(4244).write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")

        self.assertIsNone(read_record(4244))

    def test_a_record_filed_under_another_process_is_ignored(self) -> None:
        write_record(ServerRecord(pid=4245, host="127.0.0.1", port=8791, requested_port=8765))
        record_path(4245).write_text(
            json.dumps({"pid": 1, "host": "127.0.0.1", "port": 8791}), encoding="utf-8"
        )

        self.assertIsNone(read_record(4245))

    def test_records_are_listed_newest_first(self) -> None:
        write_record(
            ServerRecord(pid=4246, host="127.0.0.1", port=8791, requested_port=8765, started=1.0)
        )
        write_record(
            ServerRecord(pid=4247, host="127.0.0.1", port=8792, requested_port=8765, started=2.0)
        )

        self.assertEqual([record.pid for record in read_records()], [4247, 4246])

    def test_clearing_a_record_leaves_nothing_behind(self) -> None:
        write_record(ServerRecord(pid=4248, host="127.0.0.1", port=8791, requested_port=8765))

        clear_record(4248)

        self.assertFalse(record_path(4248).exists())

    def test_a_record_left_by_a_killed_process_is_swept_up(self) -> None:
        # A killed process never removes its own record; nothing else may read it
        # as a running server.
        write_record(ServerRecord(pid=999999, host="127.0.0.1", port=8791, requested_port=8765))
        write_record(
            ServerRecord(pid=os.getpid(), host="127.0.0.1", port=8792, requested_port=8792)
        )

        prune_records()

        self.assertFalse(record_path(999999).exists())
        self.assertTrue(record_path(os.getpid()).exists())


class PortTests(unittest.TestCase):
    def test_a_free_port_is_taken_as_asked(self) -> None:
        port = _free_port()

        sock = bind_listener("127.0.0.1", port)
        try:
            self.assertEqual(sock.getsockname()[1], port)
        finally:
            sock.close()

    def test_a_taken_port_is_reported_with_its_owner(self) -> None:
        port = _free_port()
        squatter = _take_port(port)
        try:
            with self.assertRaises(PortBusy) as caught:
                bind_listener("127.0.0.1", port)
        finally:
            squatter.close()

        # The holder is this test process; it does not look like a noteagent
        # server, so this is "somebody else has the port", not "already running".
        self.assertEqual(caught.exception.port, port)
        if caught.exception.pids:
            self.assertIn(os.getpid(), caught.exception.pids)

    def test_a_taken_port_falls_back_to_a_free_one(self) -> None:
        port = _free_port()
        squatter = _take_port(port)
        try:
            sock = bind_listener("127.0.0.1", port, fallback=True)
            try:
                self.assertNotEqual(sock.getsockname()[1], port)
            finally:
                sock.close()
        finally:
            squatter.close()

    def test_falling_back_twice_does_not_reuse_the_held_port(self) -> None:
        # Two servers may start while one preferred port is held; each must get
        # its own free port.
        port = _free_port()
        squatter = _take_port(port)
        try:
            first = bind_listener("127.0.0.1", port, fallback=True)
            second = bind_listener("127.0.0.1", port, fallback=True)
            try:
                self.assertNotEqual(
                    first.getsockname()[1],
                    second.getsockname()[1],
                )
            finally:
                first.close()
                second.close()
        finally:
            squatter.close()


class AlreadyServingTests(unittest.TestCase):
    def test_a_noteagent_holder_means_already_serving(self) -> None:
        port = _free_port()
        squatter = _take_port(port)
        previous = serve_control.looks_like_noteagent
        previous_listeners = serve_control.pids_listening
        serve_control.pids_listening = lambda _port: [1234]
        serve_control.looks_like_noteagent = lambda _pid: True
        try:
            with self.assertRaises(AlreadyServing) as caught:
                bind_listener("127.0.0.1", port)
        finally:
            serve_control.looks_like_noteagent = previous
            serve_control.pids_listening = previous_listeners
            squatter.close()

        self.assertEqual(caught.exception.pids, [1234])

    def test_falling_back_does_not_ask_who_holds_the_port(self) -> None:
        port = _free_port()
        squatter = _take_port(port)
        previous = serve_control.looks_like_noteagent

        def explode(_pid: int) -> bool:
            raise AssertionError("who holds the port does not matter when falling back")

        serve_control.looks_like_noteagent = explode
        try:
            sock = bind_listener("127.0.0.1", port, fallback=True)
            sock.close()
        finally:
            serve_control.looks_like_noteagent = previous
            squatter.close()


class RecordedServerTests(unittest.TestCase):
    """`status` and `stop` name the port the caller wants, not the one taken."""

    def setUp(self) -> None:
        self.previous_dir = serve_control.records_dir
        self.previous_alive = serve_control.process_alive
        self.previous_listeners = serve_control.pids_listening
        self.previous_terminate = serve_control.terminate_pid
        self.previous_looks = serve_control.looks_like_noteagent
        self.dir = Path(tempfile.mkdtemp(prefix="noteagent-status-"))
        serve_control.records_dir = lambda: self.dir
        serve_control.process_alive = lambda _pid: True
        serve_control.pids_listening = lambda _port: []
        # A record is only a claim; stopping still asks what the process is, in
        # case its pid was reused by something else.
        serve_control.looks_like_noteagent = lambda _pid: True
        self.killed: list[int] = []
        serve_control.terminate_pid = self.killed.append

    def tearDown(self) -> None:
        serve_control.records_dir = self.previous_dir
        serve_control.process_alive = self.previous_alive
        serve_control.pids_listening = self.previous_listeners
        serve_control.terminate_pid = self.previous_terminate
        serve_control.looks_like_noteagent = self.previous_looks

    def _record(self, pid: int, port: int, requested: int, host: str = "127.0.0.1") -> None:
        write_record(
            ServerRecord(
                pid=pid, host=host, port=port, requested_port=requested, started=1.0
            )
        )

    def test_status_finds_a_server_that_had_to_take_another_port(self) -> None:
        self._record(4301, port=4975, requested=8765)

        code, message = serve_control.status_server("127.0.0.1", 8765)

        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:4975", message)
        self.assertIn("port 8765 was taken", message)

    def test_status_does_not_claim_a_server_on_another_preferred_port(self) -> None:
        self._record(4302, port=9001, requested=9000)

        code, message = serve_control.status_server("127.0.0.1", 8765)

        self.assertEqual(code, 1)
        self.assertIn("running=(none)", message)

    def test_status_finds_a_server_started_under_the_other_loopback_name(self) -> None:
        self._record(4303, port=4975, requested=8765, host="127.0.0.1")

        code, message = serve_control.status_server("localhost", 8765)

        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:4975", message)

    def test_stop_stops_the_server_that_took_another_port(self) -> None:
        self._record(4304, port=4975, requested=8765)

        code, message = serve_control.stop_server("127.0.0.1", 8765)

        self.assertEqual(code, 0)
        self.assertEqual(self.killed, [4304])
        self.assertIn("stopped pid 4304", message)
        self.assertFalse(record_path(4304).exists())

    def test_stop_leaves_a_server_on_another_preferred_port_alone(self) -> None:
        self._record(4305, port=9001, requested=9000)

        code, message = serve_control.stop_server("127.0.0.1", 8765)

        self.assertEqual(code, 0)
        self.assertEqual(self.killed, [])
        self.assertIn("no noteagent serve", message)
        self.assertTrue(record_path(4305).exists())

    def test_a_record_left_by_a_dead_process_is_not_stopped(self) -> None:
        self._record(4306, port=4975, requested=8765)
        serve_control.process_alive = lambda _pid: False

        code, message = serve_control.status_server("127.0.0.1", 8765)

        self.assertEqual(code, 1)
        self.assertEqual(self.killed, [])
        self.assertFalse(record_path(4306).exists())


class ProcessesTests(unittest.TestCase):
    def test_parse_windows_netstat_listening_pid(self) -> None:
        text = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       35668
  TCP    127.0.0.1:87650        0.0.0.0:0              LISTENING       99
"""
        self.assertEqual(parse_listening_pids(text, 8765), [35668])

    def test_stop_when_nothing_running(self) -> None:
        code, message = stop_server("127.0.0.1", _free_port())

        self.assertEqual(code, 0)
        self.assertIn("no noteagent serve", message)

    def test_starting_on_a_held_port_stops_with_a_readable_reason(self) -> None:
        from noteagent.api.__main__ import main as api_main

        port = _free_port()
        squatter = _take_port(port)
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = api_main(["--host", "127.0.0.1", "--port", str(port)])
        finally:
            squatter.close()

        self.assertEqual(code, 1)
        self.assertIn("already in use", stderr.getvalue())
        self.assertIn("--port", stderr.getvalue())


class ServerStartTests(unittest.TestCase):
    """The core, started for real, on a port somebody else already holds."""

    def setUp(self) -> None:
        self.held = _free_port()
        self.squatter = _take_port(self.held)
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO_SRC
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "noteagent.api",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.held),
                "--fallback-port",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )

    def tearDown(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        if self.process.stdout is not None:
            self.process.stdout.close()
        clear_record(self.process.pid)
        self.squatter.close()

    def _record(self) -> ServerRecord | None:
        deadline = time.time() + 25
        while time.time() < deadline:
            record = read_record(self.process.pid)
            if record is not None:
                return record
            if self.process.poll() is not None:
                return None
            time.sleep(0.1)
        return None

    def test_it_serves_on_a_free_port_and_says_which(self) -> None:
        record = self._record()

        self.assertIsNotNone(record, "the core never wrote down where it listens")
        assert record is not None
        self.assertEqual(record.requested_port, self.held)
        self.assertNotEqual(record.port, self.held)

        # Readiness is the health endpoint, which is what the shell waits for.
        deadline = time.time() + 20
        body = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{record.url}/api/health", timeout=2) as response:
                    body = response.read().decode("utf-8")
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        self.assertIn('"ok"', body)


if __name__ == "__main__":
    unittest.main()
