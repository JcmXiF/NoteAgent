from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.pdf_source import PdfSourceAdapter
from noteagent.acquire.adapters.sniff import sniff_media_type, sniff_path, sniff_url
from noteagent.acquire.adapters.pdf_source import (
    EMPTY_PAGE_TEXT,
    PdfDeriveError,
    derived_sidecar_path,
    looks_like_pdf,
)
from noteagent.acquire.quality import assess_pdf_capture
from noteagent.acquire.tools.fetchers.html import media_skip_reason
from noteagent.acquire.tools.web_fetch import capture_rendered_html, web_fetch
from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import raw_relative_path_for_url, write_raw_captures
from noteagent.api.files import list_project_tree, read_project_bytes, read_project_text
from noteagent.project.init import init_project

try:
    from pdf_fixtures import BLANK_PDF, HELLO_PDF, TWO_PAGE_PDF
except ImportError:  # python -m unittest tests.test_pdf_source
    from tests.pdf_fixtures import BLANK_PDF, HELLO_PDF, TWO_PAGE_PDF


class PdfSniffTests(unittest.TestCase):
    def test_pdf_url_is_fetchable(self) -> None:
        result = sniff_url("https://arxiv.org/pdf/1234.5678.pdf")
        self.assertEqual(result.kind, SourceKind.PDF)
        self.assertIsNone(result.fetch_skip)

    def test_pdf_content_type_is_fetchable(self) -> None:
        result = sniff_media_type("application/pdf")
        self.assertEqual(result.kind, SourceKind.PDF)
        self.assertIsNone(media_skip_reason("application/pdf"))
        self.assertIsNone(media_skip_reason("application/x-pdf"))

    def test_local_pdf_is_ready(self) -> None:
        result = sniff_path("notes/paper.PDF")
        self.assertEqual(result.kind, SourceKind.PDF)


class PdfDeriveTests(unittest.TestCase):
    def test_page_blocks_from_bytes(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(HELLO_PDF)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].id, "p1")
        self.assertEqual(blocks[0].tag, "page")
        self.assertIn("Hello PDF", blocks[0].text)

    def test_two_pages(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(TWO_PAGE_PDF)
        self.assertEqual([block.id for block in blocks], ["p1", "p2"])
        self.assertIn("Page one", blocks[0].text)
        self.assertIn("Page two", blocks[1].text)

    def test_empty_text_layer_keeps_placeholder(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(BLANK_PDF)
        self.assertEqual(blocks[0].text, EMPTY_PAGE_TEXT)

    def test_invalid_pdf_raises(self) -> None:
        with self.assertRaises(PdfDeriveError):
            PdfSourceAdapter().derive_bytes(b"%PDF-1.4 junk")

    def test_preview_is_pdfjs(self) -> None:
        preview = PdfSourceAdapter().preview(Path("raw/pdf/x.pdf"))
        self.assertEqual(preview["renderer"], "pdfjs")
        self.assertEqual(preview["kind"], "pdf")

    def test_looks_like_pdf(self) -> None:
        self.assertTrue(looks_like_pdf(HELLO_PDF))
        self.assertTrue(looks_like_pdf(b"  %PDF-1.7\n"))
        self.assertFalse(looks_like_pdf(b"<html>"))


class PdfQualityTests(unittest.TestCase):
    def test_no_text_layer_is_low_not_failed(self) -> None:
        fidelity, reasons = assess_pdf_capture(page_count=1, text_chars=0)
        self.assertEqual(fidelity, "low")
        self.assertEqual(reasons, ("no_text_layer",))

    def test_html_min_chars_does_not_apply(self) -> None:
        fidelity, reasons = assess_pdf_capture(page_count=1, text_chars=9)
        self.assertEqual(fidelity, "ok")
        self.assertEqual(reasons, ())

    def test_truncated_and_invalid_fail(self) -> None:
        self.assertEqual(
            assess_pdf_capture(page_count=1, text_chars=10, truncated=True)[0],
            "failed",
        )
        self.assertEqual(
            assess_pdf_capture(page_count=1, text_chars=10, invalid=True)[1],
            ("invalid_pdf",),
        )


class PdfWriteRawTests(unittest.TestCase):
    def test_writes_bytes_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            capture = Capture(
                url="https://arxiv.org/pdf/hello.pdf",
                title="hello",
                markdown="[p1] Hello PDF",
                html="",
                capture_method="pdf-http",
                fidelity="ok",
                reasons=(),
                kind="pdf",
                pdf_bytes=HELLO_PDF,
                page_count=1,
                char_count=9,
            )
            result = write_raw_captures(layout, [capture])
            self.assertEqual(len(result.written_paths), 1)
            relative = result.written_paths[0]
            self.assertTrue(relative.startswith("raw/pdf/"))
            self.assertTrue(relative.endswith(".pdf"))
            path = layout.root / relative
            self.assertEqual(path.read_bytes()[:5], b"%PDF-")
            sidecar = derived_sidecar_path(layout, relative)
            self.assertTrue(sidecar.is_file())
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(data["kind"], "pdf")
            self.assertEqual(data["blocks"][0]["id"], "p1")
            payload = read_project_text(layout, relative)
            self.assertEqual(payload["kind"], "pdf")
            self.assertTrue(payload["binary"])
            self.assertEqual(payload["blocks"][0]["id"], "p1")
            self.assertNotIn("%PDF", str(payload["content"]))
            bytes_path, media = read_project_bytes(layout, relative)
            self.assertEqual(media, "application/pdf")
            self.assertEqual(bytes_path.read_bytes(), HELLO_PDF)
            tree = {item["path"] for item in list_project_tree(layout)}
            self.assertIn(relative, tree)

    def test_blank_pdf_without_text_layer_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            capture = Capture(
                url="https://example.com/scan.pdf",
                title="scan",
                markdown=f"[p1] {EMPTY_PAGE_TEXT}",
                html="",
                capture_method="pdf-http",
                fidelity="low",
                reasons=("no_text_layer",),
                kind="pdf",
                pdf_bytes=BLANK_PDF,
                page_count=1,
                char_count=0,
            )
            result = write_raw_captures(layout, [capture])
            self.assertEqual(len(result.written_paths), 1)

    def test_path_stable_and_under_pdf(self) -> None:
        url = "https://arxiv.org/pdf/1234.5678.pdf"
        first = raw_relative_path_for_url(url, kind="pdf")
        second = raw_relative_path_for_url(url, title="ignored", kind="pdf")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("raw/pdf/"))
        self.assertNotIn("/arxiv.org/", first)
        self.assertTrue(first.endswith(".pdf"))
        self.assertFalse(first.endswith(".pdf.pdf"))


class PdfFetchTests(unittest.TestCase):
    def test_content_type_pdf(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=HELLO_PDF,
                headers={"content-type": "application/pdf"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper", client=client)
        self.assertEqual(capture.kind, "pdf")
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, "pdf-http")
        self.assertEqual(capture.pdf_bytes[:5], b"%PDF-")
        self.assertIn("Hello PDF", capture.markdown)
        self.assertEqual(capture.page_count, 1)
        self.assertEqual(capture.html, "")

    def test_url_suffix_pdf(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=HELLO_PDF,
                headers={"content-type": "application/octet-stream"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://arxiv.org/pdf/hello.pdf", client=client)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.kind, "pdf")
        self.assertEqual(capture.title, "hello")

    def test_pdf_url_html_landing_stays_html(self) -> None:
        html = (
            "<html><head><title>Landing</title></head><body><article>"
            + ("正文内容。" * 80)
            + "</article></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper.pdf", client=client)
        self.assertEqual(capture.kind, "html")
        self.assertNotEqual(capture.capture_method, "pdf-http")

    def test_invalid_pdf_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=b"%PDF-1.4",
                headers={"content-type": "application/pdf"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/broken", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("invalid_pdf",))
        self.assertEqual(capture.pdf_bytes, b"")

    def test_from_rendered_rejects_pdf_url(self) -> None:
        capture = capture_rendered_html(
            "https://arxiv.org/pdf/hello.pdf",
            "<html><body><p>chrome viewer</p></body></html>",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("pdf_not_html",))


if __name__ == "__main__":
    unittest.main()
