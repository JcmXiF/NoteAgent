import { create } from "zustand";
import {
  canGoBack,
  canGoForward,
  goBack,
  goForward,
  readPair,
  record,
  startHistory,
  writePair,
  type PaneView,
  type ReadingHistory,
  type ReadingPair,
} from "@/lib/reading-history";
import { activeTab } from "@/lib/browse";
import { useBrowseStore } from "@/stores/browse-store";
import { useDocsStore } from "@/stores/docs-store";
import { useLayoutStore, type PaneSlot } from "@/stores/layout-store";
import type { ModuleId } from "@/modules";

/**
 * The step the reader is on, and the steps behind it.
 *
 * Reading produces pairs: a page and the one opened from it. Those pairs are
 * worth keeping, because going back should return to the pair — both panes
 * together — rather than to one page with the other left where it was. The pair
 * is derived from what the panes show rather than stored twice, so the panes stay
 * the truth about themselves and this only remembers where they were.
 */

/** What one pane holds of a module's own state. */
function targetOf(module: ModuleId | null, slot: PaneSlot): string | null {
  if (!module) return null;
  if (module === "docs") return useDocsStore.getState().paths[slot];
  if (module === "browse") {
    const browse = useBrowseStore.getState();
    return activeTab(browse.guest.tabs, browse.guest.activeId)?.url ?? null;
  }
  return null;
}

/**
 * What the panes are showing now.
 *
 * `edited` is for the pane an action has just changed: the browser reports its
 * page asynchronously, and a step recorded before that report would name the page
 * the reader has left.
 */
function currentPair(edited?: PaneView & { slot: PaneSlot }): ReadingPair {
  const layout = useLayoutStore.getState();
  const main: PaneView = { module: layout.view, target: targetOf(layout.view, "main") };
  const side: PaneView | null = layout.side
    ? { module: layout.side, target: targetOf(layout.side, "side") }
    : null;
  const pair: ReadingPair = { main, side };
  if (!edited) return pair;
  return edited.slot === "main"
    ? { main: { module: edited.module, target: edited.target }, side }
    : { main, side: { module: edited.module, target: edited.target } };
}

/**
 * Whether the panes are being put back by a step rather than moved by the reader.
 *
 * Both look the same from the panes' side, and only the reader's own move is worth
 * a step: recording a look back would leave another step behind them every time
 * they looked back. It cannot be decided when the step is taken either — that is
 * after the task that moved the panes, because a pane may name what it shows only
 * once it has been asked — so the restore says so on the way in and the step reads
 * it on the way out.
 */
let restoring = false;

/** Puts the panes back the way a step says they were. */
function applyPair(pair: ReadingPair): void {
  const layout = useLayoutStore.getState();
  // Armed only when a module really moves: that is the change that asks for a step,
  // and an armed flag left over would eat the reader's next move instead.
  if (layout.view !== pair.main.module || layout.side !== (pair.side?.module ?? null)) {
    restoring = true;
  }
  // Modules first, so a document is put into a pane that is showing its module.
  layout.setPaneModule("main", pair.main.module);
  if (pair.side) layout.setPaneModule("side", pair.side.module);
  else layout.closeSide();

  const place = (slot: PaneSlot, view: PaneView): void => {
    if (view.module === "docs") {
      useDocsStore.getState().setPanePath(slot, view.target);
      return;
    }
    if (view.module === "browse" && view.target) {
      void useBrowseStore.getState().open(view.target);
    }
  };
  place("main", pair.main);
  // A step with no extension leaves the pane without one, so what it was showing
  // goes too: a document kept behind a closed pane would come back later as if the
  // reader had left it there.
  if (pair.side) place("side", pair.side);
  else useDocsStore.getState().setPanePath("side", null);
}

type ReadingState = {
  history: ReadingHistory;
  /** Reads the panes and takes a step, unless they are where they already were. */
  record: (edited?: PaneView & { slot: PaneSlot }) => void;
  back: () => void;
  forward: () => void;
  /** For a project change: the steps belonged to the project that was open. */
  reset: (project?: string | null) => void;
  /** Reads back the place a project was left in, and starts from there. */
  hydrate: (project: string) => void;
};

export const useReadingStore = create<ReadingState>((set, get) => ({
  history: startHistory({ main: { module: "docs", target: null }, side: null }),

  record: (edited) => {
    const next = currentPair(edited);
    const history = record(get().history, next);
    if (history === get().history) return;
    set({ history });
  },

  back: () => {
    const history = get().history;
    if (!canGoBack(history)) return;
    const moved = goBack(history);
    applyPair(moved.current);
    set({ history: moved });
  },

  forward: () => {
    const history = get().history;
    if (!canGoForward(history)) return;
    const moved = goForward(history);
    applyPair(moved.current);
    set({ history: moved });
  },

  reset: (project) => {
    if (project) writePair(project, null);
    set({
      history: startHistory({ main: { module: "docs", target: null }, side: null }),
    });
  },

  hydrate: (project) => {
    const pair = readPair(project);
    set({ history: startHistory(pair ?? { main: { module: "docs", target: null }, side: null }) });
  },
}));

/**
 * Writes down where the reader is, so the next visit starts there.
 *
 * Called by whoever changed the panes, rather than from a subscription: what to
 * write is exactly what the action just did.
 */
export function rememberReading(edited?: PaneView & { slot: PaneSlot }): void {
  const reading = useReadingStore.getState();
  reading.record(edited);
  const project = useDocsStore.getState().project;
  if (project) writePair(project, reading.history.current);
}

/**
 * Takes a step once the move that asked for it has settled.
 *
 * Where the reader is gets read off the panes, and one of them — the browser — names
 * what it shows only after the shell has been asked. Recording the moment a pane
 * changes module would therefore write down the place they were leaving, so the
 * step waits for the rest of that task. Several changes in one task are one move.
 */
let stepDue = false;

function stepWhenSettled(): void {
  if (stepDue) return;
  stepDue = true;
  queueMicrotask(() => {
    stepDue = false;
    if (restoring) {
      restoring = false;
      return;
    }
    rememberReading();
  });
}

/**
 * Follows the panes, so that being somewhere else is a step.
 *
 * Which module sits in which pane is the reader's own move — from the rail, from a
 * split, from closing one — and it is part of where they are, not a detail of a
 * view: going back has to be able to put the graph in front with the page beside it
 * again. A view that changes the panes and knows the document it is putting there
 * records its own step; this only covers the moves nothing else reports, and the two
 * agree because a pair already recorded is not recorded twice.
 */
let started = false;

export function startReadingSync(): void {
  if (started) return;
  started = true;

  useLayoutStore.subscribe((state, previous) => {
    if (state.view === previous.view && state.side === previous.side) return;
    stepWhenSettled();
  });
}
