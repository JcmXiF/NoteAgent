from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import noteagent.api.projects as projects_mod
from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.office_source import (
    OfficeDeriveError,
    OfficeSourceAdapter,
    looks_like_office,
    sniff_office_package,
)
from noteagent.acquire.intake.local_file import (
    LOCAL_OFFICE_CAPTURE_METHOD,
    capture_from_local_file,
)
from noteagent.acquire.tools.web_fetch import capture_rendered_html, web_fetch
from noteagent.acquire.write_raw import raw_relative_path_for_local, write_raw_captures
from noteagent.api.app import create_app
from noteagent.api.files import read_project_text
from noteagent.project.init import init_project

try:
    from office_fixtures import (
        GARBAGE_OFFICE,
        HELLO_DOCX,
        HELLO_PPTX,
        IMAGE_DOCX,
        TABLE_DOCX,
        WORD_MIME,
    )
except ImportError:
    from tests.office_fixtures import (
        GARBAGE_OFFICE,
        HELLO_DOCX,
        HELLO_PPTX,
        IMAGE_DOCX,
        TABLE_DOCX,
        WORD_MIME,
    )


class OfficePackageTests(unittest.TestCase):
    def test_sniff_real_packages(self) -> None:
        self.assertEqual(sniff_office_package(HELLO_DOCX), "docx")
        self.assertEqual(sniff_office_package(HELLO_PPTX), "pptx")
        self.assertTrue(looks_like_office(HELLO_DOCX))
        self.assertFalse(looks_like_office(GARBAGE_OFFICE))
        self.assertFalse(looks_like_office(b"<html>"))


class OfficeDeriveTests(unittest.TestCase):
    def test_adapter_is_ready(self) -> None:
        self.assertEqual(OfficeSourceAdapter.kind, SourceKind.OFFICE)
        self.assertEqual(
            OfficeSourceAdapter().preview(Path("raw/office/x.docx"))["renderer"],
            "html-sandbox",
        )

    def test_docx_blocks_and_table(self) -> None:
        derived = OfficeSourceAdapter().derive_bytes(TABLE_DOCX, filename="note.docx")
        ids = [block.id for block in derived.blocks]
        self.assertIn("s1", ids)
        self.assertTrue(any(block.tag == "h1" for block in derived.blocks))
        self.assertTrue(any(block.tag == "table" for block in derived.blocks))
        self.assertIn("data-na-block", derived.html)
        self.assertIn("相对熵", derived.html)
        self.assertEqual(derived.subtype, "docx")

    def test_pptx_slide_ids(self) -> None:
        derived = OfficeSourceAdapter().derive_bytes(HELLO_PPTX, filename="deck.pptx")
        ids = [block.id for block in derived.blocks]
        self.assertEqual(ids[0], "slide-1")
        self.assertEqual(ids[1], "slide-2")
        self.assertIn('data-na-block="slide-1"', derived.html)
        self.assertEqual(derived.subtype, "pptx")

    def test_image_marker_without_touching_raw(self) -> None:
        derived = OfficeSourceAdapter().derive_bytes(IMAGE_DOCX, filename="fig.docx")
        joined = " ".join(block.text for block in derived.blocks)
        self.assertIn("[图已抽出", joined)
        self.assertIn("有图无说明", joined)

    def test_invalid_package_raises(self) -> None:
        with self.assertRaises(OfficeDeriveError):
            OfficeSourceAdapter().derive_bytes(GARBAGE_OFFICE)


class OfficeWriteRawTests(unittest.TestCase):
    def test_local_docx_writes_bytes_not_html(self) -> None:
        capture = capture_from_local_file(filename="note.docx", data=HELLO_DOCX)
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, LOCAL_OFFICE_CAPTURE_METHOD)
        expected = raw_relative_path_for_local(
            capture.url,
            filename="note.docx",
            kind="office",
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertEqual(result.written_paths, (expected,))
            disk = layout.root / expected
            self.assertEqual(disk.read_bytes(), HELLO_DOCX)
            self.assertFalse(disk.read_bytes().lstrip().startswith(b"<!DOCTYPE"))
            sidecar = layout.derived / f"{expected}.json"
            self.assertTrue(sidecar.is_file())
            payload = sidecar.read_text(encoding="utf-8")
            self.assertIn('"kind": "office"', payload)
            self.assertIn("data-na-block", payload)
            wiki_media = layout.wiki / "media"
            self.assertFalse(wiki_media.exists())

    def test_pptx_path_uses_pptx_suffix(self) -> None:
        capture = capture_from_local_file(filename="deck.pptx", data=HELLO_PPTX)
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            path = result.written_paths[0]
            self.assertTrue(path.startswith("raw/office/"))
            self.assertNotIn("/local/", path)
            self.assertTrue(path.endswith(".pptx"))
            self.assertEqual((layout.root / path).read_bytes(), HELLO_PPTX)

    def test_extracted_image_lands_in_noteagent_media(self) -> None:
        capture = capture_from_local_file(filename="fig.docx", data=IMAGE_DOCX)
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [capture])
            self.assertTrue(result.written_paths)
            media_files = [item for item in layout.media.rglob("*") if item.is_file()]
            self.assertTrue(media_files)
            self.assertTrue(layout.media_captions.is_file())
            self.assertFalse((layout.wiki / "media").exists())


class OfficeFetchTests(unittest.TestCase):
    def test_content_type_docx(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=HELLO_DOCX,
                headers={"content-type": WORD_MIME},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper", client=client)
        self.assertEqual(capture.kind, "office")
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.capture_method, "office-ooxml")
        self.assertTrue(capture.office_bytes.startswith(b"PK"))
        self.assertIn("data-na-block", capture.html)
        self.assertIn("相对熵", capture.markdown)

    def test_url_suffix_office_octet_stream(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=HELLO_PPTX,
                headers={"content-type": "application/octet-stream"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/files/deck.pptx", client=client)
        self.assertEqual(capture.kind, "office")
        self.assertEqual(capture.fidelity, "ok")
        self.assertEqual(capture.title, "deck")
        self.assertIn("slide-1", capture.html)

    def test_office_url_html_landing_stays_html(self) -> None:
        html = (
            "<html><head><title>Landing</title></head><body><article>"
            + ("正文内容。" * 80)
            + "</article></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/paper.docx", client=client)
        self.assertEqual(capture.kind, "html")
        self.assertNotEqual(capture.capture_method, "office-ooxml")

    def test_invalid_office_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                content=GARBAGE_OFFICE,
                headers={"content-type": WORD_MIME},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        capture = web_fetch("https://example.com/broken.docx", client=client)
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("invalid_office",))
        self.assertEqual(capture.office_bytes, b"")

    def test_from_rendered_rejects_office_url(self) -> None:
        capture = capture_rendered_html(
            "https://example.com/note.docx",
            "<html><body><p>chrome viewer</p></body></html>",
        )
        self.assertEqual(capture.fidelity, "failed")
        self.assertEqual(capture.reasons, ("office_not_html",))


class OfficeApiTests(unittest.TestCase):
    def test_from_local_save_and_docs_read(self) -> None:
        blob = base64.b64encode(HELLO_DOCX).decode("ascii")
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
                                "name": "note.docx",
                                "mediaType": WORD_MIME,
                                "contentBase64": blob,
                            }
                        ]
                    },
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                capture = preview.json()["captures"][0]
                self.assertEqual(capture["kind"], "office")
                self.assertEqual(capture["fidelity"], "ok")
                self.assertTrue(capture["officeBase64"])
                self.assertTrue(capture["blocks"][0]["id"].startswith("s"))
                self.assertIn("data-na-block", capture["html"])
                self.assertFalse((beta.raw / "office").exists())

                saved = client.post(
                    "/api/projects/beta/acquire/save",
                    json={"captures": [capture]},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                written = saved.json()["written"]
                self.assertEqual(len(written), 1)
                self.assertTrue(written[0].startswith("raw/office/"))
                self.assertNotIn("/local/", written[0])
                self.assertTrue(written[0].endswith(".docx"))
                self.assertEqual((beta.root / written[0]).read_bytes(), HELLO_DOCX)

                payload = read_project_text(beta, written[0])
                self.assertEqual(payload["kind"], "office")
                self.assertEqual(payload["renderer"], "html-sandbox")
                self.assertIn("data-na-block", str(payload["content"]))


if __name__ == "__main__":
    unittest.main()
