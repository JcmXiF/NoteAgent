"""How a second write lands on a page that already exists.

A page's frontmatter ``sources`` list is a claim about its prose: these are the
passages the text was written from. The list only grows, because every write
unions into it, so the claim is easy to outrun. Each rule below exists to keep
the two together; when they cannot both be kept, the page stays as it is.

The rules, in the order :func:`merge_page_content` applies them:

R1  No page on disk.
    The incoming text is written as it came. ``create``.

R2  The same text.
    Nothing is written. ``unchanged``.

R3  Frontmatter.
    :func:`merged_frontmatter` joins the two: ``title`` and ``created`` come from
    the page on disk, ``sources`` is the union, ``updated`` is today, a field only
    the page on disk has is carried over, and a field only the new text has is
    kept.

R4  The page claims nothing this write did not bring.
    Every source the page lists is part of the material this write carried, so
    the prose can be written again instead of merged. ``replace``. The annotations
    (``na-clip`` / ``na-ecg`` markers) still have to survive: they record which
    passages a section came from, and a rewrite that drops one leaves prose nobody
    can trace. Links may move — this is one text written again, not two joined.

R4b The body was written with this page's own body in front of it.
    The page-level rewrite of R0-1 asks a model for the whole page while showing
    it the page on disk, so the answer already accounts for both texts; asking a
    second model to fuse them would be a third account of one subject. It is
    written as it came — ``rewrite`` — under the same checks R7 applies to a
    merge answer: annotations kept, no annotation invented, links kept, and no
    shorter than ``RULES.smallest_body`` of the page it replaces. Links are kept
    here, unlike R4, because the rewrite was shown them.

    What it was shown is both texts, so both are what the check holds it to:
    ``rewrite_draft`` is the draft this write produced for the page, and a
    marker or a link only that draft carried must survive too. Checking the
    page alone would call a marker the new source earned "invented", and would
    let the draft's own attribution be dropped without a word.

R5  Only the frontmatter differs.
    The union already says everything new, so no model call. ``merge``.

R6  Two texts, one page.
    The model is asked for one page built from both. ``merge``.

R7  The answer would lose part of the page.
    The page is left exactly as it is. ``merge-fallback``. This covers an answer
    with no frontmatter, a dropped or invented annotation, an annotation no longer
    above a heading, a dropped ``[[link]]`` target, an answer shorter than
    ``RULES.smallest_body`` of the longer input, and a call that failed.

R7b One second attempt, with the reason.
    When a ``repair`` is supplied, a page that R7 would leave alone is put to the
    model once more, told what the last answer was refused for. A reply that
    passes the same checks is written — ``repair`` — and one that does not leaves
    the page alone with mode ``repair-fallback``, which the write path records as
    a hard failure: the page was refused twice, so the compile did not do what it
    set out to do and must not be remembered as a success. One attempt only; a
    repair that is refused is not repaired again.

A rejected answer leaves the page's ``sources`` alone too, for the reason this
docstring opens with: prose that was not absorbed must not be claimed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from noteagent.ingest.frontmatter import (
    ensure_wiki_frontmatter,
    format_inline_list,
    parse_inline_list,
    parse_page,
    reemit_page,
    replace_page_body,
    set_frontmatter_scalar,
    union_preserve_order,
)
from noteagent.ingest.marker_lines import (
    CLIP_MARK_LINE,
    ECG_MARK_LINE,
    heading_text,
    next_content_line,
    split_lines,
)
from noteagent.wiki.link_syntax import find_wikilinks


@dataclass(frozen=True, slots=True)
class MergeRules:
    """The name lists and the one fraction the rules above are built from."""

    #: Fields the page on disk owns: a newer write may not rename a page or
    #: backdate it to its own date.
    kept: tuple[str, ...] = ("title", "created")
    #: Fields that accumulate across writes rather than being replaced.
    unioned: tuple[str, ...] = ("sources",)
    #: How much of the longer input an answer has to keep, as a fraction. Below
    #: this the answer summarised the page instead of merging it.
    smallest_body: float = 0.7


RULES = MergeRules()

#: ``merger(existing, merged_incoming, source_identity) -> whole page``.
MergeFn = Callable[[str, str, str], str]

#: ``repair(existing, merged_incoming, source_identity, reason) -> whole page``.
#:
#: The reason is the refusal R7 produced, phrased for a model to act on. A repair
#: is a second answer to the same question, so it takes the same two texts.
RepairFn = Callable[[str, str, str, str], str]


@dataclass(frozen=True, slots=True)
class PageMergeResult:
    """What was decided for one page, and the text to write (the old one on R7)."""

    content: str
    mode: str  # create | merge | replace | rewrite | repair | merge-fallback | repair-fallback | unchanged
    warning: str | None = None


def default_today() -> str:
    """Today, as page frontmatter writes it."""
    return date.today().isoformat()


def source_key(identity: str) -> str:
    """How two spellings of one source identity are compared."""
    return identity.replace("\\", "/").strip().lower()


def page_sources(content: str) -> list[str]:
    """The ``sources`` a page claims, in the order it lists them."""
    page = parse_page(content)
    if not page.has_frontmatter:
        return []
    return parse_inline_list(page.fields.get("sources", "[]"))


def is_owned_only_by_material(content: str, material: Iterable[str]) -> bool:
    """Whether every source *content* claims is part of *material*.

    A page with no ``sources`` claims nothing, and claiming nothing is not the
    same as being owned: what the prose was written from is unknown, so it is
    merged rather than replaced. ``material`` holds identifiers as they are
    written down — a whole file for a source read end to end, ``path#block_id``
    for a passage — and a locator is never covered by the whole file it is part
    of, because the write may not have read that passage.
    """
    claimed = page_sources(content)
    if not claimed:
        return False
    carried = {source_key(item) for item in material if item.strip()}
    return all(source_key(item) in carried for item in claimed)


# -- frontmatter ---------------------------------------------------------


def _union_list(old_value: str, new_value: str) -> list[str]:
    """Union two inline lists, comparing entries as source identities."""
    merged = union_preserve_order(
        parse_inline_list(old_value or "[]"),
        parse_inline_list(new_value or "[]"),
    )
    seen: set[str] = set()
    out: list[str] = []
    for item in merged:
        key = source_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def merged_frontmatter(existing: str, incoming: str) -> str:
    """The incoming page's frontmatter with the values the page on disk owns.

    Edits *incoming* in place, so the keys it already carries keep their order
    and quoting. When both sides are the same page, the page comes back
    unchanged. Three shapes are tolerated on the way in: an incoming page with no
    frontmatter keeps the one on disk, a page on disk with no frontmatter is
    replaced by the incoming text as it stands, and a missing opening ``---`` is
    repaired by ``ensure_wiki_frontmatter``.
    """
    existing = ensure_wiki_frontmatter(existing).content
    incoming = ensure_wiki_frontmatter(incoming).content
    old = parse_page(existing)
    new = parse_page(incoming)
    if not new.has_frontmatter:
        return reemit_page(old, new.body) if old.has_frontmatter else incoming
    if not old.has_frontmatter:
        return incoming

    result = incoming
    for key in RULES.kept:
        if key in old.fields:
            result = set_frontmatter_scalar(result, key, old.fields[key])
    for key in RULES.unioned:
        merged = _union_list(
            old.fields.get(key, "[]"),
            new.fields.get(key, "[]"),
        )
        if merged or key in old.fields or key in new.fields:
            result = set_frontmatter_scalar(result, key, format_inline_list(merged))
    if "updated" not in new.fields and "updated" in old.fields:
        result = set_frontmatter_scalar(result, "updated", old.fields["updated"])
    for key, value in old.fields.items():
        if key not in new.fields:
            result = set_frontmatter_scalar(result, key, value)
    return result


def _accepted_page(
    answer: str,
    merged: str,
    *,
    today: Callable[[], str],
) -> str:
    """The merged frontmatter with the answer's body, stamped with today's date.

    A field the answer introduced and the page does not have is kept — the answer
    may be saying something the page lacked. A field the page owns (R3) is never
    taken from the answer, so a model cannot rename or backdate the page.
    """
    answer_page = parse_page(answer)
    body = answer_page.body if answer_page.has_frontmatter else answer
    result = replace_page_body(merged, body)
    owned = set(RULES.kept) | set(RULES.unioned) | {"updated"}
    present = parse_page(merged).fields
    if answer_page.has_frontmatter:
        for key, value in answer_page.fields.items():
            if key in owned or key in present:
                continue
            result = set_frontmatter_scalar(result, key, value)
    return set_frontmatter_scalar(result, "updated", today())


# -- annotations ---------------------------------------------------------


def annotation_key(line: str) -> str | None:
    """The annotation a stamped line carries, or ``None`` when it carries none.

    Read through the shared marker patterns, so these keys mean what the citation
    readers mean by them: a ``na-clip`` line is keyed by the collection it cites,
    an ``na-ecg`` line is itself, and the block ids a line may carry are an
    annotation on the annotation.
    """
    clip = CLIP_MARK_LINE.fullmatch(line)
    if clip:
        return f"na-clip:{clip.group(1)}"
    if ECG_MARK_LINE.fullmatch(line):
        return "na-ecg"
    return None


def _annotations(body: str) -> set[str]:
    return {
        key
        for key in (annotation_key(line) for line in split_lines(body))
        if key is not None
    }


def _annotations_off_a_heading(body: str) -> int:
    """Annotations that own no heading, by the shared adjacency rule."""
    lines = split_lines(body)
    detached = 0
    for index, line in enumerate(lines):
        if annotation_key(line) is None:
            continue
        below = next_content_line(lines, index + 1)
        if below is None or heading_text(lines[below]) is None:
            detached += 1
    return detached


def _link_targets(body: str) -> set[str]:
    return {link.target for link in find_wikilinks(body)}


def structure_breaks(
    merged_body: str,
    existing_body: str,
    incoming_body: str,
    *,
    keep_links: bool = True,
) -> list[str]:
    """Why *merged_body* may not be written, given the two bodies it came from.

    Every annotation either side carried is still present and no annotation was
    invented; every one still sits above a heading; and, unless *keep_links* is
    off, every ``[[link]]`` target either side carried is still a link.

    Adding a link is not a break: it costs the reader a click at worst, while
    dropping one silently breaks the graph. A body rewritten from material the
    page already claims (R4) is held to the annotations only — one text written
    again may legitimately point elsewhere, but its attribution must survive.
    """
    reasons: list[str] = []

    merged_marks = _annotations(merged_body)
    wanted_marks = _annotations(existing_body) | _annotations(incoming_body)
    for key in sorted(wanted_marks - merged_marks):
        reasons.append(f"dropped marker {key}")
    for key in sorted(merged_marks - wanted_marks):
        reasons.append(f"invented marker {key}")

    allowed_detached = _annotations_off_a_heading(
        existing_body
    ) + _annotations_off_a_heading(incoming_body)
    detached = _annotations_off_a_heading(merged_body)
    if detached > allowed_detached:
        reasons.append(
            f"{detached - allowed_detached} marker(s) no longer sit above a heading"
        )

    if keep_links:
        lost = sorted(
            (_link_targets(existing_body) | _link_targets(incoming_body))
            - _link_targets(merged_body)
        )
        if lost:
            reasons.append("dropped wikilink target(s) " + ", ".join(lost))
    return reasons


# -- the decision --------------------------------------------------------


def _left_alone(
    existing: str,
    label: str,
    why: str,
    *,
    mode: str = "merge-fallback",
) -> PageMergeResult:
    """R7: the page on disk, unchanged, and the reason in the warning."""
    return PageMergeResult(
        content=existing,
        mode=mode,
        warning=f"[page-merge] {label}: {why}; the page keeps its body",
    )


def _body_of(text: str) -> str:
    page = parse_page(text)
    return page.body if page.has_frontmatter else text


def _size(body: str) -> int:
    """How much text a body holds, for the R7 floor.

    The surrounding whitespace is not counted: a body that reaches the merge with
    a leading newline from the frontmatter and one that does not are the same
    amount of prose, and the floor compares prose.
    """
    return len(body.strip())


def rewrite_breaks(
    new_body: str, old_body: str, draft_body: str = ""
) -> list[str]:
    """Why a page-level rewrite may not replace the body it was shown.

    The checks are R7's. The rewrite saw two texts — the page and the draft this
    write produced — so both are what it is held to: a marker or a link either
    one carried has to survive, and a marker neither carried is an invention.
    Holding it to the page alone would refuse the marker the new source earned
    and would miss the attribution that only the draft brought.

    The floor is measured against the page it replaces: the draft is one source's
    account, and a short draft is not a reason to accept a short page.
    """
    reasons = structure_breaks(
        new_body, old_body, draft_body or old_body, keep_links=True
    )
    floor = _size(old_body) * RULES.smallest_body
    size = _size(new_body)
    if size < floor:
        reasons.append(
            f"the rewrite is {size} characters against a floor of {floor:.0f} "
            f"({RULES.smallest_body:.0%} of the page it replaces)"
        )
    return reasons


def _answer_shortfall(body: str, longest: int) -> str:
    """The R7 floor, as a sentence. Empty when the answer is long enough."""
    floor = longest * RULES.smallest_body
    size = _size(body)
    if size >= floor:
        return ""
    return (
        f"the answer is {size} characters against a floor of {floor:.0f} "
        f"({RULES.smallest_body:.0%} of the longest input, {longest})"
    )


def _finish_or_repair(
    existing_content: str,
    label: str,
    why: str,
    *,
    merged: str,
    old_body: str,
    merged_body: str,
    repair: RepairFn | None,
    source_identity: str,
    today_fn: Callable[[], str],
) -> PageMergeResult:
    """R7's refusal, or R7b's second attempt when a repair is available.

    A repair that is refused here is not asked again: one attempt, one reason.
    The message carries both refusals so a reader can see what was tried.

    *merged* is the incoming text with the settled frontmatter — exactly what the
    merger was given, since a repair is the same question asked again.
    """
    if repair is None:
        return _left_alone(existing_content, label, why)
    try:
        answer = repair(existing_content, merged, source_identity, why)
    except Exception as err:  # noqa: BLE001 — the page keeps its body
        return _left_alone(
            existing_content,
            label,
            f"{why}; the repair call failed ({err})",
            mode="repair-fallback",
        )
    if not answer.strip():
        return _left_alone(
            existing_content,
            label,
            f"{why}; the repair reply was empty",
            mode="repair-fallback",
        )
    answer_page = parse_page(answer)
    if not answer_page.has_frontmatter:
        return _left_alone(
            existing_content,
            label,
            f"{why}; the repair reply had no frontmatter",
            mode="repair-fallback",
        )
    breaks = structure_breaks(answer_page.body, old_body, merged_body)
    if breaks:
        return _left_alone(
            existing_content,
            label,
            f"{why}; the repair reply would lose part of it ("
            + "; ".join(breaks)
            + ")",
            mode="repair-fallback",
        )
    shortfall = _answer_shortfall(
        answer_page.body, max(_size(old_body), _size(merged_body))
    )
    if shortfall:
        return _left_alone(
            existing_content,
            label,
            f"{why}; {shortfall.replace('the answer', 'the repair reply', 1)}",
            mode="repair-fallback",
        )
    return PageMergeResult(
        content=_accepted_page(answer, merged, today=today_fn),
        mode="repair",
    )


def merge_page_content(
    new_content: str,
    existing_content: str | None,
    merger: MergeFn | None,
    *,
    source_identity: str = "",
    material: Iterable[str] | None = None,
    page_path: str = "",
    today: Callable[[], str] | None = None,
    rewrite_draft: str | None = None,
    repair: RepairFn | None = None,
) -> PageMergeResult:
    """Decide what happens to one page, by the rules at the top of this module.

    ``merger`` is what R6 needs; passing ``None`` means only R1–R5 can decide, so
    a page that needs a model merge keeps its body.

    ``material`` is what this write was made from and is what R4 reads. Without
    it, the whole-file ``source_identity`` stands for the material, which is what
    a source read end to end provides. An empty identity means the material is
    unknown, and then nothing is replaced.

    ``rewrite_draft`` is the draft this write produced for the page, present only
    when the body was written with the page on disk in front of it; then R4b
    decides instead of R4/R6, and the draft joins the page as what the answer is
    held to. A caller that does not know the draft says nothing, which is every
    caller but the rewrite pass.

    ``repair`` is R7b's one second attempt: a model call given the refusal R7
    produced. Without it a refused page keeps its body, which is what every
    caller that has no client gets.
    """
    today_fn = today or default_today
    label = page_path or "(page)"

    # R1
    if not existing_content:
        return PageMergeResult(content=new_content, mode="create")
    # R2
    if new_content == existing_content:
        return PageMergeResult(content=existing_content, mode="unchanged")

    # R3
    merged = merged_frontmatter(existing_content, new_content)
    old = parse_page(existing_content)
    merged_page = parse_page(merged)

    # R4b
    if rewrite_draft is not None:
        new_body = _body_of(new_content)
        breaks = rewrite_breaks(new_body, old.body, _body_of(rewrite_draft))
        if breaks:
            return _left_alone(
                existing_content,
                label,
                "its rewrite would lose part of it (" + "; ".join(breaks) + ")",
            )
        replacement = set_frontmatter_scalar(merged, "updated", today_fn())
        return PageMergeResult(content=replacement, mode="rewrite")

    if material is not None:
        carried: frozenset[str] = frozenset(item for item in material if item.strip())
    elif source_identity.strip():
        carried = frozenset({source_identity})
    else:
        carried = frozenset()

    # R4
    if is_owned_only_by_material(existing_content, carried):
        new_body = _body_of(new_content)
        breaks = structure_breaks(new_body, old.body, new_body, keep_links=False)
        if breaks:
            return _left_alone(
                existing_content,
                label,
                "its rewrite would lose part of it (" + "; ".join(breaks) + ")",
            )
        replacement = set_frontmatter_scalar(merged, "updated", today_fn())
        return PageMergeResult(content=replacement, mode="replace")

    # R5
    if old.body.strip() == merged_page.body.strip():
        stamped = set_frontmatter_scalar(merged, "updated", today_fn())
        return PageMergeResult(content=stamped, mode="merge")

    # R6
    if merger is None:
        return _left_alone(existing_content, label, "no merger was supplied")
    try:
        answer = merger(existing_content, merged, source_identity)
    except Exception as err:  # noqa: BLE001 — a failed call keeps the page
        # A call that raised is the one refusal a repair cannot improve on: the
        # text was never seen, so there is nothing the model got wrong.
        return _left_alone(existing_content, label, f"the merge call failed ({err})")

    def finish(why: str) -> PageMergeResult:
        return _finish_or_repair(
            existing_content,
            label,
            why,
            merged=merged,
            old_body=old.body,
            merged_body=merged_page.body,
            repair=repair,
            source_identity=source_identity,
            today_fn=today_fn,
        )

    # R7
    answer_page = parse_page(answer)
    if not answer_page.has_frontmatter:
        return finish("the answer had no frontmatter")

    breaks = structure_breaks(answer_page.body, old.body, merged_page.body)
    if breaks:
        return finish("the answer would lose part of it (" + "; ".join(breaks) + ")")

    shortfall = _answer_shortfall(
        answer_page.body, max(_size(old.body), _size(merged_page.body))
    )
    if shortfall:
        return finish(shortfall)

    return PageMergeResult(
        content=_accepted_page(answer, merged, today=today_fn),
        mode="merge",
    )
