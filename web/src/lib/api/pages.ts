import { getJson, postJson, projectPath } from "@/lib/api/client";

/**
 * A wiki page as something a person may change.
 *
 * The frontmatter is carried so the dialog can show what the page is made of, but
 * it is never sent back: the body is the part that is theirs.
 */
export type PageEditView = {
  path: string;
  title: string;
  body: string;
  frontmatter: string;
  /** What the editor opened, so a page written since is not overwritten. */
  revision: string;
  backlinkCount: number;
  noteCount: number;
};

export type PageSaveResult = {
  path: string;
  changed: boolean;
  diff: string;
  added: number;
  removed: number;
  revision: string;
};

export type PageVersion = {
  id: string;
  at: string;
  by: string;
  mode: string;
  bytes: number;
  current: boolean;
};

export type PageHistory = {
  path: string;
  versions: PageVersion[];
};

export type PageVersionText = {
  path: string;
  id: string;
  at: string;
  by: string;
  content: string;
};

export type PageBacklinks = {
  pages: { path: string; title: string }[];
  count: number;
  notes: number;
};

export type PageRenameResult = {
  from: string;
  path: string;
  rewritten: string[];
  notes: number;
  moved: boolean;
};

/** One thing a project search found. `pages` carry a slug; notes carry an id. */
export type ProjectSearchHit = {
  kind: "page" | "note" | "source";
  path: string;
  title: string;
  snippet: string;
  score: number;
  slug?: string;
  id?: string;
  page?: string;
};

export type ProjectSearch = {
  query: string;
  pages: ProjectSearchHit[];
  notes: ProjectSearchHit[];
  sources: ProjectSearchHit[];
  counts: { pages: number; notes: number; sources: number };
};

export function fetchPageEdit(project: string, path: string): Promise<PageEditView> {
  const query = new URLSearchParams({ path });
  return getJson<PageEditView>(`${projectPath(project, "page")}?${query}`);
}

export function savePageBody(
  project: string,
  request: { path: string; body: string; baseRevision?: string; preview?: boolean },
): Promise<PageSaveResult> {
  return postJson<PageSaveResult>(projectPath(project, "page/save"), request);
}

export function renamePage(
  project: string,
  path: string,
  to: string,
): Promise<PageRenameResult> {
  return postJson<PageRenameResult>(projectPath(project, "page/rename"), { path, to });
}

export function fetchPageBacklinks(
  project: string,
  path: string,
): Promise<PageBacklinks> {
  const query = new URLSearchParams({ path });
  return getJson<PageBacklinks>(`${projectPath(project, "page/backlinks")}?${query}`);
}

export function fetchPageHistory(project: string, path: string): Promise<PageHistory> {
  const query = new URLSearchParams({ path });
  return getJson<PageHistory>(`${projectPath(project, "page/history")}?${query}`);
}

export function fetchPageVersion(
  project: string,
  path: string,
  id: string,
): Promise<PageVersionText> {
  const query = new URLSearchParams({ path, id });
  return getJson<PageVersionText>(`${projectPath(project, "page/version")}?${query}`);
}

export function restorePageVersion(
  project: string,
  request: { path: string; id: string; baseRevision?: string },
): Promise<{ path: string; changed: boolean; revision: string }> {
  return postJson(projectPath(project, "page/restore"), request);
}

export function searchProject(
  project: string,
  query: string,
  limit = 8,
): Promise<ProjectSearch> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return getJson<ProjectSearch>(`${projectPath(project, "search")}?${params}`);
}
