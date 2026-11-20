import { create } from "zustand";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  approveTaskRequest,
  cancelTaskOp,
  fetchTaskApproval,
  fetchTaskCards,
  fetchTaskFrames,
  fetchTaskMaterials,
  fetchTaskResult,
  fetchTaskSummary,
  frameTaskMaterial,
  pickTaskCard,
  resumeTaskOp,
  searchTaskCards,
} from "@/lib/api/task";
import type {
  TaskApprovalView,
  TaskCardsView,
  TaskFramesView,
  TaskMaterialsView,
  TaskResultView,
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
  frames: TaskFramesView | null;
  /** The standing approval and the write request waiting for a person. */
  approvalView: TaskApprovalView | null;
  /** The last approved page write: its outcome, summary and page diff. */
  resultView: TaskResultView | null;
  error: string;
  /** Whether a human web search is in flight. */
  searching: boolean;
  /** Why the last search failed, in the reader's words. */
  searchError: string;
  /** What the last search left behind, in the reader's words. */
  searchNotice: string;
  /** The card being picked, while its op is in flight. */
  picking: string;
  /** Why the last pick failed, in the reader's words. */
  pickError: string;
  /** A pick that outlived its bounded wait, still running in the executor. */
  pickNotice: string;
  /** The material being framed, while its write is in flight. */
  framing: string;
  /** Why the last frame failed, in the reader's words. */
  frameError: string;
  /** What the last frame changed: saved, or an approval was cleared. */
  frameNotice: string;
  /** The decision being written, while its request is in flight. */
  deciding: "" | "approve" | "reject";
  /** Why the last decision failed, in the reader's words. */
  decisionError: string;
  /** What the last decision left behind. */
  decisionNotice: string;
  /** The op being cancelled or resumed, while its request is in flight. */
  opBusy: string;
  /** Why the last op action failed, in the reader's words. */
  opError: string;
  /** What the last op action left behind. */
  opNotice: string;
  load: () => Promise<void>;
  search: (query: string) => Promise<void>;
  pick: (cardId: string, expectedRev: number) => Promise<void>;
  frame: (
    docId: string,
    blocks: string[],
    expectedRev: number,
  ) => Promise<void>;
  decide: (decision: "approve" | "reject") => Promise<void>;
  cancelOp: (opId: string) => Promise<void>;
  resumeOp: (opId: string) => Promise<void>;
  reset: () => void;
};

/** A read that finishes after the reader switched projects must not land. */
let generation = 0;

export const useTaskStore = create<TaskState>((set, get) => ({
  status: "idle",
  summary: null,
  cards: null,
  materials: null,
  frames: null,
  approvalView: null,
  resultView: null,
  error: "",
  searching: false,
  searchError: "",
  searchNotice: "",
  picking: "",
  pickError: "",
  pickNotice: "",
  framing: "",
  frameError: "",
  frameNotice: "",
  deciding: "",
  decisionError: "",
  decisionNotice: "",
  opBusy: "",
  opError: "",
  opNotice: "",

  load: async () => {
    const mine = ++generation;
    set({ status: "loading", error: "" });
    try {
      const [summary, cards, materials, frames, approvalView, resultView] =
        await Promise.all([
          fetchTaskSummary(),
          fetchTaskCards(),
          fetchTaskMaterials(),
          fetchTaskFrames(),
          fetchTaskApproval(),
          fetchTaskResult(),
        ]);
      if (mine !== generation) return;
      set({
        status: "ready",
        summary,
        cards,
        materials,
        frames,
        approvalView,
        resultView,
      });
    } catch (reason) {
      if (mine !== generation) return;
      set({
        status: "error",
        error: errorMessage(reason, i18n.t("task.loadFailed")),
      });
    }
  },

  search: async (query) => {
    const cleaned = query.trim();
    const { summary } = get();
    if (!cleaned || !summary) return;
    set({ searching: true, searchError: "", searchNotice: "" });
    try {
      const result = await searchTaskCards(cleaned, summary.rev);
      set({
        searching: false,
        // A search that outlived its wait is still the executor's: the reader is
        // told to read the task again later, not to search a second time.
        searchNotice:
          result.status === "done"
            ? i18n.t("task.searchDone", { count: result.cards.length })
            : i18n.t("task.searchRunning"),
      });
      // The cards and the new revision are the task's; read them, do not patch.
      await get().load();
    } catch (reason) {
      set({
        searching: false,
        searchError: errorMessage(reason, i18n.t("task.searchFailed")),
      });
      // A conflict means this view's revision is stale; reading it again is
      // what lets the reader see the change and decide whether to search again.
      if (reason instanceof ApiError && reason.code === "conflict") {
        await get().load();
      }
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

  frame: async (docId, blocks, expectedRev) => {
    set({ framing: docId, frameError: "", frameNotice: "" });
    try {
      const result = await frameTaskMaterial(docId, blocks, expectedRev);
      set({
        framing: "",
        // The frame replaced the writing basis. If an approval stood on the
        // old one, the reader is told it no longer covers what would be written.
        frameNotice: result.approvalCleared
          ? i18n.t("task.approvalInvalidated")
          : i18n.t("task.frameSaved"),
      });
      await get().load();
    } catch (reason) {
      set({
        framing: "",
        frameError: errorMessage(reason, i18n.t("task.frameFailed")),
      });
      if (reason instanceof ApiError && reason.code === "conflict") {
        await get().load();
      }
    }
  },

  decide: async (decision) => {
    const { summary, approvalView } = get();
    const request = approvalView?.request;
    if (!summary || !request) return;
    set({ deciding: decision, decisionError: "", decisionNotice: "" });
    try {
      await approveTaskRequest(
        summary.id,
        summary.rev,
        request.frameId,
        request.spec,
        decision,
      );
      set({
        deciding: "",
        decisionNotice:
          decision === "approve"
            ? i18n.t("task.approvedNotice")
            : i18n.t("task.rejectedNotice"),
      });
      // The decision moved the task: read the one task again rather than
      // guessing what the approval or the removal left behind.
      await get().load();
    } catch (reason) {
      set({
        deciding: "",
        decisionError: errorMessage(reason, i18n.t("task.decisionFailed")),
      });
      // A conflict or a changed request means this view is stale; reading it
      // again shows the request the person would actually decide on.
      if (
        reason instanceof ApiError &&
        (reason.code === "conflict" || reason.code === "needs_approval")
      ) {
        await get().load();
      }
    }
  },

  cancelOp: async (opId) => {
    set({ opBusy: opId, opError: "", opNotice: "" });
    try {
      const result = await cancelTaskOp(opId);
      set({
        opBusy: "",
        opNotice: result.cancelled
          ? i18n.t("task.opCancelledNotice", { id: opId })
          : i18n.t("task.opAlreadyFinished"),
      });
      // The terminal state and any late result are the task's; read it again
      // instead of patching the view.
      await get().load();
    } catch (reason) {
      set({
        opBusy: "",
        opError: errorMessage(reason, i18n.t("task.opActionFailed")),
      });
    }
  },

  resumeOp: async (opId) => {
    set({ opBusy: opId, opError: "", opNotice: "" });
    try {
      await resumeTaskOp(opId);
      set({
        opBusy: "",
        opNotice: i18n.t("task.opResumedNotice", { id: opId }),
      });
      await get().load();
    } catch (reason) {
      set({
        opBusy: "",
        opError: errorMessage(reason, i18n.t("task.opActionFailed")),
      });
      // A conflict means the op moved since this view was read; the reloaded
      // view shows whether it finished, was cancelled or is still running.
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
      frames: null,
      approvalView: null,
      resultView: null,
      error: "",
      searching: false,
      searchError: "",
      searchNotice: "",
      picking: "",
      pickError: "",
      pickNotice: "",
      framing: "",
      frameError: "",
      frameNotice: "",
      deciding: "",
      decisionError: "",
      decisionNotice: "",
      opBusy: "",
      opError: "",
      opNotice: "",
    });
  },
}));
