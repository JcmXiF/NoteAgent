import { getJson, projectPath } from "@/lib/api/client";
import type {
  CompletedRoundPayload,
  DoctorPayload,
  IngestRecoveryJob,
  RevisionInboxItem,
} from "@/lib/api/types";

/**
 * The one answer to "what is waiting on me".
 *
 * Three queues used to be read separately — a source that changed, a compile that
 * was interrupted, and the wiki's own check — and each had its own place to say so.
 * They are one queue to the person reading them, so they come back together, and
 * the check is the part that can be asked for only when someone is looking.
 */
export type AttentionPayload = {
  revision: { openCount: number; items: RevisionInboxItem[] };
  recovery: {
    openCount: number;
    items: IngestRecoveryJob[];
    last: CompletedRoundPayload | null;
  };
  doctor: DoctorPayload | null;
  counts: { revision: number; recovery: number; doctor: number };
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
