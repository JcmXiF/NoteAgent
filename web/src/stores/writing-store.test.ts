import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/writing", () => ({
  fetchWritingState: vi.fn(),
}));

import {
  fetchWritingState,
  type WritingStatePayload,
} from "@/lib/api/writing";
import { useWritingStore } from "@/stores/writing-store";

const empty: WritingStatePayload = {
  version: 1,
  stateVersion: 3,
  writer: null,
  searches: [],
  selectedPages: [],
  frames: [],
  draft: { pages: [] },
  attention: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  useWritingStore.setState({
    project: null,
    state: null,
    loading: false,
    error: null,
  });
});

describe("the writing state read", () => {
  it("reads once per project and keeps the answer", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);

    await useWritingStore.getState().load("p");
    await useWritingStore.getState().load("p");

    expect(fetchWritingState).toHaveBeenCalledTimes(1);
    expect(fetchWritingState).toHaveBeenCalledWith("p");
    expect(useWritingStore.getState().state).toEqual(empty);
    expect(useWritingStore.getState().loading).toBe(false);
  });

  it("reads again when the project changes", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);

    await useWritingStore.getState().load("p");
    await useWritingStore.getState().load("q");

    expect(fetchWritingState).toHaveBeenCalledTimes(2);
    expect(fetchWritingState).toHaveBeenLastCalledWith("q");
  });

  it("keeps a failed read as an error, not an empty state", async () => {
    vi.mocked(fetchWritingState).mockRejectedValue(new Error("offline"));

    await useWritingStore.getState().load("p");

    const read = useWritingStore.getState();
    expect(read.state).toBeNull();
    expect(read.error).toBeTruthy();
    expect(read.loading).toBe(false);
  });

  it("does not let a slow read overwrite a newer project", async () => {
    let resolveSlow!: (value: WritingStatePayload) => void;
    const slow = new Promise<WritingStatePayload>((resolve) => {
      resolveSlow = resolve;
    });
    vi.mocked(fetchWritingState).mockReturnValueOnce(slow);
    const first = useWritingStore.getState().load("p");

    vi.mocked(fetchWritingState).mockResolvedValueOnce({
      ...empty,
      stateVersion: 9,
    });
    await useWritingStore.getState().load("q");

    resolveSlow({ ...empty, stateVersion: 7 });
    await first;

    expect(useWritingStore.getState().project).toBe("q");
    expect(useWritingStore.getState().state?.stateVersion).toBe(9);
  });
});
