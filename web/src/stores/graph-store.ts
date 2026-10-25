import i18n from "@/i18n";
import { create } from "zustand";
import { fetchGraph } from "@/lib/api/graph";
import { errorMessage } from "@/lib/api/client";
import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The wiki's link graph, and how it is being looked at.
 *
 * The data is keyed to the project and read once: a graph changes only when pages
 * are written, and asking for it again on every visit would send the whole graph
 * over the wire for nothing. What the reader is looking at — which page is
 * selected, where they have panned and how far they have zoomed — is kept here
 * too, so leaving the graph and coming back does not put them at the start again.
 */

/** Where the view is panned and zoomed to. */
export type GraphView = {
  x: number;
  y: number;
  k: number;
};

const DEFAULT_VIEW: GraphView = { x: 0, y: 0, k: 1 };

type GraphState = {
  project: string | null;
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;

  /** The page being looked at, by node id (a slug, not a path — ghosts have no path). */
  selectedId: string | null;
  /** The page the pointer is over, so its neighbourhood can be shown without selecting it. */
  hoverId: string | null;
  view: GraphView;
  /** Kinds the reader has hidden; empty means everything is shown. */
  hiddenKinds: string[];

  hydrate: (project: string) => Promise<void>;
  /** Reads the graph again, for when pages have been written. */
  refresh: () => Promise<void>;
  select: (id: string | null) => void;
  hover: (id: string | null) => void;
  setView: (view: GraphView) => void;
  resetView: () => void;
  toggleKind: (kind: string) => void;
  showAllKinds: () => void;
  reset: () => void;
};

export const useGraphStore = create<GraphState>((set, get) => ({
  project: null,
  nodes: [],
  edges: [],
  status: "idle",
  error: null,

  selectedId: null,
  hoverId: null,
  view: DEFAULT_VIEW,
  hiddenKinds: [],

  hydrate: async (project) => {
    if (!project) return;
    // Already read for this project: nothing to do, which is what keeps a visit to
    // the graph from re-reading it every time.
    if (get().project === project && get().status === "ready") return;
    set({
      project,
      nodes: [],
      edges: [],
      status: "loading",
      error: null,
      // A different project has a different graph, so what was being looked at is
      // no longer part of it.
      selectedId: null,
      hoverId: null,
      hiddenKinds: [],
      view: DEFAULT_VIEW,
    });
    await get().refresh();
  },

  refresh: async () => {
    const project = get().project;
    if (!project) return;
    set({ status: "loading" });
    try {
      const graph = await fetchGraph(project);
      if (get().project !== project) return;
      set({
        nodes: graph.nodes ?? [],
        edges: graph.edges ?? [],
        status: "ready",
        error: null,
        // A page that was selected may have gone, or may be new; either way the
        // selection is only kept if it is still there.
        selectedId: (graph.nodes ?? []).some((node) => node.id === get().selectedId)
          ? get().selectedId
          : null,
      });
    } catch (err) {
      if (get().project !== project) return;
      set({ status: "error", error: errorMessage(err, i18n.t("graph.view.failed")) });
    }
  },

  select: (id) =>
    // Nothing to tell anyone when the selection has not changed.
    set((state) => (state.selectedId === id ? state : { selectedId: id })),
  hover: (id) => set((state) => (state.hoverId === id ? state : { hoverId: id })),
  setView: (view) => set({ view }),
  resetView: () => set({ view: DEFAULT_VIEW }),

  toggleKind: (kind) =>
    set((state) => ({
      hiddenKinds: state.hiddenKinds.includes(kind)
        ? state.hiddenKinds.filter((item) => item !== kind)
        : [...state.hiddenKinds, kind],
    })),

  showAllKinds: () => set({ hiddenKinds: [] }),

  reset: () =>
    set({
      project: null,
      nodes: [],
      edges: [],
      status: "idle",
      error: null,
      selectedId: null,
      hoverId: null,
      view: DEFAULT_VIEW,
      hiddenKinds: [],
    }),
}));

/**
 * Follows the active project, the way the other stores do, so the graph is there
 * when the view is first opened rather than after a blank moment.
 */
let started = false;

export function startGraphSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const graph = useGraphStore.getState();
    if (state.current) void graph.hydrate(state.current);
    else graph.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) void useGraphStore.getState().hydrate(current);
}
