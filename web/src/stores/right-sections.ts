import { useEffect } from "react";
import { create } from "zustand";
import type { RightSectionId } from "@/stores/layout-store";

/**
 * How much each part of the right panel is holding.
 *
 * A folded section shows only its heading, and what is folded away can be the
 * thing worth knowing — a source that changed, pages waiting to be written. So the
 * number belongs on the heading, where it is readable whether the section is open
 * or not, and the body is what knows it.
 *
 * Nothing is kept beyond the count: the heading says how many, the body says which.
 */
type CountsState = {
  counts: Partial<Record<RightSectionId, number>>;
  setCount: (section: RightSectionId, count: number | null) => void;
};

export const useSectionCounts = create<CountsState>((set) => ({
  counts: {},

  setCount: (section, count) =>
    set((state) => {
      const current = state.counts[section];
      const next = count && count > 0 ? count : null;
      if (current === next || (current === undefined && next === null)) return state;
      const counts = { ...state.counts };
      if (next === null) delete counts[section];
      else counts[section] = next;
      return { counts };
    }),
}));

/**
 * Says what a section is holding, for as long as it is on screen.
 *
 * Called by the section's own body, which is the thing that knows; the count
 * clears when that body goes away, so a heading never reports what is no longer
 * there.
 */
export function useSectionCount(section: RightSectionId, count: number | null): void {
  const setCount = useSectionCounts((state) => state.setCount);
  useEffect(() => {
    setCount(section, count);
    return () => setCount(section, null);
  }, [section, count, setCount]);
}
