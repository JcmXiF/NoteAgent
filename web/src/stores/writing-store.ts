import i18n from "@/i18n";
import { create } from "zustand";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  activateWritingArchive,
  archiveWritingState,
  fetchWritingArchives,
  fetchWritingState,
  saveWritingState,
  type WritingArchive,
  type WritingDraftPage,
  type WritingFrame,
  type WritingSearch,
  type WritingSelectedPage,
  type WritingStatePayload,
} from "@/lib/api/writing";

/**
 * A person's edits to the four areas, kept beside the server's state.
 *
 * The draft is local until Save. The base version is the state version the
 * person loaded, so a save built on a stale read is refused rather than
 * overwriting a run or another tab; the local draft survives that refusal.
 */
export type WritingEditable = {
  searches: WritingSearch[];
  selectedPages: WritingSelectedPage[];
  frames: WritingFrame[];
  draft: { pages: WritingDraftPage[] };
};

export type WritingNotice = "busy" | "conflict" | "remote" | null;

export function editableFrom(state: WritingStatePayload): WritingEditable {
  return {
    searches: state.searches ?? [],
    selectedPages: state.selectedPages ?? [],
    frames: state.frames ?? [],
    draft: { pages: state.draft?.pages ?? [] },
  };
}

type WritingStore = {
  project: string | null;
  state: WritingStatePayload | null;
  draft: WritingEditable | null;
  baseVersion: number;
  dirty: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  notice: WritingNotice;
  archives: WritingArchive[];
  /** Read a project's state unless it is already read or on its way. */
  load: (project: string, force?: boolean) => Promise<void>;
  /** Change the local draft; nothing is sent until save. */
  edit: (update: (draft: WritingEditable) => WritingEditable) => void;
  save: () => Promise<void>;
  /** Drop the local draft and read the server's state again. */
  reload: () => Promise<void>;
  refreshArchives: () => Promise<void>;
  archive: () => Promise<void>;
  activate: (archiveId: string) => Promise<void>;
};

export const useWritingStore = create<WritingStore>((set, get) => {
  const runArchive = async (
    project: string,
    work: () => Promise<unknown>,
  ) => {
    set({ saving: true, error: null });
    try {
      await work();
      if (get().project !== project) return;
      set({
        saving: false,
        dirty: false,
        draft: null,
        notice: null,
      });
      await get().load(project, true);
    } catch (err) {
      if (get().project !== project) return;
      set({
        saving: false,
        error: errorMessage(
          err,
          i18n.t("writing.archiveError"),
        ),
      });
    }
  };

  return {
    project: null,
  state: null,
  draft: null,
  baseVersion: 0,
  dirty: false,
  loading: false,
  saving: false,
  error: null,
  notice: null,
  archives: [],
  load: async (project, force = false) => {
    const current = get();
    if (
      !force &&
      current.project === project &&
      (current.state !== null || current.loading)
    ) {
      return;
    }
    const moved = current.project !== project;
    set({
      project,
      loading: true,
      error: null,
      ...(moved
        ? {
            state: null,
            draft: null,
            baseVersion: 0,
            dirty: false,
            notice: null,
            archives: [],
          }
        : {}),
    });
    try {
      const state = await fetchWritingState(project);
      // A slow answer for the project we just left must not replace the one
      // the reader is looking at now.
      if (get().project !== project) return;
      let archives: WritingArchive[] = get().archives;
      try {
        const answer = await fetchWritingArchives(project);
        archives = answer.items ?? [];
      } catch {
        // The archives are a side list; their failure is not the state's.
      }
      if (get().project !== project) return;
      const { dirty, baseVersion, notice } = get();
      if (dirty) {
        // Keep the person's edits and the version they loaded; a save now is
        // meant to conflict, not to overwrite the change they never saw.
        const remote = state.stateVersion !== baseVersion;
        set({
          state,
          loading: false,
          archives,
          notice: remote ? "remote" : notice,
        });
        return;
      }
      set({
        state,
        draft: editableFrom(state),
        baseVersion: state.stateVersion,
        loading: false,
        archives,
        notice: null,
      });
    } catch (err) {
      if (get().project !== project) return;
      set({
        error: errorMessage(err, i18n.t("writing.error")),
        loading: false,
      });
    }
  },
  edit: (update) => {
    const state = get();
    const draft =
      state.draft ?? (state.state ? editableFrom(state.state) : null);
    if (!draft) return;
    set({ draft: update(draft), dirty: true });
  },
  save: async () => {
    const { project, draft, baseVersion, dirty, saving } = get();
    if (!project || !draft || !dirty || saving) return;
    set({ saving: true, notice: null, error: null });
    try {
      const state = await saveWritingState(
        project,
        baseVersion,
        draft,
      );
      if (get().project !== project) return;
      set({
        state,
        draft: editableFrom(state),
        baseVersion: state.stateVersion,
        dirty: false,
        saving: false,
        notice: null,
      });
    } catch (err) {
      if (get().project !== project) return;
      const code = err instanceof ApiError ? err.code : "";
      if (code === "state.busy" || code === "state.conflict") {
        set({
          saving: false,
          notice: code === "state.busy" ? "busy" : "conflict",
        });
        return;
      }
      set({
        saving: false,
        error: errorMessage(err, i18n.t("writing.saveError")),
      });
    }
  },
  reload: async () => {
    const project = get().project;
    if (!project) return;
    set({ dirty: false, draft: null, notice: null });
    await get().load(project, true);
  },
  refreshArchives: async () => {
    const project = get().project;
    if (!project) return;
    try {
      const answer = await fetchWritingArchives(project);
      if (get().project === project) {
        set({ archives: answer.items ?? [] });
      }
    } catch {
      // A side list; keep what is shown.
    }
  },
  archive: async () => {
    const project = get().project;
    if (!project) return;
    await runArchive(project, () =>
      archiveWritingState(project),
    );
  },
  activate: async (archiveId) => {
    const project = get().project;
    if (!project) return;
    await runArchive(project, () =>
      activateWritingArchive(project, archiveId),
    );
  },
  };
});
