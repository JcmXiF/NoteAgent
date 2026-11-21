import { getJson, projectPath } from "@/lib/api/client";

export type WritingWriter = {
  kind?: string;
  id?: string;
  since?: string;
};

export type WritingDraftCitation = {
  frameId?: string;
  frameVersion?: number;
  blockIds?: string[];
};

export type WritingDraftSection = {
  id?: string;
  heading?: string;
  body?: string;
  citations?: WritingDraftCitation[];
  version?: number;
  stale?: boolean;
  humanEdited?: boolean;
};

export type WritingDraftPage = {
  wikiFile?: string;
  title?: string;
  version?: number;
  status?: "draft" | "written";
  sections?: WritingDraftSection[];
  writtenAt?: string;
  effectIds?: string[];
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

export type WritingSelectedPage = {
  id?: string;
  hitId?: string;
  url?: string;
  title?: string;
  status?: "fetching" | "ready" | "failed";
  blockCount?: number;
  version?: number;
  material?: string;
  error?: string;
  errorKind?: string;
  fetchedAt?: string;
};

export type WritingFrame = {
  id?: string;
  pageId?: string;
  pageVersion?: number;
  blockIds?: string[];
  version?: number;
  source?: "agent" | "human";
  updatedAt?: string;
};

/** The state as the core writes it; the draft items are JSON for now. */
export type WritingStatePayload = {
  version: number;
  stateVersion: number;
  writer: WritingWriter | null;
  searches: WritingSearch[];
  selectedPages: WritingSelectedPage[];
  frames: WritingFrame[];
  draft: { pages?: WritingDraftPage[]; [key: string]: unknown };
  attention: Record<string, unknown>[];
};

export function fetchWritingState(
  project: string,
): Promise<WritingStatePayload> {
  return getJson<WritingStatePayload>(projectPath(project, "writing/state"));
}
