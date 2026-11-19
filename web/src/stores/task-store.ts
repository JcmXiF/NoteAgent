import { create } from "zustand";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  fetchTaskCards,
  fetchTaskMaterials,
  fetchTaskSummary,
  pickTaskCard,
} from "@/lib/api/task";
import type {
  TaskCardsView,
  TaskMaterialsView,
  TaskSummaryView,
} from "@/lib/api/types";
import i18n from "@/i18n";

/**
 * The writing task the project is working on.
 *
 * The core is the truth: this store only holds the last bounded views it read.
 * The revision travels with them, so a reader can tell whether what is on
 * screen is what the agent saw. A person's pick is an op on that same task;
 * after it the views are read again rather than patched in place.
 */
type TaskState = {
  status: "idle" | "loading" | "ready" | "error";
  summary: TaskSummaryView | null;
  cards: TaskCardsView | null;
  materials: TaskMaterialsView | null;
  error: string;
  /** The card being picked, while its op is in flight. */
  picking: string;
  /** Why the last pick failed, in the reader's words. */
  pickError: string;
  /** A pick that outlived its bounded wait, still running in the executor. */
  pickNotice: string;
  load: () => Promise<void>;
  pick: (cardId: string, expectedRev: number) => Promise<void>;
  reset: () => void;
};

/** A read that finishes after the reader switched projects must not land. */
let generation = 0;

export const useTaskStore = create<TaskState>((set, get) => ({
  status: "idle",
  summary: null,
  cards: null,
  materials: null,
  error: "",
  picking: "",
  pickError: "",
  pickNotice: "",

  load: async () => {
    const mine = ++generation;
    set({ status: "loading", error: "" });
    try {
      const [summary, cards, materials] = await Promise.all([
        fetchTaskSummary(),
        fetchTaskCards(),
        fetchTaskMaterials(),
      ]);
      if (mine !== generation) return;
      set({ status: "ready", summary, cards, materials });
    } catch (reason) {
      if (mine !== generation) return;
      set({
        status: "error",
        error: errorMessage(reason, i18n.t("task.loadFailed")),
      });
    }
  },

  pick: async (cardId, expectedRev) => {
    set({ picking: cardId, pickError: "", pickNotice: "" });
    try {
      const result = await pickTaskCard(cardId, expectedRev);
      set({
        picking: "",
        pickNotice:
          result.status === "done" ? "" : i18n.t("task.pickRunning"),
      });
      await get().load();
    } catch (reason) {
      set({
        picking: "",
        pickError: errorMessage(reason, i18n.t("task.pickFailed")),
      });
      // A conflict means this view's revision is stale; reading it again is
      // what lets the reader see the change and decide whether to pick again.
      if (reason instanceof ApiError && reason.code === "conflict") {
        await get().load();
      }
    }
  },

  reset: () => {
    generation += 1;
    set({
      status: "idle",
      summary: null,
      cards: null,
      materials: null,
      error: "",
      picking: "",
      pickError: "",
      pickNotice: "",
    });
  },
}));
