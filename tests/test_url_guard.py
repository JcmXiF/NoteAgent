"""The web fetch layer only reaches public addresses.

A local app still holds a browser's power: a page it is told to read may point
at the machine itself, at the home router, or at a cloud metadata address. The
guard is the one place that decides what "public" means, and every request the
fetch layer makes passes through it.
"""

from __future__ import annotations

import socket
import unittest

import httpx

from noteagent.acquire.tools.url_guard import (
    UnsafeURL,
    check_public_url,
    guarded_get,
    resolve_public_url,
)


def _resolver(*addresses: str):
    def resolve(host: str, port: int):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return resolve


class SchemeAndHostTests(unittest.TestCase):
    def test_only_http_and_https_are_fetched(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/a",
            "data:text/html,<p>x</p>",
            "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeURL) as caught:
                    check_public_url(url)
                self.assertEqual(caught.exception.reason, "blocked_scheme")

    def test_a_url_with_credentials_is_refused(self) -> None:
        with self.assertRaises(UnsafeURL) as caught:
            check_public_url("https://user:secret@example.com/page")

        self.assertEqual(caught.exception.reason, "credentials")

    def test_localhost_and_its_aliases_are_refused(self) -> None:
        for url in (
            "http://localhost:8765/api/v1/sessions",
            "http://LOCALHOST./x",
            "http://notes.localhost/x",
            "http://printer.local/x",
            "http://metadata/x",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeURL):
                    check_public_url(url)

    def test_loopback_and_private_literals_are_refused(self) -> None:
        for url in (
            "http://127.0.0.1/",
            "http://127.10.20.30/",
            "http://10.0.0.1/",
            "http://172.16.4.5/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[fc00::1]/",
            "http://[::ffff:127.0.0.1]/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeURL) as caught:
                    check_public_url(url)
                self.assertEqual(caught.exception.reason, "blocked_address")

    def test_a_public_literal_is_accepted_without_dns(self) -> None:
        self.assertEqual(
            check_public_url("https://93.184.216.34/page"),
            "https://93.184.216.34/page",
        )

    def test_an_invalid_port_is_refused_rather_than_raised_through(self) -> None:
        with self.assertRaises(UnsafeURL) as caught:
            resolve_public_url(
                "https://example.com:99999/",
                resolver=_resolver("93.184.216.34"),
            )

        self.assertEqual(caught.exception.reason, "invalid_url")


class ResolutionTests(unittest.TestCase):
    def test_a_name_that_resolves_to_a_private_address_is_refused(self) -> None:
        with self.assertRaises(UnsafeURL) as caught:
            resolve_public_url(
                "https://notes.example.com/",
                resolver=_resolver("192.168.1.10"),
            )

        self.assertEqual(caught.exception.reason, "blocked_address")

    def test_one_private_answer_among_public_ones_is_enough_to_refuse(self) -> None:
        with self.assertRaises(UnsafeURL):
            resolve_public_url(
                "https://notes.example.com/",
                resolver=_resolver("93.184.216.34", "10.0.0.7"),
            )

    def test_a_name_that_resolves_publicly_is_accepted(self) -> None:
        self.assertEqual(
            resolve_public_url(
                "https://notes.example.com/",
                resolver=_resolver("93.184.216.34"),
            ),
            "https://notes.example.com/",
        )

    def test_a_name_that_does_not_resolve_is_left_for_the_request_to_fail(self) -> None:
        def resolve(_host: str, _port: int):
            raise socket.gaierror("no such host")

        self.assertEqual(
            resolve_public_url("https://nowhere.example/", resolver=resolve),
            "https://nowhere.example/",
        )


class RedirectTests(unittest.TestCase):
    def test_a_redirect_to_a_private_address_is_blocked_before_it_is_fetched(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                302,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaises(UnsafeURL):
            guarded_get(client, "https://example.com/redirect")

        self.assertEqual(seen, ["https://example.com/redirect"])

    def test_a_public_redirect_is_followed(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if str(request.url).endswith("/old"):
                return httpx.Response(
                    301, headers={"location": "/new"}
                )
            return httpx.Response(200, text="<html><body>ok</body></html>")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        response = guarded_get(client, "https://example.com/old")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            seen, ["https://example.com/old", "https://example.com/new"]
        )

    def test_a_redirect_loop_stops(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "/again"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaises(UnsafeURL) as caught:
            guarded_get(client, "https://example.com/loop", max_redirects=2)

        self.assertEqual(caught.exception.reason, "too_many_redirects")


if __name__ == "__main__":
    unittest.main()
