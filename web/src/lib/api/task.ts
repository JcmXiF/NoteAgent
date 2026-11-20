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
  TaskPageSpec,
  TaskPickResult,
  TaskResultView,
  TaskSummaryView,
} from "@/lib/api/types";

export function fetchTaskSummary(): Promise<TaskSummaryView> {
  return getJson<TaskSummaryView>("/task?view=summary");
}

export function fetchTaskCards(): Promise<TaskCardsView> {
  return getJson<TaskCardsView>("/task?view=cards");
}

export function fetchTaskMaterials(): Promise<TaskMaterialsView> {
  return getJson<TaskMaterialsView>("/task?view=materials");
}

export function fetchTaskFrames(): Promise<TaskFramesView> {
  return getJson<TaskFramesView>("/task?view=frames");
}

/**
 * The go-ahead that stands, and the write request still waiting for a person.
 *
 * The panel renders the request from this view, not from a chat-only copy, so
 * the person and the agent read one task.
 */
export function fetchTaskApproval(): Promise<TaskApprovalView> {
  return getJson<TaskApprovalView>("/task?view=approval");
}

/**
 * The last approved page write: its fate, the page identity and the diff.
 *
 * The core records this with the task, so the panel shows the one state the
 * executor wrote rather than a copy assembled from chat events.
 */
export function fetchTaskResult(): Promise<TaskResultView> {
  return getJson<TaskResultView>("/task?view=result");
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
  return postJson<TaskPickResult>("/task/pick", { cardId, expectedRev });
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
  return postJson<TaskFrameResult>("/task/frame", {
    docId,
    blocks,
    expectedRev,
  });
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
  return postJson<TaskApproveResult>("/task/approve", {
    taskId,
    rev,
    frameId,
    spec,
    decision,
  });
}
