import i18n from "@/i18n";
import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import {
  createNote,
  deleteNote,
  fetchNotes,
  fetchProjectNotes,
  updateNote,
  type Note,
  type NoteFields,
} from "@/lib/api/notes";

/**
 * The remarks written about a page, by the page's path.
 *
 * Read once and kept, for the same reason a document is: two panes showing one
 * page should not fetch the same remarks twice, and moving between panes should
 * not lose them.
 *
 * Not being able to read a page's remarks is kept apart from the page itself. A
 * page is read because the reader asked for it, and reading it is the whole
 * point; remarks are an addition, so failing at them says so in one line and
 * leaves the page alone.
 *
 * Writing is done from the remarks screen, which is looking at one page at a
 * time, so a write is answerable there: it lands in the caches that are already
 * loaded and its failure is handed back to the caller to show. Nothing is read
 * again after a write, because a remark is one file and what the write did to it
 * is known exactly.
 */

type NotesRead = {
  status: "loading" | "ready" | "error";
  notes: Note[];
  error: string | null;
};

function reading(): NotesRead {
  return { status: "loading", notes: [], error: null };
}

function ready(notes: Note[]): NotesRead {
  return { status: "ready", notes, error: null };
}

/** One remark in place, in the order the list is read in: oldest first. */
function withNote(notes: Note[], note: Note): Note[] {
  const next = notes.filter((item) => item.id !== note.id);
  next.push(note);
  return next.sort(
    (a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id),
  );
}

function withoutNote(notes: Note[], id: string): Note[] {
  return notes.filter((item) => item.id !== id);
}

/**
 * One page's read, with its list changed — or left exactly as it was.
 *
 * Only a read that has already succeeded is touched: a list that failed to load
 * is not a list this app can add anything to, and writing into it would show a
 * remark beside an error saying the remarks could not be read. A page nobody has
 * read yet is left alone too — there is no list to keep in step, and the next
 * read will bring everything.
 */
function pagesWith(
  pages: Record<string, NotesRead>,
  page: string,
  change: (notes: Note[]) => Note[],
): Record<string, NotesRead> {
  const read = pages[page];
  if (!read || read.status !== "ready") return pages;
  return { ...pages, [page]: ready(change(read.notes)) };
}

/** The project-wide read, changed the same way. */
function allWith(
  all: NotesRead | null,
  change: (notes: Note[]) => Note[],
): NotesRead | null {
  if (!all || all.status !== "ready") return all;
  return ready(change(all.notes));
}

type NotesState = {
  project: string | null;
  pages: Record<string, NotesRead>;
  /**
   * Every remark in the project, once somebody has asked.
   *
   * Kept apart from the page reads because it is a different question — which
   * remarks exist at all, rather than what belongs to one page — and only the
   * question of which remarks have lost their page needs it.
   */
  all: NotesRead | null;
  /**
   * Reads a page's remarks unless they are already read or on their way, and
   * answers with them.
   */
  read: (project: string, page: string) => Promise<NotesRead | null>;
  /** Reads every remark in the project, for finding the ones whose page has gone. */
  readAll: (project: string) => Promise<NotesRead | null>;
  /** Writes a new remark about a page, and answers with it. */
  create: (project: string, page: string, fields: NoteFields) => Promise<Note>;
  /** Changes a remark, and answers with what it is now. */
  update: (
    project: string,
    id: string,
    fields: Partial<NoteFields>,
  ) => Promise<Note>;
  /** Removes a remark. */
  remove: (project: string, page: string, id: string) => Promise<void>;
  /** Forgets everything, for a project change. */
  reset: (project?: string | null) => void;
  /** Forgets what has been read, so the next ask reads it again. */
  dropAll: () => void;
};

/** Reads already on their way, so two panes asking at once still read once. */
const inflight = new Map<string, Promise<NotesRead | null>>();
/** The project-wide read, which is not keyed by page and has no page to be keyed by. */
let inflightAll: Promise<NotesRead | null> | null = null;

export const useNotesStore = create<NotesState>((set, get) => ({
  project: null,
  pages: {},
  all: null,

  read: (project, page) => {
    if (!project || !page) return Promise.resolve(null);
    const existing = get().pages[page];
    // A page's remarks already read stay read; ones that failed are tried again,
    // which is what makes reopening the page useful.
    if (existing && existing.status !== "error") return Promise.resolve(existing);
    const running = inflight.get(page);
    if (running) return running;
    if (get().project !== project) set({ project, pages: {}, all: null });

    const promise = (async (): Promise<NotesRead | null> => {
      set((state) => ({ pages: { ...state.pages, [page]: reading() } }));
      try {
        const notes = await fetchNotes(project, page);
        if (get().project !== project) return null;
        const read = ready(notes);
        set((state) => ({ pages: { ...state.pages, [page]: read } }));
        return read;
      } catch (err) {
        if (get().project !== project) return null;
        const failed: NotesRead = {
          status: "error",
          notes: [],
          error: errorMessage(err, i18n.t("remark.loadFailed")),
        };
        set((state) => ({ pages: { ...state.pages, [page]: failed } }));
        return failed;
      }
    })();

    inflight.set(page, promise);
    void promise.then(() => inflight.delete(page));
    return promise;
  },

  readAll: (project) => {
    if (!project) return Promise.resolve(null);
    const existing = get().all;
    if (existing && existing.status !== "error") return Promise.resolve(existing);
    if (inflightAll) return inflightAll;
    if (get().project !== project) set({ project, pages: {}, all: null });

    const promise = (async (): Promise<NotesRead | null> => {
      set({ all: reading() });
      try {
        const notes = await fetchProjectNotes(project);
        if (get().project !== project) return null;
        const read = ready(notes);
        set({ all: read });
        return read;
      } catch (err) {
        if (get().project !== project) return null;
        const failed: NotesRead = {
          status: "error",
          notes: [],
          error: errorMessage(err, i18n.t("remark.orphans.loadFailed")),
        };
        set({ all: failed });
        return failed;
      }
    })();

    inflightAll = promise;
    void promise.then(() => {
      inflightAll = null;
    });
    return promise;
  },

  create: async (project, page, fields) => {
    const note = await createNote(project, page, fields);
    const add = (notes: Note[]) => withNote(notes, note);
    set((state) => ({
      pages: pagesWith(state.pages, note.page, add),
      all: allWith(state.all, add),
    }));
    return note;
  },

  update: async (project, id, fields) => {
    const note = await updateNote(project, id, fields);
    const replace = (notes: Note[]) => withNote(notes, note);
    set((state) => ({
      pages: pagesWith(state.pages, note.page, replace),
      all: allWith(state.all, replace),
    }));
    return note;
  },

  remove: async (project, page, id) => {
    await deleteNote(project, id);
    const drop = (notes: Note[]) => withoutNote(notes, id);
    set((state) => ({
      pages: pagesWith(state.pages, page, drop),
      all: allWith(state.all, drop),
    }));
  },

  reset: (project = null) => {
    inflight.clear();
    inflightAll = null;
    set({ project, pages: {}, all: null });
  },

  dropAll: () => {
    set({ pages: {}, all: null });
  },
}));
