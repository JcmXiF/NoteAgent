// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { CommandPalette } from "@/components/search/CommandPalette";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { usePaletteStore } from "@/stores/palette-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/pages", () => ({
  searchProject: vi.fn(),
}));

import { searchProject } from "@/lib/api/pages";

/**
 * The palette does two things: it finds what a reader half-remembers, and it is
 * where the handful of actions that belong to no one view live. The second half is
 * only worth having if it is where the reader already looks — so an empty box shows
 * them rather than a sentence about typing.
 */

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: "p" });
  useLayoutStore.setState({ settingsOpen: false });
  useNavStore.setState({ seq: 0, intent: null, plan: null });
  usePaletteStore.setState({ open: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  act(() => {
    usePaletteStore.setState({ open: false });
  });
});

async function draw() {
  await act(async () => {
    root.render(<CommandPalette />);
  });
}

/** The dialog is drawn by the page, so its rows are not inside the host element. */
function action(name: string): HTMLButtonElement {
  const found = [...document.body.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(name),
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found;
}

function searchBox(): HTMLInputElement {
  const found = document.body.querySelector<HTMLInputElement>(
    `input[aria-label="${i18n.t("palette.title")}"]`,
  );
  if (!found) throw new Error("no search box");
  return found;
}

function type(value: string) {
  const input = searchBox();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Waits for the pause the palette takes before it searches. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 240));

describe("what the palette can do", () => {
  it("shows the actions before anything has been typed", async () => {
    await draw();

    expect(action(i18n.t("palette.action.source"))).toBeTruthy();
    expect(action(i18n.t("palette.action.import"))).toBeTruthy();
    expect(action(i18n.t("palette.action.export"))).toBeTruthy();
    expect(action(i18n.t("palette.action.settings"))).toBeTruthy();
  });

  it("opens settings and gets out of the way", async () => {
    await draw();

    act(() => action(i18n.t("palette.action.settings")).click());

    expect(useLayoutStore.getState().settingsOpen).toBe(true);
    expect(usePaletteStore.getState().open).toBe(false);
  });

  it("opens the folder dialog to import a project", async () => {
    await draw();

    act(() => action(i18n.t("palette.action.import")).click());

    expect(useWorkspaceStore.getState().dialog).toBe("open");
  });

  it("says a project is needed, and keeps those actions out of reach", async () => {
    useWorkspaceStore.setState({ current: null });
    await draw();

    expect(action(i18n.t("palette.action.source")).disabled).toBe(true);
    expect(action(i18n.t("palette.action.export")).disabled).toBe(true);
    expect(document.body.textContent).toContain(i18n.t("palette.action.needsProject"));
    // Importing is how a reader gets a project in the first place, and settings do
    // not belong to a project at all.
    expect(action(i18n.t("palette.action.import")).disabled).toBe(false);
    expect(action(i18n.t("palette.action.settings")).disabled).toBe(false);
  });

  it("narrows the actions to what was typed", async () => {
    await draw();

    act(() => type(i18n.t("palette.action.settings")));

    expect(document.body.textContent).not.toContain(i18n.t("palette.action.source"));
    expect(action(i18n.t("palette.action.settings"))).toBeTruthy();
  });
});

describe("finding something", () => {
  it("opens the page a result names", async () => {
    vi.mocked(searchProject).mockResolvedValue({
      query: "熵",
      pages: [
        {
          kind: "page",
          id: "entropy",
          path: "wiki/concepts/entropy.md",
          slug: "concepts/entropy",
          title: "熵",
          snippet: "",
          score: 1,
        },
      ],
      notes: [],
      sources: [],
      counts: { pages: 1, notes: 0, sources: 0 },
    });
    await draw();

    await act(async () => {
      type("熵");
    });
    await act(async () => {
      await settled();
    });

    expect(searchProject).toHaveBeenCalledWith("p", "熵", 6);
    expect(document.body.textContent).toContain("熵");

    await act(async () => {
      action("熵").click();
    });

    expect(useNavStore.getState().intent).toEqual({
      kind: "docs",
      path: "wiki/concepts/entropy.md",
    });
    expect(usePaletteStore.getState().open).toBe(false);
  });

  it("says so when the search fails, and still offers the actions", async () => {
    vi.mocked(searchProject).mockRejectedValue(new Error("搜不了"));
    await draw();

    await act(async () => {
      type("熵");
    });
    await act(async () => {
      await settled();
    });

    expect(document.body.textContent).toContain(i18n.t("palette.failed"));

    // What was typed is not an action, so the list is only the failure; clearing it
    // brings the actions back rather than leaving the palette broken.
    await act(async () => {
      type("");
    });
    expect(action(i18n.t("palette.action.settings"))).toBeTruthy();
  });
});
