import {
  getJson,
  postJson,
  postSse,
  projectPath,
} from "@/lib/api/client";

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
  staleReason?: string;
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

/** The four areas a person may edit, as the save sends them. */
export type WritingChanges = {
  searches?: WritingSearch[];
  selectedPages?: WritingSelectedPage[];
  frames?: WritingFrame[];
  draft?: { pages?: WritingDraftPage[] };
};

export type WritingArchive = {
  id?: string;
  stateVersion?: number;
  archivedAt?: string;
  searches?: number;
  selectedPages?: number;
  frames?: number;
  draftPages?: number;
};

export type WritingArchiveList = { items?: WritingArchive[] };

export function saveWritingState(
  project: string,
  baseVersion: number,
  changes: WritingChanges,
): Promise<WritingStatePayload> {
  return postJson<WritingStatePayload>(
    projectPath(project, "writing/state"),
    { baseVersion, changes },
  );
}

export function fetchWritingArchives(
  project: string,
): Promise<WritingArchiveList> {
  return getJson<WritingArchiveList>(
    projectPath(project, "writing/archives"),
  );
}

export function archiveWritingState(
  project: string,
): Promise<WritingArchiveList> {
  return postJson<WritingArchiveList>(
    projectPath(project, "writing/archive"),
  );
}

export function activateWritingArchive(
  project: string,
  archiveId: string,
): Promise<WritingStatePayload> {
  return postJson<WritingStatePayload>(
    projectPath(
      project,
      `writing/archives/${encodeURIComponent(archiveId)}/activate`,
    ),
  );
}

/**
 * The state's live read: every version change calls ``onState``.
 *
 * It is the same SSE reader as every other stream (one stall/abort policy),
 * over a POST so the request carries the project path. The stream has no
 * `done`; aborting the signal closes it.
 */
export function streamWritingState(
  project: string,
  onState: (stateVersion: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return postSse<void>(
    projectPath(project, "writing/events"),
    {},
    { signal, onState },
  );
}
