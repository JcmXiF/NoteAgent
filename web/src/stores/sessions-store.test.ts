import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/sessions", () => ({
  fetchSessions: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
}));

import {
  createSession,
  deleteSession,
  fetchSessions,
  renameSession,
} from "@/lib/api/sessions";
import { useSessionsStore } from "@/stores/sessions-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { SessionPayload } from "@/lib/api/types";

function summary(id: string, title: string) {
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
    status: "idle",
    sessions: [],
    currentId: null,
    error: "",
    migrationFailed: 0,
  });
});

describe("the session list", () => {
  it("loads the newest first and selects it when nothing was open", async () => {
    vi.mocked(fetchSessions).mockResolvedValue({
      sessions: [summary("s1", "First"), summary("s2", "Second")],
    });

    await useSessionsStore.getState().load("p");

    const state = useSessionsStore.getState();
    expect(state.status).toBe("ready");
    expect(state.sessions.map((item) => item.id)).toEqual(["s1", "s2"]);
    expect(state.currentId).toBe("s1");
  });

  it("keeps the session in front when it is still listed", async () => {
    useSessionsStore.setState({ currentId: "s2" });
    vi.mocked(fetchSessions).mockResolvedValue({
      sessions: [summary("s1", "First"), summary("s2", "Second")],
    });

    await useSessionsStore.getState().load("p");

    expect(useSessionsStore.getState().currentId).toBe("s2");
  });

  it("counts the old conversations the migration could not read", async () => {
    vi.mocked(fetchSessions).mockResolvedValue({
      sessions: [summary("s1", "First")],
      migration: {
        migrated: 1,
        failed: [{ id: "c-bad", error: "not JSON" }],
      },
    });

    await useSessionsStore.getState().load("p");

    expect(useSessionsStore.getState().migrationFailed).toBe(1);
  });

  it("creates a session, puts it first and opens it", async () => {
    useSessionsStore.setState({
      status: "ready",
      sessions: [summary("s1", "First")],
      currentId: "s1",
    });
    vi.mocked(createSession).mockResolvedValue(payload("s-new", "New"));

    await useSessionsStore.getState().create();

    const state = useSessionsStore.getState();
    expect(state.sessions[0].id).toBe("s-new");
    expect(state.currentId).toBe("s-new");
  });

  it("keeps the row current when a turn is committed", () => {
    useSessionsStore.setState({
      status: "ready",
      sessions: [summary("s1", "First"), summary("s2", "Second")],
      currentId: "s1",
    });
    const session = {
      ...payload("s2", "A better name"),
      updatedAt: "2026-11-10T10:00:00Z",
      messages: [
        { id: "e1", role: "user" as const, content: "q", createdAt: "" },
        {
          id: "e2",
          role: "assistant" as const,
          content: "a",
          createdAt: "",
        },
        {
          id: "e3",
          role: "assistant" as const,
          content: "step",
          createdAt: "",
          isStep: true,
        },
      ],
    };

    useSessionsStore.getState().touch(session);

    const state = useSessionsStore.getState();
    expect(state.sessions[0].id).toBe("s2");
    expect(state.sessions[0].title).toBe("A better name");
    expect(state.sessions[0].messageCount).toBe(3);
  });

  it("renames and removes a session", async () => {
    useSessionsStore.setState({
      status: "ready",
      sessions: [summary("s1", "First"), summary("s2", "Second")],
      currentId: "s1",
    });
    vi.mocked(renameSession).mockResolvedValue(payload("s1", "Renamed"));
    vi.mocked(deleteSession).mockResolvedValue(undefined);

    await useSessionsStore.getState().rename("s1", "Renamed");
    expect(useSessionsStore.getState().sessions[0].title).toBe("Renamed");

    await useSessionsStore.getState().remove("s1");
    expect(useSessionsStore.getState().sessions.map((item) => item.id)).toEqual([
      "s2",
    ]);
    expect(useSessionsStore.getState().currentId).toBe("s2");
  });
});
