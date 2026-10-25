import { describe, expect, it } from "vitest";
import {
  buildAdjacency,
  degreesFrom,
  edgeKey,
  ghostNodes,
  isGhost,
  kindsPresent,
  neighborsOf,
  pagesWithin,
  realNodes,
} from "@/lib/wiki-graph";
import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";

function node(id: string, overrides: Partial<WikiGraphNode> = {}): WikiGraphNode {
  return {
    id,
    path: overrides.ghost ? null : `wiki/${id}.md`,
    title: id,
    type: "note",
    ghost: false,
    degree: 0,
    ...overrides,
  };
}

function edge(source: string, target: string, count = 1): WikiGraphEdge {
  return { source, target, count };
}

describe("edgeKey", () => {
  it("distinguishes the two directions of a link", () => {
    // A link is drawn once per direction as far as this key is concerned; the
    // adjacency below is what treats them as one relationship.
    expect(edgeKey(edge("a", "b"))).not.toBe(edgeKey(edge("b", "a")));
  });

  it("does not collide names that contain the separator", () => {
    expect(edgeKey({ source: "a>b", target: "c" })).not.toBe(
      edgeKey({ source: "a", target: "b>c" }),
    );
  });
});

describe("buildAdjacency", () => {
  it("reads a link in both directions", () => {
    const adjacency = buildAdjacency([node("a"), node("b")], [edge("a", "b")]);
    expect(neighborsOf(adjacency, "a")).toEqual(["b"]);
    expect(neighborsOf(adjacency, "b")).toEqual(["a"]);
  });

  it("counts a page once however many links join it", () => {
    const adjacency = buildAdjacency(
      [node("a"), node("b")],
      [edge("a", "b", 3), edge("b", "a", 2)],
    );
    expect(degreesFrom(adjacency).get("a")).toBe(1);
  });

  it("leaves out a page naming itself", () => {
    // Self-links are not a relationship, and would inflate the page's degree.
    const adjacency = buildAdjacency([node("a")], [edge("a", "a")]);
    expect(degreesFrom(adjacency).get("a")).toBe(0);
  });

  it("keeps a page with no links at all", () => {
    const adjacency = buildAdjacency([node("lonely")], []);
    expect(adjacency.has("lonely")).toBe(true);
    expect(degreesFrom(adjacency).get("lonely")).toBe(0);
  });

  it("ignores a link whose page is not in the graph", () => {
    // The backend only sends links between pages it lists; a stray one must not
    // invent a page, or the view would have a node with nothing to draw.
    const adjacency = buildAdjacency([node("a")], [edge("a", "ghost")]);
    expect(neighborsOf(adjacency, "a")).toEqual([]);
  });
});

describe("neighborsOf", () => {
  it("answers with nothing when there is no selection", () => {
    expect(neighborsOf(buildAdjacency([node("a")], []), null)).toEqual([]);
    expect(neighborsOf(buildAdjacency([node("a")], []), undefined)).toEqual([]);
  });
});

describe("pagesWithin", () => {
  const adjacency = buildAdjacency(
    [node("a"), node("b"), node("c"), node("d")],
    [edge("a", "b"), edge("b", "c"), edge("c", "d")],
  );

  it("finds the pages one link away", () => {
    expect(pagesWithin(adjacency, "a", 1)).toEqual(["b"]);
  });

  it("walks further when asked", () => {
    expect(pagesWithin(adjacency, "a", 2)).toEqual(["b", "c"]);
  });

  it("never returns the page it started from", () => {
    expect(pagesWithin(adjacency, "a", 9)).not.toContain("a");
  });

  it("answers with nothing for no selection or no depth", () => {
    expect(pagesWithin(adjacency, null)).toEqual([]);
    expect(pagesWithin(adjacency, "a", 0)).toEqual([]);
  });
});

describe("kindsPresent", () => {
  it("lists the kinds most common first", () => {
    const nodes = [
      node("a", { type: "note" }),
      node("b", { type: "source" }),
      node("c", { type: "source" }),
    ];
    expect(kindsPresent(nodes)).toEqual(["source", "note"]);
  });

  it("leaves ghosts out of the kinds", () => {
    // A ghost is a page that does not exist, so it is not a kind to filter by.
    const nodes = [node("a"), node("wanted", { ghost: true, type: "ghost" })];
    expect(kindsPresent(nodes)).toEqual(["note"]);
  });

  it("says nothing about an empty graph", () => {
    expect(kindsPresent([])).toEqual([]);
  });
});

describe("realNodes and ghostNodes", () => {
  it("separates the pages written from those only linked to", () => {
    const nodes = [node("a"), node("wanted", { ghost: true }), node("b")];
    expect(realNodes(nodes).map((item) => item.id)).toEqual(["a", "b"]);
    expect(ghostNodes(nodes).map((item) => item.id)).toEqual(["wanted"]);
  });

  it("treats a page with no path as a ghost even if the flag says otherwise", () => {
    // The flag and the path say the same thing; either alone is enough to tell.
    expect(isGhost(node("odd", { path: null }))).toBe(true);
  });
});
