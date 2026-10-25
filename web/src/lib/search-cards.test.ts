import { describe, expect, it } from "vitest";
import { MAX_FETCH_CARDS } from "@/lib/api/acquire";
import { atLimit, keepKnown, selectedCards, toggleSelected } from "@/lib/search-cards";
import type { ResourceCard } from "@/lib/api/types";

function card(url: string): ResourceCard {
  return {
    title: url,
    url,
    site: "example.com",
    siteName: "example.com",
    favicon: "",
    snippet: "",
    via: "search",
    why: "",
    intake: "search",
  };
}

describe("ticking a result", () => {
  it("ticks it and unticks it", () => {
    const on = toggleSelected([], "a");
    expect(on.selected).toEqual(["a"]);
    expect(on.refused).toBe(false);
    const off = toggleSelected(on.selected, "a");
    expect(off.selected).toEqual([]);
  });

  it("keeps the order they were ticked in", () => {
    let selected: string[] = [];
    for (const url of ["c", "a", "b"]) selected = toggleSelected(selected, url).selected;
    expect(selected).toEqual(["c", "a", "b"]);
  });

  it("refuses one more than the backend would read", () => {
    const full = Array.from({ length: MAX_FETCH_CARDS }, (_, i) => `u${i}`);
    const extra = toggleSelected(full, "one-too-many");
    expect(extra.refused).toBe(true);
    expect(extra.selected).toEqual(full);
    expect(atLimit(full)).toBe(true);
    expect(atLimit(full.slice(0, -1))).toBe(false);
  });

  it("still lets a ticked one be unticked at the limit", () => {
    // Otherwise the limit is a place the reader cannot get out of.
    const full = Array.from({ length: MAX_FETCH_CARDS }, (_, i) => `u${i}`);
    const off = toggleSelected(full, "u3");
    expect(off.refused).toBe(false);
    expect(off.selected).toHaveLength(MAX_FETCH_CARDS - 1);
  });
});

describe("what is ticked", () => {
  it("gives the cards, in the order they were found", () => {
    const cards = [card("a"), card("b"), card("c")];
    expect(selectedCards(cards, ["c", "a"]).map((item) => item.url)).toEqual(["a", "c"]);
  });

  it("forgets ticks whose card is gone", () => {
    // A new search replaces the results; a tick for a card nobody can see would ask
    // for a page the reader did not choose.
    const kept = keepKnown(["a", "gone"], [card("a"), card("b")]);
    expect(kept).toEqual(["a"]);
  });

  it("leaves the list alone when nothing was dropped", () => {
    const selected = ["a", "b"];
    expect(keepKnown(selected, [card("a"), card("b")])).toBe(selected);
  });
});
