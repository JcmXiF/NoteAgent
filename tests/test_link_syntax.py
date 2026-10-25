from __future__ import annotations

import unittest
from pathlib import Path

from noteagent.wiki.link_syntax import (
    WIKILINK,
    WIKILINK_SOURCE,
    code_spans,
    find_wikilinks,
    format_wikilink,
)

TS_SOURCE = (
    Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "wikilink.ts"
)


def targets(text: str) -> list[str]:
    return [link.target for link in find_wikilinks(text)]


class FindWikiLinksTests(unittest.TestCase):
    def test_bare_link(self) -> None:
        (link,) = find_wikilinks("见 [[信息熵]]。")
        self.assertEqual(link.target, "信息熵")
        self.assertIsNone(link.label)
        self.assertEqual(link.display, "信息熵")
        self.assertEqual((link.start, link.end), (2, 9))

    def test_label_link(self) -> None:
        (link,) = find_wikilinks("见 [[concepts/信息熵|熵]]。")
        self.assertEqual(link.target, "concepts/信息熵")
        self.assertEqual(link.label, "熵")
        self.assertEqual(link.display, "熵")

    def test_blank_label_reads_as_the_target(self) -> None:
        (link,) = find_wikilinks("[[a|]]")
        self.assertEqual((link.target, link.display), ("a", "a"))

    def test_target_and_label_are_trimmed(self) -> None:
        (link,) = find_wikilinks("[[  a  |  A  ]]")
        self.assertEqual((link.target, link.label, link.display), ("a", "A", "A"))

    def test_several_links_come_out_in_order(self) -> None:
        self.assertEqual(targets("[[a]] 与 [[b]] [[c]]"), ["a", "b", "c"])

    # An embed asks for a file's content, which nothing here resolves. Reading one
    # as a link would point at a page named after the image.
    def test_embed_is_not_a_link(self) -> None:
        self.assertEqual(targets("![[diagram.png]]"), [])
        self.assertEqual(targets("![[a.png]] 见 [[b]]"), ["b"])

    # A link is written on one line; a `[[` left open is just text.
    def test_link_never_spans_a_line(self) -> None:
        self.assertEqual(targets("[[a\nb]]"), [])
        self.assertEqual(targets("[[a\nb]] [[c]]"), ["c"])

    def test_empty_target_is_not_a_link(self) -> None:
        self.assertEqual(targets("[[]]"), [])
        self.assertEqual(targets("[[   ]]"), [])

    def test_a_lone_bracket_pair_is_text(self) -> None:
        self.assertEqual(targets("[[a]]]"), ["a"])
        # A third `[` is read as part of the target: the match starts at the
        # leftmost `[[` and the target runs to the first `]]`.
        self.assertEqual(targets("[[[a]]"), ["[a"])

    def test_link_inside_a_fenced_block_is_code(self) -> None:
        body = "如何写链接：\n\n```md\n[[信息熵]]\n```\n\n[[真实]]\n"
        self.assertEqual(targets(body), ["真实"])

    def test_link_inside_an_inline_span_is_code(self) -> None:
        self.assertEqual(targets("写 `[[信息熵]]` 就能链接，见 [[熵]]。"), ["熵"])
        self.assertEqual(targets("``[[a]]``"), [])

    # A four-backtick fence holds a three-backtick example, so the inner fence
    # neither closes the block nor opens one of its own.
    def test_a_wider_fence_holds_a_narrower_one(self) -> None:
        body = "````md\n```\n[[a]]\n```\n````\n\n[[b]]\n"
        self.assertEqual(targets(body), ["b"])

    def test_tilde_fence(self) -> None:
        self.assertEqual(targets("~~~\n[[a]]\n~~~\n\n[[b]]\n"), ["b"])

    def test_unclosed_fence_runs_to_the_end(self) -> None:
        self.assertEqual(targets("```\n[[a]]\n[[b]]"), [])

    def test_code_spans_are_offsets_into_the_text(self) -> None:
        body = "前 `[[a]]` 后"
        self.assertEqual(code_spans(body), ((2, 9),))
        self.assertEqual(body[2:9], "`[[a]]`")

    def test_carriage_return_line_endings(self) -> None:
        body = "如何写：\r\n\r\n```md\r\n[[a]]\r\n```\r\n\r\n[[b]]\r\n"
        self.assertEqual(targets(body), ["b"])


class FormatWikiLinkTests(unittest.TestCase):
    def test_bare_form(self) -> None:
        self.assertEqual(format_wikilink("concepts/kl"), "[[concepts/kl]]")

    def test_label_form(self) -> None:
        self.assertEqual(
            format_wikilink("concepts/kl", "KL 散度"), "[[concepts/kl|KL 散度]]"
        )

    def test_a_label_that_repeats_the_target_is_dropped(self) -> None:
        self.assertEqual(format_wikilink("kl", "kl"), "[[kl]]")
        self.assertEqual(format_wikilink("kl", "  "), "[[kl]]")
        self.assertEqual(format_wikilink("  kl  "), "[[kl]]")

    def test_rejects_what_it_cannot_write(self) -> None:
        # A target may hold none of these: each would end the link early.
        for text in ("a|b", "a]b", "a\nb", "a\rb"):
            with self.subTest(target=text):
                with self.assertRaises(ValueError):
                    format_wikilink(text)
        # A label may hold a `|`; `]` and a line end still cut it short.
        for text in ("a]b", "a\nb", "a\rb"):
            with self.subTest(label=text):
                with self.assertRaises(ValueError):
                    format_wikilink("a", text)
        with self.assertRaises(ValueError):
            format_wikilink("   ")

    # Only the characters that really end a link are turned away. A `[` or a `|`
    # inside a label ends nothing, so a label may hold one.
    def test_a_label_may_hold_a_bracket_or_a_bar(self) -> None:
        self.assertEqual(format_wikilink("a", "[b"), "[[a|[b]]")
        self.assertEqual(format_wikilink("a", "b|c"), "[[a|b|c]]")
        (link,) = find_wikilinks("[[a|b|c]]")
        self.assertEqual((link.target, link.display), ("a", "b|c"))

    # The whole point of having the rule in one place: what a writer puts on the
    # page is what a reader of that page finds, target and display alike.
    def test_what_is_written_reads_back_the_same(self) -> None:
        for target, label in (
            ("concepts/kl", None),
            ("concepts/kl", "KL 散度"),
            ("信息熵", None),
            ("raw/web/x.html#b2", None),
            ("Some Page", "带空格的页"),
            ("a|b", None),
        ):
            with self.subTest(target=target, label=label):
                if target == "a|b":
                    with self.assertRaises(ValueError):
                        format_wikilink(target, label)
                    continue
                written = format_wikilink(target, label)
                (link,) = find_wikilinks(f"前缀 {written} 后缀")
                self.assertEqual(link.target, target)
                self.assertEqual(link.display, label or target)
                # And nothing about it is code or an embed.
                self.assertEqual(targets(written), [target])


class PatternTests(unittest.TestCase):
    def test_pattern_matches_the_whole_of_a_link(self) -> None:
        match = WIKILINK.search("[[a|b]]")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual((match.group(1), match.group(2)), ("a", "b"))

    def test_the_frontend_compiles_the_same_pattern(self) -> None:
        if not TS_SOURCE.is_file():
            self.skipTest("the frontend sources are not checked out")
        source = TS_SOURCE.read_text(encoding="utf-8")
        marker = "WIKILINK_PATTERN = String.raw`"
        start = source.index(marker) + len(marker)
        end = source.index("`", start)
        self.assertEqual(source[start:end], WIKILINK_SOURCE)


if __name__ == "__main__":
    unittest.main()
