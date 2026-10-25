import i18n from "@/i18n";
import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import type { Note, NoteFields } from "@/lib/api/notes";
import {
  draftOf,
  draftSave,
  newDraft,
  type Draft,
  type DraftSave,
} from "@/lib/note-draft";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The remarks screen's own state: which page is being worked on, which remark is
 * being read, and what is being written.
 *
 * Kept apart from the remarks themselves, which live in `notes-store` by page:
 * what a remark says is the project's, and which one is in front of the reader is
 * the reader's.
 *
 * Writing is settled in one place. Every way of leaving a remark — picking
 * another, starting a new one, changing the page — goes through `finish`, so the
 * rule about what an empty remark means is applied once rather than at each exit.
 * A failure there leaves the writer where they are with what they wrote, since
 * going on would lose it.
 */

type NotePageState = {
  /** The project the page, the remark and the draft belong to. */
  project: string | null;
  /** The page whose remarks are being managed. */
  target: string | null;
  /** The remark being read, if any. */
  selectedId: string | null;
  /** The remark being written, if any. Nothing else is being read while it is open. */
  draft: Draft | null;
  /** Whether a write is in flight, so the screen can say so. */
  saving: boolean;
  error: string | null;

  /** Shows a page's remarks, optionally with one of them in front. */
  openFor: (path: string, noteId?: string | null) => Promise<void>;
  /** Puts one remark in front. */
  select: (id: string | null) => Promise<void>;
  /** Starts a remark that has never been written, about the page being worked on. */
  startNew: () => Promise<void>;
  /** Starts changing a remark that is already written. */
  startEdit: (note: Note) => Promise<void>;
  setBody: (body: string) => void;
  /** Chooses where the remark goes, with the quote that stands behind the choice. */
  setPlace: (place: Pick<NoteFields, "anchor" | "quote">) => void;
  /**
   * Writes what has been written so far and stays open.
   *
   * This is the paused writer, not a writer who has left: an emptied remark is not
   * removed here, because a pause happens while text is being replaced and
   * removing it then would delete a remark mid-edit. Emptying is settled by
   * `finish`, which is what leaving looks like.
   */
  save: () => Promise<boolean>;
  /** Settles the draft, applying the whole rule — including that empty means gone. */
  finish: () => Promise<boolean>;
  /**
   * Lets the draft go without writing.
   *
   * Only ever loses a remark that was never written: what was on disk stays.
   */
  discard: () => void;
  clearError: () => void;
  /** Forgets the page and everything in front of it, for a project change. */
  reset: () => void;
};

export const useNotePageStore = create<NotePageState>((set, get) => ({
  project: null,
  target: null,
  selectedId: null,
  draft: null,
  saving: false,
  error: null,

  openFor: async (path, noteId = null) => {
    if (!(await get().finish())) return;
    if (get().target === path && get().selectedId === noteId) return;
    // The project is settled here, once: a remark written about a page belongs to
    // the wiki that page is in, however long the screen stays open.
    set({
      project: useWorkspaceStore.getState().current,
      target: path,
      selectedId: noteId,
      draft: null,
      error: null,
    });
  },

  select: async (id) => {
    if (get().selectedId === id && !get().draft) return;
    if (!(await get().finish())) return;
    set({ selectedId: id, draft: null, error: null });
  },

  startNew: async () => {
    const { target } = get();
    if (!target) return;
    if (!(await get().finish())) return;
    set({ draft: newDraft(target, { anchor: "", quote: "" }), selectedId: null, error: null });
  },

  startEdit: async (note) => {
    if (!(await get().finish())) return;
    set({ draft: draftOf(note), selectedId: note.id, error: null });
  },

  setBody: (body) => set((state) => (state.draft ? { draft: { ...state.draft, body } } : {})),

  setPlace: (place) =>
    set((state) => (state.draft ? { draft: { ...state.draft, ...place } } : {})),

  // A pause is not a departure: nothing is removed until the writer leaves.
  save: () =>
    inTurn(async () => {
      const draft = get().draft;
      const decision: DraftSave = draft ? draftSave(draft) : { kind: "nothing" };
      if (decision.kind === "delete") return true;
      return apply(decision, { close: false });
    }),

  finish: () =>
    inTurn(async () => {
      const draft = get().draft;
      const decision: DraftSave = draft ? draftSave(draft) : { kind: "nothing" };
      return apply(decision, { close: true });
    }),

  discard: () => set({ draft: null, error: null }),
  clearError: () => set({ error: null }),
  reset: () =>
    set({
      project: null,
      draft: null,
      selectedId: null,
      target: null,
      saving: false,
      error: null,
    }),
}));

/**
 * Writes run one at a time, and each judges the draft as it stands when its turn
 * comes.
 *
 * Leaving a remark happens in more than one move at once: the editor losing focus
 * writes it, and reaching for the button writes it again. Both would find the same
 * new remark and make two of it. Running them in turn means the second sees what
 * the first did — the remark now exists, so there is nothing left to create.
 */
let writing: Promise<boolean> = Promise.resolve(true);

function inTurn(work: () => Promise<boolean>): Promise<boolean> {
  const next = writing.then(work);
  // A write that somehow fails outright must not stop the ones behind it.
  writing = next.catch(() => false);
  return next;
}

/**
 * Carries out what a draft decided, and answers whether the writer can move on.
 *
 * Answering false is what keeps a failure from losing anything: the draft stays
 * exactly as it was, with the reason shown, and whoever asked to leave does not.
 */
async function apply(
  decision: DraftSave,
  { close }: { close: boolean },
): Promise<boolean> {
  const draft = useNotePageStore.getState().draft;
  if (!draft) return true;

  if (decision.kind === "nothing") {
    if (close) {
      // The remark does not stop existing because it was not changed, so it stays
      // the one in front. A draft that was never written has nothing to be.
      useNotePageStore.setState({ draft: null, selectedId: draft.id, error: null });
    }
    return true;
  }

  const project =
    useNotePageStore.getState().project ?? useWorkspaceStore.getState().current;
  if (!project) {
    // Nothing can be written without knowing where. The draft stays as it is, so
    // nothing written is lost while the reason is shown.
    useNotePageStore.setState({
      saving: false,
      error: i18n.t("remark.noProject"),
    });
    return false;
  }

  useNotePageStore.setState({ saving: true, error: null });
  try {
    const notes = useNotesStore.getState();
    let written: Note | null = null;
    if (decision.kind === "create") {
      written = await notes.create(project, draft.page, decision.fields);
    } else if (decision.kind === "update") {
      written = await notes.update(project, decision.id, decision.fields);
    } else {
      await notes.remove(project, draft.page, decision.id);
    }
    if (written && !close) {
      // Staying open: the draft keeps what was written and is now the remark that
      // exists, so typing on changes it rather than writing a second one. The text
      // is taken from the draft rather than from the answer, because the writer may
      // have typed on while the write was in flight.
      const now = useNotePageStore.getState().draft;
      const kept = now && now.id === draft.id ? now.body : draft.body;
      useNotePageStore.setState({
        saving: false,
        draft: { ...draftOf(written), body: kept },
      });
      return true;
    }
    useNotePageStore.setState({
      saving: false,
      draft: null,
      // A remark that was removed leaves nothing in front; one that was written is
      // the one the reader was working on, whether it is new or only changed.
      selectedId: decision.kind === "delete" ? null : (written?.id ?? draft.id),
    });
    return true;
  } catch (err) {
    useNotePageStore.setState({
      saving: false,
      error: errorMessage(err, i18n.t("remark.saveFailed")),
    });
    return false;
  }
}

/* ------------------------------------------------------------ project sync */

/**
 * Follows the active project.
 *
 * A remark is about a page in one wiki, and the screen follows the project that is
 * active now. A change of project therefore settles what is being written first —
 * into the wiki it belongs to, not into the one that just became active — and only
 * then lets the screen forget it. A write that fails leaves the draft exactly as it
 * was: going on would lose it.
 */
let started = false;

/** Settles a remark into its own project when the active project changes. */
export function watchNotePageProject(): () => void {
  return useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const page = useNotePageStore.getState();
    if (!page.draft) {
      page.reset();
      return;
    }
    void page.finish().then((written) => {
      if (written) useNotePageStore.getState().reset();
    });
  });
}

export function startNotePageSync(): void {
  if (started) return;
  started = true;
  watchNotePageProject();
}
