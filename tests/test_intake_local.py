from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import noteagent.api.projects as projects_mod
from noteagent.acquire.intake.local_file import (
    LOCAL_HTML_CAPTURE_METHOD,
    LOCAL_IMAGE_CAPTURE_METHOD,
    LOCAL_PDF_CAPTURE_METHOD,
    capture_from_local_file,
    local_capture_url,
)
from noteagent.acquire.media.png import encode_png
from noteagent.acquire.write_raw import (
    is_local_capture_url,
    raw_relative_path_for_local,
    write_raw_captures,
)
from noteagent.api.app import create_app
from noteagent.project.init import init_project

try:
    from office_fixtures import HELLO_DOCX
    from pdf_fixtures import HELLO_PDF, TWO_PAGE_PDF
except ImportError:
    from tests.office_fixtures import HELLO_DOCX
    from tests.pdf_fixtures import HELLO_PDF, TWO_PAGE_PDF

LONG_BODY = "KL散度是两个概率分布之间的差异度量。" * 20


def _html_bytes(body: str = LONG_BODY, extra: str = "") -> bytes:
    return (
        "<!DOCTYPE html><html><head><title>相对熵</title></head>"
        f"<body><p>{body}</p>{extra}</body></html>"
    ).encode("utf-8")


class LocalCaptureTests(unittest.TestCase):
    def test_html_keeps_dropped_markup_and_stamps_blocks(self) -> None:
        extra = '<img src="https://example.com/x.png" alt="fig">'
        capture = capture_from_local_file(
            filename="note.html",
            data=_html_bytes(extra=extra),
        )
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, LOCAL_HTML_CAPTURE_METHOD)
        self.assertTrue(is_local_capture_url(capture.url))
        self.assertIn("data-na-block", capture.html)
        self.assertIn("https://example.com/x.png", capture.html)
        self.assertNotIn("data:image", capture.html)
        self.assertEqual(capture.images_failed, 0)

    def test_short_html_is_low_not_failed(self) -> None:
        capture = capture_from_local_file(
            filename="short.html",
            data="<html><body><p>太短</p></body></html>".encode("utf-8"),
        )
        self.assertEqual(capture.fidelity, "low")
        self.assertEqual(capture.reasons, ("too_short",))

    def test_empty_html_fails(self) -> None:
        capture = capture_from_local_file(
            filename="empty.html",
            data=b"<html><body></body></html>",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("empty",))

    def test_pdf_reuses_page_derive(self) -> None:
        capture = capture_from_local_file(
            filename="hello.pdf",
            data=HELLO_PDF,
            media_type="application/pdf",
        )
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, LOCAL_PDF_CAPTURE_METHOD)
        self.assertEqual(capture.kind, "pdf")
        self.assertEqual(capture.page_count, 1)
        self.assertIn("Hello PDF", capture.markdown)
        self.assertTrue(capture.url.startswith("local:pdf:"))

    def test_garbage_office_is_invalid(self) -> None:
        capture = capture_from_local_file(
            filename="slides.docx",
            data=b"PK\x03\x04not-a-real-office",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("invalid_office",))
        self.assertEqual(capture.kind, "office")
        self.assertFalse(capture.office_bytes)

    def test_real_docx_is_ready(self) -> None:
        capture = capture_from_local_file(filename="note.docx", data=HELLO_DOCX)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.kind, "office")
        self.assertTrue(capture.office_bytes)
        self.assertIn("data-na-block", capture.html)

    def test_png_is_ready(self) -> None:
        png = encode_png(1, 1, bytes((200, 16, 16)), color_type=2)
        capture = capture_from_local_file(filename="相对熵示意图.png", data=png)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, LOCAL_IMAGE_CAPTURE_METHOD)
        self.assertEqual(capture.kind, "image")
        self.assertTrue(capture.image_bytes)
        self.assertEqual(capture.page_count, 1)
        self.assertIn("[图已抽出", capture.markdown)
        self.assertIn("相对熵示意图", capture.markdown)

    def test_garbage_png_is_invalid(self) -> None:
        capture = capture_from_local_file(
            filename="fig.png",
            data=b"not-a-png",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("invalid_image",))
        self.assertEqual(capture.kind, "image")
        self.assertFalse(capture.image_bytes)

    def test_audio_is_not_ready(self) -> None:
        capture = capture_from_local_file(filename="talk.mp3", data=b"ID3\x00")
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("adapter_not_ready",))
        self.assertEqual(capture.kind, "audio")

    def test_unknown_suffix_is_unsupported(self) -> None:
        capture = capture_from_local_file(
            filename="notes.bin",
            data=b"\x00\x01\x02",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("unsupported_kind",))

    def test_same_bytes_same_url(self) -> None:
        first = local_capture_url("html", _html_bytes())
        second = local_capture_url("html", _html_bytes())
        self.assertEqual(first, second)
        self.assertNotEqual(first, local_capture_url("html", _html_bytes("other")))


class LocalWriteRawTests(unittest.TestCase):
    def test_local_html_writes_under_web_local(self) -> None:
        capture = capture_from_local_file(
            filename="note.html",
            data=_html_bytes(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertEqual(len(result.written_paths), 1)
            path = result.written_paths[0]
            self.assertTrue(path.startswith("raw/web/"))
            self.assertNotIn("/local/", path)
            self.assertTrue(path.endswith(".html"))
            text = (layout.root / path).read_text(encoding="utf-8")
            self.assertIn("data-na-block", text)
            self.assertIn("KL散度", text)

    def test_short_local_html_still_writes(self) -> None:
        capture = capture_from_local_file(
            filename="short.html",
            data="<html><body><p>太短</p></body></html>".encode("utf-8"),
        )
        self.assertEqual(capture.fidelity, "low")
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertEqual(len(result.written_paths), 1)

    def test_local_pdf_writes_under_pdf_local(self) -> None:
        capture = capture_from_local_file(filename="hello.pdf", data=HELLO_PDF)
        expected = raw_relative_path_for_local(
            capture.url,
            filename="hello.pdf",
            kind="pdf",
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertEqual(result.written_paths, (expected,))
            disk = layout.root / expected
            self.assertEqual(disk.read_bytes(), HELLO_PDF)
            sidecar = layout.derived / f"{expected}.json"
            self.assertTrue(sidecar.is_file())

    def test_reimport_replaces_same_bytes(self) -> None:
        capture = capture_from_local_file(filename="hello.pdf", data=HELLO_PDF)
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            first = write_raw_captures(layout, [capture])
            second = write_raw_captures(layout, [capture])
            self.assertEqual(first.written_paths, second.written_paths)
            self.assertEqual(second.items[0].code, "replaced")


class LocalApiTests(unittest.TestCase):
    def test_from_local_previews_without_writing(self) -> None:
        blob = base64.b64encode(HELLO_PDF).decode("ascii")
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
                                "name": "hello.pdf",
                                "mediaType": "application/pdf",
                                "contentBase64": blob,
                            }
                        ]
                    },
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                capture = preview.json()["captures"][0]
                self.assertEqual(capture["captureMethod"], LOCAL_PDF_CAPTURE_METHOD)
                self.assertEqual(capture["intake"], "local")
                self.assertEqual(capture["fidelity"], "ok")
                self.assertTrue(capture["pdfBase64"])
                self.assertEqual(capture["blocks"][0]["id"], "p1")
                self.assertFalse((beta.raw / "pdf").exists())

                saved = client.post(
                    "/api/projects/beta/acquire/save",
                    json={"captures": [capture]},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                written = saved.json()["written"]
                self.assertEqual(len(written), 1)
                self.assertTrue(written[0].startswith("raw/pdf/"))
                self.assertNotIn("/local/", written[0])
                self.assertEqual((beta.root / written[0]).read_bytes(), HELLO_PDF)

    def test_from_local_two_page_pdf_and_office_card(self) -> None:
        pdf_blob = base64.b64encode(TWO_PAGE_PDF).decode("ascii")
        office_blob = base64.b64encode(b"PK\x03\x04office").decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "beta")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                preview = client.post(
                    "/api/projects/beta/acquire/from-local",
                    json={
                        "files": [
                            {
                                "name": "two.pdf",
                                "mediaType": "application/pdf",
                                "contentBase64": pdf_blob,
                            },
                            {
                                "name": "deck.docx",
                                "mediaType": (
                                    "application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document"
                                ),
                                "contentBase64": office_blob,
                            },
                        ]
                    },
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                captures = preview.json()["captures"]
                self.assertEqual(len(captures), 2)
                self.assertEqual(captures[0]["pageCount"], 2)
                self.assertEqual(captures[1]["fidelity"], "failed")
                self.assertIn("invalid_office", captures[1]["reasons"])


if __name__ == "__main__":
    unittest.main()
