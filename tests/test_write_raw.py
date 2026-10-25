from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import noteagent.api.projects as projects_mod
from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import (
    raw_relative_path_for_url,
    render_capture_source,
    resolve_raw_write_path,
    write_raw_captures,
)
from noteagent.api.save import capture_from_api, run_save_payload
from noteagent.project.init import init_project
from noteagent.project.identity import source_identity
from noteagent.util.run_log import RunLogger, run_log_path

LONG_BODY = "KL散度是两个概率分布之间的差异度量。" * 20


def _article_html(body: str = LONG_BODY, title: str = "相对熵") -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html><head>"
        f"<title>{title}</title>"
        '<meta name="noteagent:source_url" content="https://zh.wikipedia.org/wiki/相对熵">'
        "</head><body><article>"
        f"<p id=\"na-b1\" data-na-block=\"b1\">{body}</p>"
        "</article></body></html>\n"
    )


def _ok_capture(**overrides: object) -> Capture:
    html = _article_html()
    data = dict(
        url="https://zh.wikipedia.org/wiki/%E7%9B%B8%E5%AF%B9%E7%86%B5",
        title="相对熵",
        markdown=LONG_BODY,
        html=html,
        capture_method="trafilatura-html",
        fidelity="ok",
        reasons=(),
        http_status=200,
        final_url="https://zh.wikipedia.org/wiki/相对熵",
        char_count=len(LONG_BODY),
    )
    data.update(overrides)
    return Capture(**data)  # type: ignore[arg-type]


class RawPathTests(unittest.TestCase):
    def test_same_url_is_stable(self) -> None:
        url = "https://example.com/a/b"
        first = raw_relative_path_for_url(url, title="One")
        second = raw_relative_path_for_url(url, title="Two")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("raw/web/"))
        self.assertNotIn("/example.com/", first)
        self.assertIn("b-", first)
        self.assertTrue(first.endswith(".html"))

    def test_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(ValueError):
                resolve_raw_write_path(layout, "wiki/concepts/x.md")
            with self.assertRaises(ValueError):
                resolve_raw_write_path(layout, "raw/web/../wiki/x.md")


class WriteRawTests(unittest.TestCase):
    def test_writes_html_with_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [_ok_capture()])
            self.assertEqual(len(result.written_paths), 1)
            path = layout.root / result.written_paths[0]
            self.assertTrue(str(path).endswith(".html"))
            text = path.read_text(encoding="utf-8")
            self.assertIn("noteagent:source_url", text)
            self.assertIn("wikipedia.org", text)
            self.assertIn("KL散度", text)
            self.assertIn("<!DOCTYPE html>", text)
            self.assertEqual(
                source_identity(path, project_root=layout.root),
                result.written_paths[0],
            )

    def test_writes_into_the_kind_folder_and_no_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            result = write_raw_captures(layout, [_ok_capture()])
            relative = result.written_paths[0]
            self.assertRegex(relative, r"^raw/web/[^/]+\.html$")
            self.assertTrue((layout.raw / "web").is_dir())
            self.assertFalse((layout.raw / "pdf").exists())
            self.assertFalse((layout.raw / "sources").exists())
            self.assertEqual(
                [child.name for child in layout.raw.iterdir()], ["web"]
            )

    def test_skips_failed_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            failed = _ok_capture(fidelity="failed", markdown="", reasons=("http_blocked",))
            short = _ok_capture(fidelity="ok", markdown="太短", html="<p>太短</p>", title="短")
            result = write_raw_captures(layout, [failed, short])
            self.assertEqual(result.written_paths, ())
            self.assertEqual({item.code for item in result.items}, {"skipped"})

    def test_overwrite_same_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            first = write_raw_captures(layout, [_ok_capture()])
            second = write_raw_captures(
                layout,
                [
                    _ok_capture(
                        markdown=LONG_BODY + "第二版。",
                        html=_article_html(LONG_BODY + "第二版。"),
                    )
                ],
            )
            self.assertEqual(first.written_paths, second.written_paths)
            self.assertEqual(second.items[0].code, "replaced")
            text = (layout.root / second.written_paths[0]).read_text(encoding="utf-8")
            self.assertIn("第二版", text)

    def test_run_log_omits_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            write_raw_captures(layout, [_ok_capture()], run_logger=logger)
            blob = run_log_path(layout).read_text(encoding="utf-8")
        self.assertIn("raw.write", blob)
        self.assertNotIn("KL散度", blob)

    def test_render_quotes_url(self) -> None:
        text = render_capture_source(_ok_capture())
        self.assertIn("noteagent:source_url", text)
        self.assertIn("https://", text)
        self.assertIn("noteagent:captured_at", text)


class SaveApiTests(unittest.TestCase):
    def test_writes_named_project_not_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pointer = init_project(repo / "projects" / "alpha")
            target = init_project(repo / "projects" / "beta")
            (repo / ".noteagent-project").write_text(
                json.dumps({"path": "projects/alpha"}, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                capture = capture_from_api(
                    url="https://example.com/kl",
                    title="KL",
                    markdown=LONG_BODY,
                    html=_article_html(),
                    fidelity="ok",
                )
                payload = run_save_payload([capture], project="beta")
            written = payload["written"]
            self.assertEqual(len(written), 1)
            self.assertTrue((target.root / written[0]).is_file())
            self.assertFalse((pointer.root / written[0]).is_file())


if __name__ == "__main__":
    unittest.main()
