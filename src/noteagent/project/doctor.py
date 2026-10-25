"""Project health checks: ghosts, broken links, frontmatter, empty pages."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from noteagent.ingest.frontmatter import (
    ensure_wiki_frontmatter,
    parse_inline_list,
    parse_page,
    unquote_scalar,
)
from noteagent.ingest.update_index import (
    iter_wiki_content_pages,
    wiki_slug_for_path,
)
from noteagent.project.layout import RAW_DIR, ProjectLayout
from noteagent.project.raw_kinds import LEGACY_RAW_SOURCES, RAW_KINDS
from noteagent.wiki.link_syntax import find_wikilinks
from noteagent.wiki.links import normalize_wikilink_target
from noteagent.wiki.page_kinds import folder_of, is_legacy_type_page, is_source_page

_CLIP_COMMENT_RE = re.compile(
    r"<!--\s*na-clip(?:\s*:\s*([^>]*))?\s*-->",
    re.IGNORECASE,
)
_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
FINDING_PAYLOAD_LIMIT = 40


@dataclass(frozen=True, slots=True)
class DoctorFinding:
    """
    One health finding.

    ``message`` is written for a log or a terminal, in one language, and says what
    the finding is in prose. ``code`` and ``params`` are what an interface needs to
    say the same thing in the reader's own language, which cannot be decided here.
    """

    severity: str  # error | warning | info
    code: str
    message: str
    path: str | None = None
    params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Every finding from one doctor run, with the counts it was judged on."""

    findings: tuple[DoctorFinding, ...]
    content_pages: int
    index_links: int

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _is_summary_page(relative: str) -> bool:
    """Whether a page is one of the source summaries under ``wiki/sources/``."""
    return is_source_page(relative)


def _clip_mark_id(inner: str | None) -> str:
    """The id written in a clip mark, or an empty string when it has none."""
    if inner is None:
        return ""
    token = inner.strip().split()[0] if inner.strip() else ""
    return token


def iter_clip_mark_ids(text: str) -> tuple[str, ...]:
    """Ids from ``<!-- na-clip:... -->`` comments. Empty marks are omitted."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _CLIP_COMMENT_RE.finditer(text or ""):
        ident = _clip_mark_id(match.group(1))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        found.append(ident)
    return tuple(found)


def _disk_maps(
    layout: ProjectLayout,
) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str]], list[DoctorFinding]]:
    """The slugs and paths found on disk, plus any slug-collision findings.

    Returns ``(slug -> rel, bare name -> rel, pages, findings)``. First path wins
    for link resolution when slugs collide.
    """
    pages = list(iter_wiki_content_pages(layout))
    slug_to_rel, collisions = slug_maps_from_pages(pages)
    bare_to_rel: dict[str, str] = {}
    for slug, rel in slug_to_rel.items():
        bare_to_rel.setdefault(slug.split("/")[-1], rel)
    bare_to_rel.setdefault("index", "wiki/index.md")
    if layout.legacy_overview.is_file():
        bare_to_rel.setdefault("overview", "wiki/overview.md")
    bare_to_rel.setdefault("log", "wiki/log.md")
    findings: list[DoctorFinding] = []
    for slug, paths in collisions.items():
        findings.append(
            DoctorFinding(
                "error",
                "slug.duplicate",
                f"slug [[{slug}]] maps to {', '.join(paths)}",
                paths[0],
                params=(("slug", slug), ("paths", ", ".join(paths))),
            )
        )
    return slug_to_rel, bare_to_rel, pages, findings


def slug_maps_from_pages(
    pages: Iterable[tuple[str, str]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build slug -> first relative path, plus colliding paths per slug."""
    slug_to_rel: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for rel, _content in pages:
        try:
            slug = wiki_slug_for_path(rel)
        except ValueError:
            continue
        existing = slug_to_rel.get(slug)
        if existing is None:
            slug_to_rel[slug] = rel
            continue
        if existing == rel:
            continue
        bucket = collisions.setdefault(slug, [existing])
        if rel not in bucket:
            bucket.append(rel)
    return slug_to_rel, collisions


def _index_slugs(layout: ProjectLayout) -> set[str]:
    """The targets ``wiki/index.md`` links to."""
    if not layout.wiki_index.is_file():
        return set()
    try:
        text = layout.wiki_index.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {link.target for link in find_wikilinks(text)}


def _target_slug(
    target: str,
    slug_to_rel: dict[str, str],
    bare_to_rel: dict[str, str],
) -> str | None:
    """The page a wikilink target names, or ``None`` when no page does."""
    t = normalize_wikilink_target(target)
    if t in slug_to_rel:
        return t
    rel = bare_to_rel.get(t) or bare_to_rel.get(t.split("/")[-1])
    if rel is None:
        return None
    try:
        return wiki_slug_for_path(rel)
    except ValueError:
        return None


def _raw_locator_missing(layout: ProjectLayout, locator: str) -> bool:
    """Whether a ``raw/...`` locator points at nothing on disk."""
    text = locator.replace("\\", "/").strip()
    if not text:
        return False
    path = text.split("#", 1)[0].strip()
    if not path.startswith(f"{RAW_DIR}/") or ".." in path.split("/"):
        return False
    try:
        root = layout.root.expanduser().resolve()
        candidate = (root / path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return True
    return not candidate.is_file()


def doctor_project(layout: ProjectLayout) -> DoctorReport:
    """
    Scan the wiki for structural problems. Never writes.
    """
    findings: list[DoctorFinding] = []
    slug_to_rel, bare_to_rel, pages, slug_findings = _disk_maps(layout)
    findings.extend(slug_findings)
    content_slugs = set(slug_to_rel)
    titles: dict[str, list[str]] = {}
    inbound: dict[str, set[str]] = {slug: set() for slug in content_slugs}

    for rel, content in pages:
        try:
            source_slug = wiki_slug_for_path(rel)
        except ValueError:
            source_slug = ""
        if is_legacy_type_page(rel):
            folder = folder_of(rel)
            findings.append(
                DoctorFinding(
                    "info",
                    "layout.legacyFolder",
                    f"page is in retired type folder {folder}/; it is indexed as a note",
                    rel,
                    params=(("folder", folder),),
                )
            )
        fm = ensure_wiki_frontmatter(content)
        if fm.code == "repaired":
            findings.append(
                DoctorFinding("warning", "fm.repairable", fm.message or fm.code, rel)
            )
        elif fm.code:
            findings.append(
                DoctorFinding("error", "fm.missing", fm.message or fm.code, rel)
            )
        else:
            page = parse_page(fm.content)
            if len(page.body.strip()) < 40:
                findings.append(
                    DoctorFinding(
                        "warning",
                        "body.short",
                        f"body only {len(page.body.strip())} chars",
                        rel,
                        params=(("chars", str(len(page.body.strip()))),),
                    )
                )
            title = unquote_scalar(page.fields.get("title", "")).strip()
            if title and not _is_summary_page(rel):
                titles.setdefault(title, []).append(rel)
            for locator in parse_inline_list(page.fields.get("sources", "[]")):
                if _raw_locator_missing(layout, locator):
                    findings.append(
                        DoctorFinding(
                            "warning",
                            "source.missing",
                            f"sources locator missing on disk: {locator}",
                            rel,
                            params=(("locator", locator),),
                        )
                    )

        for match in _CLIP_COMMENT_RE.finditer(content):
            ident = _clip_mark_id(match.group(1))
            if ident and _CLIP_ID_RE.match(ident):
                continue
            findings.append(
                DoctorFinding(
                    "warning",
                    "mark.clip_bad",
                    "na-clip mark is missing or has an illegal id",
                    rel,
                )
            )

        for link in find_wikilinks(content):
            target = link.target
            target_slug = _target_slug(target, slug_to_rel, bare_to_rel)
            if target_slug is None:
                findings.append(
                    DoctorFinding(
                        "warning",
                        "link.broken",
                        f"broken wikilink [[{target.split('|')[0].strip()}]]",
                        rel,
                        params=(("target", target.split("|")[0].strip()),),
                    )
                )
                continue
            if source_slug and target_slug != source_slug:
                inbound.setdefault(target_slug, set()).add(source_slug)

    for title, paths in sorted(titles.items()):
        unique = list(dict.fromkeys(paths))
        if len(unique) < 2:
            continue
        findings.append(
            DoctorFinding(
                "warning",
                "title.duplicate",
                f'title "{title}" used by {", ".join(unique)}',
                unique[0],
                params=(("title", title), ("paths", ", ".join(unique))),
            )
        )

    for rel, _content in pages:
        if _is_summary_page(rel):
            continue
        try:
            slug = wiki_slug_for_path(rel)
        except ValueError:
            continue
        if inbound.get(slug):
            continue
        findings.append(
            DoctorFinding(
                "info",
                "link.orphan",
                f"no inbound wikilinks from other content pages [[{slug}]]",
                rel,
            )
        )

    index_links = _index_slugs(layout)
    for slug in sorted(index_links - content_slugs):
        findings.append(
            DoctorFinding(
                "error",
                "index.ghost",
                f"index lists [[{slug}]] but file missing",
                "wiki/index.md",
                params=(("slug", slug),),
            )
        )
    for slug in sorted(content_slugs - index_links):
        findings.append(
            DoctorFinding(
                "warning",
                "index.missing",
                f"on disk but not in index: [[{slug}]]",
                slug_to_rel.get(slug),
                params=(("slug", slug),),
            )
        )

    if layout.legacy_overview.is_file():
        findings.append(
            DoctorFinding(
                "info",
                "layout.overviewLeftover",
                "overview.md is no longer maintained; delete it",
                "wiki/overview.md",
            )
        )

    # An old layout is readable, so this is advice rather than a fault: the
    # project opens and works, and the finding names the one command that ends
    # the difference.
    if (layout.root / LEGACY_RAW_SOURCES).is_dir():
        findings.append(
            DoctorFinding(
                "warning",
                "layout.legacyRaw",
                "originals are still filed under raw/sources/; "
                "run `noteagent project migrate`",
                LEGACY_RAW_SOURCES,
            )
        )

    findings.extend(_empty_kind_folders(layout))

    return DoctorReport(
        findings=tuple(findings),
        content_pages=len(pages),
        index_links=len(index_links),
    )


def _empty_kind_folders(layout: ProjectLayout) -> list[DoctorFinding]:
    """Kind folders holding nothing, which a kept project does not have.

    A kind folder is made when its first file arrives and removed when its last
    file goes, so an empty one means a file was deleted behind the program's back.
    """
    findings: list[DoctorFinding] = []
    for kind in RAW_KINDS:
        folder = layout.raw / kind
        if not folder.is_dir():
            continue
        if any(item.is_file() for item in folder.rglob("*")):
            continue
        findings.append(
            DoctorFinding(
                "warning",
                "layout.emptyKindFolder",
                f"kind folder {kind}/ holds no files; remove it",
                f"{RAW_DIR}/{kind}",
                params=(("folder", kind),),
            )
        )
    return findings


def session_unknown_clip_warnings(
    pages: Iterable[tuple[str, str]],
    known_ids: set[str],
) -> list[str]:
    """
    Warn when a just-written page cites a clip id outside this session.

    Do not use as a global doctor code: ``clips.json`` is the current round.
    """
    known = {item.strip() for item in known_ids if item.strip()}
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relative, content in pages:
        path = relative.replace("\\", "/")
        if path.lower() == "wiki/log.md" or _is_summary_page(path):
            continue
        for ident in iter_clip_mark_ids(content):
            key = (path, ident)
            if ident in known or key in seen:
                continue
            seen.add(key)
            warnings.append(
                f'{path}: na-clip "{ident}" is not in the current clip session'
            )
    return warnings


def doctor_after_write(
    layout: ProjectLayout,
    *,
    dry_run: bool,
    cache_hit: bool = False,
    status: Callable[..., None] | None = None,
) -> DoctorReport | None:
    """Run doctor after a real write. Skip dry-run and cache hits."""
    if dry_run or cache_hit:
        return None
    if status is not None:
        status("Checking wiki (doctor)", step_id="doctor")
    return doctor_project(layout)


def doctor_payload(
    report: DoctorReport | None,
    *,
    limit: int = FINDING_PAYLOAD_LIMIT,
) -> dict[str, Any] | None:
    """JSON-ready doctor snapshot for ingest results. ``None`` if skipped."""
    if report is None:
        return None
    cap = max(0, limit)
    items = report.findings[:cap]
    return {
        "contentPages": report.content_pages,
        "indexLinks": report.index_links,
        "errors": report.errors,
        "warnings": report.warnings,
        "findingCount": len(report.findings),
        "truncated": len(report.findings) > cap,
        "findings": [
            {
                "severity": item.severity,
                "code": item.code,
                "message": item.message,
                "path": item.path,
                "params": dict(item.params),
            }
            for item in items
        ],
    }


def format_doctor_report(report: DoctorReport) -> str:
    """A doctor report for a terminal or a log."""
    parts = [
        f"content_pages: {report.content_pages}",
        f"index_links: {report.index_links}",
        f"errors: {report.errors}",
        f"warnings: {report.warnings}",
        f"findings: {len(report.findings)}",
    ]
    for item in report.findings:
        loc = f" @ {item.path}" if item.path else ""
        parts.append(f"- [{item.severity}/{item.code}] {item.message}{loc}")
    return "\n".join(parts)
