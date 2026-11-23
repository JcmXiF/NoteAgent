"""What the model may call, and what a call hands back."""

from __future__ import annotations

import unittest

from agent_fakes import FixedClock
from noteagent.agent.tools import (
    Diagnostic,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    UnknownToolError,
)


def _spec(name: str = "wiki_read", **overrides) -> ToolSpec:
    values = {
        "name": name,
        "description": "Open a wiki page by slug.",
        "parameters": {"type": "object", "properties": {"targets": {"type": "array"}}},
        "handler": lambda arguments, context: ToolResult(content="page"),
    }
    values.update(overrides)
    return ToolSpec(**values)


class ToolSpecTests(unittest.TestCase):
    def test_a_result_has_content_for_the_model_and_meta_for_the_interface(self) -> None:
        result = ToolResult(
            content="the page body",
            meta={"title": "Entropy", "slug": "concepts/entropy"},
            diagnostics=(Diagnostic("warning", "truncated", "the page was long"),),
        )
        self.assertEqual(result.content, "the page body")
        self.assertEqual(result.meta["slug"], "concepts/entropy")
        self.assertEqual(result.diagnostics[0].code, "truncated")

    def test_a_context_carries_the_run_it_belongs_to(self) -> None:
        context = ToolContext(
            session_id="s1",
            run_id="r1",
            clock=FixedClock(),
        )
        self.assertEqual(context.session_id, "s1")
        self.assertIsNone(context.cancel)


class ToolRegistryTests(unittest.TestCase):
    def test_a_registered_tool_is_found_by_name(self) -> None:
        registry = ToolRegistry([_spec()])
        self.assertIsNotNone(registry.get("wiki_read"))
        self.assertIsNone(registry.get("search"))

    def test_require_names_an_unknown_tool(self) -> None:
        registry = ToolRegistry()
        with self.assertRaises(UnknownToolError):
            registry.require("search")

    def test_a_duplicate_name_is_refused(self) -> None:
        registry = ToolRegistry([_spec()])
        with self.assertRaises(ValueError):
            registry.register(_spec())

    def test_a_name_the_model_cannot_call_is_refused(self) -> None:
        for name in ("", "Wiki Read", "1read", "wiki-read", "wiki read"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ToolRegistry([_spec(name)])

    def test_a_tool_without_a_description_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ToolRegistry([_spec(description="")])

    def test_only_enabled_tools_are_offered_to_the_model(self) -> None:
        registry = ToolRegistry(
            [_spec("wiki_read"), _spec("search", enabled=False), _spec("run")]
        )
        self.assertEqual([tool.name for tool in registry.enabled()], ["wiki_read", "run"])

    def test_the_model_list_is_the_openai_function_shape(self) -> None:
        registry = ToolRegistry([_spec()])
        [offered] = registry.model_tools()
        self.assertEqual(offered["type"], "function")
        function = offered["function"]
        self.assertEqual(function["name"], "wiki_read")
        self.assertEqual(function["description"], "Open a wiki page by slug.")
        self.assertEqual(function["parameters"]["type"], "object")

    def test_a_registry_can_be_built_empty(self) -> None:
        self.assertEqual(ToolRegistry().enabled(), ())


if __name__ == "__main__":
    unittest.main()
