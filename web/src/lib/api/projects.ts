import { getJson, postJson } from "@/lib/api/client";
import type { ProjectInfo } from "@/lib/api/types";

/** The backend returns `{projects: [...]}`; unwrap it so callers get an array. */
export async function fetchProjects(): Promise<ProjectInfo[]> {
  const payload = await getJson<{ projects: ProjectInfo[] }>("/projects");
  return payload.projects ?? [];
}

/** Makes `name` the project the core works on, for later requests. */
export function switchToProject(name: string): Promise<ProjectInfo> {
  return postJson<ProjectInfo>("/projects/use", { name });
}

export function createProject(name: string): Promise<ProjectInfo> {
  return postJson<ProjectInfo>("/projects", { name });
}

export function initProject(
  name: string,
): Promise<{ name: string; path: string; valid: boolean }> {
  return postJson<{ name: string; path: string; valid: boolean }>(
    `/projects/${encodeURIComponent(name)}/init`,
  );
}

type ProjectResetResult = {
  name: string;
  dryRun: boolean;
  deletedFiles: string[];
  restored: string[];
  clearedCache: boolean;
  clearedRunLog: boolean;
  /** The copy of the wiki kept in the trash, when a live reset made one. */
  trashId: string | null;
};

export function resetProject(
  name: string,
  options: { dryRun: true } | { dryRun?: false; confirmName: string },
): Promise<ProjectResetResult> {
  return postJson<ProjectResetResult>(`/projects/${encodeURIComponent(name)}/reset`, {
    dryRun: options.dryRun ?? false,
    confirmName: options.dryRun ? "" : options.confirmName,
  });
}

export function deleteProject(
  name: string,
  confirmName: string,
): Promise<{ deleted: string; active: string | null; trashId: string | null; unlinked: boolean }> {
  return postJson<{ deleted: string; active: string | null; trashId: string | null; unlinked: boolean }>(
    `/projects/${encodeURIComponent(name)}/delete`,
    { confirmName },
  );
}

/* ------------------------------------------------------- folders on disk */

/** What an existing folder holds, before it is opened or copied in. */
export type FolderInspection = {
  path: string;
  /** The name it would be listed under, empty when its own name cannot be one. */
  name: string;
  /** Already a project, so it can be read where it stands. */
  canLink: boolean;
  alreadyLinked: boolean;
  alreadyManaged: boolean;
  markdownCount: number;
  otherCount: number;
  bytes: number;
  /** The walk stopped counting; the figures are a lower bound. */
  truncated: boolean;
};

/** How a folder is opened: read where it is, or copied into the managed folder. */
export type FolderOpenMode = "link" | "copy";

export type FolderOpenResult = {
  name: string;
  path: string;
  mode: FolderOpenMode;
  imported: number;
  attachments: number;
  /** Pages left out because the program keeps a file of that name. */
  skipped: string[];
  alreadyLinked: boolean;
};

export function inspectFolder(path: string): Promise<FolderInspection> {
  return getJson<FolderInspection>(`/folders/inspect?path=${encodeURIComponent(path)}`);
}

export function openFolder(
  path: string,
  mode: FolderOpenMode,
  name = "",
): Promise<FolderOpenResult> {
  return postJson<FolderOpenResult>("/folders/open", { path, mode, name });
}

/** Where the whole project can be fetched as one archive. */
export function projectExportUrl(name: string): string {
  return `/api/projects/${encodeURIComponent(name)}/export`;
}

/** What removing a project would take: its pages, sources, notes and bytes. */
export type ProjectStats = {
  name: string;
  pages: number;
  sources: number;
  notes: number;
  bytes: number;
};

export function fetchProjectStats(name: string): Promise<ProjectStats> {
  return getJson<ProjectStats>(`/projects/${encodeURIComponent(name)}/stats`);
}
