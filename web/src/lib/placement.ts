import { isSingleModule, type ModuleId } from "@/modules";
import type { PaneSlot } from "@/stores/layout-store";

/**
 * Where a request to show something lands.
 *
 * There is one set of panes, and this is the one place that decides what goes in
 * them. A view answering a request is told which pane to fill instead of working
 * it out: the answer follows from how the reader asked (`Placement`) and from the
 * pane they were in when they asked.
 *
 * That second part has to be read *before* anything moves. Publishing a request
 * moves the panes immediately, so a view looking at them afterwards to find out
 * where the request came from would be told the destination instead — and would
 * move the very thing the reader was reading. The pane is therefore part of the
 * request.
 *
 * Nothing here needs to ask a view what it is holding: the panes are the whole
 * input. A page is brought forward because the reader asked from it, not because
 * of what it is.
 */

/** How a request asks to be placed. */
export type Placement =
  /**
   * This is what I am reading now: it takes the front pane and starts from there.
   * The pane beside it keeps what it was showing.
   */
  | "replace"
  /**
   * Open it beside what I am reading. The requested thing takes the extension
   * pane, and the page the reader asked from comes forward to the front pane; from
   * the front pane there is nothing to bring forward.
   */
  | "beside"
  /** The same thing seen another way: it takes my pane, and my pane can go back. */
  | "in-place";

/** A request to show something, and where the reader was when they asked. */
export type PlacementRequest = {
  module: ModuleId;
  placement: Placement;
  from: PaneSlot;
};

/** The panes, and what a pane swapped in place can offer to go back to. */
export type PaneLayout = {
  view: ModuleId;
  side: ModuleId | null;
  /** What the split control reopens. */
  lastSide: ModuleId;
  focused: PaneSlot;
  paneReturn: Partial<Record<PaneSlot, ModuleId>>;
};

/** The panes after the request, and where the requested thing belongs. */
export type PanePlan = PaneLayout & {
  /** The pane the requested thing is shown in. */
  target: PaneSlot;
  /**
   * The pane whose page comes forward to the front.
   *
   * Asking from the extension means the page being read was there, so it becomes
   * the page in front and the requested thing arrives where it was. Only a pane
   * the reader was in can come forward, so asking from the front moves nothing.
   */
  promoteFrom: PaneSlot | null;
};

/** The pane on the other side of the one given. */
export function otherPane(slot: PaneSlot): PaneSlot {
  return slot === "main" ? "side" : "main";
}

/** The pane already showing a module, when the window can only show one of it. */
function paneShowing(layout: PaneLayout, module: ModuleId): PaneSlot | null {
  if (layout.view === module) return "main";
  if (layout.side === module) return "side";
  return null;
}

/**
 * Where a module the window can only show one of goes.
 *
 * The shell keeps one browser per window — one page surface, one tab strip — so it
 * is on screen in one pane at most, and the pane it is in has to be the pane with
 * focus or the shell has nowhere to put the page. A request for it is therefore
 * answered by the pane that already has it: the panes stay as they are, that pane
 * takes the focus, and the page the browser is showing is what changes.
 *
 * What its pane can go back to is left alone: the pane has not changed what it is,
 * so what it could go back to is still true.
 *
 * Answers null when the browser is not on screen. The request is then an ordinary
 * arrival and the rules below decide where it goes.
 */
function singlePlan(layout: PaneLayout, module: ModuleId): PanePlan | null {
  const showing = paneShowing(layout, module);
  if (!showing) return null;
  return {
    ...panes(layout, { focused: showing }),
    target: showing,
    promoteFrom: null,
  };
}

/**
 * Forgets the way back from panes that have been given something else.
 *
 * A pane can only go back to what it was showing before it was swapped in place.
 * Which panes those are follows from the route taken, not from comparing what they
 * end up showing: an extension that moves its thing up is still the same module
 * while holding something quite different.
 */
function forgotten(
  returns: Partial<Record<PaneSlot, ModuleId>>,
  slots: PaneSlot[],
): Partial<Record<PaneSlot, ModuleId>> {
  const kept = { ...returns };
  for (const slot of slots) delete kept[slot];
  return kept;
}

/**
 * The panes as data.
 *
 * The state a caller holds the panes in carries its own actions alongside them, and
 * a plan is a value other things keep and compare: it holds the panes and nothing
 * else.
 */
function panes(layout: PaneLayout, over: Partial<PaneLayout>): PaneLayout {
  return {
    view: layout.view,
    side: layout.side,
    lastSide: layout.lastSide,
    focused: layout.focused,
    paneReturn: layout.paneReturn,
    ...over,
  };
}

export function placementPlan(
  layout: PaneLayout,
  request: PlacementRequest,
): PanePlan {
  const { module, placement, from } = request;

  // A module the window can only show one of — the browser — is on screen in one
  // pane at most, and a request for it is answered where it already is.
  if (isSingleModule(module)) {
    const one = singlePlan(layout, module);
    if (one) return one;
  }

  if (placement === "in-place") {
    const after: PaneLayout =
      from === "main"
        ? panes(layout, { view: module, focused: "main" })
        : panes(layout, { side: module, lastSide: module, focused: "side" });
    const paneReturn = forgotten(layout.paneReturn, [from]);
    const previous = from === "main" ? layout.view : layout.side;
    if (previous && previous !== module) paneReturn[from] = previous;
    return { ...after, paneReturn, target: from, promoteFrom: null };
  }

  if (placement === "replace") {
    // What is asked for outright becomes the thing being read, and that is read in
    // the front pane. The pane beside it keeps what it was showing: the reader is
    // choosing what to read, not asking for their other pane to be taken away.
    const after: PaneLayout = panes(layout, { view: module, focused: "main" });
    return {
      ...after,
      paneReturn: forgotten(layout.paneReturn, ["main"]),
      target: "main",
      promoteFrom: null,
    };
  }

  // Beside: the requested thing takes the extension pane, and the page the reader
  // asked from becomes the page in front. The front pane is the one with the
  // sidebar, so asking from the extension brings that page forward — with its
  // sidebar — and the request waits beside it. Asking from the front brings nothing
  // forward: there the page being read is already in front.
  const promote = from === "side";
  const after: PaneLayout = panes(layout, {
    view: promote ? (layout.side ?? layout.view) : layout.view,
    side: module,
    lastSide: module,
    focused: "main",
  });
  // Bringing a page forward gives the other pane something new as surely as the
  // pane the request landed in.
  const moved: PaneSlot[] = promote ? ["main", "side"] : ["side"];

  return {
    ...after,
    paneReturn: forgotten(layout.paneReturn, moved),
    target: "side",
    promoteFrom: promote ? "side" : null,
  };
}
