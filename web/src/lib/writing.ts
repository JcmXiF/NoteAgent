import type { WritingStatePayload } from "@/lib/api/writing";

export type WritingAttention = {
  kind?: unknown;
  target?: unknown;
  message?: unknown;
};

/**
 * What needs a person now: the rows the state stores, plus the failures the
 * searches and selected pages imply. The backend summary derives the same list;
 * this one feeds the panel without making it read the summary.
 */
export function writingAttention(
  state: WritingStatePayload,
): WritingAttention[] {
  return [
    ...state.attention,
    ...state.searches
      .filter((search) => search.status === "failed")
      .map((search) => ({
        kind: "fetch_failed",
        target: search.id ?? "",
        message: search.error ?? "",
      })),
    ...state.selectedPages
      .filter((page) => page.status === "failed")
      .map((page) => ({
        kind: page.errorKind ?? "fetch_failed",
        target: page.id ?? "",
        message: page.error ?? "",
      })),
  ];
}
