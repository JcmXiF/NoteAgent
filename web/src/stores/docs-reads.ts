import i18n from "@/i18n";
import { create } from "zustand";
import { fetchFile } from "@/lib/api/files";
import type { DerivedBlock } from "@/lib/api/types";
import { errorMessage } from "@/lib/api/client";

/**
 * Documents that have been read, by path.
 *
 * Where a document is shown does not change what it says, so it is read once and
 * kept here rather than by whichever pane asked for it: two panes showing the same
 * source share one read, and a read already on its way is not started again.
 *
 * The project is kept alongside, because a read that finishes after the user has
 * moved to another project must not land in the list.
 */

/** A document as it was read. */
export type FileRead = {
  status: "loading" | "ready" | "error";
  content: string;
  blocks: DerivedBlock[];
  /**
   * The blocks as a document a frame can show and a reader can drag over.
   *
   * Only originals get one: a markdown source is shown as its own blocks so a
   * passage of it can be picked, which is what a wiki page has its own reader for.
   */
  html: string | null;
  sourceUrl: string | null;
  kind: string | null;
  error: string | null;
};

function reading(): FileRead {
  return {
    status: "loading",
    content: "",
    blocks: [],
    html: null,
    sourceUrl: null,
    kind: null,
    error: null,
  };
}

type ReadsState = {
  project: string | null;
  reads: Record<string, FileRead>;
  /**
   * Starts a read unless the document is already read or on its way, and answers
   * with the document. Answering is what lets a caller use what was read — the
   * name a captured page gives itself, say — without reading it a second time.
   */
  read: (project: string, path: string) => Promise<FileRead | null>;
  /** Forgets everything, for a project change. */
  reset: (project?: string | null) => void;
  /** Forgets what has been read, so the next ask reads it again. */
  dropAll: () => void;
};

/** Reads already on their way, so two panes asking at once still read once. */
const inflight = new Map<string, Promise<FileRead | null>>();

export const useDocsReads = create<ReadsState>((set, get) => ({
  project: null,
  reads: {},

  read: (project, path) => {
    if (!project || !path) return Promise.resolve(null);
    const existing = get().reads[path];
    // A document already read stays read; one that failed is tried again, which
    // is what makes opening it a second time useful.
    if (existing && existing.status !== "error") return Promise.resolve(existing);
    const running = inflight.get(path);
    if (running) return running;
    if (get().project !== project) set({ project, reads: {} });

    const promise = (async (): Promise<FileRead | null> => {
      set((state) => ({ reads: { ...state.reads, [path]: reading() } }));
      try {
        const file = await fetchFile(project, path);
        if (get().project !== project) return null;
        const read: FileRead = {
          status: "ready",
          content: file.content ?? "",
          blocks: file.blocks ?? [],
          html: file.html ?? null,
          sourceUrl: file.sourceUrl ?? null,
          kind: file.kind ?? null,
          error: null,
        };
        set((state) => ({ reads: { ...state.reads, [path]: read } }));
        return read;
      } catch (err) {
        if (get().project !== project) return null;
        const failed: FileRead = {
          status: "error",
          content: "",
          blocks: [],
          html: null,
          sourceUrl: null,
          kind: null,
          error: errorMessage(err, i18n.t("docs.view.fileFailed")),
        };
        set((state) => ({ reads: { ...state.reads, [path]: failed } }));
        return failed;
      }
    })();

    inflight.set(path, promise);
    void promise.then(() => inflight.delete(path));
    return promise;
  },

  reset: (project = null) => {
    inflight.clear();
    set({ project, reads: {} });
  },

  dropAll: () => set({ reads: {} }),
}));
