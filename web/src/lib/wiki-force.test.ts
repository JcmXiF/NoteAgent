import { describe, expect, it } from "vitest";
import {
  averageSpeed,
  nodeRadiusForDegree,
  seedForceNodes,
  stepForceLayout,
  type ForceNode,
} from "@/lib/wiki-force";

const WIDTH = 800;
const HEIGHT = 600;

function node(id: string, x: number, y: number, radius = 12): ForceNode {
  return { id, radius, x, y, vx: 0, vy: 0 };
}

/** Runs the simulation as the view does, and answers how it ended up. */
function settle(nodes: ForceNode[], links: Array<{ source: string; target: string }>, steps = 400) {
  for (let i = 0; i < steps; i += 1) {
    stepForceLayout(nodes, links, WIDTH, HEIGHT, null);
  }
  return nodes;
}

function distance(a: ForceNode, b: ForceNode): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

describe("nodeRadiusForDegree", () => {
  it("grows with how connected a page is", () => {
    expect(nodeRadiusForDegree(0)).toBeLessThan(nodeRadiusForDegree(4));
    expect(nodeRadiusForDegree(4)).toBeLessThan(nodeRadiusForDegree(9));
  });

  it("stops growing, so one busy page cannot take over", () => {
    expect(nodeRadiusForDegree(400)).toBe(nodeRadiusForDegree(10_000));
  });

  it("treats a negative degree as none", () => {
    expect(nodeRadiusForDegree(-3)).toBe(nodeRadiusForDegree(0));
  });
});

describe("seedForceNodes", () => {
  it("gives every page a place of its own", () => {
    // Pages starting in the same spot have no direction to separate along.
    const nodes = seedForceNodes(
      Array.from({ length: 12 }, (_, i) => ({ id: `n${i}`, radius: 12 })),
      WIDTH,
      HEIGHT,
    );
    const places = new Set(nodes.map((item) => `${Math.round(item.x)},${Math.round(item.y)}`));
    expect(places.size).toBe(nodes.length);
  });

  it("starts in the middle", () => {
    const nodes = seedForceNodes([{ id: "only", radius: 12 }], WIDTH, HEIGHT);
    expect([nodes[0].x, nodes[0].y]).toEqual([WIDTH / 2, HEIGHT / 2]);
  });

  it("starts at rest", () => {
    const nodes = seedForceNodes([{ id: "a", radius: 12 }], WIDTH, HEIGHT);
    expect([nodes[0].vx, nodes[0].vy]).toEqual([0, 0]);
  });

  it("has nothing to place when there is nothing", () => {
    expect(seedForceNodes([], WIDTH, HEIGHT)).toEqual([]);
  });
});

describe("stepForceLayout", () => {
  it("pulls the ends of a link towards each other", () => {
    const a = node("a", 100, 300);
    const b = node("b", 700, 300);
    const before = distance(a, b);
    for (let i = 0; i < 30; i += 1) {
      stepForceLayout([a, b], [{ source: "a", target: "b" }], WIDTH, HEIGHT, null);
    }
    expect(distance(a, b)).toBeLessThan(before);
  });

  it("pushes pages that are not linked apart", () => {
    const a = node("a", 400, 300);
    const b = node("b", 405, 300);
    const before = distance(a, b);
    for (let i = 0; i < 10; i += 1) {
      stepForceLayout([a, b], [], WIDTH, HEIGHT, null);
    }
    expect(distance(a, b)).toBeGreaterThan(before);
  });

  it("holds still the page being dragged", () => {
    const pinned = node("a", 120, 130);
    const other = node("b", 400, 300);
    stepForceLayout([pinned, other], [{ source: "a", target: "b" }], WIDTH, HEIGHT, "a");
    expect([pinned.x, pinned.y]).toEqual([120, 130]);
    expect(pinned.vx).toBe(0);
  });

  it("keeps every page inside the view", () => {
    // A page pushed to the edge must stay reachable rather than drift outside.
    const far = node("far", 5000, -4000);
    settle([far, node("near", 400, 300)], [{ source: "far", target: "near" }]);
    expect(far.x).toBeLessThanOrEqual(WIDTH - far.radius);
    expect(far.x).toBeGreaterThanOrEqual(far.radius);
    expect(far.y).toBeLessThanOrEqual(HEIGHT - far.radius);
    expect(far.y).toBeGreaterThanOrEqual(far.radius);
  });

  it("leaves room for what is drawn around a page", () => {
    // A name under a page and a count beside it are drawn outside the page's own
    // circle, so a page held against an edge would have them cut off.
    const far = node("far", 5000, -4000);
    const margin = { x: 2, y: 26 };
    for (let i = 0; i < 200; i += 1) {
      stepForceLayout([far], [], WIDTH, HEIGHT, null, margin);
    }
    expect(far.y).toBeGreaterThanOrEqual(far.radius + 6 + margin.y);
    expect(far.y).toBeLessThanOrEqual(HEIGHT - far.radius - 6 - margin.y);
  });

  it("separates two pages that start on top of each other", () => {
    // They have no direction to separate along, which is why a step gives them one.
    const a = node("a", 400, 300);
    const b = node("b", 400, 300);
    stepForceLayout([a, b], [], WIDTH, HEIGHT, null);
    expect(distance(a, b)).toBeGreaterThan(0);
  });

  it("ignores a link whose page is not in the simulation", () => {
    const a = node("a", 400, 300);
    const before = { x: a.x, y: a.y };
    stepForceLayout([a], [{ source: "a", target: "nowhere" }], WIDTH, HEIGHT, null);
    // It moves towards the middle, but the missing page contributes nothing.
    expect(Number.isFinite(a.x) && Number.isFinite(a.y)).toBe(true);
    expect(Math.abs(a.x - before.x)).toBeLessThan(20);
  });

  it("does the same thing twice for the same input", () => {
    // No randomness: the same graph settles the same way, so a reload does not
    // shuffle the picture.
    const first = settle(seedForceNodes(
      [{ id: "a", radius: 12 }, { id: "b", radius: 12 }],
      WIDTH,
      HEIGHT,
    ), [{ source: "a", target: "b" }]);
    const second = settle(seedForceNodes(
      [{ id: "a", radius: 12 }, { id: "b", radius: 12 }],
      WIDTH,
      HEIGHT,
    ), [{ source: "a", target: "b" }]);
    expect(first.map((item) => [item.x, item.y])).toEqual(second.map((item) => [item.x, item.y]));
  });
});

describe("averageSpeed", () => {
  it("is nothing for no pages", () => {
    expect(averageSpeed([])).toBe(0);
  });

  it("is nothing for pages that are still", () => {
    expect(averageSpeed([node("a", 1, 2), node("b", 3, 4)])).toBe(0);
  });

  it("falls as the graph settles", () => {
    // This is what the drawing loop watches to know when to stop.
    const nodes = seedForceNodes(
      [
        { id: "a", radius: 12 },
        { id: "b", radius: 12 },
        { id: "c", radius: 12 },
      ],
      WIDTH,
      HEIGHT,
    );
    const links = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
    ];
    stepForceLayout(nodes, links, WIDTH, HEIGHT, null);
    const early = averageSpeed(nodes);
    for (let i = 0; i < 300; i += 1) stepForceLayout(nodes, links, WIDTH, HEIGHT, null);
    expect(averageSpeed(nodes)).toBeLessThan(early);
  });

  it("settles below the threshold the loop stops at", () => {
    const nodes = seedForceNodes(
      Array.from({ length: 18 }, (_, i) => ({ id: `n${i}`, radius: 12 })),
      WIDTH,
      HEIGHT,
    );
    const links = Array.from({ length: 24 }, (_, i) => ({
      source: `n${i % 18}`,
      target: `n${(i * 7 + 3) % 18}`,
    })).filter((link) => link.source !== link.target);
    for (let i = 0; i < 600; i += 1) stepForceLayout(nodes, links, WIDTH, HEIGHT, null);
    // The loop gives up at 0.35; a graph that cannot reach it would spin forever.
    expect(averageSpeed(nodes)).toBeLessThan(0.35);
  });

  it("settles a linked pair near the distance the spring wants", () => {
    // The spring's rest length is 110; a settled pair should be around there, not
    // touching and not flung apart.
    const nodes = seedForceNodes(
      [
        { id: "a", radius: 12 },
        { id: "b", radius: 12 },
      ],
      WIDTH,
      HEIGHT,
    );
    settle(nodes, [{ source: "a", target: "b" }], 600);
    const apart = distance(nodes[0], nodes[1]);
    expect(apart).toBeGreaterThan(80);
    expect(apart).toBeLessThan(160);
  });

  it("keeps unlinked pages further apart than linked ones", () => {
    // Two linked pairs, starting far from each other: the pairs should hold
    // together and the pairs should stay apart.
    const nodes = seedForceNodes(
      [
        { id: "a1", radius: 12 },
        { id: "a2", radius: 12 },
        { id: "b1", radius: 12 },
        { id: "b2", radius: 12 },
      ],
      WIDTH,
      HEIGHT,
    );
    settle(
      nodes,
      [
        { source: "a1", target: "a2" },
        { source: "b1", target: "b2" },
      ],
      600,
    );
    const byId = new Map(nodes.map((item) => [item.id, item]));
    const withinPair = distance(byId.get("a1") as ForceNode, byId.get("a2") as ForceNode);
    const acrossPairs = distance(byId.get("a1") as ForceNode, byId.get("b1") as ForceNode);
    expect(withinPair).toBeLessThan(acrossPairs);
  });
});
