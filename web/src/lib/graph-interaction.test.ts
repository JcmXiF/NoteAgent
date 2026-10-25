import { describe, expect, it } from "vitest";
import {
  clampZoom,
  filterGraph,
  highlightOf,
  hitNode,
  MAX_ZOOM,
  MIN_ZOOM,
  panBy,
  toCanvas,
  toScreen,
  zoomAt,
} from "@/lib/graph-interaction";
import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";

function node(id: string, overrides: Partial<WikiGraphNode> = {}): WikiGraphNode {
  return {
    id,
    path: overrides.ghost ? null : `wiki/${id}.md`,
    title: id,
    type: "note",
    ghost: false,
    degree: 1,
    ...overrides,
  };
}

function edge(source: string, target: string): WikiGraphEdge {
  return { source, target, count: 1 };
}

describe("clampZoom", () => {
  it("keeps the zoom within reach", () => {
    expect(clampZoom(0.01)).toBe(MIN_ZOOM);
    expect(clampZoom(50)).toBe(MAX_ZOOM);
  });

  it("falls back to a view with no zoom at all for a nonsense value", () => {
    expect(clampZoom(Number.NaN)).toBe(1);
  });
});

describe("toCanvas and toScreen", () => {
  it("round-trips a point", () => {
    const view = { x: 40, y: -20, k: 1.6 };
    const canvas = toCanvas({ x: 410, y: 260 }, view);
    const back = toScreen(canvas, view);
    expect(back.x).toBeCloseTo(410);
    expect(back.y).toBeCloseTo(260);
  });

  it("is the plain point when there is no pan or zoom", () => {
    expect(toCanvas({ x: 12, y: 34 }, { x: 0, y: 0, k: 1 })).toEqual({ x: 12, y: 34 });
  });

  it("accounts for both the pan and the zoom", () => {
    // 100 on screen, panned 40 and scaled 2, is 30 on the canvas.
    expect(toCanvas({ x: 100, y: 0 }, { x: 40, y: 0, k: 2 }).x).toBe(30);
  });
});

describe("panBy", () => {
  it("moves the view without changing the zoom", () => {
    expect(panBy({ x: 1, y: 2, k: 1.5 }, 10, -5)).toEqual({ x: 11, y: -3, k: 1.5 });
  });
});

describe("zoomAt", () => {
  it("keeps the point under the pointer where it is", () => {
    const view = { x: 0, y: 0, k: 1 };
    const point = { x: 300, y: 200 };
    const before = toCanvas(point, view);
    const after = toCanvas(point, zoomAt(view, point, 1.5));
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });

  it("holds the point when zooming out from a panned view too", () => {
    const view = { x: -80, y: 60, k: 1.8 };
    const point = { x: 120, y: 400 };
    const before = toCanvas(point, view);
    const after = toCanvas(point, zoomAt(view, point, 0.7));
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });

  it("stops at the limits, and says so by returning the same view", () => {
    const view = { x: 0, y: 0, k: MAX_ZOOM };
    expect(zoomAt(view, { x: 0, y: 0 }, 1.5)).toBe(view);
  });

  it("leaves the view alone when the factor is nothing", () => {
    const view = { x: 5, y: 6, k: 1 };
    expect(zoomAt(view, { x: 0, y: 0 }, 1)).toBe(view);
  });
});

describe("hitNode", () => {
  const nodes = [
    { id: "a", x: 0, y: 0, radius: 10 },
    { id: "b", x: 100, y: 0, radius: 10 },
  ];

  it("finds the page under the point", () => {
    expect(hitNode({ x: 2, y: 0 }, nodes)).toBe("a");
    expect(hitNode({ x: 98, y: 3 }, nodes)).toBe("b");
  });

  it("allows a little slack for small circles", () => {
    expect(hitNode({ x: 0, y: 12 }, nodes)).toBe("a");
  });

  it("finds nothing in the open", () => {
    expect(hitNode({ x: 50, y: 50 }, nodes)).toBeNull();
  });

  it("picks the page drawn on top when two overlap", () => {
    const overlapping = [
      { id: "under", x: 0, y: 0, radius: 20 },
      { id: "over", x: 4, y: 0, radius: 20 },
    ];
    expect(hitNode({ x: 2, y: 0 }, overlapping)).toBe("over");
  });
});

describe("filterGraph", () => {
  const nodes = [
    node("a", { type: "note" }),
    node("b", { type: "source" }),
    node("wanted", { ghost: true }),
  ];
  const edges = [edge("a", "b"), edge("a", "wanted")];

  it("hands the graph back untouched when nothing is hidden", () => {
    expect(filterGraph(nodes, edges, [])).toEqual({ nodes, edges });
  });

  it("leaves out the pages of a hidden kind", () => {
    const kept = filterGraph(nodes, edges, ["source"]);
    expect(kept.nodes.map((item) => item.id)).toEqual(["a", "wanted"]);
  });

  it("leaves out a link whose page is no longer drawn", () => {
    // A line to a page that is not there has nowhere to point.
    const kept = filterGraph(nodes, edges, ["source"]);
    expect(kept.edges.map((item) => `${item.source}>${item.target}`)).toEqual(["a>wanted"]);
  });

  it("treats pages not written yet as a kind of their own", () => {
    const kept = filterGraph(nodes, edges, ["ghost"]);
    expect(kept.nodes.map((item) => item.id)).toEqual(["a", "b"]);
    expect(kept.edges).toEqual([edge("a", "b")]);
  });
});

describe("highlightOf", () => {
  it("holds a page and its neighbours, and nothing else", () => {
    const highlight = highlightOf("a", () => ["b", "c"]);
    expect([...highlight as Set<string>].sort()).toEqual(["a", "b", "c"]);
  });

  it("holds nothing when no page is being looked at", () => {
    expect(highlightOf(null, () => ["b"])).toBeNull();
    expect(highlightOf(undefined, () => [])).toBeNull();
  });
});
