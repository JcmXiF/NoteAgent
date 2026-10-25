from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from noteagent.acquire.quality import assess_capture
from noteagent.acquire.run_fetch import run_rendered_html, run_web_fetch
from noteagent.acquire.tools.fetchers.html import MAX_HTML_BYTES, html_to_markdown
from noteagent.acquire.tools.web_fetch import capture_rendered_html, web_fetch
from noteagent.acquire.write_raw import raw_relative_path_for_url, write_raw_captures
from noteagent.api.fetch import capture_to_dict
from noteagent.project.init import init_project
from noteagent.util.run_log import RunLogger, run_log_path

try:
    from pdf_fixtures import HELLO_PDF
except ImportError:
    from tests.pdf_fixtures import HELLO_PDF


ARTICLE_HTML = """
<html>
<head><title>相对熵 - 维基百科</title></head>
<body>
<nav>
<p>22种语言</p>
<ul><li><a href="/wiki/Special:Lang">العربية</a></li></ul>
<button>开关目录</button>
</nav>
<article>
<h1>相对熵</h1>
<p>KL散度（Kullback-Leibler divergence）是两个概率分布之间的差异度量。</p>
<p>在信息论中也称为相对熵。它广泛用于机器学习中的变分推断。</p>
<pre><code>def kl(p, q):
    return sum(p * log(p / q))
</code></pre>
<p>更多说明见百科正文，用于保证抽取长度超过质量门阈值。""" + ("补充段落。" * 40) + """</p>
</article>
<footer>隐私政策 · 关于维基百科</footer>
</body>
</html>
"""

LOGIN_HTML = """
<html><head><title>登录</title></head>
<body><p>请登录后查看全文</p><p>验证码</p></body></html>
"""


class QualityGateTests(unittest.TestCase):
    def test_empty(self) -> None:
        fidelity, reasons = assess_capture("")
        self.assertEqual(fidelity, "failed")
        self.assertEqual(reasons, ("empty",))

    def test_too_short(self) -> None:
        fidelity, reasons = assess_capture("太短了")
        self.assertEqual(fidelity, "failed")
        self.assertEqual(reasons, ("too_short",))

    def test_ok_article(self) -> None:
        _, markdown = html_to_markdown(ARTICLE_HTML)
        fidelity, reasons = assess_capture(markdown)
        self.assertEqual(fidelity, "ok")
        self.assertEqual(reasons, ())

    def test_truncated_is_low(self) -> None:
        fidelity, reasons = assess_capture("正文内容。" * 80, truncated=True)
        self.assertEqual(fidelity, "low")
        self.assertEqual(reasons, ("truncated",))

    def test_a_skip_reason_stays_a_code(self) -> None:
        """The interface words it; the core only names what happened."""
        fidelity, reasons = assess_capture("", skip_reason="http_forbidden")
        self.assertEqual(fidelity, "failed")
        self.assertEqual(reasons, ("http_forbidden",))


class WebFetchTests(unittest.TestCase):
    def test_html_extract_strips_chrome(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=ARTICLE_HTML, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch(
            "https://zh.wikipedia.org/wiki/KL",
            client=client,
        )
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, "trafilatura-html")
        self.assertIn("kl", capture.markdown.casefold())
        self.assertIn("def kl", capture.markdown)
        self.assertNotIn("22种语言", capture.markdown)
        self.assertIn("data-na-block", capture.html)
        self.assertIn("def kl", capture.html)
        self.assertNotIn("22种语言", capture.html)
        self.assertNotIn("<script", capture.html.lower())
        payload = capture_to_dict(capture)
        ids = [item["id"] for item in payload["blocks"]]
        self.assertTrue(ids)
        self.assertTrue(all(item.startswith("b") for item in ids))

    def test_login_page_fails_as_empty_or_short(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=LOGIN_HTML, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/login", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertTrue(set(capture.reasons) <= {"empty", "too_short"})

    def test_http_forbidden_skips_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                text="<html><body>请登录后查看全文 验证码</body></html>",
                headers={"content-type": "text/html"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://zhuanlan.zhihu.com/p/1", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("http_forbidden",))
        self.assertEqual(capture.markdown, "")
        self.assertEqual(capture.http_status, 403)

    def test_http_unauthorized_skips_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="auth", headers={"content-type": "text/html"})

        capture = web_fetch(
            "https://example.com/private",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(capture.reasons, ("http_unauthorized",))
        self.assertEqual(capture.http_status, 401)
        self.assertEqual(capture.markdown, "")

    def test_http_rate_limited_skips_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="slow down", headers={"content-type": "text/html"})

        capture = web_fetch(
            "https://example.com/hot",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(capture.reasons, ("http_rate_limited",))
        self.assertEqual(capture.http_status, 429)
        self.assertEqual(capture.markdown, "")

    def test_http_not_modified_skips_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                304,
                text="<html><body>" + ("不是正文。" * 80) + "</body></html>",
                headers={"content-type": "text/html"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/page", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("http_error",))
        self.assertEqual(capture.markdown, "")

    def test_http_not_found_skips_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                text="<html><body>" + ("页面不存在。" * 80) + "</body></html>",
                headers={"content-type": "text/html"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/missing", client=client)
        self.assertEqual(capture.reasons, ("http_error",))
        self.assertEqual(capture.markdown, "")

    def test_pdf_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=HELLO_PDF,
                headers={"content-type": "application/pdf"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper", client=client)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.kind, "pdf")
        self.assertIn("Hello PDF", capture.markdown)

    def test_video_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"\x00\x00",
                headers={"content-type": "video/mp4"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/watch", client=client)
        self.assertEqual(capture.reasons, ("skipped_video",))

    def test_inlines_image_with_referer(self) -> None:
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
        seen_referer: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".png"):
                seen_referer.append(request.headers.get("referer", ""))
                return httpx.Response(
                    200,
                    content=png,
                    headers={"content-type": "image/png"},
                )
            page = ARTICLE_HTML.replace(
                "<h1>相对熵</h1>",
                '<h1>相对熵</h1><p><img src="https://cdn.example.com/eq.png" alt=""></p>',
            )
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://zh.wikipedia.org/wiki/KL", client=client)
        self.assertEqual(capture.fidelity, "ok")
        self.assertGreaterEqual(capture.image_count, 1)
        self.assertEqual(capture.images_failed, 0)
        self.assertIn("data:image/png;base64,", capture.html)
        self.assertTrue(any("wikipedia.org" in item for item in seen_referer))

    def test_missing_image_is_low(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".png"):
                return httpx.Response(403, text="denied")
            page = ARTICLE_HTML.replace(
                "<h1>相对熵</h1>",
                '<h1>相对熵</h1><p><img src="https://cdn.example.com/eq.png"></p>',
            )
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://zh.wikipedia.org/wiki/KL", client=client)
        self.assertEqual(capture.fidelity, "low")
        self.assertIn("missing_images", capture.reasons)
        self.assertIn('data-na-asset="missing"', capture.html)

    def test_run_log_omits_markdown(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=ARTICLE_HTML, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            run_web_fetch(
                "https://zh.wikipedia.org/wiki/KL",
                query="KL散度",
                client=client,
                run_logger=logger,
                caller="cli",
            )
            blob = run_log_path(layout).read_text(encoding="utf-8")
        events = [json.loads(line) for line in blob.splitlines() if line.strip()]
        self.assertEqual([event["event"] for event in events], ["fetch.start", "fetch.end"])
        self.assertEqual(events[1]["fidelity"], "ok")
        self.assertEqual(events[1]["caller"], "cli")
        self.assertNotIn("变分推断", blob)
        self.assertNotIn("def kl", blob)
        self.assertNotIn("data:image", blob)


class RenderedHtmlTests(unittest.TestCase):
    def test_same_blocks_as_public_fetch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=ARTICLE_HTML, headers={"content-type": "text/html"})

        url = "https://zh.wikipedia.org/wiki/KL"
        fetched = web_fetch(url, client=httpx.Client(transport=httpx.MockTransport(handler)))
        rendered = capture_rendered_html(url, ARTICLE_HTML)
        self.assertEqual(rendered.fidelity, "ok")
        self.assertEqual(rendered.capture_method, "rendered-html")
        self.assertIsNone(rendered.http_status)
        self.assertEqual(
            [item["id"] for item in capture_to_dict(fetched)["blocks"]],
            [item["id"] for item in capture_to_dict(rendered)["blocks"]],
        )
        self.assertIn("noteagent:capture_method", rendered.html)
        self.assertIn("rendered-html", rendered.html)
        self.assertNotIn("22种语言", rendered.html)

    def test_login_wall_fails_without_http_status(self) -> None:
        capture = capture_rendered_html("https://example.com/paid", LOGIN_HTML)
        self.assertEqual(capture.fidelity, "failed")
        self.assertTrue(set(capture.reasons) <= {"empty", "too_short"})
        self.assertIsNone(capture.http_status)

    def test_rejects_file_url(self) -> None:
        capture = capture_rendered_html("file:///tmp/a.html", ARTICLE_HTML)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("unsupported_url",))

    def test_keeps_data_uri_images_without_download(self) -> None:
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )

        data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        page = ARTICLE_HTML.replace(
            "<h1>相对熵</h1>",
            f'<h1>相对熵</h1><p><img src="{data_uri}" alt="eq"></p>',
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request {request.url}")

        capture = capture_rendered_html(
            "https://zhuanlan.example.com/p/1",
            page,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(capture.fidelity, "ok")
        self.assertGreaterEqual(capture.image_count, 1)
        self.assertEqual(capture.images_failed, 0)
        self.assertIn("data:image/png;base64,", capture.html)
        self.assertNotIn("na-inline", capture.html)

    def test_remote_image_403_is_low_without_cookies(self) -> None:
        seen_cookie: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookie.append(request.headers.get("cookie", ""))
            return httpx.Response(403, text="denied")

        page = ARTICLE_HTML.replace(
            "<h1>相对熵</h1>",
            '<h1>相对熵</h1><p><img src="https://cdn.example.com/eq.png"></p>',
        )
        capture = capture_rendered_html(
            "https://zhuanlan.example.com/p/1",
            page,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(capture.fidelity, "low")
        self.assertIn("missing_images", capture.reasons)
        self.assertTrue(all(item == "" for item in seen_cookie))

    def test_blob_image_is_missing(self) -> None:
        page = ARTICLE_HTML.replace(
            "<h1>相对熵</h1>",
            '<h1>相对熵</h1><p><img src="blob:https://zhuanlan.example.com/abc"></p>',
        )
        capture = capture_rendered_html("https://zhuanlan.example.com/p/1", page)
        self.assertEqual(capture.fidelity, "low")
        self.assertIn("missing_images", capture.reasons)
        self.assertIn('data-na-asset="missing"', capture.html)

    def test_oversized_html_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            capture_rendered_html(
                "https://example.com/huge",
                "x" * (MAX_HTML_BYTES + 1),
            )
        self.assertIn("exceeds", str(ctx.exception))

    def test_write_raw_uses_url_path_and_replaces(self) -> None:
        url = "https://zh.wikipedia.org/wiki/KL"
        capture = capture_rendered_html(url, ARTICLE_HTML)
        relative = raw_relative_path_for_url(url)
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            first = write_raw_captures(layout, [capture])
            self.assertEqual(first.items[0].code, "written")
            self.assertEqual(first.items[0].path, relative)
            second = write_raw_captures(layout, [capture])
            self.assertEqual(second.items[0].code, "replaced")
            self.assertEqual(second.items[0].path, relative)
            saved = (layout.root / relative).read_text(encoding="utf-8")
            self.assertIn("rendered-html", saved)
            self.assertIn("data-na-block", saved)

    def test_run_log_omits_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            run_rendered_html(
                "https://zh.wikipedia.org/wiki/KL",
                ARTICLE_HTML,
                run_logger=logger,
                caller="api",
            )
            blob = run_log_path(layout).read_text(encoding="utf-8")
        events = [json.loads(line) for line in blob.splitlines() if line.strip()]
        self.assertEqual([event["event"] for event in events], ["fetch.start", "fetch.end"])
        self.assertEqual(events[1]["method"], "rendered-html")
        self.assertNotIn("变分推断", blob)
        self.assertNotIn("def kl", blob)


if __name__ == "__main__":
    unittest.main()
