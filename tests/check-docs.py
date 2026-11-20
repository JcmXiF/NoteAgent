#!/usr/bin/env python3
"""Checks the Markdown this repository keeps its records in.

Four faults are silent until someone reads the rendered page:

* a code fence whose closing line was glued to the end of the previous line, so
  hundreds of lines were rendered as code;
* a table row with a different number of columns from its header, which splits
  the table - usually because a `|` inside inline code was not escaped;
* a file saved with a BOM or with CRLF endings, which shows up as a whole-file
  diff for a one-line change;
* a cross-reference to a section title that no longer exists, which happens when
  a section is rewritten or dropped and the pages that pointed at it are not.
  The reference reads fine and leads nowhere.

It also guards three drifts the rendered page cannot show. These read local
docs, which are gitignored, so they are skipped when the files are absent (CI):

* an ADR named in the harness index with no file under `docs/adr/`;
* the Task tool table in the contract disagreeing with the registered tools;
* the observation whitelist in the contract drifting from the code.

Checks the repository's own Markdown: `docs/` and the root files. Run with
`python tests/check-docs.py`; part of CI.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted(ROOT.glob("*.md")),
]

FENCE = re.compile(r"^\s*(```|~~~)")
# A section is pointed at by its title in corner brackets, as in `§「内容分区」`.
SECTION_REF = re.compile(r"§「([^」]+)」")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
# A row in the harness ADR index, whose first cell is the four-digit id.
ADR_ROW = re.compile(r"^\|\s*(\d{4})\s*\|")
# A row in the Task tool table, whose first cell is the tool name in backticks.
TASK_TOOL_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|")
# The contract's one-line observation whitelist.
OBSERVATION_DOC = re.compile(r"`ToolResult\.observation\(\)` 白名单：(.+)")
# The code's whitelist tuple and the tool-name constants the specs use.
OBSERVATION_SOURCE = re.compile(r"OBSERVATION_FIELDS = \((.*?)\n\)", re.S)
TOOL_CONSTANT = re.compile(
    r'^([A-Z][A-Z0-9_]*) = "([a-z][a-z0-9_]*)"', re.M
)
TOOL_NAME = re.compile(r"\bname=([A-Z][A-Z0-9_]*)\b")


def headings_of(text: str) -> list[str]:
    """The title text of every heading, which is what a section reference names."""
    return [
        match.group(1).strip()
        for line in text.split("\n")
        if (match := HEADING.match(line))
    ]


def read(path: pathlib.Path) -> tuple[str, bytes]:
    raw = path.read_bytes()
    return raw.decode("utf-8", errors="replace"), raw


def check_encoding(path: pathlib.Path, raw: bytes, problems: list[str]) -> None:
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("starts with a BOM (should be plain UTF-8)")
    if b"\r\n" in raw:
        problems.append("has CRLF line endings (should be LF)")
    if b"\r" in raw.replace(b"\r\n", b""):
        problems.append("has a bare carriage return")


def check_fences(path: pathlib.Path, text: str, problems: list[str]) -> None:
    fences = 0
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fences += 1
            continue
        # A fence marker inside a line opens or closes nothing, and it is what a
        # closing fence looks like when it has lost its newline. Everything after
        # such a line is rendered as code until the next marker, which is how one
        # glued fence turned hundreds of lines into a code block here.
        for token in ("```", "~~~"):
            if token in line:
                problems.append(
                    f"line {number}: fence marker inside a line "
                    f"(a closing fence may have lost its newline)"
                )
                break
    if fences % 2:
        problems.append(f"has {fences} fences, an odd number (one is unclosed)")


def check_tables(path: pathlib.Path, text: str, problems: list[str]) -> None:
    """Every row of a table must have the same number of columns as its header."""
    in_fence = False
    header: int | None = None
    header_line = 0

    def columns(line: str) -> int:
        # A `|` inside inline code still splits the table unless it is escaped,
        # which is exactly the mistake this looks for.
        body = line.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        return len(re.split(r"(?<!\\)\|", body))

    for number, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        is_separator = (
            stripped.replace("|", "").replace("-", "").replace(":", "").strip() == ""
        )
        if header is None or is_separator:
            header = columns(line)
            header_line = number
            continue
        if columns(line) != header:
            problems.append(
                f"line {number}: {columns(line)} columns where the table's header "
                f"(line {header_line}) has {header}"
            )


def check_section_refs(
    path: pathlib.Path, text: str, headings: list[str], problems: list[str]
) -> None:
    """Every `§「title」` reference must name a heading that still exists somewhere.

    The heading may live in another document - a reference is allowed to cross
    files - so the set of titles to match against is every heading in the repo.
    """
    for number, line in enumerate(text.split("\n"), 1):
        for title in SECTION_REF.findall(line):
            if not any(title in heading for heading in headings):
                problems.append(f"line {number}: §「{title}」 names a section no heading has")


def section(text: str, heading: str) -> str:
    """The lines under one level-two heading, up to the next heading."""
    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"## {heading}"):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def check_adr_index(problems: list[str]) -> bool:
    """Every ADR the harness index names has a file. Local docs only."""
    index = ROOT / "docs" / "agent-harness.md"
    adr_dir = ROOT / "docs" / "adr"
    if not index.is_file() or not adr_dir.is_dir():
        return False
    table = section(read(index)[0], "决策索引")
    seen: set[str] = set()
    for line in table.split("\n"):
        match = ADR_ROW.match(line.strip())
        if match is None:
            continue
        adr_id = match.group(1)
        if adr_id in seen:
            problems.append(f"agent-harness.md: ADR {adr_id} appears twice")
        seen.add(adr_id)
        if not any(adr_dir.glob(f"{adr_id}-*.md")):
            problems.append(
                f"agent-harness.md: ADR {adr_id} has no file in docs/adr"
            )
    return True


def check_tool_catalog(problems: list[str]) -> bool:
    """The Task tool table and the registered names agree. Local docs only."""
    contract = ROOT / "docs" / "tool-contract.md"
    source_path = ROOT / "src" / "noteagent" / "task" / "tools.py"
    if not contract.is_file() or not source_path.is_file():
        return False
    table = section(read(contract)[0], "写作任务工具")
    documented = {
        match.group(1)
        for line in table.split("\n")
        if (match := TASK_TOOL_ROW.match(line.strip()))
    }
    source = read(source_path)[0]
    constants = dict(TOOL_CONSTANT.findall(source))
    body = (
        source.split("def task_tools", 1)[-1]
        .split("def _search_handler", 1)[0]
    )
    registered = {
        constants[name] for name in TOOL_NAME.findall(body) if name in constants
    }
    for name in sorted(documented - registered):
        problems.append(
            f"tool-contract.md: {name} is in the Task table but not registered"
        )
    for name in sorted(registered - documented):
        problems.append(
            f"tool-contract.md: {name} is registered but not in the Task table"
        )
    return True


def check_observation_fields(problems: list[str]) -> bool:
    """The contract's observation whitelist matches the code. Local docs only."""
    contract = ROOT / "docs" / "tool-contract.md"
    source_path = ROOT / "src" / "noteagent" / "agent" / "tools.py"
    if not contract.is_file() or not source_path.is_file():
        return False
    match = OBSERVATION_DOC.search(read(contract)[0])
    if match is None:
        problems.append("tool-contract.md: no observation whitelist line")
        return True
    parts = [
        part.strip() for part in match.group(1).rstrip("。").split(" + ")
    ]
    documented: set[str] = set()
    # The first part is the event label and two meta fields; the last is the
    # separate diagnostics channel, not a meta field.
    for part in parts[:-1]:
        documented.update(
            field.strip() for field in part.split("/") if field.strip()
        )
    documented.discard("tool")
    tuple_match = OBSERVATION_SOURCE.search(read(source_path)[0])
    if tuple_match is None:
        problems.append("agent/tools.py: no OBSERVATION_FIELDS tuple")
        return True
    fields = set(re.findall(r'"([a-z_]+)"', tuple_match.group(1)))
    for field in sorted(fields - documented):
        problems.append(
            f"tool-contract.md: observation field {field} is missing"
        )
    for field in sorted(documented - fields):
        problems.append(
            f"tool-contract.md: observation field {field} is not in the code"
        )
    return True


def main() -> int:
    failed = False
    documents = [(path, *read(path)) for path in TARGETS]
    headings = [heading for _, text, _ in documents for heading in headings_of(text)]
    for path, text, raw in documents:
        problems: list[str] = []
        check_encoding(path, raw, problems)
        check_fences(path, text, problems)
        check_tables(path, text, problems)
        check_section_refs(path, text, headings, problems)
        relative = path.relative_to(ROOT)
        if problems:
            failed = True
            print(f"{relative}:")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"{relative}: ok")

    guard_problems: list[str] = []
    ran = [
        check_adr_index(guard_problems),
        check_tool_catalog(guard_problems),
        check_observation_fields(guard_problems),
    ]
    if any(ran):
        if guard_problems:
            failed = True
            print("semantic guards:")
            for problem in guard_problems:
                print(f"  {problem}")
        else:
            print("semantic guards: ok")

    if failed:
        print("\nFix the above; a rendered page is wrong until you do.")
        return 1
    print(f"\ndocs OK: {len(TARGETS)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
