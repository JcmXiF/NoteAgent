from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from noteagent.api.app import create_app
from noteagent.api.guard import LocalOnly

BOUND = "http://127.0.0.1:8791"
BOUND_PORT = 8791
# Any write endpoint will do; an empty body is refused by validation, which is a
# pass through the guard and therefore what "allowed" looks like.
WRITE = "/api/acquire/save"


def _client(base_url: str = BOUND) -> TestClient:
    return TestClient(create_app(), base_url=base_url)


class HostTests(unittest.TestCase):
    def test_a_name_that_is_not_this_machine_is_refused(self) -> None:
        # The rebinding case: the page's own name still arrives in Host.
        response = _client().get("/api/health", headers={"Host": "notes.example.com"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "request.untrustedHost")

    def test_a_request_without_a_host_is_refused(self) -> None:
        # Only hand-written requests omit it; an HTTP/1.1 client always sends one.
        response = _client().get("/api/health", headers={"Host": ""})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "request.untrustedHost")

    def test_a_host_that_is_not_a_name_is_refused(self) -> None:
        response = _client().get("/api/health", headers={"Host": "a b c"})

        self.assertEqual(response.status_code, 403)

    def test_a_name_that_only_looks_like_a_loopback_is_refused(self) -> None:
        response = _client().get("/api/health", headers={"Host": "127.0.0.1.evil.test"})

        self.assertEqual(response.status_code, 403)

    def test_the_loopback_forms_are_accepted(self) -> None:
        client = _client()
        for host in ("127.0.0.1:8791", "localhost:8791", "[::1]:8791"):
            with self.subTest(host=host):
                self.assertEqual(
                    client.get("/api/health", headers={"Host": host}).status_code,
                    200,
                )


class OriginTests(unittest.TestCase):
    def test_a_write_from_another_site_is_refused(self) -> None:
        response = _client().post(
            WRITE, json={}, headers={"Origin": "https://notes.example.com"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "request.untrustedOrigin")

    def test_a_write_from_a_page_that_says_null_is_refused(self) -> None:
        # A sandboxed frame and a file opened from disk both say "null".
        response = _client().post(WRITE, json={}, headers={"Origin": "null"})

        self.assertEqual(response.status_code, 403)

    def test_a_write_from_another_port_on_this_machine_is_refused(self) -> None:
        response = _client().post(
            WRITE, json={}, headers={"Origin": "http://127.0.0.1:6000"}
        )

        self.assertEqual(response.status_code, 403)

    def test_a_write_from_the_served_interface_is_accepted(self) -> None:
        response = _client().post(
            WRITE, json={}, headers={"Origin": f"http://127.0.0.1:{BOUND_PORT}"}
        )

        self.assertNotEqual(response.status_code, 403)

    def test_a_write_from_the_development_interface_is_accepted(self) -> None:
        # The development server forwards /api, so its pages are ours too.
        response = _client().post(
            WRITE, json={}, headers={"Origin": "http://127.0.0.1:5174"}
        )

        self.assertNotEqual(response.status_code, 403)

    def test_another_name_for_this_machine_on_the_same_port_is_accepted(self) -> None:
        # localhost and 127.0.0.1 are the same place; the port is what decides.
        response = _client().post(
            WRITE, json={}, headers={"Origin": f"http://localhost:{BOUND_PORT}"}
        )

        self.assertNotEqual(response.status_code, 403)

    def test_a_read_from_another_site_is_refused_but_a_plain_read_is_not(self) -> None:
        client = _client()

        self.assertEqual(
            client.get("/api/health", headers={"Origin": "https://elsewhere.test"}).status_code,
            403,
        )
        self.assertEqual(client.get("/api/health").status_code, 200)


class NoOriginTests(unittest.TestCase):
    def test_a_write_without_an_origin_is_let_through(self) -> None:
        # No browser marks a page's request this way; tools and the test client do.
        response = _client().post(WRITE, json={})

        self.assertNotEqual(response.status_code, 403)


class WebsocketTests(unittest.IsolatedAsyncioTestCase):
    """A handshake carries the same two headers, so it gets the same check."""

    @staticmethod
    async def _call(origin: str | None) -> tuple[bool, list[dict]]:
        reached = False
        sent: list[dict] = []

        async def app(_scope, _receive, _send) -> None:
            nonlocal reached
            reached = True

        async def send(message: dict) -> None:
            sent.append(message)

        async def receive() -> dict:
            return {"type": "websocket.connect"}

        headers = [(b"host", b"127.0.0.1:8791")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        scope = {
            "type": "websocket",
            "server": ("127.0.0.1", 8791),
            "headers": headers,
        }
        await LocalOnly(app)(scope, receive, send)
        return reached, sent

    async def test_a_handshake_from_another_site_never_reaches_the_app(self) -> None:
        reached, sent = await self._call("https://notes.example.com")

        self.assertFalse(reached)
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    async def test_a_handshake_from_our_interface_reaches_the_app(self) -> None:
        reached, sent = await self._call("http://127.0.0.1:8791")

        self.assertTrue(reached)
        self.assertEqual(sent, [])

    async def test_a_handshake_with_a_foreign_host_never_reaches_the_app(self) -> None:
        reached = False
        sent: list[dict] = []

        async def app(_scope, _receive, _send) -> None:
            nonlocal reached
            reached = True

        async def send(message: dict) -> None:
            sent.append(message)

        async def receive() -> dict:
            return {"type": "websocket.connect"}

        await LocalOnly(app)(
            {
                "type": "websocket",
                "server": ("127.0.0.1", 8791),
                "headers": [(b"host", b"notes.example.com")],
            },
            receive,
            send,
        )

        self.assertFalse(reached)
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])


class OtherProtocolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_is_not_a_request_and_is_left_alone(self) -> None:
        reached = False

        async def app(_scope, _receive, _send) -> None:
            nonlocal reached
            reached = True

        async def send(_message: dict) -> None:
            raise AssertionError("nothing should be sent")

        async def receive() -> dict:
            return {"type": "lifespan.startup"}

        await LocalOnly(app)({"type": "lifespan", "headers": []}, receive, send)

        self.assertTrue(reached)


class RefusedBeforeTheRouteTests(unittest.TestCase):
    def test_a_refused_write_does_not_reach_the_route(self) -> None:
        import noteagent.api.projects as projects_mod

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                response = _client().post(
                    "/api/projects",
                    json={"name": "sneaky"},
                    headers={"Origin": "https://notes.example.com"},
                )

                self.assertEqual(response.status_code, 403)
                self.assertFalse((repo / "projects" / "sneaky").exists())


if __name__ == "__main__":
    unittest.main()
