// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { WritingStateView } from "@/components/writing/WritingStateView";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useWritingStore } from "@/stores/writing-store";

let host: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  await i18n.changeLanguage("en");
  useWorkspaceStore.setState({ current: "p" });
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
    loading: false,
    error: null,
    load: vi.fn(async () => {}),
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function draw() {
  await act(async () => {
    root.render(<WritingStateView slot="main" />);
  });
  await act(async () => {});
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
    expect(host.textContent).toContain("信息熵 - 维基百科");
    expect(host.textContent).toContain("熵是信息的平均量。");
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

    expect(host.textContent).toContain("信息熵");
    expect(host.textContent).toContain("Ready");
    expect(host.textContent).toContain("2 blocks");
    expect(host.textContent).toContain("KL 散度");
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

    expect(host.textContent).toContain("信息熵");
    expect(host.textContent).toContain("frame-1");
    expect(host.textContent).toContain("2 blocks");
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
    expect(host.textContent).toContain("定义");
    expect(host.textContent).toContain("熵是信息的平均量。");
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
});
