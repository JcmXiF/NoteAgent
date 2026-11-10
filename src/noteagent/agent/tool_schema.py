"""The slice of JSON Schema a tool's arguments are checked against.

The schema lives with the ``ToolSpec`` and is also what the model is offered, so
the two can never disagree about an argument. Only the keywords the catalog uses
are understood: an unknown one is ignored rather than guessed at, which keeps a
typo in a schema from silently rejecting every call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TypeAlias

#: JSON values as ``json`` decodes them.
JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


class ToolArgumentError(ValueError):
    """The arguments a model sent do not satisfy the tool's schema."""


def validate_arguments(schema: Mapping[str, object], value: object) -> None:
    """Raise ``ToolArgumentError`` unless *value* satisfies *schema*."""
    _check(schema, value, "arguments")


def _check(schema: object, value: object, path: str) -> None:
    if not isinstance(schema, Mapping):
        return
    expected = schema.get("type")
    if isinstance(expected, str):
        _check_type(expected, value, path)
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        allowed = ", ".join(repr(item) for item in enum)
        raise ToolArgumentError(f"{path} must be one of {allowed}")
    if isinstance(value, str):
        _check_length(schema, value, path)
    if isinstance(value, list):
        _check_list(schema, value, path)
    if isinstance(value, Mapping):
        _check_object(schema, value, path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _check_number(schema, value, path)


def _check_type(expected: str, value: object, path: str) -> None:
    matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        # JSON has no separate boolean type; Python's bool is an int, and a
        # schema saying "integer" must not accept true.
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)
    if not matches:
        raise ToolArgumentError(f"{path} must be a {expected}")


def _check_length(schema: Mapping[str, object], value: str, path: str) -> None:
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(value) < minimum:
        raise ToolArgumentError(f"{path} must be at least {minimum} characters")
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(value) > maximum:
        raise ToolArgumentError(f"{path} must be at most {maximum} characters")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value)
        except re.error:
            # A bad pattern is the tool author's mistake, not the model's; do
            # not turn every call into a refusal because of it.
            matched = None
        if matched is None:
            raise ToolArgumentError(f"{path} must match {pattern}")


def _check_list(schema: Mapping[str, object], value: list, path: str) -> None:
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        raise ToolArgumentError(f"{path} must have at least {minimum} items")
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        raise ToolArgumentError(f"{path} must have at most {maximum} items")
    items = schema.get("items")
    if items is not None:
        for index, item in enumerate(value):
            _check(items, item, f"{path}[{index}]")


def _check_object(
    schema: Mapping[str, object], value: Mapping, path: str
) -> None:
    properties = schema.get("properties")
    known = properties if isinstance(properties, Mapping) else {}
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                raise ToolArgumentError(f"{path}.{key} is required")
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in known:
                raise ToolArgumentError(f"{path}.{key} is not a known argument")
    for key, item in value.items():
        if key in known:
            _check(known[key], item, f"{path}.{key}")


def _check_number(
    schema: Mapping[str, object], value: int | float, path: str
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        raise ToolArgumentError(f"{path} must be at least {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        raise ToolArgumentError(f"{path} must be at most {maximum}")
