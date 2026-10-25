import { create } from "zustand";
import type { ModuleId } from "@/modules";

/**
 * Long-running jobs, in one place.
 *
 * Any work that waits on the network or the model registers here so it keeps
 * reporting progress after the user switches to another view, and so it can be
 * stopped or retried from a single panel.
 */
export type ActivityKind =
  | "search"
  | "fetch"
  | "import"
  | "save"
  | "plan"
  | "compose"
  | "chat"
  | "deep-search"
  | "project"
  | "misc";

type ActivityPhase = "running" | "done" | "error" | "cancelled";

export type ActivityStep = {
  id: string;
  label: string;
  state: "pending" | "current" | "done" | "blocked";
};

export type ActivityItem = {
  id: string;
  kind: ActivityKind;
  title: string;
  /** Which view the result belongs to, for the "go to the related view" action. */
  module?: ModuleId;
  phase: ActivityPhase;
  /** One-line progress, written by the job as it advances. */
  message?: string;
  /** Optional short step track, e.g. plan → compile. */
  steps?: ActivityStep[];
  currentStepId?: string;
  /** Appended log lines, shown on demand. Truncation is reported, never hidden. */
  detail: string[];
  error?: string;
  filesWritten: string[];
  createdAt: number;
  finishedAt?: number;
  /** Stops waiting on the stream; the backend stops before its next model call. */
  cancel?: () => void;
  retry?: () => void;
};

export type StartActivity = {
  kind: ActivityKind;
  title: string;
  module?: ModuleId;
  message?: string;
  steps?: ActivityStep[];
  cancel?: () => void;
  retry?: () => void;
};

type ActivityState = {
  items: ActivityItem[];

  start: (input: StartActivity) => string;
  update: (id: string, patch: Partial<ActivityItem>) => void;
  /** Writes the progress line; `stepId` also advances the step track. */
  setStatus: (id: string, message: string, stepId?: string) => void;
  appendDetail: (id: string, line: string) => void;
  finish: (id: string, patch?: { message?: string; filesWritten?: string[] }) => void;
  fail: (id: string, error: string) => void;
  markCancelled: (id: string) => void;
  cancel: (id: string) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
};

let counter = 0;

function advanceSteps(
  steps: ActivityStep[] | undefined,
  stepId: string | undefined,
): ActivityStep[] | undefined {
  if (!steps || !stepId) return steps;
  const index = steps.findIndex((step) => step.id === stepId);
  if (index < 0) return steps;
  // A track reports how far along the work is, so it never moves back. A job that
  // revisits an earlier phase — a chat turn thinks again after every tool call — is
  // still at least as far as it was, and a track that rewound would look broken.
  const furthest = steps.reduce(
    (highest, step, i) => (step.state === "done" || step.state === "current" ? Math.max(highest, i) : highest),
    0,
  );
  const at = Math.max(furthest, index);
  return steps.map((step, i) => ({
    ...step,
    state: i < at ? "done" : i === at ? "current" : "pending",
  }));
}

export const useActivityStore = create<ActivityState>((set, get) => ({
  items: [],

  start: (input) => {
    const id = `act-${++counter}`;
    const item: ActivityItem = {
      id,
      kind: input.kind,
      title: input.title,
      module: input.module,
      phase: "running",
      message: input.message,
      steps: input.steps,
      currentStepId: input.steps?.[0]?.id,
      detail: [],
      filesWritten: [],
      createdAt: Date.now(),
      cancel: input.cancel,
      retry: input.retry,
    };
    set((state) => ({ items: [item, ...state.items] }));
    return id;
  },

  update: (id, patch) =>
    set((state) => ({
      items: state.items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    })),

  setStatus: (id, message, stepId) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.id === id
          ? {
              ...item,
              message,
              currentStepId: stepId ?? item.currentStepId,
              steps: advanceSteps(item.steps, stepId),
            }
          : item,
      ),
    })),

  appendDetail: (id, line) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.id === id ? { ...item, detail: [...item.detail, line] } : item,
      ),
    })),

  finish: (id, patch) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.id === id
          ? {
              ...item,
              phase: "done",
              // The status line describes work in progress, so it has to go
              // unless the caller supplies an outcome to show instead.
              message: patch?.message,
              filesWritten: patch?.filesWritten ?? item.filesWritten,
              steps: item.steps?.map((step) => ({ ...step, state: "done" as const })),
              finishedAt: Date.now(),
              cancel: undefined,
            }
          : item,
      ),
    })),

  fail: (id, error) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.id === id
          ? { ...item, phase: "error", error, finishedAt: Date.now(), cancel: undefined }
          : item,
      ),
    })),

  markCancelled: (id) =>
    set((state) => ({
      items: state.items.map((item) =>
        // The status line describes work in progress, which this no longer is.
        item.id === id
          ? {
              ...item,
              phase: "cancelled",
              message: undefined,
              finishedAt: Date.now(),
              cancel: undefined,
            }
          : item,
      ),
    })),

  cancel: (id) => {
    const item = get().items.find((entry) => entry.id === id);
    if (!item || item.phase !== "running") return;
    item.cancel?.();
    set((state) => ({
      items: state.items.map((entry) =>
        entry.id === id
          ? {
              ...entry,
              phase: "cancelled",
              message: undefined,
              finishedAt: Date.now(),
              cancel: undefined,
            }
          : entry,
      ),
    }));
  },

  remove: (id) =>
    set((state) => ({ items: state.items.filter((item) => item.id !== id) })),

  clearFinished: () =>
    set((state) => ({ items: state.items.filter((item) => item.phase === "running") })),
}));
