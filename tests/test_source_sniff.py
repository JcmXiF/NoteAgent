from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lxml import html as lxml_html

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.html_source import HtmlSourceAdapter
from noteagent.acquire.adapters.sniff import sniff_media_type, sniff_path, sniff_url
from noteagent.acquire.tools.fetchers.article import stamp_block_ids
from noteagent.acquire.tools.fetchers.html import media_skip_reason


class SourceSniffTests(unittest.TestCase):
    def test_http_html_url_is_html_and_fetchable(self) -> None:
        result = sniff_url("https://zh.wikipedia.org/wiki/KL")
        self.assertEqual(result.kind, SourceKind.HTML)
        self.assertIsNone(result.fetch_skip)

    def test_youtube_watch_url_stays_fetchable_html(self) -> None:
        self.assertIsNone(sniff_url("https://www.youtube.com/watch?v=1").fetch_skip)
        self.assertEqual(
            sniff_url("https://www.youtube.com/watch?v=1").kind, SourceKind.HTML
        )

    def test_filetype_pdf_query_is_not_a_pdf_url(self) -> None:
        self.assertEqual(
            sniff_url("https://example.com/search?q=kl&filetype=pdf").kind,
            SourceKind.HTML,
        )

    def test_file_url_unsupported(self) -> None:
        self.assertEqual(sniff_url("file:///tmp/a.html").fetch_skip, "unsupported_url")

    def test_html_and_skip_media_types(self) -> None:
        self.assertIsNone(media_skip_reason("text/html; charset=utf-8"))
        self.assertIsNone(media_skip_reason("application/xhtml+xml"))
        self.assertEqual(media_skip_reason("video/mp4"), "skipped_video")
        self.assertEqual(media_skip_reason("application/json"), "unsupported_media")
        self.assertEqual(media_skip_reason("text/plain"), "unsupported_media")
        self.assertEqual(media_skip_reason("text/xml"), "unsupported_media")
        self.assertEqual(media_skip_reason("application/octet-stream"), "unsupported_media")
        self.assertEqual(sniff_media_type("image/png").kind, SourceKind.IMAGE)
        self.assertEqual(sniff_media_type("audio/mpeg").kind, SourceKind.AUDIO)
        office_mime = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        self.assertEqual(sniff_media_type(office_mime).kind, SourceKind.OFFICE)
        self.assertIsNone(media_skip_reason(office_mime))

    def test_local_path_kinds(self) -> None:
        self.assertEqual(sniff_path("notes/paper.PDF").kind, SourceKind.PDF)
        self.assertEqual(sniff_path("x.docx").kind, SourceKind.OFFICE)
        self.assertEqual(sniff_path("slide.pptx").kind, SourceKind.OFFICE)
        self.assertEqual(sniff_path("fig.png").kind, SourceKind.IMAGE)
        self.assertEqual(sniff_path("page.html").kind, SourceKind.HTML)
        self.assertIsNone(sniff_path("raw/a.md").kind)

    def test_html_adapter_derive_from_stamped_html(self) -> None:
        tree = lxml_html.fromstring("<article><p>定义</p><pre>code</pre></article>")
        stamp_block_ids(tree)
        html = lxml_html.tostring(tree, encoding="unicode")
        blocks = HtmlSourceAdapter().derive_html(html)
        self.assertGreaterEqual(len(blocks), 2)
        self.assertEqual(blocks[0].id, "b1")
        self.assertIn("定义", blocks[0].text)

    def test_html_adapter_derive_from_path(self) -> None:
        tree = lxml_html.fromstring("<p>一段</p>")
        stamp_block_ids(tree)
        html = lxml_html.tostring(tree, encoding="unicode")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text(html, encoding="utf-8")
            blocks = HtmlSourceAdapter().derive(path)
        self.assertEqual(blocks[0].text.strip(), "一段")


if __name__ == "__main__":
    unittest.main()
