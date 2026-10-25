import { isRawSourcePath } from "@/lib/source-kind";
import { normalizeProbe, sharedRun } from "@/lib/text-match";
import { isRenderableOriginal, normalizePath, type SourceLocator } from "@/lib/wiki-locators";
import { CLIP_MARK_LINE, HEADING_LINE } from "@/lib/marker-lines";

/**
 * Tying a wiki page to the selections it was written from.
 *
 * The locators in a page say which passages are cited; the selections the app
 * knows about say which page each passage came from. Putting the two together is
 * what lets the documents view show a page beside the material behind it — and
 * what says so honestly when the two cannot be tied together.
 *
 * Pure text handling: no fetching, no React, no i18n.
 */

export type EvidenceCollection = {
  id: string;
  sourceKey: string;
  blockIds: string[];
  title: string;
  blocks?: { id: string; text: string }[];
};

type EvidenceGroup = {
  id: string;
  title: string;
  path: string;
  blockIds: string[];
};

export type SectionBind = {
  heading: string;
  level: number;
  groupId: string | null;
  title: string | null;
  path: string | null;
  blockIds: string[];
  /** How the section was tied to a selection; `unmatched` means it was not. */
  confidence: "marked" | "matched" | "unmatched";
};

function uniqueIds(ids: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/**
 * Combines the selections recorded on disk with the ones in this session.
 *
 * Later entries win, and only selections backed by a stored original are kept:
 * a preview key has no file for the documents view to open.
 */
export function mergeEvidenceCollections(
  catalog: EvidenceCollection[],
  session: EvidenceCollection[],
): EvidenceCollection[] {
  const byId = new Map<string, EvidenceCollection>();
  for (const item of [...catalog, ...session]) {
    if (!isRawSourcePath(normalizePath(item.sourceKey))) continue;
    byId.set(item.id, {
      ...item,
      sourceKey: normalizePath(item.sourceKey),
      blockIds: uniqueIds(item.blockIds),
    });
  }
  return [...byId.values()];
}

/**
 * Groups a page's locators by the selection they came from.
 *
 * Locators that match a known selection are grouped under it. The rest are still
 * shown, grouped by file, because a page can cite a passage the app no longer
 * has a selection for — selections are discarded when a round ends — and hiding
 * those would misrepresent the page.
 */
export function groupWikiEvidence(
  locators: SourceLocator[],
  collections: EvidenceCollection[],
): EvidenceGroup[] {
  const usable = locators.filter((item) => isRenderableOriginal(item.path));
  const claimed = new Set<string>();
  const groups: EvidenceGroup[] = [];

  for (const collection of collections) {
    const ids: string[] = [];
    for (const locator of usable) {
      if (locator.path !== collection.sourceKey || !locator.blockId) continue;
      if (!collection.blockIds.includes(locator.blockId)) continue;
      ids.push(locator.blockId);
      claimed.add(`${locator.path}#${locator.blockId}`);
    }
    if (ids.length === 0) continue;
    groups.push({
      id: collection.id,
      title: collection.title,
      path: collection.sourceKey,
      blockIds: uniqueIds(ids),
    });
  }

  const leftover = new Map<string, string[]>();
  const wholeFiles = new Set<string>();
  for (const locator of usable) {
    if (!locator.blockId) {
      wholeFiles.add(locator.path);
      continue;
    }
    if (claimed.has(`${locator.path}#${locator.blockId}`)) continue;
    leftover.set(locator.path, [...(leftover.get(locator.path) ?? []), locator.blockId]);
  }

  // A whole-file locator gets its own group, unless the file is already there.
  for (const path of wholeFiles) {
    if (groups.some((group) => group.path === path)) continue;
    groups.push({
      id: `file:${path}`,
      title: path.split("/").pop() ?? path,
      path,
      blockIds: uniqueIds(leftover.get(path) ?? []),
    });
    leftover.delete(path);
  }
  for (const [path, ids] of leftover) {
    groups.push({
      id: `file:${path}`,
      title: path.split("/").pop() ?? path,
      path,
      blockIds: uniqueIds(ids),
    });
  }

  return groups;
}

/** Removes the marker comments, for reading a page's prose as written. */
export function stripClipMarkers(markdown: string): string {
  return markdown
    .split("\n")
    .filter((line) => !CLIP_MARK_LINE.test(line))
    .join("\n");
}

function parseMarkedBlocks(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Ties each section of a page to the selection it was written from.
 *
 * A marker comment is authoritative, and it owns the heading directly below it:
 * the next line with content has to be a section heading (`##`–`######`), with
 * blank lines allowed between the two and nothing else. A marker with prose, a
 * list, another marker or the page title under it owns nothing — reading on to a
 * later heading would credit that section to material it was not written from.
 *
 * Without a marker, the section is compared against the selections' text and
 * bound only when a single selection clearly wins — a weak or ambiguous match is
 * reported as unmatched rather than guessed, because a wrong binding would point
 * a reader at material the section was not written from.
 */
export function bindWikiSections(
  body: string,
  collections: EvidenceCollection[],
): SectionBind[] {
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const pending: { id: string; blocks: string[] }[] = [];
  const sections: {
    heading: string;
    level: number;
    marked: { id: string; blocks: string[] } | null;
    text: string;
  }[] = [];
  let current: (typeof sections)[number] | null = null;

  for (const line of lines) {
    const comment = CLIP_MARK_LINE.exec(line);
    if (comment) {
      pending.push({ id: comment[1] ?? "", blocks: parseMarkedBlocks(comment[2]) });
      continue;
    }
    // Blank lines may separate a marker from the heading it owns; nothing else
    // may, so prose ends the wait rather than handing the marker on.
    if (line.trim() === "") {
      if (current) current.text += `${line}\n`;
      continue;
    }
    const heading = HEADING_LINE.exec(line);
    if (heading) {
      if (current) sections.push(current);
      // A marker applies to the heading directly below it.
      const marked = pending.pop() ?? null;
      pending.length = 0;
      current = {
        heading: (heading[2] ?? "").trim(),
        level: (heading[1] ?? "##").length,
        marked,
        text: "",
      };
      continue;
    }
    pending.length = 0;
    if (current) current.text += `${line}\n`;
  }
  if (current) sections.push(current);

  const byId = new Map(collections.map((item) => [item.id, item]));

  return sections.map((section) => {
    if (section.marked && byId.has(section.marked.id)) {
      const collection = byId.get(section.marked.id) as EvidenceCollection;
      return {
        heading: section.heading,
        level: section.level,
        groupId: collection.id,
        title: collection.title,
        path: collection.sourceKey,
        // The marker can name the passages it used; otherwise the whole
        // selection is the best available answer.
        blockIds: uniqueIds(
          section.marked.blocks.length > 0 ? section.marked.blocks : collection.blockIds,
        ),
        confidence: "marked" as const,
      };
    }

    const matched = matchSectionToCollection(section.heading, section.text, collections);
    if (matched) {
      return {
        heading: section.heading,
        level: section.level,
        groupId: matched.id,
        title: matched.title,
        path: matched.sourceKey,
        blockIds: matched.hitIds,
        confidence: "matched" as const,
      };
    }

    return {
      heading: section.heading,
      level: section.level,
      groupId: null,
      title: null,
      path: null,
      blockIds: [],
      confidence: "unmatched" as const,
    };
  });
}

/** The best-scoring selection, or nothing when the result is not decisive. */
function matchSectionToCollection(
  heading: string,
  body: string,
  collections: EvidenceCollection[],
): (EvidenceCollection & { hitIds: string[] }) | null {
  const hay = normalizeProbe(`${heading}\n${body}`);
  if (hay.length < 8) return null;

  const scored: { col: EvidenceCollection; score: number; hitIds: string[] }[] = [];
  for (const col of collections) {
    const hitIds: string[] = [];
    let score = 0;
    const headline = normalizeProbe(col.title);
    if (headline.length >= 8 && hay.includes(headline)) score += 2;
    for (const block of col.blocks ?? []) {
      const probe = normalizeProbe(block.text);
      if (!sharedRun(probe, hay)) continue;
      score += 1;
      hitIds.push(block.id);
    }
    if (score > 0) scored.push({ col, score, hitIds: uniqueIds(hitIds) });
  }

  scored.sort((a, b) => b.score - a.score);
  const winner = scored[0];
  if (!winner) return null;
  const runner = scored[1];
  // A tie, or a close call on a low score, is not a match worth showing.
  if (runner && runner.score >= winner.score) return null;
  if (runner && winner.score < 2 && runner.score * 2 >= winner.score) return null;
  if (winner.score < 2 && winner.hitIds.length === 0) return null;
  return { ...winner.col, hitIds: winner.hitIds };
}
