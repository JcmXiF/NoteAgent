import { getJson, postJson, projectPath } from "@/lib/api/client";
import type {
  CompiledClipCollection,
  DerivedBlock,
  SourceCitationBundle,
  TreeFile,
} from "@/lib/api/types";

export async function fetchTree(project: string): Promise<TreeFile[]> {
  const payload = await getJson<{ project: string; files: TreeFile[] }>(
    projectPath(project, "tree"),
  );
  return payload.files ?? [];
}

export type SourceCitations = {
  sources: Record<string, SourceCitationBundle>;
  collections: CompiledClipCollection[];
};

export async function fetchSourceCitations(project: string): Promise<SourceCitations> {
  const payload = await getJson<{
    project: string;
    sources: Record<string, SourceCitationBundle>;
    collections: CompiledClipCollection[];
  }>(projectPath(project, "source-citations"));
  return {
    sources: payload.sources ?? {},
    collections: payload.collections ?? [],
  };
}

type ProjectFile = {
  path: string;
  content: string;
  blocks?: DerivedBlock[];
  /** The same blocks as a reader document, for the originals shown in a frame. */
  html?: string;
  kind?: string;
  renderer?: string;
  binary?: boolean;
  sourceUrl?: string;
  pageCount?: number;
};

export function fetchFile(project: string, path: string): Promise<ProjectFile> {
  const query = new URLSearchParams({ path });
  return getJson<ProjectFile>(`${projectPath(project, "file")}?${query}`);
}

/**
 * Byte URL for `<img>` and pdf.js. It is used as a `src`, not fetched, so it
 * keeps the `/api` prefix.
 */
export function fileBytesUrl(project: string, path: string): string {
  const query = new URLSearchParams({ path });
  return `/api${projectPath(project, "file-bytes")}?${query}`;
}

/**
 * What deleting one file says afterwards.
 *
 * The counts describe the page as it was just before it went, for a caller that
 * chose not to ask about it first; the trash id is how it can come back.
 */
export type DeletedFile = {
  deleted: string;
  droppedCollections: number;
  trashId: string;
  backlinkCount?: number;
  backlinkPages?: { path: string; title: string }[];
  noteCount?: number;
};

export function deleteFile(project: string, path: string): Promise<DeletedFile> {
  return postJson<DeletedFile>(projectPath(project, "file/delete"), { path });
}
