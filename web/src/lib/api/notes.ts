import {
  deleteJson,
  getJson,
  postJson,
  projectPath,
  putJson,
} from "@/lib/api/client";

/**
 * A remark as the core keeps it.
 *
 * Field names are the wire format, so a rename on either side has to be mirrored.
 * `anchor` says where on the page the remark belongs, in the terms the reading
 * surface understands: empty for a remark that has not been placed yet, one of
 * two sentinels for the page's own start or end, and otherwise the text of a
 * section heading. `quote` is what was quoted when it was written, kept as the
 * way back to the section if that heading has since been renamed.
 */
export type Note = {
  id: string;
  page: string;
  anchor: string;
  quote: string;
  created: string;
  updated: string;
  body: string;
};

/**
 * What a writer sets.
 *
 * The rest of a remark is the core's to keep: it names the file, stamps the times
 * and decides where the file goes, so nothing here says where anything is stored.
 * `page` is not among these either — a remark is bound to its page once and stays
 * there.
 */
export type NoteFields = {
  /** The remark's own text. Empty is never written: that is a deletion. */
  body: string;
  anchor: string;
  quote: string;
};

/** The remarks written about one page, in the order they were made. */
export async function fetchNotes(project: string, page: string): Promise<Note[]> {
  const query = new URLSearchParams({ page });
  const payload = await getJson<{ page: string; notes: Note[] }>(
    `${projectPath(project, "notes")}?${query}`,
  );
  return payload.notes ?? [];
}

/**
 * Every remark in the project, whichever page it was written about.
 *
 * Asked for so the ones whose page has gone can be found: a remark is bound to a
 * page by name, and a page that was deleted or renamed takes nothing with it, so
 * the only way to notice is to compare the names against the pages that are there.
 */
export async function fetchProjectNotes(project: string): Promise<Note[]> {
  const payload = await getJson<{ page: string; notes: Note[] }>(
    projectPath(project, "notes"),
  );
  return payload.notes ?? [];
}

/** Writes a new remark about a page that is there, and answers with it. */
export async function createNote(
  project: string,
  page: string,
  fields: NoteFields,
): Promise<Note> {
  return postJson<Note>(projectPath(project, "notes"), { page, ...fields });
}

/** Changes a remark. A field left out keeps what it already had. */
export async function updateNote(
  project: string,
  id: string,
  fields: Partial<NoteFields>,
): Promise<Note> {
  return putJson<Note>(`${projectPath(project, "notes")}/${encodeURIComponent(id)}`, fields);
}

/**
 * Removes a remark.
 *
 * Answers whether there was one to remove: a remark deleted twice is not a
 * failure, but a caller that expected to delete something should be able to tell.
 */
export async function deleteNote(project: string, id: string): Promise<boolean> {
  const payload = await deleteJson<{ deleted: boolean }>(
    `${projectPath(project, "notes")}/${encodeURIComponent(id)}`,
  );
  return payload?.deleted === true;
}
