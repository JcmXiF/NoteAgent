import { titleOfPath, type TreeFile } from "@/lib/project-tree";
import type { CompiledClipCollection, SourceCitationBundle } from "@/lib/api/types";
import type { SourceLocator } from "@/lib/wiki-locators";
import {
  groupWikiEvidence,
  mergeEvidenceCollections,
  type EvidenceCollection,
} from "@/lib/wiki-evidence";

/**
 * What a page says it was written from.
 *
 * A page declares the passages it cites, and this turns that declaration into
 * something a reader can act on: one row per original, with the passages it
 * contributed. The passages are kept, not just the file, because that is what
 * makes reading the source beside the note worthwhile.
 *
 * Pure: no fetching, no React, no i18n.
 */

export type CitationEntry = {
  path: string;
  /** The original's own name. */
  name: string;
  /**
   * The selection the passages were taken from, when the app still knows it. Shown
   * beside the name because it says what the passage was, while the name says
   * where it is.
   */
  from: string;
  /** The cited passages, in the order the page declares them. */
  blockIds: string[];
  /**
   * Whether the project still has this file. A page outlives the material it was
   * written from, so a citation can point at something that is no longer there —
   * and saying so is better than offering to open nothing.
   */
  exists: boolean;
};

/**
 * The name a group offers, if it offers one.
 *
 * A group that came from a selection is named after it. A group that is only a
 * file is named after the file, which says nothing the path does not — so it is
 * treated as unnamed and the project's own name for the file is used instead.
 */
function groupName(title: string, path: string): string {
  const fileName = path.split("/").pop() ?? path;
  return title && title !== fileName ? title : "";
}

/**
 * One entry per cited original.
 *
 * The evidence groups come grouped by the selection they came from, which can put
 * the same file in more than one group when a page was written in stages. A reader
 * thinks in files, so they are merged here — and a passage cited twice counts
 * once, since the page cites it once.
 */
export function citationEntries(
  locators: SourceLocator[],
  collections: EvidenceCollection[],
  files: TreeFile[],
): CitationEntry[] {
  const merged = new Map<string, { from: string; blockIds: string[] }>();
  for (const group of groupWikiEvidence(locators, collections)) {
    const existing = merged.get(group.path);
    const name = groupName(group.title, group.path);
    if (!existing) {
      merged.set(group.path, { from: name, blockIds: [...group.blockIds] });
      continue;
    }
    for (const id of group.blockIds) {
      if (!existing.blockIds.includes(id)) existing.blockIds.push(id);
    }
    // A page written in stages can cite one file from more than one selection;
    // the first that names itself is the one shown.
    if (!existing.from && name) existing.from = name;
  }

  const present = new Set(files.map((file) => file.path));
  return [...merged.entries()].map(([path, item]) => ({
    path,
    name: titleOfPath(path, files),
    from: item.from,
    blockIds: item.blockIds,
    exists: present.has(path),
  }));
}

/**
 * The citations of one stored source: which notes use it, and from which passage.
 *
 * This is the reverse of a page's own citation list. A page says which sources it
 * drew on; a source says which pages drew on it, and a reader looking at a source
 * wants the second. The order is fixed so the numbered marks down the side of a
 * source do not shuffle between renders.
 */
export type SourceMark = {
  /** Stable within a source, so a mark keeps its identity as the page redraws. */
  id: string;
  notePath: string;
  noteTitle: string;
  heading: string;
  collectionId: string;
  /** The passages this note took from the source. */
  blockIds: string[];
};

export function marksForSource(
  bundle: SourceCitationBundle | undefined,
  blockOrder: string[] = [],
): SourceMark[] {
  const marks = bundle?.marks ?? [];
  const known = new Set(blockOrder);
  return marks
    .map((mark, index) => ({
      id: mark.id || `${mark.path}::${mark.collectionId}::${index}`,
      notePath: mark.path,
      noteTitle: mark.title || mark.path,
      heading: mark.heading,
      collectionId: mark.collectionId,
      // A citation with no passages of its own is about the whole file, which is
      // what an empty list already means to the caller drawing it.
      blockIds: known.size > 0 ? mark.blockIds.filter((id) => known.has(id)) : [...mark.blockIds],
    }))
    .sort(
      (a, b) =>
        a.noteTitle.localeCompare(b.noteTitle, "zh-Hans-CN") ||
        a.heading.localeCompare(b.heading, "zh-Hans-CN") ||
        a.id.localeCompare(b.id),
    );
}

/**
 * The selections a page's own record and the project's record both describe.
 *
 * A page's markers name the selection each section was written from, and the
 * project's record of compiled selections is the only place those names can be
 * looked up once a round has ended — which is why reading them together is what
 * makes a page's sections traceable at all. Selections made in this session are
 * added on top, so work in progress is described too.
 */
export function evidenceFrom(
  catalog: CompiledClipCollection[] | undefined,
  session: { id: string; sourceKey: string; blockIds: string[]; title: string }[],
): EvidenceCollection[] {
  return mergeEvidenceCollections(
    (catalog ?? []).map((item) => ({
      id: item.id,
      sourceKey: item.sourceKey,
      blockIds: item.blockIds,
      title: item.sourceLabel,
    })),
    session,
  );
}
