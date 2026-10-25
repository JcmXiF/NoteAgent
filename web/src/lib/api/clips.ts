import { getJson, putJson, projectPath } from "@/lib/api/client";
import type { ClipsPayload } from "@/lib/api/types";

/**
 * The selected passages, the stated intent, the plan and the confirmations.
 * The backend stores this document as a whole, so writes replace it entirely.
 */
export function fetchClips(project: string): Promise<ClipsPayload> {
  return getJson<ClipsPayload>(projectPath(project, "clips"));
}

export function putClips(project: string, body: ClipsPayload): Promise<ClipsPayload> {
  return putJson<ClipsPayload>(projectPath(project, "clips"), body);
}
