import { describe, expect, it } from "vitest";
import { windowHeight, windowRange } from "@/lib/window-range";

const base = { rowHeight: 32, viewport: 256, overscan: 2 };

describe("windowRange", () => {
  it("draws the rows around the viewport", () => {
    const range = windowRange({ ...base, total: 1000, scrollTop: 3200 });
    // 3200 / 32 = row 100 is at the top; 8 rows fit, so 98–110 with the overscan.
    expect(range).toEqual({ start: 98, end: 110 });
  });

  it("says a list shorter than the viewport is all on screen", () => {
    expect(windowRange({ ...base, total: 3, scrollTop: 0 })).toEqual({ start: 0, end: 3 });
  });

  it("does not run past either end", () => {
    expect(windowRange({ ...base, total: 1000, scrollTop: 0 }).start).toBe(0);
    const atEnd = windowRange({ ...base, total: 1000, scrollTop: 1000 * 32 });
    expect(atEnd.end).toBe(1000);
    expect(atEnd.start).toBeGreaterThanOrEqual(0);
  });

  it("keeps the whole list for an empty one", () => {
    expect(windowRange({ ...base, total: 0, scrollTop: 0 })).toEqual({ start: 0, end: 0 });
  });

  it("draws everything until the scroller has been measured", () => {
    // Before the first measurement it is better to draw too much than nothing.
    expect(windowRange({ ...base, total: 500, scrollTop: 0, viewport: 0 })).toEqual({
      start: 0,
      end: 500,
    });
    expect(windowRange({ ...base, total: 500, scrollTop: 0, viewport: Number.NaN })).toEqual({
      start: 0,
      end: 500,
    });
  });

  it("draws everything when a row has no height to measure against", () => {
    expect(windowRange({ ...base, total: 500, scrollTop: 0, rowHeight: 0 })).toEqual({
      start: 0,
      end: 500,
    });
  });

  it("covers a viewport that is taller than the list", () => {
    // Scrolled past the first rows with room to spare: what is on screen is from
    // row 6 on, so that is where drawing starts, and it reaches the end.
    const range = windowRange({ ...base, total: 10, scrollTop: 200, viewport: 2560 });
    expect(range).toEqual({ start: 4, end: 10 });
  });

  it("draws a row for every scroll position, without a gap", () => {
    // Whatever the offset, the rows on screen are inside the drawn range: this is
    // what keeps a fast scroll from showing blank space.
    const total = 774;
    for (let offset = 0; offset <= total * base.rowHeight; offset += 7) {
      const { start, end } = windowRange({ ...base, total, scrollTop: offset });
      const firstVisible = Math.floor(offset / base.rowHeight);
      const lastVisible = Math.floor((offset + base.viewport - 1) / base.rowHeight);
      expect(start).toBeLessThanOrEqual(firstVisible);
      expect(end).toBeGreaterThan(Math.min(lastVisible, total - 1));
    }
  });
});

describe("windowHeight", () => {
  it("is the height that makes the scrollbar the right size", () => {
    expect(windowHeight(774, 32)).toBe(24768);
  });

  it("has nothing to give for an empty list", () => {
    expect(windowHeight(0, 32)).toBe(0);
    expect(windowHeight(-3, 32)).toBe(0);
  });
});
