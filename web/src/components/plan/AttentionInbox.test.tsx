// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { AttentionInbox } from "@/components/plan/AttentionInbox";
import { fetchAttention, postCaptureInboxDismiss } from "@/lib/api/attention";
import { useLayoutStore } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/attention", () => ({
  fetchAttention: vi.fn(),
  postCaptureInboxDismiss: vi.fn(),
}));

const payload = {
  revision: { openCount: 0, items: [] },
  recovery: { openCount: 0, items: [], last: null },
  capture: {
    openCount: 1,
    items: [
      {
        id: "capture-1",
        url: "https://example.com/paid",
        title: "A page behind a sign-in",
        reason: "http_forbidden",
        httpStatus: 403,
        runId: "pr-1",
        at: "2026-11-10T09:00:00Z",
        status: "open",
      },
    ],
  },
  doctor: null,
  counts: { revision: 0, recovery: 0, capture: 1, doctor: 0 },
  total: 1,
};

describe("AttentionInbox capture queue", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    useWorkspaceStore.setState({ current: "demo" });
    useLayoutStore.setState({ rightOpen: true });
    vi.mocked(fetchAttention).mockResolvedValue(payload);
    vi.mocked(postCaptureInboxDismiss).mockResolvedValue({
      openCount: 0,
      items: [],
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  async function render(): Promise<void> {
    await act(async () => {
      root.render(<AttentionInbox />);
    });
    await act(async () => {});
  }

  it("shows a page waiting for a sign-in", async () => {
    await render();

    expect(container.textContent).toContain(i18n.t("attention.capture"));
    expect(container.textContent).toContain("A page behind a sign-in");
    expect(container.textContent).toContain("https://example.com/paid");
    expect(container.textContent).toContain(i18n.t("attention.openCapture"));
  });

  it("dismisses the page when asked", async () => {
    await render();

    const ignore = [...container.querySelectorAll("button")].find(
      (item) => item.textContent?.trim() === i18n.t("attention.ignore"),
    );
    expect(ignore).toBeTruthy();
    await act(async () => {
      ignore?.click();
    });

    expect(postCaptureInboxDismiss).toHaveBeenCalledWith("demo", "capture-1");
  });
});
