"""A refusal the interface can say, and the codes it needs a sentence for.

The core answers a refusal with a sentence for a log plus a stable code; the
interface words that code in the reader's language. Two things can then go wrong
quietly, and both are checked here:

- a code with no sentence in one of the catalogs, which leaves the reader with the
  fallback wording instead of the one written for their situation;
- a code, or its figures, dropped on the way out, which is the same thing one step
  earlier.

The codes are read out of the core rather than listed, so a new refusal is covered
by adding its sentence and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path
from typing import Any

from noteagent.api.errors import (
    CodedHTTPException,
    coded_error_handler,
    http_error,
    refusal_handler,
)
from noteagent.api.sse import sse_error_payload
from noteagent.util.refusal import Refusal

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "src" / "noteagent"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

#: A class that names its situations, and the classes it is built from.
CLASS_DECL = re.compile(r"^class (\w+)\(([^)]*)\):", re.MULTILINE)
#: A class attribute naming a situation: `code = "a.b"` alone on a line of its own.
DECLARED = re.compile(r'^\s*code = "([\w.]+)"\s*$', re.MULTILINE)


def core_sources() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(CORE.rglob("*.py"))]


def coded_class_names() -> list[str]:
    """Every class whose instances can carry a code, by inheritance from the root."""
    bases: dict[str, str] = {}
    for source in core_sources():
        bases.update(CLASS_DECL.findall(source))
    coded = {"CodedError"}
    settled = False
    while not settled:
        settled = True
        for name, parents in bases.items():
            if name in coded:
                continue
            if any(parent.strip().split(".")[-1] in coded for parent in parents.split(",")):
                coded.add(name)
                settled = False
    return sorted(coded)


def core_codes() -> set[str]:
    """Every situation the core can name, as the interface sees it."""
    # A code is raised either through the class that owns it (`SearchError("a.b")`)
    # or per instance (`Refusal("a.b")`), so both forms are read.
    raised = re.compile(
        r'(?:' + "|".join(coded_class_names()) + r')\(\s*"([\w.]+)"'
    )
    found: set[str] = set()
    for source in core_sources():
        found.update(raised.findall(source))
        found.update(DECLARED.findall(source))
    return found


def catalog_keys(language: str) -> set[str]:
    """Every key a catalog defines, flattened the way the frontend reads them."""
    raw = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    keys: set[str] = set()

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{prefix}.{key}" if prefix else key)
            return
        keys.add(prefix)

    walk(raw, "")
    return keys


class CoreCodeTests(unittest.TestCase):
    def test_the_scan_finds_the_codes_without_inventing_ones(self) -> None:
        # A scan that quietly stopped matching would make the check below pass for
        # no reason, and the markers are the ones that catch that.
        found = core_codes()
        self.assertIn("search.running", found)
        self.assertIn("project.name.tooLong", found)
        self.assertIn("provider.notConfigured", found)
        # A local variable called `code` is not a situation the interface is told
        # about, and its values must not be read as codes.
        self.assertNotIn("replaced", found)
        self.assertNotIn("written", found)

    def test_every_code_has_a_sentence_in_every_catalog(self) -> None:
        keys = {language: catalog_keys(language) for language in ("zh", "en")}
        missing = sorted(
            f"{language}: {code}"
            for code in core_codes()
            for language in keys
            if f"error.code.{code}" not in keys[language]
        )
        self.assertEqual(
            missing,
            [],
            "codes with no sentence for the reader: " + ", ".join(missing),
        )


class RefusalAnswerTests(unittest.TestCase):
    def test_an_http_error_carries_the_code_and_its_figures(self) -> None:
        refusal = Refusal(
            "project.exists",
            "project already exists: rl",
            params={"name": "rl"},
            status=409,
        )
        response = http_error(refusal, 400)
        self.assertIsInstance(response, CodedHTTPException)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.code, "project.exists")
        self.assertEqual(response.params, {"name": "rl"})
        # What a client that knows nothing about codes reads is still a sentence.
        self.assertEqual(response.detail, "project already exists: rl")

    def test_an_error_without_a_code_stays_a_plain_answer(self) -> None:
        response = http_error(ValueError("no idea"), 400)
        self.assertNotIsInstance(response, CodedHTTPException)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.detail, "no idea")

    def test_the_answer_bodies_carry_what_the_interface_reads(self) -> None:
        refusal = Refusal("search.unsettled", "pending", params={"count": "2"})
        body = self._body(refusal_handler, refusal)
        self.assertEqual(
            body, {"detail": "pending", "code": "search.unsettled", "params": {"count": "2"}}
        )

        coded = http_error(refusal, 400)
        assert isinstance(coded, CodedHTTPException)
        self.assertEqual(self._body(coded_error_handler, coded), body)

    def test_a_code_with_no_figures_sends_none(self) -> None:
        body = self._body(refusal_handler, Refusal("note.empty", "a note has no content"))
        self.assertEqual(body, {"detail": "a note has no content", "code": "note.empty"})

    def _body(self, handler: Any, err: Any) -> dict[str, Any]:
        response = asyncio.run(handler(None, err))
        return json.loads(response.body)


class SseErrorPayloadTests(unittest.TestCase):
    def test_a_job_error_carries_the_code_and_its_figures(self) -> None:
        payload = sse_error_payload(
            Refusal("search.wrongStep", "step 2", params={"current": "1", "asked": "2"})
        )
        self.assertEqual(
            payload,
            {
                "detail": "step 2",
                "code": "search.wrongStep",
                "params": {"current": "1", "asked": "2"},
            },
        )

    def test_a_job_error_with_no_code_carries_a_sentence_only(self) -> None:
        payload = sse_error_payload(ValueError("the model said nothing"))
        self.assertEqual(payload, {"detail": "the model said nothing"})


if __name__ == "__main__":
    unittest.main()
