import { getJson, postJson, projectPath } from "@/lib/api/client";
import type {
  CompletedRoundPayload,
  DoctorPayload,
  IngestRecoveryJob,
  RevisionInboxItem,
} from "@/lib/api/types";

/** A page a run could not read behind a sign-in. */
export type CaptureInboxItem = {
  id: string;
  url: string;
  title: string;
  reason: string;
  httpStatus: number | null;
  runId: string;
  at: string;
  status: string;
};

/**
 * The one answer to "what is waiting on me".
 *
 * Four queues used to be read separately — a source that changed, a compile that
 * was interrupted, a run a crash left unfinished, and the wiki's own check — and
 * each had its own place to say so. They are one queue to the person reading
 * them, so they come back together, and the check is the part that can be asked
 * for only when someone is looking.
 */
export type InterruptedRunPayload = {
  sessionId: string;
  title: string;
  runId: string;
  reason: string;
  at: string;
  step: number;
  lastEntryId: string;
  lastMessage: string;
};

export type AttentionPayload = {
  revision: { openCount: number; items: RevisionInboxItem[] };
  recovery: {
    openCount: number;
    items: IngestRecoveryJob[];
    last: CompletedRoundPayload | null;
  };
  capture: { openCount: number; items: CaptureInboxItem[] };
  doctor: DoctorPayload | null;
  interruptedRuns?: InterruptedRunPayload[];
  counts: {
    revision: number;
    recovery: number;
    capture: number;
    doctor: number;
    interrupted?: number;
  };
  total: number;
};

/**
 * Reads the queue. `check` runs the wiki's own check as part of the answer; it is
 * skipped when this is a repeat read, because a check reads every page.
 */
export function fetchAttention(project: string, check = true): Promise<AttentionPayload> {
  const query = new URLSearchParams({ check: String(check) });
  return getJson<AttentionPayload>(`${projectPath(project, "attention")}?${query}`);
}

/** Puts one sign-in page away; it comes back only if it is met again. */
export function postCaptureInboxDismiss(
  project: string,
  id: string,
): Promise<{ openCount: number; items: CaptureInboxItem[] }> {
  return postJson(`${projectPath(project, "capture-inbox/dismiss")}`, { id });
}
