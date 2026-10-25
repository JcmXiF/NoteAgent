import { postSse, projectPath } from "@/lib/api/client";
import { currentLanguage } from "@/i18n";
import type { ConfirmedPage, IngestDone, TopicMap } from "@/lib/api/types";

type StepStatus = (message: string, stepId?: string) => void;

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
    onStatus?: StepStatus;
    signal?: AbortSignal;
  },
): Promise<TopicMap> {
  return postSse<TopicMap>(
    projectPath(project, "topic-map"),
    {
      intent: options.intent,
      ...(options.sourceIdentity ? { sourceIdentity: options.sourceIdentity } : {}),
      // The core cannot know what language the reader is using, and the reasons it
      // asks the model for are read by that reader. Page names follow the material
      // either way.
      language: currentLanguage(),
    },
    { onStatus: options.onStatus, signal: options.signal },
  );
}

/** Compiles only the pages the user confirmed, merging into existing pages where they exist. */
export function compileConfirmed(
  project: string,
  body: { intent: string; confirmed: ConfirmedPage[] },
  options: { onStatus?: StepStatus; signal?: AbortSignal } = {},
): Promise<IngestDone> {
  return postSse<IngestDone>(projectPath(project, "ingest-confirmed"), body, {
    onStatus: options.onStatus,
    signal: options.signal,
  });
}
