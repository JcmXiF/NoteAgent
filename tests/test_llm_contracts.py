"""The rule that keeps a hidden constraint out of the model's way.

A check applied after a model answers may only read what the request carried.
Otherwise the answer is refused for a reason the model could not have known, or it is
quietly altered by something nobody saw — the two failures this work exists to remove.

These tests hold two things: that no check reads outside its request, and that every
place the model is asked something has a declared contract. Both are enforced against
the source, so the next model call cannot arrive without one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.llm.contracts import (
    KNOWN_EXCEPTIONS,
    MODEL_CALLS,
    ModelCall,
    PostCheck,
    call_sites_on_disk,
    check_id,
    declared_call_sites,
    undeclared_sources,
    violations,
)


class TheRuleTests(unittest.TestCase):
    def test_no_check_reads_what_the_request_did_not_carry(self) -> None:
        # The known exceptions are decisions, not oversights: this fails if a new one
        # appears, and it fails if one is fixed without being removed from the list.
        self.assertEqual(violations(), sorted(KNOWN_EXCEPTIONS))

    def test_every_exception_says_why(self) -> None:
        for name, reason in KNOWN_EXCEPTIONS.items():
            self.assertTrue(reason.strip(), name)
            self.assertGreater(len(reason), 40, name)

    def test_the_round_weighs_its_answer_against_the_graph_it_was_given(self) -> None:
        # A round's answer is judged twice against the graph: whether a page it left
        # unnamed is new or a change, and whether a link it proposed can be drawn. Both
        # may only read the summary the prompt carries — the project holds pages that
        # went into no request, and the summary is capped and, when seeded, is one
        # neighbourhood of it.
        call = next(item for item in MODEL_CALLS if item.name == "round-pages")
        self.assertIn("graph-summary", call.given)
        declared = {check.name: check for check in call.checks}
        for name in ("default-page-action", "drawable-overlay-edges"):
            self.assertIn(name, declared)
            self.assertEqual(declared[name].needs, frozenset({"graph-summary"}))

    def test_a_report_is_not_a_check(self) -> None:
        # A report tells the reader afterwards and decides nothing, so it is allowed
        # to need the whole project — which no request could carry.
        call = ModelCall(
            name="example",
            source="example.py",
            given=frozenset({"prompt"}),
            checks=(PostCheck("doctor", "report", frozenset({"whole-project"})),),
        )
        self.assertEqual(violations((call,)), [])

    def test_a_check_reading_outside_its_request_is_caught(self) -> None:
        call = ModelCall(
            name="example",
            source="example.py",
            given=frozenset({"prompt"}),
            checks=(PostCheck("looks-elsewhere", "gate", frozenset({"elsewhere"})),),
        )
        self.assertEqual(violations((call,)), ["example/looks-elsewhere"])

    def test_constants_need_nothing_declared(self) -> None:
        # A cap compares against a number in the code, not against information.
        call = ModelCall(
            name="example",
            source="example.py",
            given=frozenset(),
            checks=(PostCheck("cap", "clamp"),),
        )
        self.assertEqual(violations((call,)), [])


class DeclaredCallsTests(unittest.TestCase):
    def test_every_call_in_the_source_is_declared(self) -> None:
        self.assertEqual(undeclared_sources(), [])

    def test_the_declarations_match_what_is_actually_there(self) -> None:
        on_disk = call_sites_on_disk()
        declared = declared_call_sites()
        self.assertEqual(sorted(on_disk), sorted(declared))
        self.assertEqual(sum(on_disk.values()), sum(declared.values()))

    def test_a_call_without_a_contract_is_reported(self) -> None:
        # The checker has to notice a call nobody declared, or it protects nothing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "somewhere").mkdir()
            (root / "somewhere" / "new.py").write_text(
                "def go(client):\n    return client.chat([])\n", encoding="utf-8"
            )
            problems = undeclared_sources(root)
            self.assertIn(
                "somewhere/new.py: 1 call(s) in the source, 0 declared", problems
            )

    def test_a_declaration_pointing_at_nothing_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problems = undeclared_sources(Path(tmp))
            # Nothing is on disk, so every declared file is short of its calls.
            self.assertEqual(len(problems), len(declared_call_sites()))
            self.assertTrue(all("0 call(s) in the source" in item for item in problems))

    def test_a_matching_declaration_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(
                "def go(client):\n    return client.complete([])\n", encoding="utf-8"
            )
            calls = (ModelCall(name="a", source="a.py", given=frozenset()),)
            self.assertEqual(undeclared_sources(root, calls), [])

    def test_each_named_call_points_at_a_real_file(self) -> None:
        base = Path(__file__).resolve().parent.parent / "src" / "noteagent"
        for call in MODEL_CALLS:
            self.assertTrue((base / call.source).is_file(), call.source)
            self.assertTrue(call.name.strip(), call.source)

    def test_call_names_are_unique(self) -> None:
        names = [call.name for call in MODEL_CALLS]
        self.assertEqual(len(names), len(set(names)))

    def test_a_violation_is_named_the_way_the_list_holds_it(self) -> None:
        call = ModelCall(
            name="somewhere",
            source="somewhere.py",
            given=frozenset(),
            checks=(PostCheck("reads-hidden", "gate", frozenset({"hidden"})),),
        )
        self.assertEqual(check_id(call, call.checks[0]), "somewhere/reads-hidden")


if __name__ == "__main__":
    unittest.main()
