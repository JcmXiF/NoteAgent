import type { Note, NoteFields } from "@/lib/api/notes";

/**
 * What writing a remark should do when it is committed.
 *
 * A remark is written on its own screen, with no save button: it is kept when the
 * writer looks away, moves on, or pauses. That makes "is there anything to keep"
 * a question asked of the remark rather than of the writer, which is why it is
 * decided here and by one function.
 *
 * The rule that matters is that an empty remark leaves nothing behind. A new one
 * exists only in memory until something is written into it, so abandoning it
 * writes nothing at all; an existing one that has been emptied is a deletion,
 * because there is no such thing as an empty remark — the core refuses to write
 * one. Emptiness is judged on the way out rather than on every keystroke: judging
 * it while typing would delete a remark the moment its text was selected and
 * replaced.
 *
 * Pure: no fetching, no React, no i18n. The caller does what this answers with.
 */

/** A remark being written, and what it was when writing began. */
export type Draft = {
  /** The remark's own name, or null while it is new and nothing has been written. */
  id: string | null;
  /** The page it is written about; a remark belongs to one page for good. */
  page: string;
  body: string;
  anchor: string;
  quote: string;
  /** What was there when writing began, so a commit can tell what changed. */
  baseline: NoteFields;
};

export type DraftSave =
  | { kind: "nothing" }
  | { kind: "create"; fields: NoteFields }
  | { kind: "update"; id: string; fields: NoteFields }
  | { kind: "delete"; id: string };

/** What committing this draft should do. */
export function draftSave(draft: Draft): DraftSave {
  const fields: NoteFields = {
    body: draft.body,
    anchor: draft.anchor,
    quote: draft.quote,
  };
  if (!draft.body.trim()) {
    // Nothing written and nothing on disk: abandoning it leaves no trace, which is
    // the whole point of a draft. Nothing written but already on disk: it is gone.
    return draft.id === null ? { kind: "nothing" } : { kind: "delete", id: draft.id };
  }
  if (draft.id === null) return { kind: "create", fields };
  if (sameFields(fields, draft.baseline)) return { kind: "nothing" };
  return { kind: "update", id: draft.id, fields };
}

/** A remark about to be written for the first time, about a page that is there. */
export function newDraft(page: string, place: Pick<NoteFields, "anchor" | "quote">): Draft {
  const baseline: NoteFields = { body: "", anchor: place.anchor, quote: place.quote };
  return { id: null, page, baseline, ...baseline };
}

/** A remark being changed, holding what it said so a commit can tell. */
export function draftOf(note: Note): Draft {
  const baseline: NoteFields = {
    body: note.body,
    anchor: note.anchor,
    quote: note.quote,
  };
  return { id: note.id, page: note.page, baseline, ...baseline };
}

function sameFields(a: NoteFields, b: NoteFields): boolean {
  return a.body === b.body && a.anchor === b.anchor && a.quote === b.quote;
}
