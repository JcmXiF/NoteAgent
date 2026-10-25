import { beforeEach, describe, expect, it } from "vitest";
import { useSectionCounts } from "@/stores/right-sections";

beforeEach(() => {
  useSectionCounts.setState({ counts: {} });
});

describe("what a section reports", () => {
  it("keeps a count for the heading to show", () => {
    useSectionCounts.getState().setCount("plan", 3);
    expect(useSectionCounts.getState().counts.plan).toBe(3);
  });

  it("says nothing for an empty section", () => {
    // Zero and absent mean the same thing to a reader: no badge.
    useSectionCounts.getState().setCount("attention", 0);
    expect(useSectionCounts.getState().counts.attention).toBeUndefined();
    useSectionCounts.getState().setCount("attention", 2);
    useSectionCounts.getState().setCount("attention", null);
    expect(useSectionCounts.getState().counts.attention).toBeUndefined();
  });

  it("leaves the other sections alone", () => {
    useSectionCounts.getState().setCount("plan", 2);
    useSectionCounts.getState().setCount("compile", 5);
    useSectionCounts.getState().setCount("plan", null);
    expect(useSectionCounts.getState().counts).toEqual({ compile: 5 });
  });

  it("does not report the same number twice", () => {
    // The heading is rendering while this is called on every render of a body.
    useSectionCounts.getState().setCount("plan", 2);
    const before = useSectionCounts.getState().counts;
    useSectionCounts.getState().setCount("plan", 2);
    expect(useSectionCounts.getState().counts).toBe(before);
  });
});
