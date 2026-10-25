import { isSameCapture } from "@/lib/capture";
import { storageOrNull, type StorageLike } from "@/lib/storage";
import type { CapturePreview } from "@/lib/api/types";

/**
 * The list of sources kept in the workbench.
 *
 * Only a small record of each source is stored in the browser; the extracted
 * text and the original file are not, because the original lives under
 * `raw/` as soon as it is stored and everything else is rebuilt from it.
 * An entry that outlives its capture — after a reload — therefore knows what was
 * captured but not its text, and has to be re-fetched before it can be used.
 */
export type CaptureEntry = {
  /** Request URL. This is the identity, and matches the `preview:` key. */
  url: string;
  title: string;
  fidelity: string;
  kind: string;
  capturedAt: number;
  /** Set once the original has been stored under raw/. */
  path?: string;
  /** Full capture, present only while it is still in memory. */
  capture?: CapturePreview;
};

const KEY_PREFIX = "noteagent.captures.";

/**
 * How many sources the workbench remembers.
 *
 * The list is kept in the browser, so it needs a bound; the project's own copies
 * of stored sources live in `raw/` and are listed in the notes module, so
 * an entry falling off the end loses a bookmark rather than an original.
 */
export const STORED_ENTRY_LIMIT = 500;

export function indexKey(project: string): string {
  return `${KEY_PREFIX}${project}`;
}

function toEntry(raw: unknown): CaptureEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.url !== "string" || !value.url) return null;
  const path = typeof value.path === "string" && value.path ? value.path : undefined;
  return {
    url: value.url,
    title: typeof value.title === "string" ? value.title : "",
    fidelity: typeof value.fidelity === "string" ? value.fidelity : "",
    kind: typeof value.kind === "string" ? value.kind : "",
    capturedAt: typeof value.capturedAt === "number" ? value.capturedAt : 0,
    ...(path ? { path } : {}),
  };
}

/**
 * Keeps the list within its bound, dropping the least recently captured.
 *
 * The survivors keep the order they were in: the list is what the reader sees,
 * and having rows jump around when an old source is fetched again would be worse
 * than the old ones quietly falling off the end. What counts as newest is the
 * last time a source was captured, so fetching an old one keeps it.
 */
export function capEntries(
  entries: CaptureEntry[],
  limit = STORED_ENTRY_LIMIT,
): CaptureEntry[] {
  if (entries.length <= limit) return entries;
  const keep = new Set(
    [...entries]
      .sort((a, b) => b.capturedAt - a.capturedAt)
      .slice(0, limit)
      .map((entry) => entry.url),
  );
  return entries.filter((entry) => keep.has(entry.url));
}

/**
 * Reads the stored list. Anything malformed is dropped rather than trusted:
 * the value is user-editable and may predate a format change.
 */
export function readIndex(
  project: string,
  storage: StorageLike | null = storageOrNull(),
): CaptureEntry[] {
  if (!storage || !project) return [];
  try {
    const raw = storage.getItem(indexKey(project));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return capEntries(
      parsed.map(toEntry).filter((entry): entry is CaptureEntry => entry !== null),
    );
  } catch {
    return [];
  }
}

export function writeIndex(
  project: string,
  entries: CaptureEntry[],
  storage: StorageLike | null = storageOrNull(),
): void {
  if (!storage || !project) return;
  const kept = capEntries(entries);
  try {
    if (kept.length === 0) {
      storage.removeItem(indexKey(project));
      return;
    }
    // Only the fields that belong in storage; a live capture stays in memory.
    storage.setItem(
      indexKey(project),
      JSON.stringify(
        kept.map((entry) => ({
          url: entry.url,
          title: entry.title,
          fidelity: entry.fidelity,
          kind: entry.kind,
          capturedAt: entry.capturedAt,
          ...(entry.path ? { path: entry.path } : {}),
        })),
      ),
    );
  } catch {
    // A full or unavailable storage must not break the workbench.
  }
}

export function clearIndex(
  project: string,
  storage: StorageLike | null = storageOrNull(),
): void {
  if (!storage || !project) return;
  try {
    storage.removeItem(indexKey(project));
  } catch {
    // ignore
  }
}

export function entryFromCapture(
  capture: CapturePreview,
  capturedAt = Date.now(),
): CaptureEntry {
  return {
    url: capture.url,
    title: capture.title || capture.url,
    fidelity: capture.fidelity,
    kind: capture.kind ?? "",
    capturedAt,
    capture,
  };
}

/** Newest first, replacing the entry for the same page if there is one. */
export function mergeEntries(
  current: CaptureEntry[],
  incoming: CaptureEntry[],
): CaptureEntry[] {
  const next = [...current];
  for (const entry of incoming) {
    const index = next.findIndex((item) =>
      item.capture && entry.capture
        ? isSameCapture(item.capture, entry.capture)
        : item.url === entry.url,
    );
    if (index >= 0) {
      // Keep whatever was already recorded about the stored original; a new
      // fetch knows nothing about it.
      next[index] = { ...entry, path: entry.path ?? next[index].path };
    } else {
      next.unshift(entry);
    }
  }
  return capEntries(next);
}

/**
 * Where each page was written, keyed by URL.
 *
 * Redirects mean a page can be stored under its request URL while the backend
 * reports it under the settled URL, so both are recorded when both are known.
 */
export function writtenPathsFrom(
  entries: CaptureEntry[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const entry of entries) {
    if (!entry.path) continue;
    out[entry.url] = entry.path;
    if (entry.capture?.finalUrl) out[entry.capture.finalUrl] = entry.path;
  }
  return out;
}
