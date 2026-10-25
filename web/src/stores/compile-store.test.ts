import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/inbox", () => ({
  fetchIngestRecovery: vi.fn(),
  postIngestRecoveryDiscard: vi.fn(),
}));

vi.mock("@/lib/api/plan", () => ({
  compileConfirmed: vi.fn(),
}));

// The job wrapper is about the jobs panel, not about the round: running the work
// here is enough to see what the panel is left with.
vi.mock("@/lib/run", () => ({
  runAsActivity: vi.fn(async (options: { run: (ctx: unknown) => Promise<unknown> }) =>
    options.run({ status: () => {}, note: () => {}, signal: undefined }),
  ),
}));

import { fetchIngestRecovery } from "@/lib/api/inbox";
import { compileConfirmed } from "@/lib/api/plan";
import type { IngestRecoveryPayload } from "@/lib/api/types";
import { roundFromRecord } from "@/lib/compile-round";
import { useCompileStore } from "@/stores/compile-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * What the writing panel has to say after the window is gone.
 *
 * A compile writes files; the round that says which files is kept by the project,
 * so opening it again reads that round back. What is checked here is that half:
 * that the round on screen follows the project being looked at, and that a late
 * answer for a project the reader has left cannot take its place.
 */
const read = vi.mocked(fetchIngestRecovery);

/** What a project with a finished round answers, with nothing to resume. */
function payload(last: IngestRecoveryPayload["last"]): IngestRecoveryPayload {
  return { project: "A", openCount: 0, items: [], last };
}

const kept = {
  intent: "Entropy",
  finishedAt: "2026-10-14T12:00:00+00:00",
  written: [{ path: "wiki/a.md", mode: "create" }],
  doctor: {
    contentPages: 1,
    indexLinks: 2,
    errors: 0,
    warnings: 0,
    findingCount: 0,
    truncated: false,
    findings: [],
  },
  writeWarnings: [],
  parseWarnings: [],
};

beforeEach(() => {
  read.mockReset();
  vi.mocked(compileConfirmed).mockReset();
  useWorkspaceStore.setState({ current: "A" });
  useCompileStore.setState({ round: null, running: false, error: null, last: null });
});

describe("the round a project finished last", () => {
  it("is read back on opening it, so the files do not go away with the window", async () => {
    read.mockResolvedValue(payload(kept));

    await useCompileStore.getState().load("A");

    expect(read).toHaveBeenCalledWith("A");
    const round = useCompileStore.getState().round;
    expect(round?.fromProject).toBe(true);
    expect(round?.written).toEqual([{ path: "wiki/a.md", mode: "create" }]);
    expect(round?.intent).toBe("Entropy");
  });

  it("is replaced by the next project's round, not carried over", async () => {
    read.mockResolvedValue(payload(kept));
    await useCompileStore.getState().load("A");

    useWorkspaceStore.setState({ current: "B" });
    read.mockResolvedValue(
      payload({
        ...kept,
        intent: "Enthalpy",
        written: [{ path: "wiki/b.md", mode: "merge" }],
      }),
    );
    await useCompileStore.getState().load("B");

    const round = useCompileStore.getState().round;
    expect(round?.intent).toBe("Enthalpy");
    expect(round?.written).toEqual([{ path: "wiki/b.md", mode: "merge" }]);
  });

  it("does not land after the reader has left the project it belongs to", async () => {
    // The answer arrives late. Showing it now would show another project's files.
    let land: ((value: IngestRecoveryPayload) => void) | undefined;
    read.mockImplementation(() => new Promise((resolve) => (land = resolve)));

    const pending = useCompileStore.getState().load("A");
    useWorkspaceStore.setState({ current: "B" });
    land?.(payload(kept));
    await pending;

    expect(useCompileStore.getState().round).toBeNull();
  });

  it("reports nothing rather than a stale round when the project cannot be read", async () => {
    read.mockResolvedValue(payload(kept));
    await useCompileStore.getState().load("A");

    read.mockRejectedValue(new Error("offline"));
    await useCompileStore.getState().load("A");

    expect(useCompileStore.getState().round).toBeNull();
  });

  it("is dropped when the project itself is", () => {
    useCompileStore.setState({ round: roundFromRecord(kept) });

    useCompileStore.getState().reset();

    expect(useCompileStore.getState().round).toBeNull();
  });
});

describe("a round that did not finish", () => {
  it("does not leave the previous round looking like its result", async () => {
    vi.mocked(compileConfirmed).mockRejectedValue(new Error("provider is down"));
    useCompileStore.setState({ round: roundFromRecord(kept) });

    await useCompileStore.getState().run({
      confirmed: [
        {
          name: "a",
          slug: "a",
          wikiPath: "",
          action: "write",
          sectionIds: [],
        },
      ],
      intent: "Entropy",
    });

    const state = useCompileStore.getState();
    expect(state.round).toBeNull();
    expect(state.error).toBe(i18n.t("compile.runFailed"));
    expect(state.running).toBe(false);
  });

  it("can be tried again with the same pages", async () => {
    vi.mocked(compileConfirmed).mockRejectedValueOnce(new Error("provider is down"));
    const input = {
      confirmed: [
        {
          name: "a",
          slug: "a",
          wikiPath: "",
          action: "write" as const,
          sectionIds: [],
        },
      ],
      intent: "Entropy",
    };

    await useCompileStore.getState().run(input);
    useCompileStore.getState().retry();
    await vi.waitFor(() => expect(compileConfirmed).toHaveBeenCalledTimes(2));

    expect(vi.mocked(compileConfirmed).mock.calls[1][1]).toEqual({
      intent: "Entropy",
      confirmed: input.confirmed,
    });
  });
});
