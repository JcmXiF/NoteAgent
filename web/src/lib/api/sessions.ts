import { getJson, postJson, postSse, type SseHandlers } from "@/lib/api/client";
import type { SessionPayload } from "@/lib/api/types";

/** One session, by id, as the versioned core reads it. */
export function fetchSession(sessionId: string): Promise<SessionPayload> {
  return getJson<SessionPayload>(`/v1/sessions/${encodeURIComponent(sessionId)}`);
}

/**
 * Asks the run to stop. The stream carries the answer, so it stays open: the
 * end of the run arrives there with the part that had been written.
 */
export function postSessionCancel(
  sessionId: string,
): Promise<{ cancelled: boolean }> {
  return postJson(`/v1/sessions/${encodeURIComponent(sessionId)}/cancel`);
}

type SessionQueryDone = {
  session: SessionPayload;
  run?: { id: string; status: string };
};

/**
 * Asks one question and resolves with the session after the run committed it.
 *
 * `onToken` carries the answer as it is written; `onTokenReset` takes back a
 * round that turned out to be a tool call rather than the answer.
 */
export function postSessionQuery(
  sessionId: string,
  content: string,
  handlers: SseHandlers = {},
): Promise<SessionPayload> {
  return postSse<SessionQueryDone>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/query`,
    { content },
    handlers,
  ).then((done) => done.session);
}
