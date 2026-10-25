import { create } from "zustand";
import { persist } from "zustand/middleware";
import { isModuleId, isSingleModule, MODULE_IDS, type ModuleId } from "@/modules";
import type { PanePlan } from "@/lib/placement";

/** Which pane has focus, and therefore drives the sidebar and keyboard commands. */
export type PaneSlot = "main" | "side";

/** Both panes, in the order they appear. */
export const PANE_SLOTS = ["main", "side"] as const;

const LEFT_MIN = 180;
const LEFT_MAX = 420;
const RIGHT_MIN = 260;
/** Absolute ceiling; dragging also caps the panel at a share of the window. */
const RIGHT_MAX_ABS = 760;
export const RIGHT_MAX_RATIO = 0.5;
/** Minimum width of a workspace pane while split. */
const PANE_MIN = 240;

/**
 * How wide each module's sidebar starts.
 *
 * These are starting points, not rules: a module with a list of names (chat) needs
 * less room than one with a tree (docs), and a reader can drag any of them and
 * have it remembered.
 */
const DEFAULT_LEFT_WIDTHS: Record<ModuleId, number> = {
  chat: 240,
  sources: 260,
  docs: 320,
  notes: 280,
  graph: 260,
  browse: 260,
};

/**
 * The right panel's sections, in the order they appear.
 *
 * The panel belongs to the workspace rather than to a module — what it lists comes
 * from several views — but which parts matter does follow from what is being read.
 */
export const RIGHT_SECTIONS = ["attention", "selection", "plan", "compile"] as const;
export type RightSectionId = (typeof RIGHT_SECTIONS)[number];

/** i18n key per section, used for its heading and its fold control. */
export const RIGHT_SECTION_LABEL: Record<RightSectionId, string> = {
  attention: "selections.attention",
  selection: "selections.selection",
  plan: "selections.plan",
  compile: "selections.compile",
};

/**
 * Which sections are open when the reader has not said otherwise.
 *
 * A module's work happens in one or two of them: planning while looking at the
 * graph, choosing, planning and compiling while looking at the sources workbench.
 * Opening everything means the reader closes things before they can start; the
 * count on a heading means nothing folded away is lost.
 */
const DEFAULT_OPEN: Record<ModuleId, RightSectionId[]> = {
  chat: ["selection"],
  // Selecting, planning and compiling is the one sitting the workbench exists for.
  sources: ["selection", "plan", "compile"],
  docs: ["selection", "plan"],
  // Writing a remark is not part of planning or compiling anything, so nothing of
  // the right panel has a claim on it and the panel starts empty.
  notes: [],
  graph: ["plan", "compile"],
  browse: ["selection"],
};

/** A section's width, per module, as its starting point. */
const DEFAULT_RIGHT_WIDTH = 320;

/**
 * The sidebar's sections, and the order they start in.
 *
 * A sidebar says what the module is (a list of conversations, a file tree) and what
 * the app is doing; those are different things with different claims on the room,
 * so they are peers rather than one column that happens to hold both.
 */
const LEFT_SECTIONS = ["module", "jobs"] as const;
export type LeftSectionId = (typeof LEFT_SECTIONS)[number];

/**
 * The section that takes whatever room is left.
 *
 * It is the module's own panel, and its content is a list that should fill the
 * column. The others are remembered heights the reader can drag, which is only
 * meaningful because exactly one of them grows.
 */
const GROWING_LEFT_SECTION: LeftSectionId = "module";

/** How short and how tall a dragged section may be. */
const SECTION_MIN_HEIGHT = 64;
const SECTION_MAX_HEIGHT = 520;

type LeftSections = {
  /** Top to bottom. */
  order: LeftSectionId[];
  /** Sections the reader has folded, by id. */
  folds: Partial<Record<LeftSectionId, boolean>>;
  /** Heights the reader has dragged, for every section that does not grow. */
  heights: Partial<Record<LeftSectionId, number>>;
};

function defaultLeftSections(): LeftSections {
  return { order: [...LEFT_SECTIONS], folds: {}, heights: {} };
}

/**
 * Reads a stored section arrangement.
 *
 * Unknown ids are dropped and anything missing is appended in the default order, so
 * a sidebar written by an older version — or a newer one that knows a section this
 * does not — still opens with every section present exactly once.
 */
export function normalizeLeftSections(raw: unknown): LeftSections {
  const out = defaultLeftSections();
  if (!raw || typeof raw !== "object") return out;
  const saved = raw as Partial<LeftSections>;

  const order: LeftSectionId[] = [];
  for (const id of Array.isArray(saved.order) ? saved.order : []) {
    if (LEFT_SECTIONS.includes(id) && !order.includes(id)) order.push(id);
  }
  for (const id of LEFT_SECTIONS) if (!order.includes(id)) order.push(id);
  out.order = order;

  for (const id of LEFT_SECTIONS) {
    const folded = saved.folds?.[id];
    if (typeof folded === "boolean") out.folds[id] = folded;
    const height = saved.heights?.[id];
    if (typeof height === "number") {
      out.heights[id] = clamp(height, SECTION_MIN_HEIGHT, SECTION_MAX_HEIGHT);
    }
  }
  return out;
}

/** Moves a section one place up or down, and answers with the new arrangement. */
export function reorderLeftSection(
  sections: LeftSections,
  id: LeftSectionId,
  delta: -1 | 1,
): LeftSections {
  const index = sections.order.indexOf(id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= sections.order.length) return sections;
  const order = [...sections.order];
  [order[index], order[target]] = [order[target], order[index]];
  return { ...sections, order };
}

/**
 * Whether a section's body is shown. Everything starts open.
 */
export function leftSectionOpen(sections: LeftSections, id: LeftSectionId): boolean {
  return sections.folds[id] !== true;
}

/**
 * Which section takes the room nobody else claimed.
 *
 * The module's own panel while it is open, because its list is the thing that should
 * fill the column. Once it is folded away the room goes to whatever the reader left
 * open, which is what folding it was for; with everything folded there is nothing to
 * fill and the column simply ends.
 */
export function growingLeftSection(sections: LeftSections): LeftSectionId | null {
  if (leftSectionOpen(sections, GROWING_LEFT_SECTION)) return GROWING_LEFT_SECTION;
  return sections.order.find((id) => leftSectionOpen(sections, id)) ?? null;
}

/** The height of a section that does not grow. */
export function leftSectionHeight(sections: LeftSections, id: LeftSectionId): number {
  return sections.heights[id] ?? 160;
}

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

export function clampSplitRatio(ratio: number, width: number): number {
  if (!Number.isFinite(width) || width <= 0) return 0.5;
  const min = PANE_MIN / width;
  if (min * 2 >= 1) return 0.5;
  return clamp(ratio, min, 1 - min);
}

type LayoutState = {
  /** Module shown in the main pane. */
  view: ModuleId;
  /** Module shown beside it; null means a single pane. */
  side: ModuleId | null;
  /** Remembered so the split button reopens whatever was last placed beside. */
  lastSide: ModuleId;
  focused: PaneSlot;

  leftOpen: boolean;
  /**
   * The sidebar's width, per module.
   *
   * A module's sidebar says what that module is: a list of conversations needs a
   * little room, a file tree needs more. One width for all of them means every
   * switch is either too narrow or a waste of space.
   */
  leftWidths: Record<ModuleId, number>;
  /**
   * The sidebar's sections: which, in what order, folded or not, and how tall.
   *
   * A module's panel and the jobs are peers here, so the reader can decide what the
   * sidebar is for rather than living with one fixed stack.
   */
  leftSections: LeftSections;
  rightOpen: boolean;
  rightWidth: number;
  splitRatio: number;
  /**
   * Which right-panel sections the reader has opened or closed by hand.
   *
   * Only the exceptions are kept: what a section does by default follows from the
   * module being looked at, and a reader who never touches these gets that.
   */
  rightFolds: Partial<Record<RightSectionId, boolean>>;

  /**
   * Whether this window belongs to the desktop shell.
   *
   * The shell's browser is drawn over the page rather than in it, so anything that
   * covers the window has to make room for it; a page in a browser has nothing to
   * make room for.
   */
  hasShell: boolean;
  /** Whether the jobs overlay is in front. */
  jobsOpen: boolean;
  setJobsOpen: (open: boolean | ((prev: boolean) => boolean)) => void;

  /**
   * Settings replace the workspace. They are not a module: a second pane of the
   * same form would not be a second thing to read.
   *
   * Unsaved edits are remembered here so the rail can ask before it leaves,
   * which it could not see if the form kept that to itself.
   */
  settingsOpen: boolean;
  settingsDirty: boolean;
  settingsConfirm: boolean;
  pendingOpen: { id: ModuleId; side: boolean } | null;
  setSettingsOpen: (open: boolean) => void;
  setSettingsDirty: (dirty: boolean) => void;
  confirmSettingsLeave: () => void;
  cancelSettingsLeave: () => void;
  /**
   * Whether something is in front of the page that the shell's browser would
   * otherwise be drawn over.
   *
   * Counted rather than flagged: two dialogs can be open at once, and the page
   * should only come back when the last of them closes.
   */
  guestHolds: number;
  holdGuest: () => void;
  releaseGuest: () => void;

  /**
   * What a pane was showing before it was swapped in place, per pane.
   *
   * Not persisted: it is about the current visit, and a reloaded window has nothing
   * to go back to.
   */
  paneReturn: Partial<Record<PaneSlot, ModuleId>>;

  /**
   * Shows a module from the rail: in the front pane, or beside it with the
   * modifier.
   *
   * Selecting a page means the reader wants to be in it, not that they want their
   * other pane taken away, so what is beside stays. Only the two panes' *order*
   * changes when the page selected is the one already beside: it comes forward to
   * be read, and the page that was in front takes its place, since losing one of
   * two open pages over a change of which is read would be losing the reader's work.
   */
  openModule: (id: ModuleId, options?: { side?: boolean }) => void;
  /**
   * Puts a module in one pane, leaving the other alone.
   *
   * For moving one pane to another module while the split stays — an action that
   * needs a document beside what is being read, without giving up the pane next to
   * it, or the rail, which selects a page rather than arranging panes.
   */
  setPaneModule: (slot: PaneSlot, id: ModuleId) => void;
  /**
   * Shows a module in a pane *in place of* what it was showing, remembering what
   * that was.
   *
   * For something that is the same thing seen another way — the web page a note was
   * written from, say: it belongs where the note is, and the pane should be able to
   * go back to the note rather than leaving the reader to find it again.
   */
  pushPaneModule: (slot: PaneSlot, id: ModuleId) => void;
  /** Goes back to what the pane was showing before it was swapped. */
  returnPaneModule: (slot: PaneSlot) => void;
  /**
   * Puts the panes where a placement says they go.
   *
   * The plan is the whole answer — which module is in front, what is beside it,
   * which pane has focus, and what a pane can still go back to — so this is one
   * write rather than a sequence of decisions to get right again.
   */
  applyPlacement: (plan: PanePlan) => void;
  closeSide: () => void;
  focusPane: (slot: PaneSlot) => void;
  swapPanes: () => void;

  setLeftOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  setRightOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  /** Widens the sidebar of the module being looked at. */
  setLeftWidth: (width: number) => void;
  /** `maxWidth` is computed from the window so the panel never takes over. */
  setRightWidth: (width: number, maxWidth?: number) => void;
  setSplitRatio: (ratio: number) => void;
  /** Opens or closes one section of the right panel, and remembers the choice. */
  setSectionOpen: (section: RightSectionId, open: boolean) => void;
  /** Opens or closes one section of the sidebar. */
  setLeftSectionOpen: (section: LeftSectionId, open: boolean) => void;
  /** Moves a sidebar section one place up or down. */
  moveLeftSection: (section: LeftSectionId, delta: -1 | 1) => void;
  /** Remembers the height a sidebar section was dragged to. */
  setLeftSectionHeight: (section: LeftSectionId, height: number) => void;
};

/** The sidebar's width for a module, and whether each right section is open. */
export function leftWidthFor(state: LayoutState, module: ModuleId): number {
  return state.leftWidths[module] ?? DEFAULT_LEFT_WIDTHS[module] ?? 260;
}

/**
 * Reads a stored arrangement, whichever shape it is in.
 *
 * Two things are being defended against. Stored values are user-editable, so every
 * number is clamped and every id checked. And the shape changed once — the sidebar
 * width went from one number to one per module — so a file written before that
 * still loads: its single width becomes the width of the module the reader left
 * off in, which is the width they had chosen while looking at it.
 */
export function mergeStoredLayout(
  persisted: unknown,
  current: LayoutState,
): LayoutState {
  const saved = (persisted ?? {}) as Partial<LayoutState> & { leftWidth?: unknown };
  const view = isModuleId(saved.view) ? saved.view : current.view;
  const side = isModuleId(saved.side) ? saved.side : null;

  const widths: Record<ModuleId, number> = { ...current.leftWidths };
  for (const id of MODULE_IDS) {
    const stored = saved.leftWidths?.[id];
    if (typeof stored === "number") widths[id] = clamp(stored, LEFT_MIN, LEFT_MAX);
  }
  // The single width is the old shape, so it only applies to a file that has no
  // per-module widths at all: a file carrying both is a newer one, and last
  // writer wins would let a stale key overrule what the reader set.
  if (!saved.leftWidths && typeof saved.leftWidth === "number") {
    widths[view] = clamp(saved.leftWidth, LEFT_MIN, LEFT_MAX);
  }

  const folds: Partial<Record<RightSectionId, boolean>> = {};
  for (const section of RIGHT_SECTIONS) {
    const chosen = saved.rightFolds?.[section];
    if (typeof chosen === "boolean") folds[section] = chosen;
  }

  const returns: Partial<Record<PaneSlot, ModuleId>> = {};
  for (const slot of PANE_SLOTS) {
    const back = saved.paneReturn?.[slot];
    if (isModuleId(back)) returns[slot] = back;
  }

  return {
    ...current,
    view,
    side,
    lastSide: isModuleId(saved.lastSide) ? saved.lastSide : current.lastSide,
    focused: side === null ? "main" : (saved.focused === "side" ? "side" : "main"),
    leftOpen: typeof saved.leftOpen === "boolean" ? saved.leftOpen : current.leftOpen,
    leftWidths: widths,
    leftSections: normalizeLeftSections(saved.leftSections),
    rightOpen: typeof saved.rightOpen === "boolean" ? saved.rightOpen : current.rightOpen,
    rightWidth: clamp(
      typeof saved.rightWidth === "number" ? saved.rightWidth : current.rightWidth,
      RIGHT_MIN,
      RIGHT_MAX_ABS,
    ),
    splitRatio: clamp(
      typeof saved.splitRatio === "number" ? saved.splitRatio : current.splitRatio,
      0,
      1,
    ),
    rightFolds: folds,
    paneReturn: returns,
  };
}

export function sectionIsOpen(
  state: Pick<LayoutState, "rightFolds">,
  section: RightSectionId,
  module: ModuleId,
): boolean {
  const chosen = state.rightFolds[section];
  if (chosen !== undefined) return chosen;
  return DEFAULT_OPEN[module].includes(section);
}

function resolveBool(
  next: boolean | ((prev: boolean) => boolean),
  prev: boolean,
): boolean {
  return typeof next === "function" ? next(prev) : next;
}

/** The return recorded for a pane, without that key left behind as `undefined`. */
function withoutReturn(
  paneReturn: Partial<Record<PaneSlot, ModuleId>>,
  slot: PaneSlot,
): Partial<Record<PaneSlot, ModuleId>> {
  const { [slot]: _dropped, ...rest } = paneReturn;
  return rest;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set, get) => ({
      view: "docs",
      side: null,
      lastSide: "graph",
      focused: "main",

      leftOpen: true,
      leftWidths: { ...DEFAULT_LEFT_WIDTHS },
      leftSections: defaultLeftSections(),
      rightOpen: true,
      rightWidth: DEFAULT_RIGHT_WIDTH,
      splitRatio: 0.5,
      rightFolds: {},
      hasShell: false,
      jobsOpen: false,
      guestHolds: 0,
      paneReturn: {},
      settingsOpen: false,
      settingsDirty: false,
      settingsConfirm: false,
      pendingOpen: null,

      setSettingsOpen: (open) => {
        if (open) {
          set({ settingsOpen: true, settingsConfirm: false, pendingOpen: null });
          return;
        }
        if (get().settingsOpen && get().settingsDirty) {
          set({ settingsConfirm: true, pendingOpen: null });
          return;
        }
        set({ settingsOpen: false, settingsConfirm: false, pendingOpen: null });
      },
      setSettingsDirty: (dirty) => {
        if (get().settingsDirty === dirty) return;
        set({ settingsDirty: dirty });
      },
      confirmSettingsLeave: () => {
        const pending = get().pendingOpen;
        set({
          settingsOpen: false,
          settingsDirty: false,
          settingsConfirm: false,
          pendingOpen: null,
        });
        if (pending) get().openModule(pending.id, { side: pending.side });
      },
      cancelSettingsLeave: () => set({ settingsConfirm: false, pendingOpen: null }),

      openModule: (id, options) => {
        if (get().settingsOpen) {
          if (get().settingsDirty) {
            set({
              settingsConfirm: true,
              pendingOpen: { id, side: Boolean(options?.side) },
            });
            return;
          }
          set({ settingsOpen: false });
        }
        const { view, side, focused, paneReturn } = get();
        if (!options?.side) {
          // A module the window can only show one of is on screen at most once, and
          // the pane holding it is the one the shell needs in focus. Selecting its
          // rail entry is therefore a matter of going to it.
          if (isSingleModule(id)) {
            const showing = view === id ? "main" : side === id ? "side" : null;
            if (showing) {
              if (focused !== showing) set({ focused: showing });
              return;
            }
          }
          if (view === id) {
            // The page in front was selected again: it is already where the reader
            // is reading, and the pane beside it was not part of the request.
            if (focused !== "main") set({ focused: "main" });
            return;
          }
          if (side === id) {
            // The page beside comes forward to be read, and the page that was in
            // front takes its place — neither is lost over a change of what is read.
            set({ view: id, side: view, lastSide: view, focused: "main", paneReturn: {} });
            return;
          }
          // A page that is not open takes the front pane, and the pane beside keeps
          // what it was showing: choosing a page is not asking for that one to go.
          set({
            view: id,
            focused: "main",
            paneReturn: withoutReturn(paneReturn, "main"),
          });
          return;
        }
        // Beside is its own pane, so it can hold the same module as the main one
        // — two readers side by side is the point of a split here. A module the
        // window can only show one of has no second pane to be opened into: it is
        // already on screen, so the request is answered by going to it.
        if (side === id) {
          set({ focused: "side" });
          return;
        }
        if (isSingleModule(id) && view === id) {
          set({ focused: "main" });
          return;
        }
        set({
          side: id,
          lastSide: id,
          focused: "side",
          paneReturn: withoutReturn(paneReturn, "side"),
        });
      },

      setPaneModule: (slot, id) =>
        set((state) => {
          const next =
            slot === "main"
              ? state.view === id
                ? state
                : { view: id }
              : state.side === id
                ? state
                : { side: id, lastSide: id };
          if (next === state) return state;
          // Showing something else by any other route forgets where the pane came
          // from: there is no longer anything to go back to.
          return { ...next, paneReturn: withoutReturn(state.paneReturn, slot) };
        }),

      pushPaneModule: (slot, id) =>
        set((state) => {
          const showing = slot === "main" ? state.view : state.side;
          if (!showing || showing === id) return state;
          return {
            ...(slot === "main" ? { view: id } : { side: id, lastSide: id }),
            paneReturn: { ...state.paneReturn, [slot]: showing },
          };
        }),

      returnPaneModule: (slot) =>
        set((state) => {
          const back = state.paneReturn[slot];
          if (!back) return state;
          return {
            ...(slot === "main" ? { view: back } : { side: back, lastSide: back }),
            paneReturn: withoutReturn(state.paneReturn, slot),
          };
        }),

      applyPlacement: (plan) =>
        set({
          view: plan.view,
          side: plan.side,
          lastSide: plan.lastSide,
          // Focus does not belong to a pane that is not there.
          focused: plan.side === null ? "main" : plan.focused,
          paneReturn: plan.paneReturn,
        }),

      closeSide: () => set({ side: null, focused: "main" }),

      focusPane: (slot) =>
        set((state) => {
          if (slot === "side" && state.side === null) return state;
          return { focused: slot };
        }),

      swapPanes: () =>
        set((state) =>
          state.side === null
            ? state
            : {
                view: state.side,
                side: state.view,
                focused: state.focused === "main" ? "side" : "main",
              },
        ),

      setLeftOpen: (open) =>
        set((state) => ({ leftOpen: resolveBool(open, state.leftOpen) })),
      setRightOpen: (open) =>
        set((state) => ({ rightOpen: resolveBool(open, state.rightOpen) })),
      setLeftWidth: (width) =>
        set((state) => ({
          // Remembered against the module it was dragged for, not for all of them.
          leftWidths: { ...state.leftWidths, [state.view]: clamp(width, LEFT_MIN, LEFT_MAX) },
        })),

      setSectionOpen: (section, open) =>
        set((state) => ({ rightFolds: { ...state.rightFolds, [section]: open } })),

      setLeftSectionOpen: (section, open) =>
        set((state) => ({
          leftSections: { ...state.leftSections, folds: { ...state.leftSections.folds, [section]: !open } },
        })),

      moveLeftSection: (section, delta) =>
        set((state) => ({ leftSections: reorderLeftSection(state.leftSections, section, delta) })),

      setLeftSectionHeight: (section, height) =>
        set((state) => ({
          leftSections: {
            ...state.leftSections,
            heights: {
              ...state.leftSections.heights,
              [section]: clamp(height, SECTION_MIN_HEIGHT, SECTION_MAX_HEIGHT),
            },
          },
        })),

      setJobsOpen: (open) =>
        set((state) => {
          const next = resolveBool(open, state.jobsOpen);
          if (next === state.jobsOpen) return state;
          // Nothing to make room for where there is no shell, and the overlay would
          // be in the way of the page for no reason.
          if (next && !state.hasShell) return state;
          return { jobsOpen: next };
        }),

      holdGuest: () => set((state) => ({ guestHolds: state.guestHolds + 1 })),

      releaseGuest: () =>
        set((state) => ({ guestHolds: Math.max(0, state.guestHolds - 1) })),
      setRightWidth: (width, maxWidth) =>
        set({
          rightWidth: clamp(
            width,
            RIGHT_MIN,
            Math.max(RIGHT_MIN, Math.min(RIGHT_MAX_ABS, maxWidth ?? RIGHT_MAX_ABS)),
          ),
        }),
      setSplitRatio: (ratio) => set({ splitRatio: clamp(ratio, 0, 1) }),
    }),
    {
      name: "noteagent.layout",
      version: 3,
      /**
       * Keeps what an older version stored, shape and all.
       *
       * The shape changed once, when one sidebar width became one per module; `merge`
       * below reads either. Without this, a version bump would throw the reader's
       * arrangement away — and since layouts are invisible until something looks
       * wrong, that would go unnoticed.
       */
      migrate: (persisted) => persisted as LayoutState,
      partialize: (state) => ({
        view: state.view,
        side: state.side,
        lastSide: state.lastSide,
        focused: state.focused,
        leftOpen: state.leftOpen,
        leftWidths: state.leftWidths,
        leftSections: state.leftSections,
        rightOpen: state.rightOpen,
        rightWidth: state.rightWidth,
        splitRatio: state.splitRatio,
        rightFolds: state.rightFolds,
      }),
      // Stored values are user-editable, so validate them on the way in.
      merge: (persisted, current) => mergeStoredLayout(persisted, current),
    },
  ),
);

/**
 * The pane the sidebar and the keyboard act on: the focused one, or the main one
 * when there is nothing beside it. Defined once so the sidebar and the pane it
 * belongs to cannot disagree about which pane that is.
 */
export function activePaneSlot(state: {
  focused: PaneSlot;
  side: ModuleId | null;
}): PaneSlot {
  return state.focused === "side" && state.side !== null ? "side" : "main";
}

export function useActivePaneSlot(): PaneSlot {
  return useLayoutStore(activePaneSlot);
}
