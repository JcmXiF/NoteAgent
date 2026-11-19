/**
 * The writing task of the active project.
 *
 * Both reads are bounded views with a revision, the same ones the agent's
 * `inspect` sees. The interface never asks for the whole task file.
 */

import { getJson, postJson } from "@/lib/api/client";
import type {
  TaskCardsView,
  TaskMaterialsView,
  TaskPickResult,
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
