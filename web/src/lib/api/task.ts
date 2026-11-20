/**
 * The writing task of the active project.
 *
 * Both reads are bounded views with a revision, the same ones the agent's
 * `inspect` sees. The interface never asks for the whole task file.
 */

import { getJson, postJson } from "@/lib/api/client";
import type {
  TaskApproveResult,
  TaskApprovalView,
  TaskCardsView,
  TaskFrameResult,
  TaskFramesView,
  TaskMaterialsView,
  TaskOpCancelResult,
  TaskOpResumeResult,
  TaskOverviewView,
  TaskPageSpec,
  TaskPickResult,
  TaskResultView,
  TaskSearchResult,
  TaskSummaryView,
} from "@/lib/api/types";

/**
 * Every bounded view from one task read, at one revision.
 *
 * The interface's snapshot: six separate reads could straddle a write and draw
 * two revisions at once. The individual views stay for other clients.
 */
export function fetchTaskOverview(): Promise<TaskOverviewView> {
  return getJson<TaskOverviewView>("/v1/task?view=overview");
}

export function fetchTaskSummary(): Promise<TaskSummaryView> {
  return getJson<TaskSummaryView>("/v1/task?view=summary");
}

export function fetchTaskCards(): Promise<TaskCardsView> {
  return getJson<TaskCardsView>("/v1/task?view=cards");
}

export function fetchTaskMaterials(): Promise<TaskMaterialsView> {
  return getJson<TaskMaterialsView>("/v1/task?view=materials");
}

export function fetchTaskFrames(): Promise<TaskFramesView> {
  return getJson<TaskFramesView>("/v1/task?view=frames");
}

/**
 * The go-ahead that stands, and the write request still waiting for a person.
 *
 * The panel renders the request from this view, not from a chat-only copy, so
 * the person and the agent read one task.
 */
export function fetchTaskApproval(): Promise<TaskApprovalView> {
  return getJson<TaskApprovalView>("/v1/task?view=approval");
}

/**
 * The last approved page write: its fate, the page identity and the diff.
 *
 * The core records this with the task, so the panel shows the one state the
 * executor wrote rather than a copy assembled from chat events.
 */
export function fetchTaskResult(): Promise<TaskResultView> {
  return getJson<TaskResultView>("/v1/task?view=result");
}

/**
 * A person's web search, on the same task, node and executor the agent uses.
 *
 * The revision is the one shown by the card view the reader searched from; a
 * task that moved since then answers `conflict` instead of letting the search
 * land on a state the person never saw. A search that takes longer than its
 * bounded wait answers requested/running and the executor finishes it.
 */
export function searchTaskCards(
  query: string,
  expectedRev: number,
): Promise<TaskSearchResult> {
  return postJson<TaskSearchResult>("/v1/task/search", { query, expectedRev });
}

/**
 * A person's pick, on the same task and executor the chat agent uses.
 *
 * The revision is the one shown by the view the reader clicked from; a task
 * that moved since then answers `conflict` instead of letting the pick land on
 * a state the person never saw.
 */
export function pickTaskCard(
  cardId: string,
  expectedRev: number,
): Promise<TaskPickResult> {
  return postJson<TaskPickResult>("/v1/task/pick", { cardId, expectedRev });
}

/**
 * A person's frame, on the same task and writer the chat agent uses.
 *
 * The revision is the one shown by the frames view the reader clicked from; a
 * task that moved since then answers `conflict` instead of letting the frame
 * land on a state the person never saw.
 */
export function frameTaskMaterial(
  docId: string,
  blocks: string[],
  expectedRev: number,
): Promise<TaskFrameResult> {
  return postJson<TaskFrameResult>("/v1/task/frame", {
    docId,
    blocks,
    expectedRev,
  });
}

/**
 * Stop an unfinished op for good.
 *
 * The core writes the terminal state before it signals the executor, so a page
 * is never saved and the conversation is not resumed for a cancelled op. A
 * cancel that loses the race with a finishing op answers `cancelled: false`.
 */
export function cancelTaskOp(opId: string): Promise<TaskOpCancelResult> {
  return postJson<TaskOpCancelResult>(
    `/task/ops/${encodeURIComponent(opId)}/cancel`,
  );
}

/**
 * Re-run an op a restart left interrupted, then resume the session once.
 *
 * The op must still be active and marked interrupted; a finished or cancelled
 * op is refused, so nothing already answered (or stopped by a person) runs
 * again.
 */
export function resumeTaskOp(opId: string): Promise<TaskOpResumeResult> {
  return postJson<TaskOpResumeResult>(
    `/task/ops/${encodeURIComponent(opId)}/resume`,
  );
}

/**
 * A person's decision on the pending write request.
 *
 * This is the only way an approval is written; no agent tool reaches it. The
 * revision is the one this view was read at, and the spec is the canonical one
 * the task stored, so a request that moved is refused instead of decided on.
 */
export function approveTaskRequest(
  taskId: string,
  rev: number,
  frameId: string,
  spec: TaskPageSpec,
  decision: "approve" | "reject",
): Promise<TaskApproveResult> {
  return postJson<TaskApproveResult>("/v1/task/approve", {
    taskId,
    rev,
    frameId,
    spec,
    decision,
  });
}

/* ------------------------------------------------------ change stream */

/** Every Task write announces its revision here; the task itself is not sent. */
export const TASK_EVENTS_URL = "/api/v1/task/events";

/** What a task stream reports: one invalidation, and each (re)connection. */
export type TaskStreamHandlers = {
  onEvent: (rev: number) => void;
  onOpen: () => void;
};

/**
 * The browser's EventSource, narrowed to what the task stream uses.
 *
 * It is a seam: a test hands in a fake source, and the store never reaches the
 * network. The real source reconnects on its own; the browser fires `open`
 * again after it does, which is the client's cue to read the task once.
 */
export type TaskEventSource = {
  addEventListener: (
    kind: string,
    listener: (event: MessageEvent) => void,
  ) => void;
  close: () => void;
  onopen: (() => void) | null;
};

export type TaskEventSourceFactory = (url: string) => TaskEventSource;

function openTaskEvents(url: string): TaskEventSource {
  return new EventSource(url) as unknown as TaskEventSource;
}

/**
 * Listen for the active project's task revisions.
 *
 * The stream carries `{rev}` only. A content write moves the revision; a log
 * write (op status, cancel, resume) may repeat it and is still sent, because
 * the panel reads op state from the same overview. The returned function
 * closes the connection; a project switch or an unmount calls it.
 */
export function subscribeTaskEvents(
  handlers: TaskStreamHandlers,
  createSource: TaskEventSourceFactory = openTaskEvents,
): () => void {
  const source = createSource(TASK_EVENTS_URL);
  source.addEventListener("task", (event) => {
    let payload: unknown;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    const rev =
      typeof payload === "object" && payload !== null
        ? (payload as { rev?: unknown }).rev
        : undefined;
    if (typeof rev === "number") handlers.onEvent(rev);
  });
  source.onopen = () => handlers.onOpen();
  return () => source.close();
}
