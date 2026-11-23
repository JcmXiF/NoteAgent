// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";

vi.mock("@/lib/api/writing", () => ({
  fetchWritingState: vi.fn(),
  fetchWritingArchives: vi.fn(),
  saveWritingState: vi.fn(),
  archiveWritingState: vi.fn(),
  activateWritingArchive: vi.fn(),
  streamWritingState: vi.fn(() => new Promise<void>(() => {})),
  startAutoFrame: vi.fn(),
}));

import { WritingStateView } from "@/components/writing/WritingStateView";
import { startAutoFrame } from "@/lib/api/writing";
import { useActivityStore } from "@/stores/activity-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useWritingStore } from "@/stores/writing-store";

let host: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("en");
  useWorkspaceStore.setState({ current: "p" });
  useActivityStore.setState({ items: [] });
  useWritingStore.setState({
    project: "p",
    state: {
      version: 1,
      stateVersion: 4,
      writer: null,
      searches: [],
      selectedPages: [],
      frames: [],
      draft: { pages: [] },
      attention: [],
    },
    draft: null,
    baseVersion: 4,
    dirty: false,
    loading: false,
    saving: false,
    error: null,
    notice: null,
    archives: [],
    load: vi.fn(async () => {}),
    save: vi.fn(async () => {}),
    reload: vi.fn(async () => {}),
    archive: vi.fn(async () => {}),
    activate: vi.fn(async () => {}),
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function draw() {
  await act(async () => {
    root.render(<WritingStateView slot="main" />);
  });
  await act(async () => {});
}

function hasInput(value: string): boolean {
  return [...host.querySelectorAll("input")].some(
    (input) => (input as HTMLInputElement).value === value,
  );
}

function hasTextarea(value: string): boolean {
  return [...host.querySelectorAll("textarea")].some(
    (area) => (area as HTMLTextAreaElement).value === value,
  );
}

function button(label: string): HTMLButtonElement | undefined {
  return [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(label),
  );
}

describe("the writing state panel", () => {
  it("shows the version, the writer and the four sections", async () => {
    await draw();

    expect(host.textContent).toContain("Writing state");
    expect(host.textContent).toContain("State version 4");
    expect(host.textContent).toContain("No writer");
    expect(host.textContent).toContain("Search results");
    expect(host.textContent).toContain("Selected pages");
    expect(host.textContent).toContain("Frames");
    expect(host.textContent).toContain("Draft");
    expect(host.textContent).toContain("Nothing here yet.");
    expect(host.textContent).toContain("Attention");
    expect(host.textContent).toContain("Nothing needs attention.");
    expect(host.textContent).toContain("Save");
    expect(host.textContent).toContain("Archive current");
  });

  it("lists the latest search's hits", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 5,
        writer: null,
        searches: [
          {
            id: "search-1",
            query: "信息熵",
            status: "ok",
            hits: [
              {
                id: "hit-1",
                title: "信息熵 - 维基百科",
                url: "https://example.com/entropy",
                snippet: "熵是信息的平均量。",
                rank: 1,
              },
            ],
            createdAt: "2026-11-21T00:00:00Z",
          },
        ],
        selectedPages: [],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
    });

    await draw();

    expect(host.textContent).toContain("Query: 信息熵");
    expect(host.textContent).toContain("#1");
    expect(hasInput("信息熵 - 维基百科")).toBe(true);
    expect(hasInput("https://example.com/entropy")).toBe(true);
    expect(hasInput("熵是信息的平均量。")).toBe(true);
  });

  it("shows a failed search in attention", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 6,
        writer: null,
        searches: [
          {
            id: "search-2",
            query: "坏查询",
            status: "failed",
            hits: [],
            error: "the provider is down",
            createdAt: "2026-11-21T00:00:00Z",
          },
        ],
        selectedPages: [],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
    });

    await draw();

    expect(host.textContent).toContain("The search failed.");
    expect(host.textContent).toContain("the provider is down");
  });

  it("lists selected pages with status and block count", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 7,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
            version: 2,
          },
          {
            id: "page-2",
            url: "https://example.com/kl",
            title: "KL 散度",
            status: "failed",
            blockCount: 0,
            error: "http_forbidden",
            errorKind: "login_wall",
          },
        ],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
    });

    await draw();

    expect(hasInput("信息熵")).toBe(true);
    expect(hasInput("KL 散度")).toBe(true);
    expect(host.textContent).toContain("Ready");
    expect(host.textContent).toContain("2 blocks");
    expect(host.textContent).toContain("Failed");
    expect(host.textContent).toContain("http_forbidden");
  });

  it("lists frames by page", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 8,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
          },
        ],
        frames: [
          {
            id: "frame-1",
            pageId: "page-1",
            blockIds: ["b1", "b2"],
            version: 1,
            source: "agent",
          },
        ],
        draft: { pages: [] },
        attention: [],
      },
    });

    await draw();

    expect(hasInput("信息熵")).toBe(true);
    expect(host.textContent).toContain("frame-1");
    expect(host.textContent).toContain("2 blocks");
    expect(hasInput("b1, b2")).toBe(true);
  });

  it("lists the draft pages, sections and bodies", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 9,
        writer: null,
        searches: [],
        selectedPages: [],
        frames: [],
        draft: {
          pages: [
            {
              wikiFile: "wiki/concepts/信息熵.md",
              title: "信息熵",
              version: 2,
              status: "draft",
              sections: [
                {
                  id: "definition",
                  heading: "定义",
                  body: "熵是信息的平均量。",
                  citations: [],
                  version: 1,
                  stale: false,
                  humanEdited: false,
                },
              ],
            },
          ],
        },
        attention: [],
      },
    });

    await draw();

    expect(host.textContent).toContain("信息熵");
    expect(host.textContent).toContain("v2");
    expect(host.textContent).toContain("Draft");
    expect(hasInput("定义")).toBe(true);
    expect(hasTextarea("熵是信息的平均量。")).toBe(true);
  });

  it("shows a published draft's status and effect ids", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 10,
        writer: null,
        searches: [],
        selectedPages: [],
        frames: [],
        draft: {
          pages: [
            {
              wikiFile: "wiki/concepts/信息熵.md",
              title: "信息熵",
              version: 3,
              status: "written",
              writtenAt: "2026-11-21T00:00:00Z",
              effectIds: ["eff-1"],
              sections: [
                {
                  id: "definition",
                  heading: "定义",
                  body: "熵是信息的平均量。",
                  citations: [],
                  version: 1,
                  stale: false,
                  humanEdited: false,
                },
              ],
            },
          ],
        },
        attention: [],
      },
    });

    await draw();

    expect(host.textContent).toContain("Written");
    expect(host.textContent).toContain("eff-1");
  });

  it("shows a stale section and its reason", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 13,
        writer: null,
        searches: [],
        selectedPages: [],
        frames: [],
        draft: {
          pages: [
            {
              wikiFile: "wiki/concepts/信息熵.md",
              title: "信息熵",
              version: 2,
              status: "draft",
              sections: [
                {
                  id: "definition",
                  heading: "定义",
                  body: "熵是信息的平均量。",
                  citations: [],
                  version: 2,
                  stale: true,
                  staleReason: "frame_changed",
                  humanEdited: false,
                },
              ],
            },
          ],
        },
        attention: [],
      },
    });

    await draw();

    expect(host.textContent).toContain("May be out of date");
    expect(host.textContent).toContain(
      "The frame changed after “定义” was written.",
    );
  });

  it("starts the auto-frame node and reloads the state", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 15,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
          },
        ],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
    });
    vi.mocked(startAutoFrame).mockResolvedValue({
      status: "completed",
    });
    await draw();
    const button = [...host.querySelectorAll("button")].find(
      (item) => item.textContent?.includes("Auto-select frames"),
    );

    await act(async () => {
      button?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(startAutoFrame).toHaveBeenCalledWith(
      "p",
      "page-1",
      expect.stringMatching(/^node-/),
      expect.anything(),
    );
    expect(useWritingStore.getState().load).toHaveBeenCalled();
  });

  it("shows why the auto-frame node failed", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 16,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
          },
        ],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
    });
    vi.mocked(startAutoFrame).mockResolvedValue({
      status: "failed",
      error: "the model refused",
    });
    await draw();
    const button = [...host.querySelectorAll("button")].find(
      (item) => item.textContent?.includes("Auto-select frames"),
    );

    await act(async () => {
      button?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(host.textContent).toContain(
      "Auto-select frames failed.",
    );
    expect(host.textContent).not.toContain("the model refused");
  });

  it("asks before starting the node on unsaved edits", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 17,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
          },
        ],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
      dirty: true,
    });
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    await draw();
    const button = [...host.querySelectorAll("button")].find(
      (item) => item.textContent?.includes("Auto-select frames"),
    );

    await act(async () => {
      button?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await Promise.resolve();
    });

    expect(confirm).toHaveBeenCalled();
    expect(startAutoFrame).not.toHaveBeenCalled();
  });

  it("edits a frame's blocks and can remove it", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 14,
        writer: null,
        searches: [],
        selectedPages: [
          {
            id: "page-1",
            url: "https://example.com/entropy",
            title: "信息熵",
            status: "ready",
            blockCount: 2,
          },
        ],
        frames: [
          {
            id: "frame-1",
            pageId: "page-1",
            blockIds: ["b1", "b2"],
            version: 1,
            source: "agent",
          },
        ],
        draft: { pages: [] },
        attention: [],
      },
    });
    await draw();
    const input = [...host.querySelectorAll("input")].find(
      (item) =>
        item.getAttribute("aria-label") ===
        "Block ids, comma separated",
    ) as HTMLInputElement;

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "b1, b3");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(
      useWritingStore.getState().draft?.frames[0].blockIds,
    ).toEqual(["b1", "b3"]);

    const row = input.closest("li");
    const remove = row?.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove"]',
    );
    await act(async () => {
      remove?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(useWritingStore.getState().draft?.frames).toEqual([]);
  });

  it("edits a section locally and saves it", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 11,
        writer: null,
        searches: [],
        selectedPages: [],
        frames: [],
        draft: {
          pages: [
            {
              wikiFile: "wiki/concepts/信息熵.md",
              title: "信息熵",
              version: 1,
              status: "draft",
              sections: [
                {
                  id: "definition",
                  heading: "定义",
                  body: "熵是信息的平均量。",
                  citations: [],
                  version: 1,
                  stale: false,
                  humanEdited: false,
                },
              ],
            },
          ],
        },
        attention: [],
      },
    });
    await draw();
    const heading = [...host.querySelectorAll("input")].find(
      (input) => input.getAttribute("aria-label") === "Section heading",
    ) as HTMLInputElement;

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(heading, "新标题");
      heading.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(useWritingStore.getState().dirty).toBe(true);
    expect(
      useWritingStore.getState().draft?.draft.pages[0].sections?.[0]
        .heading,
    ).toBe("新标题");

    const save = button("Save");
    await act(async () => {
      save?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(useWritingStore.getState().save).toHaveBeenCalled();
  });

  it("keeps the edits and offers a reload after a conflict", async () => {
    useWritingStore.setState({
      state: {
        version: 1,
        stateVersion: 12,
        writer: null,
        searches: [],
        selectedPages: [],
        frames: [],
        draft: { pages: [] },
        attention: [],
      },
      dirty: true,
      notice: "conflict",
    });

    await draw();

    expect(host.textContent).toContain(
      "The writing state changed after you loaded it.",
    );
    const confirm = vi.fn(() => true);
    vi.stubGlobal("confirm", confirm);
    const reload = button("Reload");
    await act(async () => {
      reload?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(confirm).toHaveBeenCalled();
    expect(useWritingStore.getState().reload).toHaveBeenCalled();
  });

  it("lists archived states and can activate one", async () => {
    useWritingStore.setState({
      archives: [
        {
          id: "000001",
          stateVersion: 2,
          searches: 1,
          selectedPages: 2,
          frames: 3,
          draftPages: 4,
        },
      ],
    });

    await draw();

    expect(host.textContent).toContain("Archived states (1)");
    expect(host.textContent).toContain("v2");
    const activate = button("Activate");
    await act(async () => {
      activate?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(useWritingStore.getState().activate).toHaveBeenCalledWith(
      "000001",
    );
  });
});
