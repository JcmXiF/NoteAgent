import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/writing", () => ({
  fetchWritingState: vi.fn(),
  fetchWritingArchives: vi.fn(),
  saveWritingState: vi.fn(),
  archiveWritingState: vi.fn(),
  activateWritingArchive: vi.fn(),
}));

import { ApiError } from "@/lib/api/client";
import {
  fetchWritingArchives,
  fetchWritingState,
  saveWritingState,
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
  vi.mocked(fetchWritingArchives).mockResolvedValue({ items: [] });
  useWritingStore.setState({
    project: null,
    state: null,
    draft: null,
    baseVersion: 0,
    dirty: false,
    loading: false,
    saving: false,
    error: null,
    notice: null,
    archives: [],
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

describe("a person's save", () => {
  it("sends the local draft with the base version and clears the mark", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);
    vi.mocked(saveWritingState).mockResolvedValue({
      ...empty,
      stateVersion: 4,
      searches: [{ id: "s1", query: "q" }],
    });
    await useWritingStore.getState().load("p");
    useWritingStore.getState().edit((draft) => ({
      ...draft,
      searches: [{ id: "s1", query: "q" }],
    }));

    await useWritingStore.getState().save();

    expect(saveWritingState).toHaveBeenCalledWith(
      "p",
      3,
      expect.objectContaining({
        searches: [{ id: "s1", query: "q" }],
      }),
    );
    const read = useWritingStore.getState();
    expect(read.state?.stateVersion).toBe(4);
    expect(read.baseVersion).toBe(4);
    expect(read.dirty).toBe(false);
    expect(read.draft?.searches).toEqual([
      { id: "s1", query: "q" },
    ]);
  });

  it("keeps the local edit when a run holds the state", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);
    vi.mocked(saveWritingState).mockRejectedValue(
      new ApiError("held", 409, "state.busy"),
    );
    await useWritingStore.getState().load("p");
    useWritingStore.getState().edit((draft) => ({
      ...draft,
      searches: [{ id: "s1", query: "q" }],
    }));

    await useWritingStore.getState().save();

    const read = useWritingStore.getState();
    expect(read.notice).toBe("busy");
    expect(read.dirty).toBe(true);
    expect(read.draft?.searches).toEqual([
      { id: "s1", query: "q" },
    ]);
  });

  it("keeps the local edit when the base version is stale", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);
    vi.mocked(saveWritingState).mockRejectedValue(
      new ApiError("stale", 409, "state.conflict"),
    );
    await useWritingStore.getState().load("p");
    useWritingStore.getState().edit((draft) => ({
      ...draft,
      frames: [{ id: "f1" }],
    }));

    await useWritingStore.getState().save();

    const read = useWritingStore.getState();
    expect(read.notice).toBe("conflict");
    expect(read.dirty).toBe(true);
    expect(read.draft?.frames).toEqual([{ id: "f1" }]);
  });

  it("keeps a dirty draft across a forced read", async () => {
    vi.mocked(fetchWritingState).mockResolvedValue(empty);
    await useWritingStore.getState().load("p");
    useWritingStore.getState().edit((draft) => ({
      ...draft,
      searches: [{ id: "s1" }],
    }));
    vi.mocked(fetchWritingState).mockResolvedValueOnce({
      ...empty,
      stateVersion: 5,
    });

    await useWritingStore.getState().load("p", true);

    const read = useWritingStore.getState();
    expect(read.state?.stateVersion).toBe(5);
    expect(read.baseVersion).toBe(3);
    expect(read.draft?.searches).toEqual([{ id: "s1" }]);
    expect(read.notice).toBe("remote");
  });
});
