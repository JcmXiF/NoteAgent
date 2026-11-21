import { describe, expect, it } from "vitest";
import type { WritingStatePayload } from "@/lib/api/writing";
import { writingAttention } from "@/lib/writing";

function state(
  overrides: Partial<WritingStatePayload> = {},
): WritingStatePayload {
  return {
    version: 1,
    stateVersion: 1,
    writer: null,
    searches: [],
    selectedPages: [],
    frames: [],
    draft: { pages: [] },
    attention: [],
    ...overrides,
  };
}

describe("writing attention", () => {
  it("derives failed searches and selected pages", () => {
    const attention = writingAttention(
      state({
        searches: [
          {
            id: "s1",
            query: "信息熵",
            status: "failed",
            hits: [],
            error: "down",
          },
        ],
        selectedPages: [
          {
            id: "p1",
            url: "https://example.com/entropy",
            status: "failed",
            errorKind: "login_wall",
            error: "forbidden",
          },
        ],
      }),
    );

    expect(attention).toEqual([
      { kind: "fetch_failed", target: "s1", message: "down" },
      { kind: "login_wall", target: "p1", message: "forbidden" },
    ]);
  });

  it("keeps stored attention first", () => {
    const attention = writingAttention(
      state({ attention: [{ kind: "blocked", message: "waiting" }] }),
    );

    expect(attention).toEqual([{ kind: "blocked", message: "waiting" }]);
  });
});
