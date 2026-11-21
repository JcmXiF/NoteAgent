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

export type WritingSearchHit = {
  id?: string;
  title?: string;
  url?: string;
  snippet?: string;
  rank?: number;
};

export type WritingSearch = {
  id?: string;
  query?: string;
  status?: "ok" | "failed";
  hits?: WritingSearchHit[];
  error?: string;
  createdAt?: string;
};

/** The state as the core writes it; the other section items are JSON for now. */
export type WritingStatePayload = {
  version: number;
  stateVersion: number;
  writer: WritingWriter | null;
  searches: WritingSearch[];
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
