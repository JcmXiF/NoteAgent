import { describe, expect, it } from "vitest";
import { activityHeadline } from "@/components/layout/JobsList";
import type { ActivityItem } from "@/stores/activity-store";

function job(patch: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: patch.id ?? "act-1",
    kind: "plan",
    title: "a job",
    phase: "done",
    detail: [],
    filesWritten: [],
    createdAt: 1,
    ...patch,
  };
}

/**
 * The one line that stands for all the jobs when the list is closed. It has to say
 * the most useful thing there is, because it is all the reader sees.
 */
describe("the jobs headline", () => {
  it("says nothing is going on when nothing is", () => {
    expect(activityHeadline([]).kind).toBe("idle");
  });

  it("prefers what is running over everything else", () => {
    // Something still happening is what the reader most needs to know.
    const items = [
      job({ id: "a", phase: "error", error: "it broke" }),
      job({ id: "b", phase: "running", message: "reading a page", title: "a page" }),
    ];
    expect(activityHeadline(items)).toEqual({ kind: "running", text: "reading a page" });
  });

  it("falls back to the job's title when it has not said anything yet", () => {
    const items = [job({ phase: "running", title: "https://example.com", message: undefined })];
    expect(activityHeadline(items)).toEqual({
      kind: "running",
      text: "https://example.com",
    });
  });

  it("counts what failed when nothing is running", () => {
    const items = [
      job({ id: "a", phase: "error" }),
      job({ id: "b", phase: "error" }),
      job({ id: "c", phase: "done" }),
    ];
    expect(activityHeadline(items)).toEqual({ kind: "failed", count: 2 });
  });

  it("says so when everything finished", () => {
    expect(activityHeadline([job({ phase: "done" })]).kind).toBe("done");
    // A job the reader stopped is finished too, not failed.
    expect(activityHeadline([job({ phase: "cancelled" })]).kind).toBe("done");
  });

  it("looks at the newest job first for what is running", () => {
    // The list is newest-first, and the line should describe the one in front.
    const items = [
      job({ id: "new", phase: "running", message: "the newest" }),
      job({ id: "old", phase: "running", message: "an older one" }),
    ];
    expect(activityHeadline(items)).toEqual({ kind: "running", text: "the newest" });
  });
});
