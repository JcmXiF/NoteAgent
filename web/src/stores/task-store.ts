import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import { fetchTaskCards, fetchTaskSummary } from "@/lib/api/task";
import type { TaskCardsView, TaskSummaryView } from "@/lib/api/types";
import i18n from "@/i18n";

/**
 * The writing task the project is working on.
 *
 * The core is the truth: this store only holds the last bounded views it read.
 * The revision travels with them, so a reader can tell whether what is on
 * screen is what the agent saw.
 */
type TaskState = {
  status: "idle" | "loading" | "ready" | "error";
  summary: TaskSummaryView | null;
  cards: TaskCardsView | null;
  error: string;
  load: () => Promise<void>;
  reset: () => void;
};

/** A read that finishes after the reader switched projects must not land. */
let generation = 0;

export const useTaskStore = create<TaskState>((set) => ({
  status: "idle",
  summary: null,
  cards: null,
  error: "",

  load: async () => {
    const mine = ++generation;
    set({ status: "loading", error: "" });
    try {
      const [summary, cards] = await Promise.all([
        fetchTaskSummary(),
        fetchTaskCards(),
      ]);
      if (mine !== generation) return;
      set({ status: "ready", summary, cards });
    } catch (reason) {
      if (mine !== generation) return;
      set({
        status: "error",
        error: errorMessage(reason, i18n.t("task.loadFailed")),
      });
    }
  },

  reset: () => {
    generation += 1;
    set({ status: "idle", summary: null, cards: null, error: "" });
  },
}));
