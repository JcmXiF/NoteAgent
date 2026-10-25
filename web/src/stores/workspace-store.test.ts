import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/projects", () => ({
  fetchProjects: vi.fn(),
  switchToProject: vi.fn(),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
  openFolder: vi.fn(),
  inspectFolder: vi.fn(),
}));

/**
 * The store keeps how many retries are left as it goes, because it is a count of
 * consecutive failures rather than something a view reads. Each test therefore starts
 * from a fresh copy of the module, as a reload would.
 */
async function freshStore() {
  vi.resetModules();
  const { ApiError } = await import("@/lib/api/client");
  const projects = await import("@/lib/api/projects");
  const store = (await import("@/stores/workspace-store")).useWorkspaceStore;
  const i18n = (await import("@/i18n")).default;
  return { store, ApiError, projects, i18n };
}

/** The retry runs as a timer callback, so its async work needs flushing after. */
async function advance(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a service that is not up yet", () => {
  it("is looked for again, a few times", async () => {
    const { store, ApiError, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockRejectedValue(new ApiError("连不上本地服务", 0));

    await store.getState().refresh();
    expect(projects.fetchProjects).toHaveBeenCalledTimes(1);
    expect(store.getState().error).toBeTruthy();

    await advance(2_100);
    expect(projects.fetchProjects).toHaveBeenCalledTimes(2);
    await advance(2_100);
    expect(projects.fetchProjects).toHaveBeenCalledTimes(3);
  });

  it("stops trying rather than retrying forever", async () => {
    const { store, ApiError, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockRejectedValue(new ApiError("连不上", 0));

    await store.getState().refresh();
    await advance(60_000);
    const calls = vi.mocked(projects.fetchProjects).mock.calls.length;
    // The first call plus the handful of retries, and then quiet.
    expect(calls).toBeLessThanOrEqual(6);
    await advance(60_000);
    expect(vi.mocked(projects.fetchProjects).mock.calls.length).toBe(calls);
  });

  it("clears the message once it works, and stops looking", async () => {
    const { store, ApiError, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockRejectedValueOnce(new ApiError("连不上", 0));
    await store.getState().refresh();
    expect(store.getState().error).toBeTruthy();

    vi.mocked(projects.fetchProjects).mockResolvedValue([
      { name: "information", path: "/p", active: true },
    ] as never);
    await advance(2_100);

    const state = store.getState();
    expect(state.error).toBeNull();
    expect(state.current).toBe("information");

    await advance(10_000);
    // Once, plus the one retry that worked: nothing further.
    expect(projects.fetchProjects).toHaveBeenCalledTimes(2);
  });

  it("does not repeat a refusal, which is an answer", async () => {
    // A project list that cannot be read is not going to read on the second ask.
    const { store, ApiError, projects, i18n } = await freshStore();
    vi.mocked(projects.fetchProjects).mockRejectedValue(new ApiError("读不到项目列表", 500));

    await store.getState().refresh();
    await advance(10_000);

    expect(projects.fetchProjects).toHaveBeenCalledTimes(1);
    // The reader gets the wording for a list that could not be read, not the
    // sentence the failure came with.
    expect(store.getState().error).toBe(i18n.t("error.loadProjects"));
  });
});

describe("asking again", () => {
  it("tries again when the reader asks, whatever went wrong", async () => {
    const { store, ApiError, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockRejectedValueOnce(new ApiError("读不到项目列表", 500));
    await store.getState().refresh();

    vi.mocked(projects.fetchProjects).mockResolvedValue([
      { name: "rl", path: "/p", active: true },
    ] as never);
    await store.getState().refresh();

    expect(store.getState().error).toBeNull();
    expect(store.getState().current).toBe("rl");
  });

  it("refills the retries after a working load", async () => {
    // A service that goes away later is worth retrying afresh.
    const { store, ApiError, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockResolvedValueOnce([
      { name: "rl", path: "/p", active: true },
    ] as never);
    await store.getState().refresh();

    vi.mocked(projects.fetchProjects).mockRejectedValue(new ApiError("连不上", 0));
    await store.getState().refresh();
    await advance(30_000);

    expect(vi.mocked(projects.fetchProjects).mock.calls.length).toBeGreaterThan(3);
  });
});

describe("the project that is open", () => {
  it("forgets a project that is no longer in the list", async () => {
    // Removing the last project leaves nothing to read, and the workspace should
    // offer to begin again rather than keep asking a project that is gone.
    const { store, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockResolvedValueOnce([
      { name: "alpha", path: "/p/alpha", active: true },
    ] as never);
    await store.getState().refresh();
    expect(store.getState().current).toBe("alpha");

    vi.mocked(projects.deleteProject).mockResolvedValue({
      deleted: "alpha",
      active: null,
      trashId: null,
    } as never);
    vi.mocked(projects.fetchProjects).mockResolvedValueOnce([] as never);
    await store.getState().remove("alpha", "alpha");

    expect(store.getState().projects).toEqual([]);
    expect(store.getState().current).toBeNull();
  });

  it("keeps a project that is still in the list", async () => {
    const { store, projects } = await freshStore();
    vi.mocked(projects.fetchProjects).mockResolvedValue([
      { name: "alpha", path: "/p/alpha", active: false },
    ] as never);
    store.setState({ current: "alpha" });

    await store.getState().refresh();

    expect(store.getState().current).toBe("alpha");
  });
});

describe("opening a folder that already exists", () => {
  it("hands back what came in, and lists the project it made", async () => {
    const { store, projects } = await freshStore();
    const opened = {
      name: "notes-2",
      path: "/elsewhere/notes",
      mode: "copy" as const,
      imported: 3,
      attachments: 1,
      skipped: ["index.md"],
      alreadyLinked: false,
    };
    vi.mocked(projects.openFolder).mockResolvedValue(opened);
    vi.mocked(projects.fetchProjects).mockResolvedValue([
      { name: "notes-2", path: "/elsewhere/notes", active: true },
    ] as never);

    const answer = await store.getState().openExisting("/elsewhere/notes", "copy", "notes");

    // The name the reader typed is the one asked for; trimming is the store's job.
    expect(projects.openFolder).toHaveBeenCalledWith("/elsewhere/notes", "copy", "notes");
    expect(answer).toEqual(opened);
    expect(store.getState().current).toBe("notes-2");
    expect(store.getState().busy).toBe(false);
  });

  it("says what the core refused it with, and answers nothing", async () => {
    const { store, ApiError, projects, i18n } = await freshStore();
    vi.mocked(projects.openFolder).mockRejectedValue(
      new ApiError("not a project", 400, "project.folder.notProject", {}),
    );

    const answer = await store.getState().openExisting("/elsewhere/plain", "link");

    expect(answer).toBeNull();
    expect(store.getState().error).toBe(i18n.t("error.code.project.folder.notProject"));
    expect(store.getState().busy).toBe(false);
  });

  it("does not ask about an empty path", async () => {
    const { store, projects } = await freshStore();
    const answer = await store.getState().openExisting("   ", "link");
    expect(answer).toBeNull();
    expect(projects.openFolder).not.toHaveBeenCalled();
  });

  it("remembers which dialog is in front, and forgets it again", async () => {
    const { store } = await freshStore();
    store.getState().setDialog("open");
    expect(store.getState().dialog).toBe("open");
    store.getState().setDialog(null);
    expect(store.getState().dialog).toBeNull();
  });
});
