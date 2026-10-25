import type { Note } from "@/lib/api/notes";
import { placeNotes } from "@/lib/note-placement";
import type { TreeFile } from "@/lib/project-tree";
import type { Section } from "@/lib/sections";

/**
 * The list of a page's remarks, in the order the page reads.
 *
 * The order is the reading surface's own: a remark belongs after the section it
 * names, so the list says what the page says and the two cannot disagree about
 * where a remark sits. A remark with no place comes last and says so.
 *
 * Pure: no fetching, no React, no i18n.
 */

/** Where a remark sits on its page. */
export type NotePlace =
  | { kind: "preamble" }
  | { kind: "end" }
  | { kind: "section"; heading: string }
  | { kind: "none" };

/** One remark, with where it sits. */
type ListedNote = {
  note: Note;
  place: NotePlace;
};

/** The page's remarks, ordered as the page reads. */
export function orderedNotes(
  notes: Note[],
  body: string,
  sections: Section[],
): ListedNote[] {
  const placement = placeNotes(notes, body, sections);
  const out: ListedNote[] = [];
  for (const note of placement.preamble) out.push({ note, place: { kind: "preamble" } });
  placement.sections.forEach((group, index) => {
    const heading = sections[index]?.heading ?? "";
    for (const note of group) out.push({ note, place: { kind: "section", heading } });
  });
  for (const note of placement.end) out.push({ note, place: { kind: "end" } });
  for (const note of placement.unplaced) out.push({ note, place: { kind: "none" } });
  return out;
}

/**
 * The remarks whose page is no longer in the project.
 *
 * A remark is bound to its page by name, so a page that was deleted or renamed
 * leaves the remark behind rather than taking it along. Neither this app nor the
 * core can tell a deleted page from a renamed one — both are a name that is not
 * there any more — so these are only reported, not acted on: which page they
 * belong to now is the reader's to say, and saying it is not something this stage
 * does.
 *
 * Pure: the caller supplies the pages the project has.
 */
export function orphanNotes(notes: Note[], files: TreeFile[]): Note[] {
  const known = new Set(files.map((file) => file.path));
  return notes.filter((note) => !known.has(note.page));
}

/**
 * The pages a remark can be written about.
 *
 * The project's own notes, and only those: a remark is about what the project has
 * written, and a stored original is material rather than a page anyone remarks on.
 * Ordered by the name each page goes by, because that is how they are picked.
 */
export function remarkPages(files: TreeFile[]): TreeFile[] {
  return files
    .filter((file) => file.path.startsWith("wiki/") && file.path.endsWith(".md"))
    .sort((a, b) => pageName(a).localeCompare(pageName(b), "zh"));
}

/** The name a page is shown by: its own title, or the last part of its path. */
export function pageName(file: TreeFile): string {
  return file.title || file.name.replace(/\.md$/i, "");
}
