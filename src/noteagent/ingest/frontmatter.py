"""Read and edit YAML frontmatter without rewriting what a writer did not touch.

A page on disk is edited in place: the frontmatter block is re-emitted exactly as
it was written, and a single field is changed by editing its own line. Reading is
tolerant of a missing opening fence and of a stray ``---FILE:`` envelope, so a
page that a repair pass fixed still parses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import yaml

_FRONTMATTER = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)
_INLINE_LIST = re.compile(r"^\[(.*)\]\s*$")
_CLOSE_FENCE = re.compile(r"\n---\s*\n")
#: A ``---FILE:`` opener left inside a page (compose / repair).
_FILE_WRAPPER = re.compile(
    r"\A(?:---[ \t]*\n)?---[ \t]*FILE:[^\n]*\n(?:---[ \t]*\n)?",
    re.IGNORECASE,
)
_FILE_OPENER_LINE = re.compile(r"^---[ \t]*FILE:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """Markdown page split into YAML frontmatter fields and body."""

    fields: dict[str, str]  # raw scalar / inline-list string values
    body: str
    has_frontmatter: bool
    #: The frontmatter block exactly as written (fences, key order, quoting,
    #: and the whitespace that separates it from the body). ``prefix + body``
    #: is the input text with ``\r\n`` folded to ``\n``. Keeping it means a
    #: writer that only replaces the body re-emits the frontmatter untouched.
    prefix: str = ""


@dataclass(frozen=True, slots=True)
class FrontmatterEnsureResult:
    """Result of repairing / validating wiki frontmatter."""

    content: str
    code: str | None = None
    message: str | None = None


def strip_leftover_file_wrapper(content: str) -> str:
    """
    Remove a leftover ``---FILE:`` envelope from page text.

    The marker belongs in the LLM stream, not on disk. Compose and the
    missing-``---`` repair can wrap a real frontmatter block in one.
    """
    text = content.replace("\r\n", "\n")
    while True:
        stripped = _FILE_WRAPPER.sub("", text, count=1)
        if stripped == text:
            return text
        text = stripped


def parse_page(content: str) -> ParsedPage:
    """
    Split markdown into frontmatter fields + body.
    """
    text = strip_leftover_file_wrapper(content)
    return _parse_page_text(text)


def _parse_page_text(text: str) -> ParsedPage:
    match = _FRONTMATTER.match(text)
    if match:
        payload, rest = match.group(1), match.group(2)
        prefix = text[: match.start(2)]
        if _is_file_leftover_block(payload):
            return _parse_page_text(rest)
        loaded = _load_mapping(payload)
        if loaded is None:
            return ParsedPage(
                fields={}, body=rest, has_frontmatter=True, prefix=prefix
            )
        fields = {
            str(key): _yaml_value_to_field(value) for key, value in loaded.items()
        }
        return ParsedPage(
            fields=fields, body=rest, has_frontmatter=True, prefix=prefix
        )

    close = _CLOSE_FENCE.search(text)
    if close:
        payload, rest = text[: close.start()], text[close.end() :]
        if payload.strip() and not _is_file_leftover_block(payload):
            loaded = _load_mapping(payload)
            if loaded is not None:
                fields = {
                    str(key): _yaml_value_to_field(value)
                    for key, value in loaded.items()
                }
                # Missing opening fence: emit the repaired form on write.
                return ParsedPage(
                    fields=fields,
                    body=rest,
                    has_frontmatter=True,
                    prefix="---\n" + text[: close.end()],
                )

    return ParsedPage(fields={}, body=text, has_frontmatter=False)


def unquote_scalar(value: str) -> str:
    """
    Strip one layer of matching quotes from a YAML scalar.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def parse_inline_list(value: str) -> list[str]:
    """
    Parse ``[a, b]`` / ``["a", "b"]`` into a list of strings.
    Non-list scalars become a single-item list when non-empty.
    """
    text = value.strip()
    match = _INLINE_LIST.match(text)
    if not match:
        item = unquote_scalar(text)
        return [item] if item else []

    inner = match.group(1).strip()
    if not inner:
        return []

    items: list[str] = []
    current: list[str] = []
    in_quote: str | None = None
    for ch in inner:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            current.append(ch)
            continue
        if ch in "'\"":
            in_quote = ch
            current.append(ch)
            continue
        if ch == ",":
            piece = unquote_scalar("".join(current))
            if piece:
                items.append(piece)
            current = []
            continue
        current.append(ch)
    piece = unquote_scalar("".join(current))
    if piece:
        items.append(piece)
    return items


def format_inline_list(items: list[str]) -> str:
    """
    Format a list as inline YAML, quoting items that need it.
    """
    rendered: list[str] = []
    for item in items:
        if not item:
            continue
        if any(ch in item for ch in " :[]{},\"'") or item.startswith("["):
            escaped = item.replace("\\", "\\\\").replace('"', '\\"')
            rendered.append(f'"{escaped}"')
        else:
            rendered.append(item)
    return "[" + ", ".join(rendered) + "]"


#: Leading characters YAML reads as syntax rather than as part of a scalar.
_PLAIN_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"


def _yaml_scalar(value: str) -> str:
    """
    Write a scalar so it reads back as the same string.

    Quotes are added only when a plain value would not survive the round trip,
    so ordinary titles and ISO dates stay unquoted and readable.
    """
    if not value:
        return '""'
    needs_quotes = (
        value != value.strip()
        or ": " in value
        or value.endswith(":")
        or " #" in value
        or "\n" in value
        or value[0] in _PLAIN_INDICATORS
    )
    if not needs_quotes:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_scalar(value: str) -> str:
    """Render *value* as a YAML scalar that parses back to the same string."""
    return _yaml_scalar(value)


def _scalar_for_yaml(value: str) -> str:
    """
    Render a frontmatter value, leaving inline lists in list form.

    List fields reach here already rendered by :func:`format_inline_list`, whose
    quoting rules are the only ones that survive a YAML round trip.
    """
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        return text
    return _yaml_scalar(value)


def union_preserve_order(*groups: list[str]) -> list[str]:
    """
    Union lists while preserving first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
    return out


def render_page(fields: dict[str, str], body: str) -> str:
    """
    Render frontmatter + body. Field order: common keys first, then the rest.

    Builds a page from parsed fields, so it normalises what it is given: use it
    for a newly composed page. To rewrite a page already on disk, use
    :func:`reemit_page` / :func:`replace_page_body` instead, which keep the
    frontmatter exactly as written.
    """
    preferred = (
        "title",
        "created",
        "updated",
        "sources",
    )
    lines = ["---"]
    seen: set[str] = set()
    for key in preferred:
        if key in fields:
            lines.append(f"{key}: {_scalar_for_yaml(fields[key])}")
            seen.add(key)
    for key in sorted(k for k in fields if k not in seen):
        lines.append(f"{key}: {_scalar_for_yaml(fields[key])}")
    lines.append("---")
    if not body:
        body_text = "\n"
    else:
        body_text = body if body.startswith("\n") else "\n" + body
        if not body_text.endswith("\n"):
            body_text += "\n"
    return "\n".join(lines) + body_text


def reemit_page(page: ParsedPage, body: str | None = None) -> str:
    """
    Re-emit a parsed page, replacing the body only.

    ``prefix + body`` is the input text, so a page nobody edited comes back
    byte for byte: no YAML re-parse, no key re-ordering, no added quotes.
    """
    if body is None:
        return page.prefix + page.body
    if not body:
        return page.prefix
    return page.prefix + (body if body.endswith("\n") else body + "\n")


_CLOSING_FENCE = re.compile(r"^---[ \t]*$", re.MULTILINE)


def set_frontmatter_scalar(content: str, key: str, value: str) -> str:
    """
    Set a frontmatter field by editing that one line in place.

    Everything else keeps its exact bytes: other keys, their order, and their
    quoting. A field already holding the wanted value is left alone, so
    re-applying the same edit does not rewrite the page.
    """
    page = parse_page(content)
    if not page.has_frontmatter:
        return content
    if page.fields.get(key) == value:
        return content
    return _with_edited_field(page, key, _scalar_for_yaml(value))


def _with_edited_field(page: ParsedPage, key: str, rendered: str) -> str:
    line = f"{key}: {rendered}"
    lines = page.prefix.split("\n")
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*:")
    for index, text in enumerate(lines):
        if not pattern.match(text):
            continue
        end = index + 1
        # A block sequence or folded scalar keeps going on indented lines.
        while end < len(lines):
            following = lines[end]
            if following[:1] in (" ", "\t"):
                end += 1
                continue
            # A blank line only belongs here when an indented line follows it.
            if (
                following.strip() == ""
                and end + 1 < len(lines)
                and lines[end + 1][:1] in (" ", "\t")
            ):
                end += 1
                continue
            break
        lines[index:end] = [line]
        return "\n".join(lines) + page.body
    return _with_inserted_field(page, line)


def _with_inserted_field(page: ParsedPage, line: str) -> str:
    closing = None
    for match in _CLOSING_FENCE.finditer(page.prefix):
        closing = match
    if closing is None:
        return page.prefix + line + "\n" + page.body
    head = page.prefix[: closing.start()]
    tail = page.prefix[closing.start() :]
    return f"{head}{line}\n{tail}{page.body}"


def replace_page_body(content: str, body: str) -> str:
    """
    Keep frontmatter from *content*, swap in a new body.
    """
    page = parse_page(content)
    if not page.has_frontmatter:
        return body if body.endswith("\n") else body + "\n"
    return reemit_page(page, body)


def ensure_wiki_frontmatter(content: str) -> FrontmatterEnsureResult:
    """
    Strip leftover FILE wrappers; repair a missing opening ``---`` when the
    YAML mapping is otherwise valid. Never treat a ``---FILE:`` line as YAML.
    """
    text = content.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    stripped = strip_leftover_file_wrapper(text)
    wrapper_removed = stripped != text

    page = parse_page(stripped)
    if page.has_frontmatter:
        fenced = bool(_FRONTMATTER.match(stripped))
        if wrapper_removed or not fenced:
            repaired = stripped if fenced else "---\n" + stripped
            message = (
                "stripped leftover FILE wrapper from wiki page"
                if wrapper_removed
                else "repaired missing opening frontmatter delimiter"
            )
            return FrontmatterEnsureResult(
                repaired,
                code="repaired",
                message=message,
            )
        return FrontmatterEnsureResult(stripped)

    close = _CLOSE_FENCE.search(stripped)
    prefix = stripped[: close.start()] if close else ""
    if close and _is_yaml_mapping(prefix) and not _is_file_leftover_block(prefix):
        repaired = "---\n" + stripped
        if parse_page(repaired).has_frontmatter:
            return FrontmatterEnsureResult(
                repaired,
                code="repaired",
                message="repaired missing opening frontmatter delimiter",
            )

    if _is_yaml_mapping(stripped) and not _is_file_leftover_block(stripped):
        return FrontmatterEnsureResult(
            stripped,
            code="malformed_frontmatter",
            message="malformed YAML frontmatter (no closing ---)",
        )
    return FrontmatterEnsureResult(
        stripped,
        code="missing_frontmatter",
        message="missing YAML frontmatter",
    )


_FM_FENCE = re.compile(r"^---\s*\n.*?^---\s*\n?", re.MULTILINE | re.DOTALL)
_FILE_MARKER = re.compile(r"^---(?:FILE:[^\n]*|END FILE)---\s*$", re.MULTILINE)


def sanitize_log_entry(content: str) -> str:
    """
    Strip YAML frontmatter fences and FILE markers from a log append payload.
    """
    text = content.replace("\r\n", "\n").strip()
    if not text:
        return ""
    text = _FM_FENCE.sub("", text)
    text = _FILE_MARKER.sub("", text)
    return text.strip()


def _load_mapping(raw: str) -> dict[object, object] | None:
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        return None
    return loaded


def _is_yaml_mapping(raw: str) -> bool:
    loaded = _load_mapping(raw)
    return bool(loaded)


def _is_file_leftover_block(raw: str) -> bool:
    """True when a YAML candidate is a leftover ``---FILE:`` opener, not page metadata."""
    trimmed = raw.strip()
    if not trimmed:
        return False
    if _FILE_OPENER_LINE.search(trimmed):
        return True
    loaded = _load_mapping(raw)
    if not isinstance(loaded, dict) or len(loaded) != 1:
        return False
    key = str(next(iter(loaded))).lstrip("-").strip().upper()
    return key == "FILE"


def _yaml_value_to_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return format_inline_list([_scalar_to_str(item) for item in value])
    return _scalar_to_str(value)


def _scalar_to_str(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
