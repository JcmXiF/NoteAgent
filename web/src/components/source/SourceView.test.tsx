// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SourceView } from "@/components/source/SourceView";
import { fetchFile } from "@/lib/api/files";
import { useAcquireStore } from "@/stores/acquire-store";
import { useBrowseStore } from "@/stores/browse-store";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsReads } from "@/stores/docs-reads";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { GuestTab } from "@/lib/desktop";

vi.mock("@/lib/api/files", () => ({
  fetchFile: vi.fn(),
  fileBytesUrl: (project: string, path: string) => `/api/${project}/file-bytes?path=${path}`,
}));

/**
 * What a source shows, and what is offered when its text is in neither place.
 *
 * The text is read from the capture this session made or from the original stored in
 * the project, and only the second of those outlives the session. That makes "the
 * capture is gone, the file is not" the case worth holding down — it is where a source
 * lands every time the reader comes back the next day, and it used to be answered with
 * "fetch it again" while the file it was asking for sat in the project.
 */

const project = "proj";
const stored = "raw/web/entropy.html";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  useWorkspaceStore.setState({ current: project });
  useClipsStore.setState({ collections: [], focusedId: null });
  useAcquireStore.setState({
    project,
    entries: [],
    focusedUrl: null,
    notice: null,
    noticeOrigin: null,
    busy: false,
  });
  useDocsReads.getState().reset(project);
  useBrowseStore.setState({
    guest: { tabs: [], activeId: 0, address: "", canGoBack: false, canGoForward: false },
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  vi.mocked(fetchFile).mockReset();
});

/** Draws the view and lets a stored original be read before anything is looked at. */
async function draw(): Promise<HTMLElement> {
  await act(async () => {
    root.render(<SourceView slot="main" />);
  });
  await act(async () => {});
  return host;
}

function button(el: HTMLElement, key: string): HTMLButtonElement | null {
  const label = i18n.t(key);
  return [...el.querySelectorAll("button")].find((item) => item.textContent?.includes(label)) ?? null;
}

/** A tab in the shell's browser, at the given address. */
function tab(url: string): GuestTab {
  return { id: 1, title: "Entropy", url, loading: false, active: true };
}

describe("a source whose capture is gone", () => {
  it("shows the original that was stored in the project", async () => {
    vi.mocked(fetchFile).mockResolvedValue({
      path: stored,
      content: "<html><body><p>Entropy is…</p></body></html>",
      blocks: [{ id: "b1", tag: "p", text: "Entropy is…", preview: "Entropy is…" }],
      kind: "html",
      sourceUrl: "https://example.com/entropy",
    });
    useAcquireStore.setState({
      entries: [
        {
          url: "https://example.com/entropy",
          title: "Entropy",
          fidelity: "ok",
          kind: "html",
          capturedAt: 1,
          path: stored,
        },
      ],
      focusedUrl: "https://example.com/entropy",
    });

    const shown = await draw();

    expect(vi.mocked(fetchFile)).toHaveBeenCalledWith(project, stored);
    // The stored page is drawn as itself, which is what picking passages needs.
    expect(shown.querySelector("iframe")?.getAttribute("title")).toBe("Entropy");
    expect(shown.textContent).not.toContain(i18n.t("sources.preview.needFetch.title"));
    expect(shown.textContent).toContain(i18n.t("sources.saved"));
    // The address written into the original is what the way to the page is made of.
    expect(button(shown, "sources.openInBrowse")).not.toBeNull();
  });

  it("shows a stored markdown original as blocks, so a passage can be picked", async () => {
    const markdown = "raw/files/entropy.md";
    vi.mocked(fetchFile).mockResolvedValue({
      path: markdown,
      content: "# 熵\n\n熵是接收的每条消息中包含的信息的平均量。",
      blocks: [
        { id: "m1", tag: "h1", text: "熵", preview: "熵" },
        { id: "m2", tag: "p", text: "熵是接收的每条消息中包含的信息的平均量。", preview: "熵是…" },
      ],
      html: '<!DOCTYPE html><html><body><article><h1 id="na-m1" data-na-block="m1">熵</h1></article></body></html>',
      kind: "markdown",
    });
    useAcquireStore.setState({
      entries: [
        {
          url: markdown,
          title: "entropy.md",
          fidelity: "ok",
          kind: "markdown",
          capturedAt: 1,
          path: markdown,
        },
      ],
      focusedUrl: markdown,
    });

    const shown = await draw();

    // The same picker every other original uses: the block document is what is in
    // the frame, and the plain text view is what it replaces.
    expect(shown.querySelector("iframe")).not.toBeNull();
    expect(shown.querySelector("pre")).toBeNull();
    expect(shown.textContent).toContain(i18n.t("sources.picker.dragHint"));
  });

  it("offers fetching it again, and the browser, when no file stands behind it", async () => {
    useAcquireStore.setState({
      entries: [
        {
          url: "https://example.com/entropy",
          title: "Entropy",
          fidelity: "",
          kind: "html",
          capturedAt: 1,
        },
      ],
      focusedUrl: "https://example.com/entropy",
    });

    const shown = await draw();

    expect(shown.textContent).toContain(i18n.t("sources.preview.needFetch.title"));
    // Which page is being offered, so the reader can decide.
    expect(shown.textContent).toContain("example.com");
    expect(button(shown, "sources.refetch")).not.toBeNull();
    expect(button(shown, "sources.openInBrowse")).not.toBeNull();
    expect(vi.mocked(fetchFile)).not.toHaveBeenCalled();
  });

  it("asks the browser for a page it is already on, rather than opening it again", async () => {
    useAcquireStore.setState({
      entries: [
        {
          url: "https://example.com/entropy",
          title: "Entropy",
          fidelity: "",
          kind: "html",
          capturedAt: 1,
        },
      ],
      focusedUrl: "https://example.com/entropy",
    });
    // A page the reader is already on can only be signed in to there; opening it
    // again would start that over.
    useBrowseStore.setState((state) => ({
      guest: { ...state.guest, tabs: [tab("https://example.com/entropy")], activeId: 1 },
    }));

    const shown = await draw();

    expect(shown.textContent).toContain(i18n.t("sources.preview.needFetch.inBrowser"));
    expect(button(shown, "sources.takeFromBrowser")).not.toBeNull();
    expect(button(shown, "sources.openInBrowse")).toBeNull();
  });

  it("asks for a file that came from the computer to be added again", async () => {
    useAcquireStore.setState({
      entries: [
        {
          url: "local:html:4a073a162c2b",
          title: "notes.html",
          fidelity: "",
          kind: "html",
          capturedAt: 1,
        },
      ],
      focusedUrl: "local:html:4a073a162c2b",
    });

    const shown = await draw();

    // Nothing to fetch and no page to open: a button offering either would be a
    // promise the file's own identity cannot keep.
    expect(shown.textContent).toContain(i18n.t("sources.fallback.readd.title"));
    expect(button(shown, "sources.refetch")).toBeNull();
    expect(button(shown, "sources.openInBrowse")).toBeNull();
    expect(vi.mocked(fetchFile)).not.toHaveBeenCalled();
  });
});
