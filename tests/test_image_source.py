from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import noteagent.api.projects as projects_mod
from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.image_source import (
    IMAGE_BLOCK_ID,
    ImageDeriveError,
    ImageSourceAdapter,
    filename_caption,
    looks_like_image,
)
from noteagent.acquire.intake.local_file import (
    LOCAL_IMAGE_CAPTURE_METHOD,
    capture_from_local_file,
)
from noteagent.acquire.media.captions import PLACEHOLDER_CAPTION
from noteagent.acquire.media.data_uri import sniff_image
from noteagent.acquire.media.png import encode_png
from noteagent.acquire.tools.web_fetch import capture_rendered_html, web_fetch
from noteagent.acquire.write_raw import raw_relative_path_for_local, write_raw_captures
from noteagent.api.app import create_app
from noteagent.api.files import read_project_bytes, read_project_text
from noteagent.project.init import init_project

PNG_MIME = "image/png"


def _red_png(width: int = 80, height: int = 80) -> bytes:
    pixels = bytes((200, 16, 16)) * (width * height)
    return encode_png(width, height, pixels, color_type=2)


class ImageSniffTests(unittest.TestCase):
    def test_magic_and_svg_not_html(self) -> None:
        png = _red_png(1, 1)
        self.assertEqual(sniff_image(png)[:2], ("image/png", "png"))
        self.assertTrue(looks_like_image(png))
        self.assertEqual(
            sniff_image(b"RIFF\x00\x00\x00\x00WEBP")[:2],
            ("image/webp", "webp"),
        )
        self.assertEqual(
            sniff_image(b"\x00\x00\x00\x1cftypavif")[:2],
            ("image/avif", "avif"),
        )
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>'
        self.assertEqual(sniff_image(svg)[:2], ("image/svg+xml", "svg"))
        xml_svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.assertEqual(sniff_image(xml_svg)[:2], ("image/svg+xml", "svg"))
        html_with_svg = (
            b"<!DOCTYPE html><html><body>"
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
            b"</body></html>"
        )
        self.assertIsNone(sniff_image(html_with_svg))
        self.assertFalse(looks_like_image(b"<html>not an image</html>"))

    def test_filename_caption_skips_generic(self) -> None:
        self.assertEqual(filename_caption("相对熵示意图"), "相对熵示意图")
        self.assertEqual(filename_caption("photo"), "")
        self.assertEqual(filename_caption("image"), "")
        self.assertEqual(filename_caption("a1b2c3d4"), "")


class ImageDeriveTests(unittest.TestCase):
    def test_adapter_is_ready(self) -> None:
        self.assertEqual(ImageSourceAdapter.kind, SourceKind.IMAGE)
        self.assertEqual(
            ImageSourceAdapter().preview(Path("raw/media/x.png"))["renderer"],
            "image",
        )

    def test_one_leaf_and_filename_caption(self) -> None:
        derived = ImageSourceAdapter().derive_bytes(
            _red_png(),
            filename="相对熵示意图.png",
        )
        self.assertEqual([block.id for block in derived.blocks], [IMAGE_BLOCK_ID])
        self.assertEqual(derived.blocks[0].tag, "figure")
        self.assertIn("[图已抽出 相对熵示意图]", derived.blocks[0].text)
        self.assertIn('data-na-block="img"', derived.html)
        self.assertNotIn("data:image", derived.html)
        self.assertEqual(derived.media_type, PNG_MIME)

    def test_generic_name_uses_placeholder(self) -> None:
        derived = ImageSourceAdapter().derive_bytes(_red_png(1, 1), filename="photo.png")
        self.assertIn(PLACEHOLDER_CAPTION, derived.blocks[0].text)

    def test_tiny_png_is_not_skipped(self) -> None:
        derived = ImageSourceAdapter().derive_bytes(_red_png(1, 1), filename="dot.png")
        self.assertEqual(len(derived.blocks), 1)

    def test_invalid_bytes_raise(self) -> None:
        with self.assertRaises(ImageDeriveError):
            ImageSourceAdapter().derive_bytes(b"not-an-image")


class ImageWriteRawTests(unittest.TestCase):
    def test_local_png_writes_bytes_not_html(self) -> None:
        png = _red_png()
        capture = capture_from_local_file(filename="相对熵示意图.png", data=png)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, LOCAL_IMAGE_CAPTURE_METHOD)
        expected = raw_relative_path_for_local(
            capture.url,
            filename="相对熵示意图.png",
            kind="image",
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertEqual(result.written_paths, (expected,))
            disk = layout.root / expected
            self.assertTrue(expected.startswith("raw/media/"))
            self.assertNotIn("/local/", expected)
            self.assertTrue(expected.endswith(".png"))
            self.assertEqual(disk.read_bytes(), png)
            sidecar = layout.derived / f"{expected}.json"
            self.assertTrue(sidecar.is_file())
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "image")
            self.assertEqual(payload["blocks"][0]["id"], "img")
            self.assertNotIn("data:image", sidecar.read_text(encoding="utf-8"))
            self.assertFalse((layout.wiki / "media").exists())
            media_copies = [item for item in layout.media.rglob("*") if item.is_file()]
            self.assertEqual(media_copies, [])
            digest = hashlib.sha256(png).hexdigest()
            captions = json.loads(layout.media_captions.read_text(encoding="utf-8"))
            self.assertIn(digest, captions)
            self.assertEqual(captions[digest]["source"], "filename")
            self.assertIn("相对熵示意图", captions[digest]["caption"])


class ImageFetchTests(unittest.TestCase):
    def test_content_type_png(self) -> None:
        png = _red_png()

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=png,
                headers={"content-type": PNG_MIME},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper", client=client)
        self.assertEqual(capture.kind, "image")
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, "image-bytes")
        self.assertEqual(capture.image_bytes, png)
        self.assertIn("[图已抽出", capture.markdown)

    def test_url_suffix_image_octet_stream(self) -> None:
        png = _red_png()

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=png,
                headers={"content-type": "application/octet-stream"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/files/相对熵示意图.png", client=client)
        self.assertEqual(capture.kind, "image")
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.title, "相对熵示意图")
        self.assertIn("相对熵示意图", capture.markdown)

    def test_png_url_html_landing_stays_html(self) -> None:
        html = (
            "<html><head><title>Landing</title></head><body><article>"
            + ("正文内容。" * 80)
            + "</article></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/fig.png", client=client)
        self.assertEqual(capture.kind, "html")
        self.assertNotEqual(capture.capture_method, "image-bytes")

    def test_invalid_image_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=b"not-a-png",
                headers={"content-type": PNG_MIME},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/broken.png", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("invalid_image",))
        self.assertEqual(capture.image_bytes, b"")

    def test_from_rendered_rejects_image_url(self) -> None:
        capture = capture_rendered_html(
            "https://example.com/fig.png",
            "<html><body><p>chrome viewer</p></body></html>",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("image_not_html",))


class ImageApiTests(unittest.TestCase):
    def test_from_local_save_and_docs_read(self) -> None:
        png = _red_png()
        blob = base64.b64encode(png).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            beta = init_project(repo / "projects" / "beta")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                preview = client.post(
                    "/api/projects/beta/acquire/from-local",
                    json={
                        "files": [
                            {
                                "name": "相对熵示意图.png",
                                "mediaType": PNG_MIME,
                                "contentBase64": blob,
                            }
                        ]
                    },
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                capture = preview.json()["captures"][0]
                self.assertEqual(capture["kind"], "image")
                self.assertEqual(capture["fidelity"], "ok")
                self.assertTrue(capture["imageBase64"])
                self.assertEqual(capture["imageMediaType"], PNG_MIME)
                self.assertEqual(capture["blocks"][0]["id"], "img")
                self.assertFalse((beta.raw / "media").exists())

                saved = client.post(
                    "/api/projects/beta/acquire/save",
                    json={"captures": [capture]},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                written = saved.json()["written"]
                self.assertEqual(len(written), 1)
                self.assertTrue(written[0].startswith("raw/media/"))
                self.assertNotIn("/local/", written[0])
                self.assertTrue(written[0].endswith(".png"))
                self.assertEqual((beta.root / written[0]).read_bytes(), png)

                payload = read_project_text(beta, written[0])
                self.assertEqual(payload["kind"], "image")
                self.assertEqual(payload["renderer"], "image")
                self.assertIn("[图已抽出", str(payload["content"]))
                self.assertNotIn("data:image", str(payload["content"]))
                path, media_type = read_project_bytes(beta, written[0])
                self.assertEqual(media_type, PNG_MIME)
                self.assertEqual(path.read_bytes(), png)


if __name__ == "__main__":
    unittest.main()
