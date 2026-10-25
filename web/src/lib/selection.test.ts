import { describe, expect, it } from "vitest";
import {
  boxSize,
  consecutiveRuns,
  hitTest,
  intersects,
  isDragSelection,
  normalizeBox,
  scrollDelta,
  toDocPoint,
  toViewPoint,
  unionBoxes,
  type Box,
  type BlockRect,
} from "@/lib/selection";

function box(left: number, top: number, right: number, bottom: number): Box {
  return { left, top, right, bottom };
}

function block(id: string, b: Box): BlockRect {
  return { id, box: b };
}

/** Three stacked paragraphs, 100 wide and 20 tall, 30 apart. */
const COLUMN: BlockRect[] = [
  block("b1", box(0, 0, 100, 20)),
  block("b2", box(0, 30, 100, 50)),
  block("b3", box(0, 60, 100, 80)),
];

describe("normalizeBox", () => {
  it("orders the corners however the drag went", () => {
    const expected = box(10, 20, 50, 60);
    expect(normalizeBox({ x: 10, y: 20 }, { x: 50, y: 60 })).toEqual(expected);
    expect(normalizeBox({ x: 50, y: 60 }, { x: 10, y: 20 })).toEqual(expected);
    expect(normalizeBox({ x: 50, y: 20 }, { x: 10, y: 60 })).toEqual(expected);
  });
});

describe("boxSize", () => {
  it("reports width and height", () => {
    expect(boxSize(box(10, 20, 50, 70))).toEqual({ width: 40, height: 50 });
  });

  it("reports zero for a degenerate box", () => {
    expect(boxSize(box(10, 10, 10, 10))).toEqual({ width: 0, height: 0 });
  });
});

describe("coordinate conversion", () => {
  it("adds the scroll offset going into the document", () => {
    expect(toDocPoint({ x: 10, y: 20 }, { x: 100, y: 200 })).toEqual({ x: 110, y: 220 });
  });

  it("takes it away coming back to the viewport", () => {
    expect(toViewPoint({ x: 110, y: 220 }, { x: 100, y: 200 })).toEqual({ x: 10, y: 20 });
  });

  it("round-trips", () => {
    const scroll = { x: 37, y: 91 };
    const point = { x: 5, y: 6 };
    expect(toViewPoint(toDocPoint(point, scroll), scroll)).toEqual(point);
  });

  it("is a no-op without scroll", () => {
    expect(toDocPoint({ x: 3, y: 4 }, { x: 0, y: 0 })).toEqual({ x: 3, y: 4 });
  });
});

describe("isDragSelection", () => {
  it("accepts a box that is wide enough", () => {
    expect(isDragSelection(box(0, 0, 20, 1))).toBe(true);
  });

  it("accepts a box that is tall enough", () => {
    expect(isDragSelection(box(0, 0, 1, 20))).toBe(true);
  });

  it("rejects a box below the minimum in both directions", () => {
    expect(isDragSelection(box(0, 0, 4, 4))).toBe(false);
    expect(isDragSelection(box(10, 10, 10, 10))).toBe(false);
  });

  it("counts a box exactly at the minimum", () => {
    expect(isDragSelection(box(0, 0, 8, 0))).toBe(true);
    expect(isDragSelection(box(0, 0, 7, 7))).toBe(false);
  });
});

describe("intersects", () => {
  it("detects overlap", () => {
    expect(intersects(box(0, 0, 10, 10), box(5, 5, 15, 15))).toBe(true);
  });

  it("does not count touching edges as overlap", () => {
    // A drag that stops exactly at the next block's edge must not take it.
    expect(intersects(box(0, 0, 10, 10), box(10, 0, 20, 10))).toBe(false);
    expect(intersects(box(0, 0, 10, 10), box(0, 10, 10, 20))).toBe(false);
  });

  it("detects containment either way", () => {
    expect(intersects(box(0, 0, 100, 100), box(10, 10, 20, 20))).toBe(true);
    expect(intersects(box(10, 10, 20, 20), box(0, 0, 100, 100))).toBe(true);
  });

  it("reports no overlap for separated boxes", () => {
    expect(intersects(box(0, 0, 10, 10), box(20, 20, 30, 30))).toBe(false);
  });
});

describe("hitTest", () => {
  it("returns the covered blocks in document order", () => {
    expect(hitTest(box(0, 0, 100, 45), COLUMN)).toEqual(["b1", "b2"]);
  });

  it("follows the order it was given, not the geometry", () => {
    const shuffled = [COLUMN[2], COLUMN[0], COLUMN[1]];
    expect(hitTest(box(0, 0, 100, 100), shuffled)).toEqual(["b3", "b1", "b2"]);
  });

  it("returns nothing for a box that touches no block", () => {
    expect(hitTest(box(200, 200, 300, 300), COLUMN)).toEqual([]);
  });

  it("counts a zero-size box inside a block as covered", () => {
    // Whether a click counts as a selection is the interaction's call, via a
    // minimum drag distance; geometrically a point inside a block does overlap it.
    expect(hitTest(box(10, 10, 10, 10), COLUMN)).toEqual(["b1"]);
  });

  it("returns nothing for a zero-size box outside every block", () => {
    expect(hitTest(box(10, 25, 10, 25), COLUMN)).toEqual([]);
  });

  it("takes a block the box only clips the corner of", () => {
    expect(hitTest(box(95, 15, 200, 40), COLUMN)).toEqual(["b1", "b2"]);
  });

  it("handles an empty document", () => {
    expect(hitTest(box(0, 0, 100, 100), [])).toEqual([]);
  });
});

describe("scrollDelta", () => {
  it("scrolls up near the top edge and down near the bottom", () => {
    expect(scrollDelta(10, 1000)).toBeLessThan(0);
    expect(scrollDelta(990, 1000)).toBeGreaterThan(0);
  });

  it("does not scroll in the middle", () => {
    expect(scrollDelta(500, 1000)).toBe(0);
  });

  it("scrolls faster the closer the pointer gets to the edge", () => {
    const near = Math.abs(scrollDelta(2, 1000));
    const farther = Math.abs(scrollDelta(30, 1000));
    expect(near).toBeGreaterThan(farther);
  });

  it("stops growing past twice the base step", () => {
    // Position 0 is one edge-width away; beyond that the request is clamped.
    expect(Math.abs(scrollDelta(0, 1000))).toBeLessThanOrEqual(28 * 2);
  });

  it("does nothing without a viewport size or an edge zone", () => {
    expect(scrollDelta(0, 0)).toBe(0);
    expect(scrollDelta(0, 1000, 0)).toBe(0);
  });

  it("scopes the bottom zone to the given size", () => {
    // 390 is inside the zone for a 400px viewport but not for a 1000px one.
    expect(scrollDelta(390, 400)).toBeGreaterThan(0);
    expect(scrollDelta(390, 1000)).toBe(0);
  });
});

describe("consecutiveRuns", () => {
  const order = ["b1", "b2", "b3", "b4", "b5"];

  it("keeps an adjacent selection as one run", () => {
    expect(consecutiveRuns(["b2", "b3"], order)).toEqual([["b2", "b3"]]);
  });

  it("splits a selection with a gap in it", () => {
    expect(consecutiveRuns(["b1", "b3", "b4"], order)).toEqual([["b1"], ["b3", "b4"]]);
  });

  it("orders the runs and the ids within them by the document", () => {
    expect(consecutiveRuns(["b3", "b1"], order)).toEqual([["b1"], ["b3"]]);
  });

  it("ignores duplicates", () => {
    expect(consecutiveRuns(["b2", "b2", "b3"], order)).toEqual([["b2", "b3"]]);
  });

  it("returns nothing for an empty selection", () => {
    expect(consecutiveRuns([], order)).toEqual([]);
  });

  it("keeps ids the document did not know about, in one trailing run", () => {
    // Dropping them would hide part of a selection; the caller can still draw it.
    expect(consecutiveRuns(["b2", "gone"], order)).toEqual([["b2"], ["gone"]]);
  });
});

describe("unionBoxes", () => {
  it("bounds every box", () => {
    expect(unionBoxes([box(10, 10, 20, 20), box(0, 30, 5, 40)])).toEqual(
      box(0, 10, 20, 40),
    );
  });

  it("pads the result when asked", () => {
    expect(unionBoxes([box(10, 10, 20, 20)], 4)).toEqual(box(6, 6, 24, 24));
  });

  it("returns nothing for no boxes", () => {
    expect(unionBoxes([])).toBeNull();
  });
});
