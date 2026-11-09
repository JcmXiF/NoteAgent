// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SessionSidebar } from "@/components/chat/SessionSidebar";
import { useSessionsStore } from "@/stores/sessions-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/sessions", () => ({
  fetchSessions: vi.fn(),
  fetchSession: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  postSessionCancel: vi.fn(),
  postSessionQuery: vi.fn(),
  postSessionWaitCancel: vi.fn(),
}));

import {
  createSession,
  deleteSession,
  fetchSessions,
  renameSession,
} from "@/lib/api/sessions";
import type { SessionPayload, SessionSummary } from "@/lib/api/types";

let host: HTMLDivElement;
let root: Root;

function summary(id: string, title: string): SessionSummary {
  return {
    id,
    title,
    updatedAt: "2026-11-10T09:00:00Z",
    messageCount: 2,
  };
}

function payload(id: string, title: string): SessionPayload {
  return {
    id,
    title,
    updatedAt: "2026-11-10T09:00:00Z",
    state: { busy: false },
    messages: [],
    derived: { sections: {}, summary: "", messages: [], diagnostics: [] },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: "p" });
  useSessionsStore.setState({
    status: "ready",
    sessions: [summary("s1", "First"), summary("s2", "Second")],
    currentId: "s1",
    error: "",
    migrationFailed: 0,
  });
  vi.mocked(fetchSessions).mockResolvedValue({
    sessions: [summary("s1", "First"), summary("s2", "Second")],
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
});

async function draw() {
  await act(async () => {
    root.render(<SessionSidebar />);
  });
  await act(async () => {});
}

function button(name: string): HTMLButtonElement {
  const found = [...host.querySelectorAll("button")].find(
    (item) =>
      item.getAttribute("aria-label") === name ||
      item.textContent?.includes(name),
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found as HTMLButtonElement;
}

function renameBox(): HTMLInputElement {
  const found = host.querySelector<HTMLInputElement>(
    `input[aria-label="${i18n.t("chat.rename")}"]`,
  );
  if (!found) throw new Error("no rename box");
  return found;
}

function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("the session list", () => {
  it("draws the sessions and says when old ones could not be imported", async () => {
    vi.mocked(fetchSessions).mockResolvedValue({
      sessions: [summary("s1", "First"), summary("s2", "Second")],
      migration: {
        migrated: 1,
        failed: [{ id: "c-bad", error: "not JSON" }],
      },
    });

    await draw();

    expect(host.textContent).toContain("First");
    expect(host.textContent).toContain("Second");
    expect(host.textContent).toContain(
      i18n.t("chat.migrationFailed", { count: 1 }),
    );
  });

  it("creates a session and opens it", async () => {
    vi.mocked(createSession).mockResolvedValue(payload("s-new", ""));

    await draw();
    act(() => button(i18n.t("chat.new")).click());
    await act(async () => {});

    expect(createSession).toHaveBeenCalled();
    expect(useSessionsStore.getState().currentId).toBe("s-new");
  });

  it("renames in the row", async () => {
    vi.mocked(renameSession).mockResolvedValue(payload("s1", "Renamed"));

    await draw();
    act(() => button(i18n.t("chat.rename")).click());
    act(() => type(renameBox(), "Renamed"));
    await act(async () => {
      renameBox().dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });

    expect(renameSession).toHaveBeenCalledWith("s1", "Renamed");
    expect(host.textContent).toContain("Renamed");
  });

  it("deletes only after the reader confirms", async () => {
    vi.mocked(deleteSession).mockResolvedValue(undefined);

    await draw();
    act(() => button(i18n.t("chat.remove")).click());
    expect(deleteSession).not.toHaveBeenCalled();

    const confirm = [...document.body.querySelectorAll("button")].find(
      (item) => item.textContent?.includes(i18n.t("chat.delete.submit")),
    );
    await act(async () => {
      confirm?.click();
    });

    expect(deleteSession).toHaveBeenCalledWith("s1");
    expect(useSessionsStore.getState().sessions.map((item) => item.id)).toEqual(
      ["s2"],
    );
  });
});
