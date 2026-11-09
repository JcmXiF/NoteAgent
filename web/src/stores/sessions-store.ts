import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import {
  createSession,
  deleteSession,
  fetchSessions,
  renameSession,
} from "@/lib/api/sessions";
import type { SessionPayload, SessionSummary } from "@/lib/api/types";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * The sessions in the active project, and the one in front.
 *
 * The list is the product's front door: a session is created here, opened
 * here, and named or removed here. The session view itself reads only
 * `currentId`, so selecting a row is all it takes to show that conversation.
 */
type SessionsState = {
  status: "idle" | "loading" | "ready" | "error";
  sessions: SessionSummary[];
  currentId: string | null;
  error: string;
  /** How many old conversations the one-time migration could not read. */
  migrationFailed: number;
  load: (project: string) => Promise<void>;
  open: (sessionId: string) => void;
  /** A turn was committed: keep its row's name, count and place current. */
  touch: (session: SessionPayload) => void;
  create: () => Promise<void>;
  rename: (sessionId: string, title: string) => Promise<boolean>;
  remove: (sessionId: string) => Promise<boolean>;
  reset: () => void;
};

function summaryFrom(payload: SessionPayload): SessionSummary {
  return {
    id: payload.id,
    title: payload.title,
    updatedAt: payload.updatedAt,
    messageCount: payload.messages.length,
  };
}

export const useSessionsStore = create<SessionsState>((set, get) => ({
  status: "idle",
  sessions: [],
  currentId: null,
  error: "",
  migrationFailed: 0,

  load: async (project) => {
    set({ status: "loading", error: "" });
    try {
      const payload = await fetchSessions();
      // A project switch may have happened while the list was in flight.
      if (useWorkspaceStore.getState().current !== project) return;
      const sessions = payload.sessions;
      const kept = get().currentId;
      const currentId =
        kept && sessions.some((item) => item.id === kept)
          ? kept
          : (sessions[0]?.id ?? null);
      set({
        status: "ready",
        sessions,
        currentId,
        migrationFailed: payload.migration?.failed?.length ?? 0,
      });
    } catch (err) {
      if (useWorkspaceStore.getState().current !== project) return;
      set({
        status: "error",
        error: errorMessage(err, i18n.t("chat.listFailed")),
      });
    }
  },

  open: (sessionId) => {
    if (sessionId) set({ currentId: sessionId });
  },

  touch: (session) => {
    const messageCount = session.messages.filter(
      (item) => item.role === "user" || item.role === "assistant",
    ).length;
    const summary: SessionSummary = {
      id: session.id,
      title: session.title,
      updatedAt: session.updatedAt,
      messageCount,
    };
    const sessions = [
      summary,
      ...get().sessions.filter((item) => item.id !== session.id),
    ].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt),
    );
    set({ sessions, currentId: get().currentId ?? session.id });
  },

  create: async () => {
    const project = useWorkspaceStore.getState().current;
    if (!project) return;
    try {
      const created = await createSession();
      if (useWorkspaceStore.getState().current !== project) return;
      set({
        status: "ready",
        sessions: [summaryFrom(created), ...get().sessions],
        currentId: created.id,
        error: "",
      });
    } catch (err) {
      if (useWorkspaceStore.getState().current !== project) return;
      set({ error: errorMessage(err, i18n.t("chat.createFailed")) });
    }
  },

  rename: async (sessionId, title) => {
    try {
      const updated = await renameSession(sessionId, title);
      set({
        sessions: get().sessions.map((item) =>
          item.id === sessionId
            ? {
                ...item,
                title: updated.title,
                updatedAt: updated.updatedAt,
              }
            : item,
        ),
      });
      return true;
    } catch (err) {
      set({ error: errorMessage(err, i18n.t("chat.renameFailed")) });
      return false;
    }
  },

  remove: async (sessionId) => {
    try {
      await deleteSession(sessionId);
      const sessions = get().sessions.filter(
        (item) => item.id !== sessionId,
      );
      set({
        sessions,
        currentId:
          get().currentId === sessionId
            ? (sessions[0]?.id ?? null)
            : get().currentId,
      });
      return true;
    } catch (err) {
      set({ error: errorMessage(err, i18n.t("chat.deleteFailed")) });
      return false;
    }
  },

  reset: () =>
    set({
      status: "idle",
      sessions: [],
      currentId: null,
      error: "",
      migrationFailed: 0,
    }),
}));
