import type { ModuleId } from "@/modules";
import { storageOrNull, type StorageLike } from "@/lib/storage";

/**
 * Where the reader has been, and which step they are on.
 *
 * Reading is a walk: a page, something it points at, something that points at
 * that. What is on screen is a pair — the page being read and the one opened from
 * it — and going back means the pair before this one, which is a different thing
 * from opening a second page and losing the first. So the steps are kept whole,
 * and back and forward move between them.
 *
 * A document opened outright starts a new step; so does following a link, because
 * the pair it produces is a new place. Steps after the current one are dropped
 * when a new step is taken, the way a fresh move in a browser discards its
 * forward history.
 */

/** What one pane is showing. */
export type PaneView = {
  module: ModuleId;
  /** The document or address in it, when the module has one. */
  target: string | null;
};

/** What the reader sees: the page being read, and the one opened from it. */
export type ReadingPair = {
  main: PaneView;
  side: PaneView | null;
};

/**
 * How many steps back are kept.
 *
 * Enough to retrace a session of reading, few enough that the steps do not
 * become a list to tidy. The oldest falls away first.
 */
export const HISTORY_DEPTH = 50;

export type ReadingHistory = {
  /** Steps before the current one, oldest first. */
  past: ReadingPair[];
  current: ReadingPair;
  /** Steps taken back from, nearest first. */
  future: ReadingPair[];
};

export function view(module: ModuleId, target: string | null = null): PaneView {
  return { module, target };
}

function sameView(left: PaneView, right: PaneView): boolean {
  return left.module === right.module && left.target === right.target;
}

export function samePair(left: ReadingPair, right: ReadingPair): boolean {
  if (!sameView(left.main, right.main)) return false;
  if (left.side === null || right.side === null) return left.side === right.side;
  return sameView(left.side, right.side);
}

export function startHistory(current: ReadingPair): ReadingHistory {
  return { past: [], current, future: [] };
}

/**
 * Takes a step.
 *
 * A pair the reader is already looking at is not a step: switching between two
 * tabs of the same page, or re-opening what is in front, would otherwise fill the
 * history with places they never left. Anything they had gone back from is
 * dropped, because the walk has moved on.
 */
export function record(
  history: ReadingHistory,
  next: ReadingPair,
  depth = HISTORY_DEPTH,
): ReadingHistory {
  if (samePair(history.current, next)) return history;
  const keep = Math.max(1, depth);
  return {
    past: [...history.past, history.current].slice(-keep),
    current: next,
    future: [],
  };
}

export function canGoBack(history: ReadingHistory): boolean {
  return history.past.length > 0;
}

export function canGoForward(history: ReadingHistory): boolean {
  return history.future.length > 0;
}

/** One step back, or nothing when this is the first step. */
export function goBack(history: ReadingHistory): ReadingHistory {
  if (history.past.length === 0) return history;
  const past = history.past.slice(0, -1);
  const current = history.past[history.past.length - 1] as ReadingPair;
  return { past, current, future: [history.current, ...history.future] };
}

/** One step forward again, or nothing when nothing was taken back. */
export function goForward(history: ReadingHistory): ReadingHistory {
  if (history.future.length === 0) return history;
  const [current, ...future] = history.future as [ReadingPair, ...ReadingPair[]];
  return { past: [...history.past, history.current], current, future };
}

/* ------------------------------------------------------------- persistence */

const KEY_PREFIX = "noteagent.reading.";

function pairKey(project: string): string {
  return `${KEY_PREFIX}${project}`;
}

function toView(raw: unknown): PaneView | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.module !== "string" || !value.module) return null;
  const target = typeof value.target === "string" && value.target ? value.target : null;
  return { module: value.module as ModuleId, target };
}

function toPair(raw: unknown): ReadingPair | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const main = toView(value.main);
  if (!main) return null;
  return { main, side: toView(value.side) };
}

/**
 * The place the reader left off, so a reload comes back to it.
 *
 * Only this one step is kept: the history is where they have been, and coming
 * back to the last place is what a reload should do. Malformed values are dropped
 * rather than trusted, since the value is user-editable.
 */
export function readPair(
  project: string,
  storage: StorageLike | null = storageOrNull(),
): ReadingPair | null {
  if (!storage || !project) return null;
  try {
    const raw = storage.getItem(pairKey(project));
    if (!raw) return null;
    return toPair(JSON.parse(raw) as unknown);
  } catch {
    return null;
  }
}

export function writePair(
  project: string,
  pair: ReadingPair | null,
  storage: StorageLike | null = storageOrNull(),
): void {
  if (!storage || !project) return;
  try {
    if (!pair) {
      storage.removeItem(pairKey(project));
      return;
    }
    storage.setItem(pairKey(project), JSON.stringify(pair));
  } catch {
    // A full or unavailable storage must not break the reader.
  }
}
