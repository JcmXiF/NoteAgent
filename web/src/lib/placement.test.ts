import { describe, expect, it } from "vitest";
import {
  otherPane,
  placementPlan,
  type PaneLayout,
  type Placement,
  type PlacementRequest,
} from "@/lib/placement";
import type { PaneSlot } from "@/stores/layout-store";

/**
 * Where requests to show something land.
 *
 * The two panes of the workspace are the thing under test: which pane a request
 * fills, what happens to the pane it came from, and what a pane can still go back
 * to afterwards. The panes are the whole input — nothing here asks a view what it
 * holds — so a case is only about how the reader asked and where they were.
 */

function layout(over: Partial<PaneLayout> = {}): PaneLayout {
  return {
    view: "docs",
    side: null,
    lastSide: "graph",
    focused: "main",
    paneReturn: {},
    ...over,
  };
}

function ask(
  placement: Placement,
  module: PlacementRequest["module"],
  from: PaneSlot,
): PlacementRequest {
  return { placement, module, from };
}

describe("the other pane", () => {
  it("is the one that is not the one given", () => {
    expect(otherPane("main")).toBe("side");
    expect(otherPane("side")).toBe("main");
  });
});

describe("something asked for in place", () => {
  it("takes the pane the reader is in, and records the way back", () => {
    const plan = placementPlan(layout({ view: "docs" }), ask("in-place", "browse", "main"));
    expect(plan.view).toBe("browse");
    expect(plan.target).toBe("main");
    expect(plan.paneReturn.main).toBe("docs");
  });

  it("moves the split's memory when it takes the side pane", () => {
    const plan = placementPlan(
      layout({ view: "docs", side: "docs", focused: "side" }),
      ask("in-place", "browse", "side"),
    );
    expect(plan.side).toBe("browse");
    expect(plan.lastSide).toBe("browse");
    expect(plan.focused).toBe("side");
    expect(plan.view).toBe("docs");
    expect(plan.paneReturn.side).toBe("docs");
  });

  it("records nothing when the pane is already showing that module", () => {
    const plan = placementPlan(layout({ view: "docs" }), ask("in-place", "docs", "main"));
    expect(plan.view).toBe("docs");
    expect(plan.paneReturn).toEqual({});
  });

  it("fills a pane that is showing nothing", () => {
    const plan = placementPlan(
      layout({ view: "docs", side: null }),
      ask("in-place", "browse", "side"),
    );
    expect(plan.side).toBe("browse");
    expect(plan.paneReturn).toEqual({});
  });
});

describe("something asked for outright", () => {
  it("takes the front pane and leaves the pane beside it alone", () => {
    // The reader is choosing what to read next, and the front pane is where that is
    // read. The other pane is where they put it, so it keeps what it was showing —
    // including when that is another page of the same kind.
    const plan = placementPlan(
      layout({ view: "docs", side: "docs", focused: "side" }),
      ask("replace", "docs", "main"),
    );
    expect(plan.view).toBe("docs");
    expect(plan.side).toBe("docs");
    expect(plan.focused).toBe("main");
    expect(plan.target).toBe("main");
    expect(plan.lastSide).toBe("graph");
  });

  it("keeps a pane that is showing another module", () => {
    const plan = placementPlan(
      layout({ view: "docs", side: "graph", focused: "main" }),
      ask("replace", "docs", "main"),
    );
    expect(plan.view).toBe("docs");
    expect(plan.side).toBe("graph");
    expect(plan.focused).toBe("main");
  });

  it("leaves a window without a split without one", () => {
    const plan = placementPlan(layout({ view: "graph" }), ask("replace", "docs", "main"));
    expect(plan.view).toBe("docs");
    expect(plan.side).toBeNull();
  });

  it("forgets the way back from the pane it changed, and only that one", () => {
    const plan = placementPlan(
      layout({ view: "docs", paneReturn: { main: "browse" } }),
      ask("replace", "docs", "main"),
    );
    expect(plan.paneReturn).toEqual({});

    const kept = placementPlan(
      layout({ view: "docs", side: "graph", paneReturn: { side: "docs" } }),
      ask("replace", "docs", "main"),
    );
    expect(kept.paneReturn).toEqual({ side: "docs" });
  });
});

describe("something asked for beside what is being read", () => {
  it("lands in the extension pane and leaves the front pane where it is", () => {
    const plan = placementPlan(
      layout({ view: "graph", side: null, focused: "main" }),
      ask("beside", "docs", "main"),
    );
    expect(plan.target).toBe("side");
    expect(plan.side).toBe("docs");
    expect(plan.view).toBe("graph");
    expect(plan.promoteFrom).toBeNull();
  });

  it("brings the page being read forward when the reader asked from the extension", () => {
    // The page in the extension is the page being read, and the front pane is the
    // one with the sidebar, so it comes forward there and the request waits where
    // it was.
    const plan = placementPlan(
      layout({ view: "docs", side: "sources", focused: "side" }),
      ask("beside", "graph", "side"),
    );
    expect(plan.promoteFrom).toBe("side");
    expect(plan.target).toBe("side");
    expect(plan.view).toBe("sources");
    expect(plan.side).toBe("graph");
  });

  it("brings forward whatever the extension is showing, not only a page", () => {
    // A map, a list or a page: it is what the reader was in, and nothing here asks
    // what it is.
    const plan = placementPlan(
      layout({ view: "docs", side: "graph", focused: "side" }),
      ask("beside", "chat", "side"),
    );
    expect(plan.view).toBe("graph");
    expect(plan.side).toBe("chat");
    expect(plan.promoteFrom).toBe("side");
  });

  it("moves the split's memory when it fills the side pane", () => {
    const plan = placementPlan(
      layout({ view: "graph", side: null }),
      ask("beside", "docs", "main"),
    );
    expect(plan.lastSide).toBe("docs");
  });

  it("forgets the way back from the pane it landed in", () => {
    const plan = placementPlan(
      layout({ view: "graph", side: null, paneReturn: { main: "browse" } }),
      ask("beside", "docs", "main"),
    );
    expect(plan.paneReturn).toEqual({ main: "browse" });
  });

  it("forgets both panes when it brings a page forward", () => {
    const plan = placementPlan(
      layout({
        view: "docs",
        side: "sources",
        focused: "side",
        paneReturn: { main: "browse", side: "browse" },
      }),
      ask("beside", "graph", "side"),
    );
    expect(plan.paneReturn).toEqual({});
  });

  it("leaves the focus where the sidebar is, which is the front pane", () => {
    const landing = placementPlan(layout({ view: "graph" }), ask("beside", "docs", "main"));
    const forwarded = placementPlan(
      layout({ view: "docs", side: "graph", focused: "side" }),
      ask("beside", "chat", "side"),
    );
    expect(landing.focused).toBe("main");
    expect(forwarded.focused).toBe("main");
  });
});

describe("a request that must not disturb the pane it came from", () => {
  const modules: PlacementRequest["module"][] = ["docs", "graph", "browse", "chat", "sources"];

  it("leaves the front pane alone when the reader asked from it", () => {
    for (const module of modules) {
      const before = layout({ view: "graph", side: "sources", focused: "main" });
      const plan = placementPlan(before, ask("beside", module, "main"));
      expect(plan.view).toBe(before.view);
      expect(plan.side).toBe(module);
    }
  });

  it("brings the extension's module forward when the reader asked from there", () => {
    for (const module of modules) {
      const before = layout({ view: "docs", side: "sources", focused: "side" });
      const plan = placementPlan(before, ask("beside", module, "side"));
      expect(plan.view).toBe(before.side);
      expect(plan.side).toBe(module);
    }
  });
});

describe("a module the window can only show one of", () => {
  // The shell keeps one browser per window, and only the pane with focus can give it
  // somewhere to be. Wherever the browser already is, that is where a request for it
  // is answered, and what changes is the page it shows.
  it("stays where it is and takes the focus, whatever was asked for", () => {
    for (const placement of ["replace", "beside", "in-place"] as Placement[]) {
      for (const from of ["main", "side"] as PaneSlot[]) {
        const before = layout({ view: "docs", side: "browse", focused: from });
        const plan = placementPlan(before, ask(placement, "browse", from));
        expect(plan.view).toBe("docs");
        expect(plan.side).toBe("browse");
        expect(plan.focused).toBe("side");
        expect(plan.target).toBe("side");
        expect(plan.promoteFrom).toBeNull();
      }
    }
  });

  it("leaves what its pane can go back to alone", () => {
    // The pane has not changed what it is, so what it could go back to still holds.
    const plan = placementPlan(
      layout({ view: "docs", side: "browse", paneReturn: { side: "docs" } }),
      ask("in-place", "browse", "main"),
    );
    expect(plan.paneReturn).toEqual({ side: "docs" });
  });

  it("is placed by the ordinary rules when it is not on screen", () => {
    const landing = placementPlan(
      layout({ view: "docs", side: "graph" }),
      ask("in-place", "browse", "main"),
    );
    expect(landing.view).toBe("browse");
    expect(landing.side).toBe("graph");
    expect(landing.target).toBe("main");

    const beside = placementPlan(layout({ view: "docs" }), ask("beside", "browse", "main"));
    expect(beside.side).toBe("browse");
    expect(beside.target).toBe("side");
  });
});
