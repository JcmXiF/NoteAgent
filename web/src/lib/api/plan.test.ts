import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  postSse: vi.fn(() => Promise.resolve({ intent: "熵" })),
  projectPath: (project: string, suffix: string) =>
    `/projects/${project}/${suffix}`,
}));

vi.mock("@/i18n", () => ({
  currentLanguage: () => "zh",
}));

import { postSse } from "@/lib/api/client";
import { planTopicMap } from "@/lib/api/plan";

beforeEach(() => {
  vi.mocked(postSse).mockClear();
});

describe("the plan request", () => {
  it("carries the wait the original button belongs to", async () => {
    await planTopicMap("demo", {
      intent: "熵",
      wait: { sessionId: "s1", waitId: "w1" },
    });

    expect(postSse).toHaveBeenCalledWith(
      "/projects/demo/topic-map",
      {
        intent: "熵",
        sessionId: "s1",
        waitId: "w1",
        language: "zh",
      },
      { onStatus: undefined, signal: undefined },
    );
  });

  it("leaves the wait out when nobody proposed one", async () => {
    await planTopicMap("demo", { intent: "熵" });

    expect(vi.mocked(postSse).mock.calls[0][1]).toEqual({
      intent: "熵",
      language: "zh",
    });
  });
});
