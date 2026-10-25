import { postAcquireSave } from "@/lib/api/acquire";
import type { CapturePreview, RawSaveItem } from "@/lib/api/types";
import { captureMatches } from "@/lib/capture";
import { isPreviewSource, previewSourceKey } from "@/lib/clips";
import type { BlockCollection } from "@/lib/clips";
import i18n from "@/i18n";

/**
 * Storing the originals behind a selection.
 *
 * Compiling a page requires the material it was built from to exist under
 * `raw/`, because `sources[]` has to point at a real file. Until then a
 * selection is keyed by its page URL (`preview:…`); storing it swaps that key
 * for the path where the file landed.
 *
 * This is the only path that writes originals: storing on demand and storing
 * before a compile are the same call, so the two cases cannot drift apart.
 */

type SourceRemap = {
  fromKey: string;
  toKey: string;
};

/** The selections whose originals are not on disk yet. */
export function collectionsNeedingStorage(
  collections: BlockCollection[],
): BlockCollection[] {
  return collections.filter((collection) => isPreviewSource(collection.sourceKey));
}

/**
 * The captures to store, and the URLs nothing can be found for.
 *
 * A capture is only in memory for the session in which it was fetched; after a
 * reload, the list remembers the page but not its text. A selection made from
 * that page therefore cannot be stored until the page is fetched again, and the
 * caller needs to say so rather than failing obscurely.
 */
export function capturesForCollections(
  collections: BlockCollection[],
  captures: CapturePreview[],
): { captures: CapturePreview[]; missing: string[] } {
  const needed = new Map<string, CapturePreview>();
  const missing: string[] = [];

  for (const collection of collections) {
    if (!isPreviewSource(collection.sourceKey)) continue;
    const url = collection.sourceKey.slice("preview:".length);
    const capture = captures.find((item) => captureMatches(item, url));
    if (!capture || capture.fidelity === "failed") {
      missing.push(collection.sourceLabel || url);
      continue;
    }
    needed.set(capture.url, capture);
  }

  return { captures: [...needed.values()], missing };
}

/**
 * Where each stored original went, as key rewrites.
 *
 * Only keys belonging to the captures that were sent are rewritten. A stored
 * path is attribution: pointing a selection at a file it did not come from
 * would be worse than leaving it unstored, so an item that matches none of the
 * captures is ignored rather than trusted.
 *
 * A capture can be reachable under both the URL requested and the URL it
 * settled on, and a selection may have been keyed by either, so both are
 * rewritten.
 */
export function remapPairsFromSave(
  items: RawSaveItem[],
  captures: CapturePreview[],
): SourceRemap[] {
  const pairs: SourceRemap[] = [];
  const seen = new Set<string>();
  const add = (fromKey: string, toKey: string) => {
    if (!fromKey || !toKey) return;
    const token = `${fromKey}=>${toKey}`;
    if (seen.has(token)) return;
    seen.add(token);
    pairs.push({ fromKey, toKey });
  };

  for (const item of items) {
    if (!item.path) continue;
    const matched = captures.filter((capture) => captureMatches(capture, item.url));
    if (matched.length === 0) continue;

    add(previewSourceKey(item.url), item.path);
    for (const capture of matched) {
      add(previewSourceKey(capture.url), item.path);
      if (capture.finalUrl) add(previewSourceKey(capture.finalUrl), item.path);
    }
  }
  return pairs;
}

/** Where each page was written, for a source list that shows what has been stored. */
export function writtenPathRecords(
  items: RawSaveItem[],
  captures: CapturePreview[],
): Array<{ url: string; path: string }> {
  const out: Array<{ url: string; path: string }> = [];
  const seen = new Set<string>();
  const add = (url: string, path: string) => {
    if (!url || !path || seen.has(url)) return;
    seen.add(url);
    out.push({ url, path });
  };

  // Guarded like the key rewrites: a record says "this page is stored", which
  // has to be about a page that was actually sent.
  for (const item of items) {
    if (!item.path) continue;
    const matched = captures.filter((capture) => captureMatches(capture, item.url));
    if (matched.length === 0) continue;

    add(item.url, item.path);
    for (const capture of matched) {
      add(capture.url, item.path);
      if (capture.finalUrl) add(capture.finalUrl, item.path);
    }
  }
  return out;
}

/** What was stored, and what the backend refused to store. */
type SaveOutcome = {
  written: RawSaveItem[];
  refused: RawSaveItem[];
};

export function summarizeSave(items: RawSaveItem[]): SaveOutcome {
  const written: RawSaveItem[] = [];
  const refused: RawSaveItem[] = [];
  for (const item of items) {
    if (item.path) written.push(item);
    else refused.push(item);
  }
  return { written, refused };
}

/**
 * Thrown when the material behind a selection is no longer available.
 *
 * Carries the labels rather than a sentence: the sentence belongs to the view,
 * which is the only place that knows what is being said and in which language.
 * `missingSourceLabels` is there to name them for it.
 */
export class MissingSourcesError extends Error {
  readonly labels: string[];

  constructor(labels: string[]) {
    super("sources are missing from this session");
    this.name = "MissingSourcesError";
    this.labels = labels;
  }
}

/**
 * The pages that were missing, named for a sentence that lists them.
 *
 * A few are enough for the reader to recognise what has to be fetched again;
 * naming all of them would bury the sentence, so the rest are left to the
 * ellipsis. The list is joined the way the interface's language joins one.
 */
export function missingSourceLabels(err: MissingSourcesError): string {
  const shown = err.labels.slice(0, 4).join(i18n.t("common.listSeparator"));
  return err.labels.length > 4 ? `${shown}…` : shown;
}

/** Thrown when the backend accepted the request but stored nothing. */
export class SourceNotStoredError extends Error {
  /** The backend's own explanation, when it gave one. */
  readonly detail: string;

  constructor(detail: string) {
    super("the original was not stored");
    this.name = "SourceNotStoredError";
    this.detail = detail;
  }
}

type EnsureSourcesResult = {
  /** How many originals were written. */
  wrote: number;
  remaps: SourceRemap[];
  writtenPaths: Array<{ url: string; path: string }>;
};

/**
 * Stores the originals for the given selections.
 *
 * Returns the key rewrites and recorded paths instead of applying them, so the
 * caller decides what to update and this stays a plain function.
 *
 * Throws when something cannot be stored, since `sources[]` must never point at
 * a file that is not there.
 */
export async function ensureSourcesOnDisk(input: {
  project: string;
  collections: BlockCollection[];
  captures: CapturePreview[];
}): Promise<EnsureSourcesResult> {
  const pending = collectionsNeedingStorage(input.collections);
  if (pending.length === 0) {
    return { wrote: 0, remaps: [], writtenPaths: [] };
  }

  const { captures: needed, missing } = capturesForCollections(
    pending,
    input.captures,
  );
  if (missing.length > 0) {
    throw new MissingSourcesError(missing);
  }

  const data = await postAcquireSave(input.project, needed);
  const items = data.items ?? [];
  const { written, refused } = summarizeSave(items);
  const unwritten = needed.filter(
    (capture) => !written.some((item) => captureMatches(capture, item.url)),
  );
  if (unwritten.length > 0) {
    throw new SourceNotStoredError(refused[0]?.message ?? "");
  }

  return {
    wrote: written.length,
    remaps: remapPairsFromSave(items, needed),
    writtenPaths: writtenPathRecords(items, needed),
  };
}
