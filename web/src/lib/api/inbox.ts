import { getJson, postJson, projectPath } from "@/lib/api/client";
import type {
  IngestRecoveryPayload,
  RevisionInboxPayload,
} from "@/lib/api/types";

/*
 * Two queues that are reported but never acted on automatically.
 *
 * The revision inbox lists sources that changed after they were compiled. It
 * only informs; nothing is written to the wiki until the user asks for it.
 */

export function fetchRevisionInbox(project: string): Promise<RevisionInboxPayload> {
  return getJson<RevisionInboxPayload>(projectPath(project, "revision-inbox"));
}

export function postRevisionInboxIgnore(
  project: string,
  identity: string,
  kind: string,
): Promise<RevisionInboxPayload> {
  return postJson<RevisionInboxPayload>(
    projectPath(project, "revision-inbox/ignore"),
    { identity, kind },
  );
}

export function postRevisionInboxAck(
  project: string,
  identity: string,
): Promise<RevisionInboxPayload> {
  return postJson<RevisionInboxPayload>(
    projectPath(project, "revision-inbox/ack"),
    { identity },
  );
}

/*
 * A compilation that was confirmed but interrupted. Opening a project does not
 * resume it; the user has to ask for it explicitly.
 */

export function fetchIngestRecovery(project: string): Promise<IngestRecoveryPayload> {
  return getJson<IngestRecoveryPayload>(projectPath(project, "ingest-recovery"));
}

export function postIngestRecoveryDiscard(
  project: string,
  id: string,
): Promise<IngestRecoveryPayload> {
  return postJson<IngestRecoveryPayload>(
    projectPath(project, "ingest-recovery/discard"),
    { id },
  );
}
