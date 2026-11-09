import { postSse, projectPath } from "@/lib/api/client";
import { currentLanguage } from "@/i18n";
import type { ConfirmedPage, IngestDone, TopicMap } from "@/lib/api/types";

type StepStatus = (message: string, stepId?: string) => void;

/** The chat wait this press is entering, when the job was proposed there. */
export type JobWait = {
  sessionId: string;
  waitId: string;
};

/** The body fields a job endpoint reads when the press belongs to a wait. */
function waitFields(wait?: JobWait): Record<string, string> {
  return wait ? { sessionId: wait.sessionId, waitId: wait.waitId } : {};
}

/**
 * Turns the selected passages into candidate pages.
 *
 * The prompt receives only the text derived from those passages, never search
 * snippets or material the user has not selected.
 */
export function planTopicMap(
  project: string,
  options: {
    intent: string;
    sourceIdentity?: string;
    wait?: JobWait;
    onStatus?: StepStatus;
    signal?: AbortSignal;
  },
): Promise<TopicMap> {
  return postSse<TopicMap>(
    projectPath(project, "topic-map"),
    {
      intent: options.intent,
      ...(options.sourceIdentity ? { sourceIdentity: options.sourceIdentity } : {}),
      ...waitFields(options.wait),
      // The core cannot know what language the reader is using, and the reasons it
      // asks the model for are read by that reader. Page names follow the material
      // either way.
      language: currentLanguage(),
    },
    { onStatus: options.onStatus, signal: options.signal },
  );
}

/**
 * Compiles only the pages the user confirmed, merging into existing pages where
 * they exist. A compile proposed by the chat carries the wait it belongs to, the
 * same way a plan does.
 */
export function compileConfirmed(
  project: string,
  body: { intent: string; confirmed: ConfirmedPage[] },
  options: { onStatus?: StepStatus; signal?: AbortSignal; wait?: JobWait } = {},
): Promise<IngestDone> {
  return postSse<IngestDone>(
    projectPath(project, "ingest-confirmed"),
    {
      ...body,
      ...waitFields(options.wait),
    },
    {
      onStatus: options.onStatus,
      signal: options.signal,
    },
  );
}
