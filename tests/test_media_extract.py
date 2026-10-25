from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.acquire.adapters.pdf_source import (
    EMPTY_PAGE_TEXT,
    PdfSourceAdapter,
    derived_sidecar_path,
)
from noteagent.acquire.derive import extract_derived_blocks
from noteagent.acquire.media.captions import PLACEHOLDER_CAPTION, CaptionStore
from noteagent.acquire.media.data_uri import decode_data_uri, sniff_image
from noteagent.acquire.media.extract import (
    ExtractedImage,
    apply_source_cap,
    extract_html_images,
    image_marker,
    media_work_dir,
    persist_html_media,
)
from noteagent.acquire.media.png import encode_png, png_from_pixels, png_size
from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import write_raw_captures
from noteagent.project.init import init_project

try:
    from pdf_fixtures import (
        HELLO_PDF,
        IMAGE_PDF,
        MIXED_SIZE_PDF,
        SCAN_NO_TEXT_PDF,
        SCAN_WITH_TEXT_PDF,
        TINY_IMAGE_PDF,
    )
except ImportError:
    from tests.pdf_fixtures import (
        HELLO_PDF,
        IMAGE_PDF,
        MIXED_SIZE_PDF,
        SCAN_NO_TEXT_PDF,
        SCAN_WITH_TEXT_PDF,
        TINY_IMAGE_PDF,
    )

LONG_BODY = "KL散度是两个概率分布之间的差异度量。" * 20


def _red_png(width: int = 80, height: int = 80) -> bytes:
    pixels = bytes((200, 16, 16)) * (width * height)
    return encode_png(width, height, pixels, color_type=2)


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class PngEncodeTests(unittest.TestCase):
    def test_ihdr_matches_pixels(self) -> None:
        pixels = bytes((1, 2, 3, 4, 5, 6))
        data = encode_png(2, 1, pixels, color_type=2)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(png_size(data), (2, 1))
        self.assertEqual(sniff_image(data)[:2], ("image/png", "png"))

    def test_bitmap_bgr_to_png(self) -> None:
        # One pixel BGR = blue,green,red stored as B,G,R
        data = png_from_pixels(1, 1, 3, 3, "BGR", bytes((9, 8, 7)))
        self.assertEqual(png_size(data), (1, 1))


class CaptionStoreTests(unittest.TestCase):
    def test_non_placeholder_wins_over_later_placeholder(self) -> None:
        store = CaptionStore.memory()
        digest = "a" * 64
        store.remember(digest, caption="示意图", source="alt")
        store.remember(digest, caption=PLACEHOLDER_CAPTION, source="placeholder")
        self.assertEqual(store.lookup(digest), "示意图")

    def test_roundtrip_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            store = CaptionStore.for_layout(layout)
            digest = "b" * 64
            store.remember(
                digest,
                caption="H(X)",
                source="alt",
                media_type="image/png",
                width=80,
                height=80,
            )
            store.save()
            loaded = CaptionStore.for_layout(layout)
            record = loaded.get(digest)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.caption, "H(X)")
            self.assertEqual(record.source, "alt")
            self.assertEqual(record.width, 80)


class FilterTests(unittest.TestCase):
    def test_source_cap(self) -> None:
        images = [
            ExtractedImage(
                sha256=f"{i:064x}",
                data=b"x",
                media_type="image/png",
                extension="png",
                width=80,
                height=80,
                page=1,
                hint="",
            )
            for i in range(3)
        ]
        capped = apply_source_cap(images, limit=2)
        self.assertEqual([im.skip_reason for im in capped], ["", "", "cap"])


class PdfExtractTests(unittest.TestCase):
    def test_page_text_includes_extracted_marker(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(IMAGE_PDF)
        self.assertEqual(blocks[0].id, "p1")
        self.assertIn("Hello PDF", blocks[0].text)
        self.assertIn(image_marker(PLACEHOLDER_CAPTION), blocks[0].text)

    def test_tiny_image_is_dropped(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(TINY_IMAGE_PDF)
        self.assertEqual(blocks[0].text, "Hello PDF")
        self.assertNotIn("[图", blocks[0].text)

    def test_mixed_keeps_only_large(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(MIXED_SIZE_PDF)
        self.assertEqual(blocks[0].text.count("[图已抽出"), 1)

    def test_page_scan_skipped_when_text_exists(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(SCAN_WITH_TEXT_PDF)
        self.assertEqual(blocks[0].text, "Body text")
        self.assertNotIn("[图", blocks[0].text)

    def test_scan_without_text_is_kept(self) -> None:
        blocks = PdfSourceAdapter().derive_bytes(SCAN_NO_TEXT_PDF)
        self.assertNotEqual(blocks[0].text, EMPTY_PAGE_TEXT)
        self.assertIn(image_marker(PLACEHOLDER_CAPTION), blocks[0].text)

    def test_env_off_skips_extract(self) -> None:
        with patch.dict(os.environ, {"NOTEAGENT_EXTRACT_MEDIA": "0"}):
            blocks = PdfSourceAdapter().derive_bytes(IMAGE_PDF)
        self.assertEqual(blocks[0].text, "Hello PDF")

    def test_write_raw_persists_work_copy_not_wiki_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            capture = Capture(
                url="https://example.com/fig.pdf",
                title="fig",
                markdown="",
                html="",
                capture_method="pdf-http",
                fidelity="ok",
                reasons=(),
                kind="pdf",
                pdf_bytes=IMAGE_PDF,
                page_count=1,
                char_count=9,
            )
            result = write_raw_captures(layout, [capture])
            identity = result.written_paths[0]
            sidecar = json.loads(
                derived_sidecar_path(layout, identity).read_text(encoding="utf-8")
            )
            self.assertIn("[图已抽出", sidecar["blocks"][0]["text"])
            media_dir = media_work_dir(layout, identity)
            files = list(media_dir.glob("*.png"))
            self.assertEqual(len(files), 1)
            self.assertEqual(len(files[0].stem), 64)
            cache = json.loads(layout.media_captions.read_text(encoding="utf-8"))
            self.assertEqual(cache[files[0].stem]["caption"], PLACEHOLDER_CAPTION)
            self.assertFalse((layout.wiki / "media").exists())
            self.assertEqual((layout.root / identity).read_bytes()[:5], b"%PDF-")

    def test_text_only_pdf_does_not_create_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            capture = Capture(
                url="https://example.com/hello.pdf",
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
            identity = result.written_paths[0]
            self.assertFalse(media_work_dir(layout, identity).exists())
            self.assertFalse(layout.media_captions.is_file())


class HtmlExtractTests(unittest.TestCase):
    def test_data_uri_uses_alt_and_skips_junk(self) -> None:
        png = _red_png()
        html = (
            f'<p><img src="{_data_uri(png)}" alt="H(X)"></p>'
            '<p><img src="data:image/png;base64,abc" alt="junk"></p>'
        )
        images = extract_html_images(html)
        kept = [im for im in images if not im.skip_reason]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].hint, "H(X)")
        self.assertEqual(kept[0].caption_source, "alt")

    def test_tiny_html_image_skipped(self) -> None:
        html = f'<p><img src="{_data_uri(_red_png(2, 2))}" alt="icon"></p>'
        images = extract_html_images(html)
        self.assertTrue(images)
        self.assertTrue(all(im.skip_reason == "too_small" for im in images))

    def test_write_raw_keeps_html_bytes_and_caches_caption(self) -> None:
        png = _red_png()
        uri = _data_uri(png)
        html = (
            "<!DOCTYPE html><html><head><title>相对熵</title></head><body><article>"
            f"<p id='na-b1' data-na-block='b1'>{LONG_BODY}"
            f'<img src="{uri}" alt="示意图">'
            "</p></article></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            capture = Capture(
                url="https://zh.wikipedia.org/wiki/相对熵",
                title="相对熵",
                markdown=LONG_BODY,
                html=html,
                capture_method="trafilatura-html",
                fidelity="ok",
                reasons=(),
                char_count=len(LONG_BODY),
            )
            result = write_raw_captures(layout, [capture])
            identity = result.written_paths[0]
            raw = (layout.root / identity).read_text(encoding="utf-8")
            self.assertIn(uri, raw)
            media_files = list(media_work_dir(layout, identity).glob("*.png"))
            self.assertEqual(len(media_files), 1)
            cache = json.loads(layout.media_captions.read_text(encoding="utf-8"))
            self.assertEqual(cache[media_files[0].stem]["caption"], "示意图")
            self.assertEqual(cache[media_files[0].stem]["source"], "alt")
            self.assertFalse((layout.wiki / "media").exists())
            store = CaptionStore.for_layout(layout)
            blocks = extract_derived_blocks(raw, caption_lookup=store.lookup)
            self.assertTrue(
                any("示意图" in block.text and "[图已本地化" in block.text for block in blocks)
            )

    def test_persist_html_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            png = _red_png()
            html = f'<img src="{_data_uri(png)}" alt="公式">'
            kept = persist_html_media(layout, "raw/web/example.com/x.html", html)
            self.assertEqual(len(kept), 1)
            self.assertTrue(layout.media_captions.is_file())


class DataUriTests(unittest.TestCase):
    def test_what_decodes_is_still_checked_for_being_an_image(self) -> None:
        # `abc` has no padding, so the decoder recovers two bytes; those bytes are
        # not a PNG header, which is what the caller has to look at next.
        decoded = decode_data_uri("data:image/png;base64,abc")
        assert decoded is not None
        self.assertIsNone(sniff_image(decoded[0]))

    def test_what_cannot_decode_at_all_is_refused(self) -> None:
        self.assertIsNone(decode_data_uri("data:image/png;base64,!!!!"))


if __name__ == "__main__":
    unittest.main()
