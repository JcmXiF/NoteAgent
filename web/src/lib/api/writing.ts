import { getJson, projectPath } from "@/lib/api/client";

export type WritingWriter = {
  kind?: string;
  id?: string;
  since?: string;
};

export type WritingDraftPage = {
  wikiFile?: string;
  sections?: unknown[];
  [key: string]: unknown;
};

/** The state as the core writes it; section items are JSON for now. */
export type WritingStatePayload = {
  version: number;
  stateVersion: number;
  writer: WritingWriter | null;
  searches: Record<string, unknown>[];
  selectedPages: Record<string, unknown>[];
  frames: Record<string, unknown>[];
  draft: { pages?: WritingDraftPage[]; [key: string]: unknown };
  attention: Record<string, unknown>[];
};

export function fetchWritingState(
  project: string,
): Promise<WritingStatePayload> {
  return getJson<WritingStatePayload>(projectPath(project, "writing/state"));
}
