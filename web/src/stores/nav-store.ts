import { create } from "zustand";
import type { ModuleId } from "@/modules";
import {
  placementPlan,
  type PanePlan,
  type Placement,
  type PlacementRequest,
} from "@/lib/placement";
import { activePaneSlot, useLayoutStore, type PaneSlot } from "@/stores/layout-store";

/**
 * Cross-view navigation intents.
 *
 * A view cannot reach into another view's state directly, so actions that need
 * to open a specific thing elsewhere — a page, a source, a graph node, a URL —
 * publish an intent here. The target view subscribes to `seq` and acts once per
 * intent, which keeps the request one-shot instead of re-firing on every render.
 *
 * The panes move as the request is made, because the request has to bring the
 * module it wants on screen for its view to be there to answer it. Where they move
 * to is not decided here either: `placementPlan` is the one answer to that, and the
 * request only says how the reader asked to be placed.
 */
type NavIntent =
  | { kind: "docs"; path: string; locator?: string }
  | { kind: "sources"; sourceKey?: string; locator?: string }
  | { kind: "graph"; nodeId?: string }
  | { kind: "browse"; url: string }
  | { kind: "chat"; conversationId?: string }
  /**
   * The remarks about a page, and the one of them to put in front.
   *
   * A remark is written about a page, so the request always names the page; it
   * names a remark too when the reader got here by pointing at one, because
   * arriving without it would leave them to find it again in a list.
   */
  | { kind: "notes"; path: string; noteId?: string };

function moduleForIntent(intent: NavIntent): ModuleId {
  switch (intent.kind) {
    case "docs":
      return "docs";
    case "sources":
      return "sources";
    case "graph":
      return "graph";
    case "browse":
      return "browse";
    case "chat":
      return "chat";
    case "notes":
      return "notes";
  }
}

/** How a request asks to be placed among the panes, and where it came from. */
type NavRequest = {
  placement: Placement;
  /**
   * The pane the reader was in, when the caller knows it.
   *
   * A view inside a pane reads its own slot and says so; a request that crosses
   * views has no pane of its own to speak from, and the panes are consulted for it.
   */
  from?: PaneSlot;
};

type NavState = {
  seq: number;
  intent: NavIntent | null;
  /**
   * Where the request landed, and what it moved on the way.
   *
   * The panes are not the whole answer: the view that shows the thing asked for
   * needs to know which pane got it, and what the pane it came from was left
   * holding. Both are in here, so nobody has to work them out again.
   */
  plan: PanePlan | null;
  /** How the request asked to be placed, which is what its view has to keep to. */
  placement: Placement;
  /**
   * Works out where a request goes and moves the panes there.
   *
   * The half of `go` that a view already inside a pane needs: it decides and
   * applies, but publishes nothing, because no other view is being asked to answer.
   */
  place: (request: PlacementRequest) => PanePlan;
  /**
   * Shows what the request names, publishes it, and answers where it landed.
   *
   * The answer is returned as well as published, for a caller that has something of
   * its own to move along with the pane.
   */
  go: (intent: NavIntent, request: NavRequest) => PanePlan;
};

export const useNavStore = create<NavState>((set, get) => ({
  seq: 0,
  intent: null,
  plan: null,
  placement: "replace",

  place: (request) => {
    const layout = useLayoutStore.getState();
    const plan = placementPlan(layout, request);
    layout.applyPlacement(plan);
    return plan;
  },

  go: (intent, request) => {
    const layout = useLayoutStore.getState();
    // Which pane the reader is in is read *before* the panes move. A view looking
    // at them afterwards to find out where the request came from would be told
    // where it landed, and would move the very thing the reader was reading.
    const from: PaneSlot = request.from ?? activePaneSlot(layout);
    const plan = get().place({
      module: moduleForIntent(intent),
      placement: request.placement,
      from,
    });
    set((state) => ({
      seq: state.seq + 1,
      intent,
      plan,
      placement: request.placement,
    }));
    return plan;
  },
}));
