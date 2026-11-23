// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionViewHost } from "@/components/chat/SessionViewHost";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useSessionsStore } from "@/stores/sessions-store";

vi.mock("@/lib/api/sessions", () => ({
  fetchSession: vi.fn(),
  fetchSessions: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  postSessionCancel: vi.fn(),
  postSessionQuery: vi.fn(),
  postSessionWaitCancel: vi.fn(),
}));

import { fetchSession } from "@/lib/api/sessions";
import type { SessionPayload } from "@/lib/api/types";

let host: HTMLDivElement;
let root: Root;

function payload(id: string): SessionPayload {
  return {
    id,
    title: "Entropy",
    updatedAt: "2026-11-10T09:00:00Z",
    state: { busy: false },
    messages: [
      {
        id: "e1",
        role: "user",
        content: "安排一下",
        createdAt: "2026-11-10T09:00:00Z",
      },
    ],

    derived: { sections: {}, summary: "", messages: [], diagnostics: [] },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useSessionsStore.setState({
    status: "ready",
    sessions: [
      {
        id: "s1",
        title: "First",
        updatedAt: "2026-11-10T09:00:00Z",
        messageCount: 1,
      },
    ],
    currentId: "s1",
    error: "",
    migrationFailed: 0,
  });
  useLayoutStore.setState({ rightOpen: false, rightFolds: {} });
  useNavStore.setState({ seq: 0, intent: null });
  vi.mocked(fetchSession).mockResolvedValue(payload("s1"));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
});

async function draw() {
  await act(async () => {
    root.render(<SessionViewHost slot="main" />);
  });
  await act(async () => {});
}

describe("the session shell", () => {
  it("opens the session in front", async () => {
    await draw();

    expect(fetchSession).toHaveBeenCalledWith("s1");
    expect(host.textContent).toContain("安排一下");
  });

  it("opens the session a navigation intent names", async () => {
    vi.mocked(fetchSession).mockImplementation(async (id) => payload(id));
    useNavStore.setState({
      seq: 1,
      intent: { kind: "chat", conversationId: "s2" },
    });

    await draw();
    await act(async () => {});

    expect(useSessionsStore.getState().currentId).toBe("s2");
    expect(fetchSession).toHaveBeenCalledWith("s2");
  });
});
