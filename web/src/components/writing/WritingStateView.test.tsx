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
});
