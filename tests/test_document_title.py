"""The name a stored document gives itself, and how the tree passes it on."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.api.files import list_project_tree
from noteagent.io.document_title import document_title
from noteagent.project.init import init_project

try:
    from office_fixtures import build_docx, build_pptx
    from pdf_fixtures import build_pdf_with_title, HELLO_PDF
except ImportError:  # python -m unittest tests.test_document_title
    from tests.office_fixtures import build_docx, build_pptx
    from tests.pdf_fixtures import build_pdf_with_title, HELLO_PDF


def page(head: str) -> bytes:
    return f"<!DOCTYPE html><html><head>{head}</head><body><p>x</p></body></html>".encode()


class HtmlTitleTests(unittest.TestCase):
    def test_reads_the_title_a_page_gives_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(page("<title>机器学习笔记-信息熵</title>"))
            self.assertEqual(document_title(path), "机器学习笔记-信息熵")

    def test_decodes_escapes_and_flattens_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(page("<title>A &amp; B\n   &#39;C&#39;</title>"))
            self.assertEqual(document_title(path), "A & B 'C'")

    def test_takes_the_tag_however_it_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(page('<TITLE data-x="1">标题</TITLE>'))
            self.assertEqual(document_title(path), "标题")

    def test_says_nothing_when_the_page_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (
                ("none.html", page('<meta charset="utf-8">')),
                ("blank.html", page("<title>   </title>")),
                ("headless.html", b"<html><body>no head</body></html>"),
            ):
                path = Path(tmp) / name
                path.write_bytes(body)
                self.assertEqual(document_title(path), "", name)

    def test_does_not_take_the_address_the_writer_falls_back_to(self) -> None:
        # A page captured without a title is stamped with its own address, and the
        # file name says more about it than a URL does.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(page("<title>https://example.com/p/1</title>"))
            self.assertEqual(document_title(path), "")

    def test_does_not_look_past_the_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(
                b'<html><head><meta charset="utf-8"></head><body><title>late</title></body></html>'
            )
            self.assertEqual(document_title(path), "")

    def test_survives_bytes_that_are_not_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.html"
            path.write_bytes(b"<html><head><title>\xff\xfe\xb7</title></head></html>")
            self.assertTrue(document_title(path))


class PdfTitleTests(unittest.TestCase):
    def test_reads_the_documents_own_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(
                build_pdf_with_title(["page"], title="Elements of Information Theory")
            )
            self.assertEqual(document_title(path), "Elements of Information Theory")

    def test_says_nothing_when_the_document_carries_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(HELLO_PDF)
            self.assertEqual(document_title(path), "")

    def test_says_nothing_for_a_file_that_is_not_a_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(b"not a pdf at all")
            self.assertEqual(document_title(path), "")


class OfficeTitleTests(unittest.TestCase):
    def test_reads_a_documents_core_properties(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "a.docx"
            docx.write_bytes(build_docx(title="报告：关于信息熵"))
            self.assertEqual(document_title(docx), "报告：关于信息熵")

            pptx = Path(tmp) / "a.pptx"
            pptx.write_bytes(build_pptx(title="线性代数讲义"))
            self.assertEqual(document_title(pptx), "线性代数讲义")

    def test_says_nothing_when_the_properties_hold_no_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.docx"
            path.write_bytes(build_docx())
            self.assertEqual(document_title(path), "")

    def test_says_nothing_for_a_package_that_cannot_be_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.docx"
            path.write_bytes(b"PK not really a package")
            self.assertEqual(document_title(path), "")


class OtherKindsTests(unittest.TestCase):
    def test_other_kinds_have_no_title_to_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.png", "a.md", "a.txt", "a"):
                path = Path(tmp) / name
                path.write_bytes(b"whatever")
                self.assertEqual(document_title(path), "", name)

    def test_nothing_to_read_is_never_an_error(self) -> None:
        # A title is a nicety and the listing is not: a missing file, a directory,
        # and empty bytes all answer with an empty title rather than raising.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.html"
            self.assertEqual(document_title(missing), "")
            self.assertEqual(document_title(Path(tmp) / "sub" / "x.html"), "")
            empty = Path(tmp) / "empty.pdf"
            empty.write_bytes(b"")
            self.assertEqual(document_title(empty), "")

    def test_a_read_that_fails_says_why_when_asked(self) -> None:
        # A failed read is reported; a document that simply has no title is not.
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.pdf"
            broken.write_bytes(b"not a pdf at all")
            problems: list[str] = []
            self.assertEqual(document_title(broken, on_problem=problems.append), "")
            self.assertEqual(len(problems), 1)
            self.assertIn("Error", problems[0])

    def test_a_document_with_no_title_is_not_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(HELLO_PDF)
            problems: list[str] = []
            document_title(path, on_problem=problems.append)
            self.assertEqual(problems, [])


class TreeTitleTests(unittest.TestCase):
    """What the file list calls each file."""

    def titles(self, layout) -> dict[str, str]:
        return {item["path"]: item["title"] for item in list_project_tree(layout)}

    def test_a_page_is_named_by_its_slug(self) -> None:
        # Links and the graph are written in terms of the slug.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page_path = layout.wiki / "concepts" / "KL散度.md"
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text("# KL散度\n", encoding="utf-8")
            self.assertEqual(self.titles(layout)[f"wiki/concepts/KL散度.md"], "KL散度")

    def test_a_markdown_source_is_named_by_its_file_name(self) -> None:
        # Unlike a capture, it was not filed under an address.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = layout.raw / "1. 矩阵与线性方程.md"
            source.write_text("# 1. 矩阵与线性方程\n", encoding="utf-8")
            self.assertEqual(
                self.titles(layout)["raw/1. 矩阵与线性方程.md"], "1. 矩阵与线性方程"
            )

    def test_a_captured_page_is_named_by_what_it_says_it_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            stored = layout.raw / "web" / "12906163.html-9dd0e3d5e910.html"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(page("<title>机器学习笔记-信息熵、条件熵</title>"))
            titles = self.titles(layout)
            self.assertEqual(
                titles["raw/web/12906163.html-9dd0e3d5e910.html"],
                "机器学习笔记-信息熵、条件熵",
            )

    def test_an_original_with_no_name_of_its_own_comes_back_empty(self) -> None:
        # The interface reads a name off the file name for these.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            stored = layout.raw / "web" / "a-000000000001.html"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(page("<meta charset=\"utf-8\">"))
            image = layout.raw / "media" / "图-a1b2c3d4e5f6.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            titles = self.titles(layout)
            self.assertEqual(titles["raw/web/a-000000000001.html"], "")
            self.assertEqual(titles["raw/media/图-a1b2c3d4e5f6.png"], "")

    def test_every_entry_still_carries_its_path_and_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            stored = layout.raw / "web" / "a-000000000001.html"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(page("<title>名字</title>"))
            item = next(
                entry
                for entry in list_project_tree(layout)
                if entry["path"].endswith("a-000000000001.html")
            )
            self.assertEqual(item["name"], "a-000000000001.html")
            self.assertEqual(item["title"], "名字")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
