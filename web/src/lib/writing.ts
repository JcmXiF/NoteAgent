import i18n from "@/i18n";
import type { WritingStatePayload } from "@/lib/api/writing";

export type WritingAttention = {
  kind?: unknown;
  target?: unknown;
  reason?: unknown;
  message?: unknown;
};

/** A missing source is a hard failure; a drifted one only needs a rewrite. */
export function isMissingReason(reason?: string): boolean {
  return Boolean(reason?.includes("missing"));
}

export function staleMessage(
  reason: string | undefined,
  heading: string | undefined,
): string {
  return i18n.t(`writing.stale.${reason || "frame_changed"}`, {
    heading: heading ?? "",
  });
}

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
    ...(state.draft?.pages ?? []).flatMap((page) =>
      (page.sections ?? [])
        .filter((section) => section.staleReason)
        .map((section) => ({
          kind: isMissingReason(section.staleReason)
            ? "missing_target"
            : "stale_section",
          target: `${page.wikiFile ?? ""}#${section.id ?? ""}`,
          reason: section.staleReason,
          message: staleMessage(
            section.staleReason,
            section.heading,
          ),
        })),
    ),
  ];
}
